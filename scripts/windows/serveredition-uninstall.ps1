# ============================================================================
# SlowBooks Pro - Server Edition uninstall (Windows)
#
# Removes the startup task and firewall rule. Books and keys under
# C:\ProgramData\SlowBooksPro are deliberately left in place - delete
# that folder yourself if you're sure.
# ============================================================================
#Requires -RunAsAdministrator
$ErrorActionPreference = "SilentlyContinue"

# The books are wherever the task served them from (an install made with
# -DataDir keeps them elsewhere than the default).
$ServerData = "$env:ProgramData\SlowBooksPro\data"
$task = Get-ScheduledTask -TaskName "SlowBooksProServer"
if ($task -and $task.Actions.Count -gt 0 -and $task.Actions[0].Arguments -match '--data-dir "([^"]+)"') {
    $ServerData = $Matches[1]
}
$DataDir = Split-Path -Parent $ServerData

Stop-ScheduledTask -TaskName "SlowBooksProServer"
Get-Process -Name SlowBooksPro | Stop-Process -Force
schtasks /Delete /TN "SlowBooksProServer" /F | Out-Null
netsh advfirewall firewall delete rule name="SlowBooks Pro Server Edition" | Out-Null

Write-Host "Server Edition task + firewall rule removed." -ForegroundColor Green
Write-Host "Your books are untouched at $ServerData (keys in $DataDir\.env)."
