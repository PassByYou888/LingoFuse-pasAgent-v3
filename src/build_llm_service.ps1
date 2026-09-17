# =============================================================================
# build_llm_service.ps1
#
# Compiles the four LLM services into standalone Windows executables:
#
#     llm_service.exe       - local inference server (llama.cpp)
#     llm_proxy.exe         - pure text proxy (no tools)
#     llm_proxy_tool.exe    - proxy with server-side tools (LTB)
#     llm_test.exe          - interactive test client
#
# All four are packaged with PyInstaller --onefile. Output goes to .\dist\.
#
# Notes:
#   * The llm_common package must be collected as a whole, so we use
#     --collect-submodules llm_common instead of a list of --hidden-import
#     flags. New modules added to llm_common are picked up automatically.
#   * llm_proxy_tool.py imports language_middleware inside a try/except
#     ImportError block. PyInstaller's static analysis cannot see that
#     import, so it must be declared with --hidden-import.
#   * requests is intentionally NOT declared. Since the sse_client was
#     rewritten on top of http.client, neither llm_proxy.py nor
#     llm_proxy_tool.py imports requests at runtime. Leaving the flag in
#     would only add a dead package to the bundle.
#
# Usage:
#     cd src
#     .\build_llm_service.ps1
#
# Run from an elevated PowerShell if the output directory is protected.
# =============================================================================

$ErrorActionPreference = "Stop"

# --- Sanity checks -----------------------------------------------------------
if (-not (Test-Path ".\llm_proxy.py")) {
    Write-Host "[FATAL] llm_proxy.py not found in the current directory." -ForegroundColor Red
    Write-Host "        Run this script from the src\ directory that contains" -ForegroundColor Red
    Write-Host "        llm_service.py, llm_proxy.py, llm_proxy_tool.py and llm_test.py." -ForegroundColor Red
    exit 1
}

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Host "[FATAL] pyinstaller is not on PATH." -ForegroundColor Red
    Write-Host "        Install it with: pip install pyinstaller" -ForegroundColor Red
    exit 1
}

# --- Build helper ------------------------------------------------------------
# Returns $true on success, $false on failure. Records the result in
# $build_results so that the summary at the end can list both outcomes.
$build_results = @{}

function Invoke-PyInstaller {
    param(
        [string]$Target,   # "llm_service"
        [string]$Source    # "llm_service.py"
    )

    Write-Host ""
    Write-Host "==== Building $Target.exe ====" -ForegroundColor Cyan

    $common_args = @(
        "--onefile",
        "--name", $Target,
        "--noconfirm",
        "--clean",
        "--paths", ".",
        "--collect-submodules", "lingofuse",
        "--collect-submodules", "llm_common"
    )

    # Target-specific extra flags. Only llm_proxy_tool needs
    # language_middleware, which is imported inside a try/except.
    $extra_args = @()
    if ($Target -eq "llm_proxy_tool") {
        $extra_args += @("--hidden-import", "language_middleware")
    }

    $full_args = $common_args + $extra_args + @($Source)

    Write-Host "  pyinstaller $($full_args -join ' ')" -ForegroundColor DarkGray

    try {
        & pyinstaller @full_args
        if ($LASTEXITCODE -ne 0) {
            throw "pyinstaller exited with code $LASTEXITCODE"
        }
        Write-Host "  [OK] $Target.exe" -ForegroundColor Green
        $script:build_results[$Target] = "OK"
    }
    catch {
        Write-Host "  [FAILED] $Target.exe : $_" -ForegroundColor Red
        $script:build_results[$Target] = "FAILED"
    }
}

# --- Build each target -------------------------------------------------------
Invoke-PyInstaller -Target "llm_service"    -Source "llm_service.py"
Invoke-PyInstaller -Target "llm_proxy"      -Source "llm_proxy.py"
Invoke-PyInstaller -Target "llm_proxy_tool" -Source "llm_proxy_tool.py"
Invoke-PyInstaller -Target "llm_test"       -Source "llm_test.py"

# --- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "==== Build summary ====" -ForegroundColor Cyan
$any_failed = $false
foreach ($name in @("llm_service", "llm_proxy", "llm_proxy_tool", "llm_test")) {
    $status = $script:build_results[$name]
    if ($status -eq "OK") {
        Write-Host ("  {0,-20} {1}" -f $name, "OK") -ForegroundColor Green
    }
    else {
        Write-Host ("  {0,-20} {1}" -f $name, "FAILED") -ForegroundColor Red
        $any_failed = $true
    }
}

Write-Host ""
Write-Host "Output directory: .\dist\" -ForegroundColor Cyan
Write-Host "Runtime dependency: LingoFuse64.dll + z_ipc_64.dll must be on PATH" -ForegroundColor Cyan
Write-Host "                    or copied next to each exe before first run." -ForegroundColor Cyan

if ($any_failed) {
    exit 1
}
exit 0