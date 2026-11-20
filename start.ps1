param(
    [switch]$Setup,
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$echoPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if ($Setup) {
    if (-not (Test-Path -LiteralPath $echoPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    & $echoPython -m pip --cache-dir .cache\pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $echoPython -m echo.model_setup
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm.cmd ci --cache .cache\npm --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not (Test-Path -LiteralPath $echoPython) -or -not (Test-Path -LiteralPath "dist\index.html")) {
    throw "Run .\start.ps1 -Setup to install and build ECHO."
}
Write-Host "Open http://127.0.0.1:$Port in your browser."
& $echoPython -m uvicorn echo.server:app --host 127.0.0.1 --port $Port --no-access-log
exit $LASTEXITCODE
