param([switch]$Background, [switch]$Reconnect)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sshPath = (Get-Command ssh).Source
$sshArguments = @('-N', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-L', '127.0.0.1:13306:127.0.0.1:3306', '-p', '11622', 'airhust@airhust.cn')
if (-not $Background) {
    Write-Host 'MySQL tunnel: 127.0.0.1:13306 -> airhust.cn:3306. Keep this terminal open; Ctrl+C closes only the tunnel.'
    do {
        & $sshPath @sshArguments
        if (-not $Reconnect) { exit $LASTEXITCODE }
        Write-Host 'SSH tunnel disconnected. Retrying in 5 seconds; Ctrl+C stops reconnection.'
        Start-Sleep -Seconds 5
    } while ($Reconnect)
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
$supervisorPidPath = Join-Path $runtimeRoot 'mysql-tunnel-supervisor.pid'
$tunnelProcess = $null
if (Test-Path -LiteralPath $supervisorPidPath) {
    $savedPid = [int](Get-Content -LiteralPath $supervisorPidPath)
    $supervisor = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid"
    if ($supervisor.CommandLine -like '*mysql-tunnel.ps1*' -and $supervisor.CommandLine -like '*-Reconnect*') {
        $tunnelProcess = Get-Process -Id $savedPid
    }
}
if (-not $tunnelProcess) {
    $supervisorArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Reconnect')
    $tunnelProcess = Start-Process -FilePath 'powershell.exe' -ArgumentList $supervisorArguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeRoot 'mysql-tunnel.stdout.log') -RedirectStandardError (Join-Path $runtimeRoot 'mysql-tunnel.stderr.log')
    Set-Content -LiteralPath $supervisorPidPath -Value $tunnelProcess.Id -Encoding ascii
}
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 300
    $tunnelProcess.Refresh()
    if ($tunnelProcess.HasExited) { throw 'SSH tunnel failed. Check .runtime/mysql-tunnel.stderr.log and your SSH key access.' }
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort 13306 -ErrorAction SilentlyContinue)
    if ($listener.Count -gt 0) {
        $listenerOwner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
        if ($listenerOwner.Name -ne 'ssh.exe' -or $listenerOwner.CommandLine -notmatch '13306:127.0.0.1:3306' -or $listenerOwner.CommandLine -notmatch 'airhust@airhust.cn') { throw 'Port 13306 belongs to an unexpected process.' }
        Set-Content -LiteralPath (Join-Path $runtimeRoot 'mysql-tunnel.pid') -Value $listener[0].OwningProcess -Encoding ascii
        Write-Host 'MySQL tunnel is ready on 127.0.0.1:13306.'
        return
    }
}
throw 'SSH tunnel did not become ready. The hidden supervisor is retrying; check .runtime/mysql-tunnel.stderr.log.'
