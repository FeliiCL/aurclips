# Registra la tarea programada de Windows para que el bot corra solo todos los días.
# Uso:  powershell -ExecutionPolicy Bypass -File setup_task.ps1 [-Hora "03:00"]
param(
    [string]$Hora = "03:00"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$taskName = "aurclips-diario"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$root\run.ps1`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $Hora
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew

try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop } catch {}
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "aurclips: ingesta, recorte y subida diaria" | Out-Null

Write-Host "Tarea '$taskName' registrada: corre todos los días a las $Hora." -ForegroundColor Green
Write-Host "Puedes probarla ya mismo con:  Start-ScheduledTask -TaskName $taskName"
