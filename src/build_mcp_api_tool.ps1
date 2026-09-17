# =============================================================================
# build_mcp_api_tool.ps1
#
# Compiles the MCP gateway and its stdio debugging proxy:
#
#     mcp_api_tool.exe     - MCP gateway (path A: client-side tools)
#     mcp_api_proxy.exe    - stdio forwarder for protocol debugging
#
# Output goes to .\dist\.
#
# Notes:
#   * mcp_api_tool.py imports language_middleware and generate_agent_json
#     inside try/except ImportError blocks. PyInstaller's static analysis
#     cannot see those imports, so they must be declared with
#     --hidden-import.
#   * fastmcp, pydantic, and tzdata carry runtime data files. They are
#     collected as a whole with --collect-all.
#   * The lingofuse package is bundled as a local package alongside
#     llm_common (which mcp_api_tool does not actually use at runtime,
#     but the command line is shared with the other build scripts for
#     consistency).
#
# Usage:
#     cd src
#     .\build_mcp_api_tool.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".\mcp_api_tool.py")) {
    Write-Host "[FATAL] mcp_api_tool.py not found in the current directory." -ForegroundColor Red
    exit 1
}

$pyinstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyinstaller) {
    Write-Host "[FATAL] pyinstaller is not on PATH." -ForegroundColor Red
    Write-Host "        Install it with: pip install pyinstaller" -ForegroundColor Red
    exit 1
}

$build_results = @{}

function Invoke-PyInstaller {
    param(
        [string]$Target,
        [string]$Source,
        [string[]]$ExtraArgs = @()
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

    $full_args = $common_args + $ExtraArgs + @($Source)

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

# --- mcp_api_tool.exe --------------------------------------------------------
Invoke-PyInstaller -Target "mcp_api_tool" -Source "mcp_api_tool.py" -ExtraArgs @(
    "--collect-all", "fastmcp",
    "--collect-all", "pydantic",
    "--collect-all", "tzdata",
    "--hidden-import", "language_middleware",
    "--hidden-import", "generate_agent_json"
)

# --- mcp_api_proxy.exe -------------------------------------------------------
# The proxy is a pure stdlib script: no hidden imports required.
Invoke-PyInstaller -Target "mcp_api_proxy" -Source "mcp_api_proxy.py"

# --- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "==== Build summary ====" -ForegroundColor Cyan
$any_failed = $false
foreach ($name in @("mcp_api_tool", "mcp_api_proxy")) {
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

if ($any_failed) {
    exit 1
}
exit 0