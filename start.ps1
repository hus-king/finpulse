param([int]$Port = 8000, [switch]$Rebuild)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Python environment missing. Follow README.md to create .venv and install requirements.'
}
$localConfigPath = Join-Path $PSScriptRoot 'config.local.json'
if (Test-Path -LiteralPath $localConfigPath) {
    $localConfig = Get-Content -LiteralPath $localConfigPath -Raw | ConvertFrom-Json
    if ($env:FINPULSE_DB_ENGINE -ne 'sqlite' -and $localConfig.database.engine -eq 'mysql') {
        & (Join-Path $PSScriptRoot 'scripts\mysql-tunnel.ps1') -Background
    }
}
if ($Rebuild -or -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
}
Write-Host "FinPulse Demo: http://localhost:$Port"
& $pythonPath -m uvicorn backend.app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
