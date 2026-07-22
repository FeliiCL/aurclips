# 🎬 aurclips — de videos largos a YouTube Shorts, 100% local

Convierte contenido largo (videos de YouTube, streams, VODs, archivos locales) en
**YouTube Shorts** verticales con subtítulos estilo viral, y los deja **programados
para publicarse solos, uno por día**. Toda la transcripción, selección y edición
corre en tu máquina, **gratis y sin API keys de pago**; solo la subida automática
a YouTube usa credenciales OAuth gratuitas de Google (con cuota diaria).

> Pensado para **Windows** (PowerShell + Programador de tareas). El código Python
> es portable, pero los scripts de instalación y automatización son de Windows.

## Cómo funciona el pipeline

```
Canal de YouTube / carpeta inbox
        │  (yt-dlp descarga lo nuevo)
        ▼
Transcripción local con Whisper (con tiempos por palabra)
        │
        ▼
Selector local de highlights:
  · energía del audio (picos, risas, subidas de intensidad)
  · estructura narrativa: ganchos, preguntas, cierre de la idea,
    densidad de contenido, sin muletillas de arranque
  → decide CUÁNTOS Shorts salen según duración y calidad del video
    (mejor pocos buenos que rellenar el cupo)
  → recorta cada clip para que termine en una frase completa
  → genera título, descripción y hashtags a partir del propio clip
  (si tienes Ollama corriendo, un LLM local mejora títulos y elección)
        │
        ▼
Filtros de calidad:
  · anti-contenido no apto (términos es/en que desmonetizan, en dos
    niveles: siempre los fuertes; con safety.strict también groserías)
  · limpieza de duplicados (similitud de transcripción entre clips)
        │
        ▼
ffmpeg recorta, aplica jump cuts (elimina pausas más largas que
`render.max_pause`, 1.5s por defecto), detecta el rostro y centra el
encuadre vertical en él, convierte a 9:16 (1080x1920) y quema
subtítulos estilo viral: MAYÚSCULAS en fuente Anton, palabra por
palabra, con la palabra clave de cada frase resaltada en amarillo/verde
        │
        ▼
Se suben a YouTube en privado con "publishAt" programado:
YouTube los publica solo, uno cada día a la hora que configures
```

## Requisitos

