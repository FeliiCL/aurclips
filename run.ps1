# Corrida diaria del bot (la usa el Programador de tareas de Windows).
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("run_" + (Get-Date -Format "yyyy-MM-dd_HHmm") + ".log")

& $python -m aurclips run *>&1 | Tee-Object -FilePath $log

# conservar solo los últimos 30 logs
Get-ChildItem $logDir -Filter "run_*.log" | Sort-Object Name -Descending |
    Select-Object -Skip 30 | Remove-Item -Force -ErrorAction SilentlyContinue
