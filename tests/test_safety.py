"""Tests de la política de duplicados.

Seam bajo test: ``find_duplicate`` (texto + textos ya aceptados -> veredicto).
Es la política que comparten el pipeline y el modo recortador: uno le pasa los
clips de la base, el otro los recortes sueltos de la misma corrida. Aquí se
afirma la regla, no de dónde salen los textos.

``is_duplicate`` se prueba aparte, y solo para lo suyo: que sigue leyendo la
base y delegando en la misma política.
"""

from types import SimpleNamespace

from aurclips.safety import find_duplicate, is_duplicate
from aurclips.state import State

# Jaccard sobre tokens de 3+ caracteres. Siete palabras compartidas sobre ocho
# distintas dan 0.875: por encima del umbral normal (0.8) y por debajo del
# exigente (0.9) que se aplica a los textos cortos. Es el par que separa las
# dos reglas.
SIETE = "alfa bravo charlie delta echo foxtrot golf"
OCHO = "alfa bravo charlie delta echo foxtrot golf hotel"
NUEVE = "alfa bravo charlie delta echo foxtrot golf hotel india"


def test_sin_textos_conocidos_nada_es_duplicado():
    assert find_duplicate("cualquier cosa que se diga aquí", [], 0.8) == (False, None)


def test_un_texto_distinto_no_es_duplicado():
    known = [(1, "hablando de cocinar pasta con salsa de tomate y albahaca")]
    assert find_duplicate(
        "una partida de ajedrez que termina en tablas por repetición",
        known, 0.8) == (False, None)


def test_un_texto_casi_idéntico_es_duplicado_y_dice_contra_cuál():
    known = [(7, "el jefe final aparece justo cuando se acaban las pociones"),
             (9, "el jefe final aparece justo cuando se acaban las pociones y curas")]
    duplicate, clip_id = find_duplicate(
        "el jefe final aparece justo cuando se acaban las pociones", known, 0.8)
    assert duplicate
    assert clip_id == 7


def test_los_textos_cortos_exigen_más_parecido_para_contar_como_duplicados():
    """Con pocas palabras el parecido engaña, así que el umbral sube 0.1."""
    # el texto nuevo tiene 7 tokens (corto) y se parece 0.875 al conocido
    assert find_duplicate(SIETE, [(1, OCHO)], 0.8) == (False, None)
    # el mismo parecido, con un texto lo bastante largo, sí cuenta
    duplicate, _ = find_duplicate(OCHO, [(1, NUEVE)], 0.8)
    assert duplicate


def test_un_texto_sin_palabras_útiles_no_es_duplicado_de_nada():
    """Solo tokens de 3+ caracteres cuentan; sin ninguno no hay qué comparar."""
    assert find_duplicate("y a mí no", [(1, "y a mí no")], 0.8) == (False, None)


def test_el_texto_conocido_vacío_no_dispara_falsos_positivos():
    assert find_duplicate("una frase con suficientes palabras para comparar",
                          [(1, "")], 0.8) == (False, None)


# --- el envoltorio con base ------------------------------------------------

def test_is_duplicate_compara_contra_los_clips_de_la_base():
    """El envoltorio no tiene política propia: saca las filas y delega."""
    db = State(":memory:")
    video_id = db.add_video("local", "grabacion.mp4", "grabación",
                            "grabacion.mp4", 600.0)
    texto = "el jefe final aparece justo cuando se acaban las pociones"
    clip = SimpleNamespace(start=0.0, end=30.0, title="Un título",
                           description="Una descripción", tags=["gaming"],
                           score=1.0, marked=False)
    clip_id = db.add_clip(video_id, 0, clip, texto)

    assert is_duplicate(db, texto, 0.8) == (True, clip_id)
    assert is_duplicate(db, "algo completamente distinto sobre recetas de cocina",
                        0.8) == (False, None)
