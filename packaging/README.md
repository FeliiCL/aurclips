# Automatización por sistema operativo

Dos formas de dejar aurclips trabajando solo, combinables:

- **Corrida diaria** (`aurclips run`): el scheduler la dispara una vez al día.
- **Modo continuo** (`aurclips watch`): un demonio que vigila el inbox y
  procesa lo que llegue, en minutos en vez de al día siguiente. Comparte el
  lock con `run` (no se pisan), un ciclo fallido no lo mata (backoff + evento
  de error), y SIGTERM/Ctrl+C paran ordenado: termina lo que está en curso,
  guarda, y al re-arrancar retoma lo pendiente.

Ambos dejan su propio log con rotación y se protegen contra solapes; el
scheduler solo los invoca. **YouTube publica solo** los Shorts ya subidos con
fecha, así que nada de esto afecta la cadencia de publicación.

## Modo continuo por SO

- **Linux**: `packaging/systemd/aurclips-watch.service` (Type=simple,
  Restart=on-failure, TimeoutStopSec generoso porque un render tarda minutos):
  cópialo a `~/.config/systemd/user/`, ajusta la ruta y
  `systemctl --user enable --now aurclips-watch`.
- **macOS**: `packaging/launchd/com.aurclips.watch.plist` (RunAtLoad +
  KeepAlive) a `~/Library/LaunchAgents/` y `launchctl load`.
- **Windows**: una tarea "al iniciar sesión" que ejecute
  `.venv\Scripts\aurclips.exe watch`:
  `schtasks /create /tn aurclips-watch /sc onlogon /tr "F:\ruta\a\aurclips\.venv\Scripts\aurclips.exe watch"`

Elige el mecanismo de tu SO:

## Linux — systemd (recomendado) o cron

**systemd (user timer):** copia las unidades, ajusta la ruta del checkout y la
hora, y actívalas:

```bash
mkdir -p ~/.config/systemd/user
cp packaging/systemd/aurclips.service ~/.config/systemd/user/
cp packaging/systemd/aurclips.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aurclips.timer
# para que corra aunque no tengas sesión abierta:
loginctl enable-linger "$USER"
```

Prueba una corrida ya: `systemctl --user start aurclips.service`.

**cron (universal):** `crontab -e` y pega la línea de
[`crontab.example`](crontab.example), ajustando la ruta.

## macOS — launchd

Ajusta `TU_USUARIO` y la ruta en el plist, cópialo y cárgalo:

```bash
cp packaging/launchd/com.aurclips.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aurclips.daily.plist
# probar ya:
launchctl start com.aurclips.daily
```

cron también funciona en macOS ([`crontab.example`](crontab.example)).

## Windows — Programador de tareas

```powershell
powershell -ExecutionPolicy Bypass -File setup_task.ps1 -Hora "03:00"
```

Registra la tarea `aurclips-diario`. Pruébala con
`Start-ScheduledTask -TaskName aurclips-diario`.
