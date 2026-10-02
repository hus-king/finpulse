param([switch]$Background)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sshPath = (Get-Command ssh).Source
$sshArguments = @('-N', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3', '-L', '127.0.0.1:13306:127.0.0.1:3306', '-p', '11622', 'airhust@airhust.cn')
if (-not $Background) {
    Write-Host 'MySQL tunnel: 127.0.0.1:13306 -> airhust.cn:3306. Keep this terminal open; Ctrl+C closes only the tunnel.'
    & $sshPath @sshArguments
    exit $LASTEXITCODE
}
$existing = @(Get-NetTCPConnection -State Listen -LocalPort 13306 -ErrorAction SilentlyContinue)
if ($existing.Count -gt 0) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($existing[0].OwningProcess)"
    if ($owner.Name -eq 'ssh.exe' -and $owner.CommandLine -match 'airhust@airhust.cn' -and $owner.CommandLine -match '11622' -and $owner.CommandLine -match '13306:127.0.0.1:3306') {
        Write-Host 'Reusing the existing FinPulse MySQL tunnel.'
        return
    }
    throw 'Port 13306 is occupied by another process. Resolve the conflict before starting the MySQL tunnel.'
}
$runtimeRoot = Join-Path $projectRoot '.runtime'
New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
$tunnelProcess = Start-Process -FilePath $sshPath -ArgumentList $sshArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'mysql-tunnel.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'mysql-tunnel.stderr.log')
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Milliseconds 300
    $tunnelProcess.Refresh()
    if ($tunnelProcess.HasExited) { throw 'SSH tunnel failed. Check .runtime/mysql-tunnel.stderr.log and your SSH key access.' }
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort 13306 -ErrorAction SilentlyContinue)
    if ($listener.Count -gt 0 -and $listener[0].OwningProcess -eq $tunnelProcess.Id) {
        Set-Content -LiteralPath (Join-Path $runtimeRoot 'mysql-tunnel.pid') -Value $tunnelProcess.Id -Encoding ascii
        Write-Host 'MySQL tunnel is ready on 127.0.0.1:13306.'
        return
    }
}
if (-not $tunnelProcess.HasExited) { $tunnelProcess.Kill() }
throw 'SSH tunnel did not become ready within the timeout.'
