"""Transcripción local con faster-whisper (timestamps por palabra).

Usa la GPU NVIDIA si está disponible (las DLLs de CUDA vienen en las wheels
nvidia-cublas-cu12 / nvidia-cudnn-cu12); si la GPU falla, cae a CPU solo.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .config import Config

_model_cache: dict[tuple, object] = {}
_CUDA_ERRORS = ("cublas", "cudnn", "cuda", "dll", "device")


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


def transcribe(cfg: Config, video_path: str, out_json: Path) -> dict:
    """Transcribe un video y guarda el resultado como JSON.

    Estructura: {"language": str, "segments": [{"start","end","text",
    "words": [{"start","end","word"}]}]}
    """
    try:
        result = _run(_get_model(cfg), cfg, video_path)
    except RuntimeError as e:
        if not any(k in str(e).lower() for k in _CUDA_ERRORS):
            raise
        print(f"  [whisper] GPU no disponible ({e}); reintentando en CPU...")
        result = _run(_get_model(cfg, force_cpu=True), cfg, video_path)

    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print(f"  [whisper] {len(result['segments'])} segmentos, "
          f"idioma detectado: {result['language']}")
    return result
