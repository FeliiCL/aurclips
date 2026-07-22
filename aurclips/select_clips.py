"""Selección de highlights, 100% local.

Motores disponibles (config: selection.engine):
- "heuristic": análisis de audio + transcripción, sin ningún modelo de lenguaje.
- "ollama":    las candidatas las encuentra la heurística y un LLM local
               (Ollama) elige las mejores y escribe título/descripción.
- "auto":      usa Ollama si está corriendo; si no, heurística pura.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import List

from pydantic import BaseModel

from .config import Config
from .heuristics import Candidate, find_candidates, make_metadata
from .ingest import probe_duration


class Clip(BaseModel):
    start_s: float
    end_s: float
    title: str
    description: str
    hashtags: List[str]
    score: float = 0.0


# ---------------------------------------------------------------------------
# Motor Ollama (opcional, local)
# ---------------------------------------------------------------------------

class _Pick(BaseModel):
    candidato: int
    titulo: str
    descripcion: str
    hashtags: List[str]


class _Selection(BaseModel):
    elegidos: List[_Pick]


OLLAMA_SYSTEM = """\
Eres un editor experto en YouTube Shorts. Te doy fragmentos candidatos ya
recortados de un video largo. Elige los que tengan más gancho y potencial
viral, y escribe para cada uno un título (máx 90 caracteres, tipo gancho),
una descripción corta (1-2 frases) y 3-5 hashtags sin '#'. Escribe todo en el
MISMO idioma de los fragmentos. Responde solo con el JSON pedido.
"""


def _ollama_available(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _select_with_ollama(cfg: Config, candidates: list[Candidate],
                        video_title: str, n: int) -> list[Clip] | None:
    url = cfg.get("selection.ollama.url", "http://localhost:11434").rstrip("/")
    model = cfg.get("selection.ollama.model", "llama3.1:8b")

    parts = [f"Video: {video_title}", f"Elige los {n} mejores fragmentos.", ""]
    for i, cand in enumerate(candidates):
        parts.append(f"--- Candidato {i} [{cand.start:.0f}s-{cand.end:.0f}s] ---")
        parts.append(cand.text[:2000])
        parts.append("")

    payload = json.dumps({
        "model": model,
        "stream": False,
        "format": _Selection.model_json_schema(),
        "options": {"num_ctx": 8192},
        "messages": [
            {"role": "system", "content": OLLAMA_SYSTEM},
            {"role": "user", "content": "\n".join(parts)},
        ],
    }).encode("utf-8")

    print(f"  [selector] consultando Ollama ({model})...")
    req = urllib.request.Request(
        f"{url}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read())
        selection = _Selection.model_validate_json(data["message"]["content"])
    except Exception as e:  # noqa: BLE001 — cualquier fallo => heurística
        print(f"  [selector] Ollama falló ({e}); se usa la heurística")
        return None

    clips: list[Clip] = []
    for pick in selection.elegidos:
        if not 0 <= pick.candidato < len(candidates):
            continue
        cand = candidates[pick.candidato]
        if any(c.start_s == cand.start for c in clips):
            continue
        from .safety import strip_mild
        clips.append(Clip(
            start_s=cand.start, end_s=cand.end,
            title=strip_mild(pick.titulo)[:90] or make_metadata(cand)[0],
            description=pick.descripcion,
            hashtags=[h.lstrip("#") for h in pick.hashtags][:5],
            score=cand.score,
        ))
        if len(clips) >= n:
            break
    return clips or None


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def select_clips(cfg: Config, transcript: dict, video_title: str,
                 video_path: str) -> list[Clip]:
    n = cfg.get("selection.clips_per_video", 3)
    engine = cfg.get("selection.engine", "auto")
    max_s = cfg.get("selection.max_clip_seconds", 59)

    # video que ya cabe como Short -> se usa completo, sin recortar
    segs = [s for s in transcript["segments"] if s["words"]]
    total = probe_duration(cfg, video_path) or (segs[-1]["end"] if segs else 0)
    if total and total <= max_s + 2:
        if not segs:
            print("  [selector] clip corto sin voz detectada; se omite")
            return []
        cand = Candidate(0.0, total, 1.0, segs)
        title, description, hashtags = make_metadata(cand)
        print(f"  [selector] video corto ({total:.0f}s): se usa completo como un Short")
        return [Clip(start_s=0.0, end_s=total, title=title,
                     description=description, hashtags=hashtags, score=1.0)]

    # número objetivo adaptativo: ~1 Short por cada minutes_per_short minutos
    # de video, acotado entre 1 y clips_per_video (que actúa de tope)
    mps = cfg.get("selection.minutes_per_short", 4)
    if total and mps:
        n = max(1, min(n, int(total / (mps * 60))))

    # la heurística siempre genera las candidatas (con margen extra si un LLM
    # local va a elegir entre ellas)
    want_llm = engine in ("auto", "ollama")
    limit = max(n * 3, 8) if want_llm else n
    candidates = find_candidates(cfg, transcript, video_path, limit)
    if not candidates:
        print("  [selector] no se encontraron ventanas útiles")
        return []

    # descarte por calidad relativa: mejor pocos Shorts buenos que rellenar
    # el cupo con candidatos muy por debajo del mejor del propio video
    floor = cfg.get("selection.quality_floor", 0.55)
    best = max(c.score for c in candidates)
    if floor:
        if best > 0:
            kept = [c for c in candidates if c.score >= floor * best]
        else:
            # todo el campo es flojo (puntuaciones <= 0, posible con audio
            # real): la fracción del mejor pierde sentido; conservar solo el
            # mejor en vez de rellenar el cupo con lo peor
            kept = [max(candidates, key=lambda c: c.score)]
        if len(kept) < len(candidates):
            print(f"  [selector] {len(candidates) - len(kept)} candidato(s) "
                  f"descartados por calidad (umbral {floor:.2f} del mejor)")
        candidates = kept

    clips: list[Clip] | None = None
    if want_llm:
        url = cfg.get("selection.ollama.url", "http://localhost:11434").rstrip("/")
        if _ollama_available(url):
            clips = _select_with_ollama(cfg, candidates, video_title, n)
        elif engine == "ollama":
            print("  [selector] Ollama no responde; se usa la heurística")

    if clips is None:
        clips = []
        for cand in candidates[:n]:
            title, description, hashtags = make_metadata(cand)
            clips.append(Clip(start_s=cand.start, end_s=cand.end, title=title,
                              description=description, hashtags=hashtags,
                              score=cand.score))

    clips.sort(key=lambda c: c.start_s)
    print(f"  [selector] {len(clips)} clip(s) seleccionados")
    for c in clips:
        print(f"    - [{c.start_s:.0f}s-{c.end_s:.0f}s] {c.title}")
    return clips


def clip_words(transcript: dict, start: float, end: float) -> list[dict]:
    """Palabras (con tiempos relativos al clip) dentro de [start, end]."""
    words = []
    for seg in transcript["segments"]:
        if seg["end"] < start or seg["start"] > end:
            continue
        for w in seg["words"]:
            if w["start"] >= start - 0.05 and w["end"] <= end + 0.05:
                words.append({
                    "start": max(0.0, w["start"] - start),
                    "end": max(0.0, w["end"] - start),
                    "word": w["word"],
                })
    return words
