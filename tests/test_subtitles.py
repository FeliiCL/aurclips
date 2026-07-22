"""Tests del generador de subtítulos ASS.

Seam bajo test: build_ass (palabras con tiempos + config -> archivo .ass).
Los tests leen el .ass resultante; no tocan helpers internos.
"""

from pathlib import Path

from aurclips.subtitles import build_ass


def _words(*tokens: str) -> list[dict]:
    """Palabras sintéticas consecutivas, cada una de 0.5 s."""
    return [
        {"word": tok, "start": i * 0.5, "end": (i + 1) * 0.5}
        for i, tok in enumerate(tokens)
    ]


def _style_fields(ass_path: Path) -> list[str]:
    """Campos de la línea de estilo del caption del .ass generado."""
    for line in ass_path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("Style: "):
            return line[len("Style: "):].split(",")
    raise AssertionError("el .ass no contiene línea de estilo")


def _secs(ts: str) -> float:
    """'h:mm:ss.cc' de ASS -> segundos."""
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _events(ass_path: Path) -> list[tuple[float, float]]:
    """(inicio, fin) en segundos de cada línea Dialogue, en orden de archivo."""
    evs = []
    for line in ass_path.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("Dialogue: "):
            _, start, end = line.split(",")[:3]
            evs.append((_secs(start), _secs(end)))
    return evs


def _assert_sin_solapes(evs: list[tuple[float, float]]) -> None:
    """Invariante: como máximo un caption activo en cualquier instante."""
    for (_, end), (nxt_start, _) in zip(evs, evs[1:]):
        assert end <= nxt_start + 1e-6


# Índices según la línea Format de [V4+ Styles] (orden estándar de ASS):
# Name, Fontname, Fontsize, ..., Alignment, MarginL, MarginR, MarginV, Encoding
FONTSIZE = 2
ALIGNMENT = -5
MARGIN_V = -2


def test_defaults_cuando_la_config_no_trae_claves(tmp_path):
    out = build_ass(_words("hola", "mundo", "shorts"), {}, tmp_path / "subs.ass")
    fields = _style_fields(out)
    assert int(fields[FONTSIZE]) == 112
    # caption_position 0.70 sobre lienzo de 1920 -> texto anclado a 576 px del borde
    assert int(fields[MARGIN_V]) == 576
    assert fields[ALIGNMENT] == "2"  # anclaje abajo-centro


def test_el_estilo_refleja_los_valores_de_la_config(tmp_path):
    cfg = {"font": "Arial", "font_size": 96, "caption_position": 0.5}
    out = build_ass(_words("hola", "mundo"), cfg, tmp_path / "subs.ass")
    fields = _style_fields(out)
    assert fields[1] == "Arial"
    assert int(fields[FONTSIZE]) == 96
    # 0.5 de 1920 -> anclado a 960 px del borde inferior
    assert int(fields[MARGIN_V]) == 960


def test_frases_consecutivas_no_se_solapan(tmp_path):
    # 6 palabras continuas -> dos frases de 3 sin hueco entre ellas: la
    # primera debe ceder la pantalla en cuanto entra la segunda
    out = build_ass(_words("uno", "dos", "tres", "cuatro", "cinco", "seis"),
                    {}, tmp_path / "subs.ass")
    evs = _events(out)
    assert evs == sorted(evs)
    _assert_sin_solapes(evs)


def _timed(*triples: tuple) -> list[dict]:
    """Palabras sintéticas con tiempos explícitos: (palabra, inicio, fin)."""
    return [{"word": w, "start": s, "end": e} for w, s, e in triples]


def test_con_hueco_amplio_se_conserva_la_cortesia(tmp_path):
    # dos frases separadas por 1.5 s de silencio: la primera retiene la
    # pantalla ~0.15 s tras su última palabra
    words = _timed(("uno", 0.0, 0.5), ("dos", 0.5, 1.0), ("tres", 1.0, 1.5),
                   ("cuatro", 3.0, 3.5), ("cinco", 3.5, 4.0), ("seis", 4.0, 4.5))
    out = build_ass(words, {}, tmp_path / "subs.ass")
    ends_primera_frase = [end for start, end in _events(out) if start < 3.0]
    assert abs(max(ends_primera_frase) - 1.65) < 1e-6


def test_hueco_pequeno_recorta_la_cortesia(tmp_path):
    # hueco de 0.05 s entre frases (menor que la cortesía de 0.15 s): la
    # primera frase cede justo cuando entra la siguiente
    words = _timed(("uno", 0.0, 0.5), ("dos", 0.5, 1.0), ("tres", 1.0, 1.5),
                   ("cuatro", 1.55, 2.0), ("cinco", 2.0, 2.5), ("seis", 2.5, 3.0))
    out = build_ass(words, {}, tmp_path / "subs.ass")
    evs = _events(out)
    ends_primera_frase = [end for start, end in evs if start < 1.55]
    assert abs(max(ends_primera_frase) - 1.55) < 1e-6
    _assert_sin_solapes(evs)


def test_tiempos_pisados_entre_frases_no_producen_solapes(tmp_path):
    # Whisper puede dar la primera palabra de una frase empezando antes de
    # que termine la última de la anterior; el .ass debe seguir sin solapes
    words = _timed(("uno", 0.0, 0.5), ("dos", 0.5, 1.0), ("tres", 1.0, 1.6),
                   ("cuatro", 1.45, 1.9), ("cinco", 1.9, 2.4), ("seis", 2.4, 2.9))
    out = build_ass(words, {}, tmp_path / "subs.ass")
    evs = _events(out)
    _assert_sin_solapes(evs)
    assert all(end > start for start, end in evs)


def test_evento_infimo_tras_el_recorte_se_descarta(tmp_path):
    # la siguiente frase entra 0.02 s después de que empiece la última
    # palabra: ese evento queda por debajo del mínimo y se descarta
    words = _timed(("uno", 0.0, 0.5), ("dos", 0.5, 1.0), ("tres", 1.0, 1.5),
                   ("cuatro", 1.02, 1.6), ("cinco", 1.6, 2.1), ("seis", 2.1, 2.6))
    out = build_ass(words, {}, tmp_path / "subs.ass")
    evs = _events(out)
    assert len(evs) == 5
    _assert_sin_solapes(evs)


def test_desorden_extremo_de_tiempos_no_produce_solapes(tmp_path):
    # la frase siguiente empieza antes incluso de la última palabra de la
    # anterior: ningún evento (ni los intermedios) debe quedar cruzado
    words = _timed(("uno", 0.0, 0.5), ("dos", 0.5, 1.0), ("tres", 1.0, 1.5),
                   ("cuatro", 0.9, 1.4), ("cinco", 1.4, 1.9), ("seis", 1.9, 2.4))
    out = build_ass(words, {}, tmp_path / "subs.ass")
    evs = _events(out)
    _assert_sin_solapes(evs)
