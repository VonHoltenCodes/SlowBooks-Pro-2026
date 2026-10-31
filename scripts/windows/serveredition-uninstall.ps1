# ============================================================================
# SlowBooks Pro - Server Edition uninstall (Windows)
#
# Removes the startup task and firewall rule. Books and keys under
# C:\ProgramData\SlowBooksPro are deliberately left in place - delete
# that folder yourself if you're sure.
# ============================================================================
#Requires -RunAsAdministrator
$ErrorActionPreference = "SilentlyContinue"

Stop-ScheduledTask -TaskName "SlowBooksProServer"
Get-Process -Name SlowBooksPro | Stop-Process -Force
schtasks /Delete /TN "SlowBooksProServer" /F | Out-Null
netsh advfirewall firewall delete rule name="SlowBooks Pro Server Edition" | Out-Null

Write-Host "Server Edition task + firewall rule removed." -ForegroundColor Green
Write-Host "Your books are untouched at $env:ProgramData\SlowBooksPro\data (keys in $env:ProgramData\SlowBooksPro\.env)."
