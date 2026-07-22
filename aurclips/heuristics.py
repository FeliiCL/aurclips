"""Selección local de highlights: energía de audio + ritmo + ganchos del texto.

No usa ninguna API. Analiza el audio con ffmpeg y la transcripción de Whisper
para puntuar ventanas candidatas y generar título/descripción/hashtags.
"""

from __future__ import annotations

import math
import re
import subprocess
from array import array
from collections import Counter
from dataclasses import dataclass, field

from .config import Config

ENERGY_WINDOW = 0.5  # segundos por ventana de energía

SENTENCE_END = (".", "?", "!")  # un texto que termina así cierra la idea
MIN_GAP_S = 30.0  # separación mínima entre clips elegidos del mismo video


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(SENTENCE_END)


def _significant_words(text: str) -> list[str]:
    """Palabras con contenido: largas y fuera de las stopwords."""
    return [w for w in re.findall(r"[a-záéíóúüñ]{4,}", text.lower())
            if w not in STOPWORDS]

# Palabras que suelen abrir un buen gancho (es + en)
HOOK_WORDS = {
    "secreto", "nunca", "nadie", "error", "truco", "gratis", "dinero", "peor",
    "mejor", "increible", "increíble", "importante", "cuidado", "verdad",
    "mentira", "mira", "escucha", "atencion", "atención", "locura", "brutal",
    "secret", "never", "nobody", "mistake", "trick", "free", "money", "worst",
    "best", "insane", "important", "careful", "truth", "crazy", "listen",
}

# Arranques de relleno: se limpian del título y penalizan el inicio de un clip
FILLER_STARTS = (
    "y ", "pero ", "bueno ", "entonces ", "o sea ", "osea ", "este ", "eh ",
    "pues ", "a ver ", "vale ", "ok ", "and ", "but ", "so ", "well ", "um ",
    "uh ", "like ",
)

STOPWORDS = {
    # español
    "que", "de", "la", "el", "en", "y", "a", "los", "las", "del", "se", "un",
    "una", "por", "con", "no", "es", "lo", "como", "para", "mas", "más", "pero",
    "sus", "le", "ya", "o", "este", "si", "sí", "porque", "esta", "entre",
    "cuando", "muy", "sin", "sobre", "también", "me", "hasta", "hay", "donde",
    "quien", "desde", "todo", "nos", "durante", "todos", "uno", "les", "ni",
    "contra", "otros", "ese", "eso", "ante", "ellos", "e", "esto", "mí", "antes",
    "algunos", "qué", "unos", "yo", "otro", "otras", "otra", "él", "tanto",
    "esa", "estos", "mucho", "quienes", "nada", "muchos", "cual", "poco",
    "ella", "estar", "estas", "algunas", "algo", "nosotros", "tiene", "tienen",
    "era", "eres", "soy", "somos", "está", "están", "fue", "ser", "hacer",
    "hace", "puede", "pueden", "tengo", "vamos", "bueno", "entonces", "pues",
    "osea", "creo", "digo", "dice", "decir", "ahora", "aquí", "ahí", "así",
    # inglés
    "the", "be", "to", "of", "and", "in", "that", "have", "it", "for", "not",
    "on", "with", "he", "as", "you", "do", "at", "this", "but", "his", "by",
    "from", "they", "we", "say", "her", "she", "or", "an", "will", "my", "one",
    "all", "would", "there", "their", "what", "so", "up", "out", "if", "about",
    "who", "get", "which", "go", "me", "when", "make", "can", "like", "time",
    "just", "him", "know", "take", "people", "into", "year", "your", "good",
    "some", "could", "them", "see", "other", "than", "then", "now", "look",
    "only", "come", "its", "over", "think", "also", "back", "after", "use",
    "two", "how", "our", "work", "first", "well", "way", "even", "new", "want",
    "because", "any", "these", "give", "day", "most", "us", "was", "were",
    "been", "being", "are", "is", "very", "really", "going", "gonna", "yeah",
    "without", "here", "there", "every", "single", "thing", "things",
    "something", "everything", "anything", "nothing", "actually", "always",
    "never", "still", "much", "many", "where", "while", "again", "right",
    "okay", "need", "keep", "let", "lets", "got", "does", "did", "doing",
    "means", "part", "whole", "follows", "already",
}


