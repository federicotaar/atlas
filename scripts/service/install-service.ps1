# Installa ATLAS come task pianificato "ATLAS Agent" (avvio al login utente).
# Esegui dalla root del repo: powershell -File scripts/service/install-service.ps1
$Dir = (Resolve-Path "$PSScriptRoot\..\..").Path
$Python = Join-Path $Dir "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = (Get-Command python).Source }
$Main = Join-Path $Dir "main.py"
$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Main`" --headless" -WorkingDirectory $Dir
$Trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "ATLAS Agent" -Action $Action -Trigger $Trigger -Description "ATLAS agent (headless: canali chat + scheduler)" -Force
Write-Host "ATLAS installato come task pianificato (avvio al login). Per rimuoverlo: scripts/service/uninstall-service.ps1"
