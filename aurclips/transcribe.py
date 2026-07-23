"""Transcripción local con faster-whisper (timestamps por palabra).

Usa la GPU NVIDIA si está disponible (las DLLs de CUDA vienen en las wheels
nvidia-cublas-cu12 / nvidia-cudnn-cu12); si la GPU falla, cae a CPU solo.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from .config import Config

_model_cache: dict[tuple, object] = {}
_CUDA_ERRORS = ("cublas", "cudnn", "cuda", "dll", "device")

# Muestra por extremo/centro con que se identifica una grabación. Hashear
# varios GB tardaría más que lo que la caché ahorra, así que se muestrea:
# tamaño + principio + centro + final. Dos archivos del mismo tamaño que solo
# difieran fuera de las muestras se considerarían el mismo — en la práctica eso
# es un recodificado, no una grabación distinta.
SAMPLE_BYTES = 1024 * 1024


def _register_cuda_dlls():
    """Registra las DLLs de CUDA instaladas vía pip (Windows no las ve solo)."""
    base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for sub in ("cublas", "cudnn"):
        d = base / sub / "bin"
        if d.is_dir():
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _get_model(cfg: Config, force_cpu: bool = False):
    from faster_whisper import WhisperModel  # import perezoso: tarda en cargar

    _register_cuda_dlls()
    device = "cpu" if force_cpu else cfg.get("whisper.device", "auto")
    compute = "int8" if force_cpu else cfg.get("whisper.compute_type", "auto")
    key = (cfg.get("whisper.model", "small"), device, compute)
    if key not in _model_cache:
        print(f"  [whisper] cargando modelo '{key[0]}' ({device}/{compute})...")
        _model_cache[key] = WhisperModel(key[0], device=device, compute_type=compute)
    return _model_cache[key]


def _run(model, cfg: Config, video_path: str) -> dict:
    language = cfg.get("whisper.language") or None
    segments_iter, info = model.transcribe(
        video_path,
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )
    segments = []
    for seg in segments_iter:
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        segments.append({
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        if len(segments) % 100 == 0:
            print(f"  [whisper] ... {seg.end/60:.1f} min transcritos")
    return {"language": info.language, "segments": segments}


# ---------------------------------------------------------------------------
# Caché: una grabación se transcribe una sola vez
# ---------------------------------------------------------------------------

def content_key(cfg: Config, video_path: str | Path) -> str:
    """Identidad de una grabación de cara a la caché.

    Es el contenido lo que identifica, no la ruta: renombrar o mover un archivo
    no obliga a transcribirlo otra vez. El modelo y el idioma forzado entran en
    la clave porque cambiarlos cambia el resultado — bajar de 'medium' a
    'small' no puede servir la transcripción vieja.
    """
    path = Path(video_path)
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(f"{size}|{cfg.get('whisper.model', 'small')}|"
                  f"{cfg.get('whisper.language') or 'auto'}".encode())
    offsets = (0, max(0, size // 2 - SAMPLE_BYTES // 2), max(0, size - SAMPLE_BYTES))
    with open(path, "rb") as f:
        for offset in offsets:
            f.seek(offset)
            digest.update(f.read(SAMPLE_BYTES))
    return digest.hexdigest()[:20]


def cache_path(cfg: Config, key: str) -> Path:
    return cfg.work_dir / "transcripts" / f"{key}.json"


def cached_transcript(cfg: Config, video_path: str | Path) -> dict | None:
    """La transcripción ya hecha de esta grabación, o None si no hay."""
    path = cache_path(cfg, content_key(cfg, video_path))
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # una caché a medias (corrida interrumpida) no vale, pero tampoco
        # justifica romper la corrida: se transcribe de nuevo
        return None


def store_transcript(cfg: Config, video_path: str | Path, result: dict) -> Path:
    path = cache_path(cfg, content_key(cfg, video_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    return path


def transcribe(cfg: Config, video_path: str, out_json: Path | None = None) -> dict:
    """Transcribe una grabación (o reutiliza lo ya transcrito) y la devuelve.

    Estructura: {"language": str, "segments": [{"start","end","text",
    "words": [{"start","end","word"}]}]}

    Con ``out_json`` deja además una copia ahí, que es como el pipeline espera
    encontrar su transcript.json.
    """
    result = cached_transcript(cfg, video_path)
    if result is not None:
        print(f"  [whisper] ya transcrito ({len(result['segments'])} segmentos); "
              f"se reutiliza")
    else:
        try:
            result = _run(_get_model(cfg), cfg, video_path)
        except RuntimeError as e:
            if not any(k in str(e).lower() for k in _CUDA_ERRORS):
                raise
            print(f"  [whisper] GPU no disponible ({e}); reintentando en CPU...")
            result = _run(_get_model(cfg, force_cpu=True), cfg, video_path)
        store_transcript(cfg, video_path, result)
        print(f"  [whisper] {len(result['segments'])} segmentos, "
              f"idioma detectado: {result['language']}")

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
    return result