@dataclass
class Candidate:
    start: float
    end: float
    score: float
    segments: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s["text"] for s in self.segments)

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------------------
# Energía de audio
# ---------------------------------------------------------------------------

def audio_energy(cfg: Config, video_path: str) -> list[float]:
    """RMS del audio por ventana de ENERGY_WINDOW segundos (vía ffmpeg)."""
    try:
        cmd = [
            cfg.ffmpeg, "-v", "error", "-i", video_path,
            "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "8000", "-",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:  # ffmpeg ausente (setup incompleto): energía neutra
        return []
    chunk_bytes = int(8000 * ENERGY_WINDOW) * 2
    energies: list[float] = []
    try:
        import audioop  # rápido (C); existe en Python <= 3.12

        def rms(chunk: bytes) -> float:
            return float(audioop.rms(chunk, 2))
    except ImportError:
        def rms(chunk: bytes) -> float:
            samples = array("h", chunk[: len(chunk) // 2 * 2])
            if not samples:
                return 0.0
            return math.sqrt(sum(s * s for s in samples) / len(samples))

    while True:
        chunk = proc.stdout.read(chunk_bytes)
        if not chunk:
            break
        energies.append(rms(chunk))
    proc.wait()
    return energies


def _percentile_ranks(values: list[float]) -> list[float]:
    """Convierte valores a rangos 0..1 (robusto frente a volumen absoluto)."""
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    denom = max(1, len(values) - 1)
    for rank, idx in enumerate(order):
        ranks[idx] = rank / denom
    return ranks


# ---------------------------------------------------------------------------
# Puntuación de ventanas candidatas
# ---------------------------------------------------------------------------

def _window_energy(ranks: list[float], start: float, end: float) -> float:
    if not ranks:
        return 0.5
    a = min(len(ranks) - 1, int(start / ENERGY_WINDOW))
    b = min(len(ranks), max(a + 1, int(end / ENERGY_WINDOW)))
    window = ranks[a:b]
    return sum(window) / len(window)


def _score_window(segs: list[dict], energy_ranks: list[float],
                  median_pace: float) -> float:
    start, end = segs[0]["start"], segs[-1]["end"]
    dur = max(0.1, end - start)
    text = " ".join(s["text"] for s in segs).lower()

    energy = _window_energy(energy_ranks, start, end)

    n_words = sum(len(s["words"]) for s in segs)
    pace = min(2.0, (n_words / dur) / max(0.1, median_pace)) / 2.0

    first_8s = " ".join(s["text"] for s in segs if s["start"] < start + 8).lower()
    hook_hits = sum(1 for w in HOOK_WORDS if w in first_8s)
    hook = min(0.3, hook_hits * 0.15)

    punct = min(0.15, (text.count("?") + text.count("!")) * 0.05)

    gaps = 0.0
    for prev, nxt in zip(segs, segs[1:]):
        gaps += max(0.0, nxt["start"] - prev["end"] - 1.0)
    gap_penalty = min(0.4, gaps / dur)

    # cerrar la idea pesa fuerte: un clip que muere a mitad de frase se
    # siente roto aunque el momento sea bueno
    closes_sentence = 0.18 if _ends_sentence(text) else 0.0

    # arrancar con muletilla desperdicia el primer segundo del Short
    filler_pen = 0.12 if text.lstrip().startswith(FILLER_STARTS) else 0.0

    # densidad de contenido: fracción de palabras significativas (no
    # stopwords); el relleno conversacional puro no merece un Short
    tokens = re.findall(r"[a-záéíóúüñ]+", text)
    density = (min(0.12, 0.25 * len(_significant_words(text)) / len(tokens))
               if tokens else 0.0)

    # duración óptima según la investigación: 15-30s retiene mejor; la
    # retención cae fuerte pasados los ~45s
    if dur <= 35:
        dur_bonus = 0.08
    elif dur <= 45:
        dur_bonus = 0.03
    else:
        dur_bonus = -0.05

    # la energía manda menos que antes (0.45 -> 0.30): lo ruidoso no es
    # necesariamente lo interesante; la estructura narrativa pesa más
    return (0.30 * energy + 0.2 * pace + hook + punct + closes_sentence
            + dur_bonus + density - gap_penalty - filler_pen)


def find_candidates(cfg: Config, transcript: dict, video_path: str,
                    limit: int) -> list[Candidate]:
    """Devuelve las mejores ventanas candidatas, sin traslapes."""
    min_s = cfg.get("selection.min_clip_seconds", 15)
    max_s = cfg.get("selection.max_clip_seconds", 59)
    segs = [s for s in transcript["segments"] if s["words"]]
    if not segs:
        return []

    print("  [selector] analizando energía del audio...")
    energy_ranks = _percentile_ranks(audio_energy(cfg, video_path))

    paces = [len(s["words"]) / max(0.1, s["end"] - s["start"]) for s in segs]
    median_pace = sorted(paces)[len(paces) // 2]

    # todas las ventanas que empiezan en un límite de frase
    windows: list[Candidate] = []
    for i in range(len(segs)):
        best: Candidate | None = None
        j = i
        while j < len(segs) and segs[j]["end"] - segs[i]["start"] <= max_s + 2:
            dur = segs[j]["end"] - segs[i]["start"]
            if dur >= min_s:
                window = segs[i:j + 1]
                score = _score_window(window, energy_ranks, median_pace)
                if best is None or score > best.score:
                    best = Candidate(segs[i]["start"], segs[j]["end"], score, window)
            j += 1
        if best:
            windows.append(best)

    # selección voraz sin traslapes y con separación mínima
    windows.sort(key=lambda c: c.score, reverse=True)
    chosen: list[Candidate] = []
    for cand in windows:
        if len(chosen) >= limit:
            break
        if all(cand.end + MIN_GAP_S <= c.start or cand.start >= c.end + MIN_GAP_S
               for c in chosen):
            chosen.append(cand)
    chosen.sort(key=lambda c: c.start)

    # recorte a frase completa: si la ventana ganadora no termina cerrando
    # la idea, retroceder hasta el último segmento que sí lo haga, sin bajar
    # de la duración mínima; si no hay corte válido, se conserva el final
    for cand in chosen:
        if _ends_sentence(cand.segments[-1]["text"]):
            continue
        for j in range(len(cand.segments) - 2, -1, -1):
            seg = cand.segments[j]
            if seg["end"] - cand.start < min_s:
                break
            if _ends_sentence(seg["text"]):
                cand.segments = cand.segments[:j + 1]
                cand.end = seg["end"]
                break
    return chosen


# ---------------------------------------------------------------------------
# Metadatos sin LLM
# ---------------------------------------------------------------------------

def _clean_title(text: str, max_len: int = 85) -> str:
    text = text.strip()
    lowered = text.lower()
    for filler in FILLER_STARTS:
        if lowered.startswith(filler):
            text = text[len(filler):].strip()
            lowered = text.lower()
    text = text.rstrip(".,;: ")
    if len(text) > max_len:
        cut = text[:max_len].rsplit(" ", 1)[0]
        text = cut.rstrip(".,;: ")
    return text[:1].upper() + text[1:] if text else "Clip"


def _hashtags(text: str, limit: int = 4) -> list[str]:
    freq = Counter(_significant_words(text))
    return [w for w, _ in freq.most_common(limit)]


def make_metadata(cand: Candidate) -> tuple[str, str, list[str]]:
    """(título, descripción, hashtags) generados a partir del propio clip."""
    from .safety import strip_mild  # título sin groserías (política de títulos)

    title = _clean_title(strip_mild(cand.segments[0]["text"]))
    text = cand.text.strip()
    description = text[:220].rsplit(" ", 1)[0] + "…" if len(text) > 220 else text
    return title, description, _hashtags(cand.text)
