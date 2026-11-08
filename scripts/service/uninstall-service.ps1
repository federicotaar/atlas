# Rimuove il task pianificato "ATLAS Agent". Non tocca dati, memoria o .env.
Unregister-ScheduledTask -TaskName "ATLAS Agent" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Task pianificato rimosso."
