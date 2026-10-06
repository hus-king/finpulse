param([int]$LocalPort = 18000, [switch]$Background)
$ErrorActionPreference = 'Stop'
if ($LocalPort -lt 1024 -or $LocalPort -gt 65535) { throw 'LocalPort must be between 1024 and 65535.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$sshPath = (Get-Command ssh).Source
$sshArguments = @('-N', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-L', "127.0.0.1:${LocalPort}:127.0.0.1:8000", '-p', '11622', 'airhust@airhust.cn')
# OpenSSH uses the member's existing SSH configuration and authentication.
# Website access does not require this project's API keys or MySQL settings.
if (Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue) { throw "Port $LocalPort is in use. Choose another LocalPort." }
Write-Host "Website: http://localhost:$LocalPort (served by the remote server)."
if (-not $Background) {
    Write-Host 'Keep this SSH session running. Ctrl+C closes only the website tunnel.'
    & $sshPath @sshArguments
    exit $LASTEXITCODE
}
$runtimeRoot = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$sshArguments = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15') + $sshArguments
$tunnel = Start-Process -FilePath $sshPath -ArgumentList $sshArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'web-tunnel.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'web-tunnel.stderr.log')
Set-Content -LiteralPath (Join-Path $runtimeRoot 'web-tunnel.pid') -Value $tunnel.Id -Encoding ascii
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    Start-Sleep -Milliseconds 300
    $tunnel.Refresh()
    if ($tunnel.HasExited) { throw 'Website tunnel failed; check .runtime/web-tunnel.stderr.log.' }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0 -and $listeners[0].OwningProcess -eq $tunnel.Id) { Write-Host 'Website SSH tunnel is ready.'; return }
}
Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue
throw 'Website SSH tunnel did not become ready.'