- **Windows 10/11** con PowerShell
- **[Python 3.12](https://www.python.org/downloads/)** (con el launcher `py`)
- Conexión a internet para el setup (~90 MB: ffmpeg, deno y la fuente Anton se
  descargan a `tools\`)
- **GPU NVIDIA opcional** — acelera mucho la transcripción; el setup la detecta
  e instala el soporte CUDA solo. En CPU también funciona (usa un modelo más chico).
- (Opcional) **[Ollama](https://ollama.com)** para mejores títulos y selección
- (Solo para subir) una cuenta de Google y un proyecto gratuito en Google Cloud

## Instalación (una sola vez)

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Eso crea el entorno de Python, instala dependencias, descarga ffmpeg/deno/fuente
en `tools\` e instala soporte CUDA si detecta una GPU NVIDIA.

Después:

1. **Configura** `config.yaml`:
   - `channels`: los canales a vigilar (o deja vacío y usa solo la carpeta `data\inbox`).
   - `upload.publish_time`: hora local a la que quieres que salga el Short diario.
   - `whisper.model`: el default es `medium` (ideal con GPU NVIDIA); si solo
     tienes CPU, bájalo a `small`.
2. **Credenciales de YouTube** (solo para la subida automática):
   1. Entra a [Google Cloud Console](https://console.cloud.google.com/), crea un
      proyecto y habilita **YouTube Data API v3**.
   2. En *Credentials* crea un **OAuth client ID** de tipo **Desktop app** y
      descarga el JSON como `credentials\client_secrets.json`.
   3. En *OAuth consent screen* agrega tu cuenta como *test user*.
   4. Inicia sesión (abre el navegador una sola vez):
      ```powershell
      .venv\Scripts\python -m aurclips auth
      ```
3. **(Opcional) Ollama** — si instalas [Ollama](https://ollama.com) y bajas un
   modelo (`ollama pull llama3.1:8b`), el bot lo detecta automáticamente y lo usa
   para elegir entre los candidatos y escribir mejores títulos. Sigue siendo
   local y gratis. Sin Ollama, la heurística pura funciona sola.

> **La subida viene desactivada por defecto** (`upload.enabled: false`). Genera
> primero unos Shorts, revísalos en `data\output`, y cuando te convenzan cambia
> `upload.enabled: true` en `config.yaml`.

## Uso

```powershell
.venv\Scripts\python -m aurclips run       # pipeline completo
.venv\Scripts\python -m aurclips status    # ver qué hay en cola
.venv\Scripts\python -m aurclips report    # métricas de los publicados + monitoreo
.venv\Scripts\python -m aurclips retry     # reencolar videos/clips fallidos
.venv\Scripts\python -m aurclips ingest    # solo buscar contenido nuevo
.venv\Scripts\python -m aurclips process   # solo transcribir/recortar
.venv\Scripts\python -m aurclips upload    # solo subir lo renderizado
```

**Priorización por rendimiento:** el comando `report` trae las vistas/likes de
tus Shorts publicados; con esos datos, la cola de subida da prioridad a los
clips de los videos fuente que mejor han rendido (y dentro de cada video, a los
de mayor puntuación del selector).

**Alertas:** cada corrida escribe en `logs\events.log`. Si pones una URL de
webhook de Discord en `alerts.discord_webhook` (config.yaml), recibes un aviso
en tu servidor cuando se sube un Short o cuando algo falla.

También puedes soltar cualquier video largo en `data\inbox\` y correr `run`.

## Configuración útil

Las claves de `config.yaml` que más vas a tocar (todas documentadas en el
propio archivo):

| Clave | Qué controla | Default |
| --- | --- | --- |
| `selection.clips_per_video` | Tope máximo de Shorts por video | `3` |
| `selection.minutes_per_short` | Densidad: ~1 Short por cada N min de video | `4` |
| `selection.quality_floor` | Descarta candidatos bajo esa fracción del mejor (0 = off) | `0.55` |
| `selection.engine` | `heuristic` / `ollama` / `auto` | `auto` |
| `render.font_size` / `caption_position` | Tamaño y altura de los subtítulos | `112` / `0.70` |
| `render.words_per_caption` | Palabras por frase en pantalla | `3` |
| `render.max_pause` | Pausas más largas que esto (s) se recortan | `1.5` |
| `safety.action` / `safety.strict` | `skip`/`flag` y nivel del filtro | `skip` / `false` |
| `upload.publish_time` | Hora local de publicación diaria | `19:00` |
| `limits.max_videos_per_run` / `max_uploads_per_run` | Carga por corrida | `3` / `5` |

## Dejarlo trabajando solo

```powershell
powershell -ExecutionPolicy Bypass -File setup_task.ps1 -Hora "03:00"
```

Registra una tarea de Windows que corre el bot todos los días a las 3 AM:
busca contenido nuevo, genera los Shorts y los deja programados. La publicación
diaria la hace YouTube por sí solo (los videos se suben en privado con fecha de
publicación), así que aunque un día no haya contenido nuevo, los Shorts ya
encolados siguen saliendo. Cada corrida escribe su log en `logs\run_<fecha>.log`
(se conservan los últimos 30); los eventos importantes van además a `logs\events.log`.

## Notas y límites

- **Cuota de YouTube API**: cada subida cuesta ~1600 unidades de las 10,000
  diarias por defecto → máximo ~6 subidas al día. El bot programa las fechas de
  publicación en cadena, así que no necesitas subir más de unos pocos por corrida.
- **Publicación programada**: YouTube requiere que el video se suba como
  `private` con `publishAt`; el bot lo hace automáticamente.
- **Calidad de la selección**: la heurística funciona mejor con contenido
  hablado y dinámico (streams, podcasts, gaming). Con Ollama instalado los
  títulos mejoran bastante. Ajusta `selection.engine` en `config.yaml`.
- **Encuadre**: con `crop.face_tracking` activado, el recorte vertical se
  centra en el rostro dominante detectado (OpenCV local); si no hay rostro,
  recorte centrado clásico.
- **Escalado**: `limits.max_videos_per_run` y `limits.max_uploads_per_run`
  controlan la carga por corrida; lo que no entra hoy queda en cola para la
  siguiente. Los estados son resumibles y `retry` reencola lo fallido, así que
  puedes agregar más canales sin tocar nada más.
- **Filtro de contenido**: `safety.action: skip` descarta los clips con
  lenguaje que desmonetiza; con `flag` los guarda marcados para que tú decidas
  (aparecen en `status`/`report`). Agrega términos propios en
  `safety.extra_words`.
- Los clips fallidos quedan marcados en la base (`data\state.db`); revisa con
  `status` y los logs en `logs\`.

## Desarrollo

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Los tests corren en milisegundos, sin GPU, sin video real y sin Ollama.

## Licencia

[MIT](LICENSE). El modelo de detección de rostros embebido
([YuNet](https://github.com/opencv/opencv_zoo), int8) es también MIT.

Eres responsable de tener derechos sobre el contenido que recortas y de cumplir
los [términos de servicio de YouTube](https://www.youtube.com/t/terms) y las
políticas de la YouTube Data API al usar la subida automática.
