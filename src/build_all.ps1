# build_all.ps1
# Run 3 ps1 + 1 bat build scripts in order.

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

# Task list: type (ps1/bat) + file name, in run order
$tasks = @(
    @{ Type = 'ps1'; File = 'build_llm_service.ps1'  },
    @{ Type = 'ps1'; File = 'build_mcp_api_tool.ps1' },
    @{ Type = 'ps1'; File = 'build_bridge.ps1'       },
    @{ Type = 'bat'; File = 'build_pascal_agent.bat' }
)

$total = $tasks.Count
$index = 0
$sw = [System.Diagnostics.Stopwatch]::StartNew()

foreach ($t in $tasks) {
    $index++
    $path = Join-Path $scriptDir $t.File

    Write-Host ""
    Write-Host "=== [$index/$total] $($t.File) ===" -ForegroundColor Cyan

    if (-not (Test-Path $path)) {
        Write-Host "SKIP: file not found - $path" -ForegroundColor Yellow
        continue
    }

    $itemSw = [System.Diagnostics.Stopwatch]::StartNew()

    if ($t.Type -eq 'ps1') {
        # Run ps1 in current session (keeps env vars, cwd, etc.)
        & $path
    }
    else {
        # bat must be run via cmd /c
        & cmd.exe /c "`"$path`""
    }

    $code = $LASTEXITCODE
    $itemSw.Stop()
    $secs = '{0:N1}' -f $itemSw.Elapsed.TotalSeconds

    if ($code -ne 0 -and $null -ne $code) {
        Write-Host "FAIL: $($t.File) exit code=$code, $secs s" -ForegroundColor Red
        throw "Build failed: $($t.File)"
    }
    else {
        Write-Host "OK: $($t.File) done in $secs s" -ForegroundColor Green
    }
}

$sw.Stop()
$totalSecs = '{0:N1}' -f $sw.Elapsed.TotalSeconds
Write-Host ""
Write-Host "=== All builds done in $totalSecs s ===" -ForegroundColor Green
