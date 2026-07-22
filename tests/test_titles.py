"""Tests del camino LLM de títulos (titles.py) con un stub que finge ser Ollama.

Seam de integración: select_clips (la propuesta del stub debe llegar al Clip).
Seam unitario: titles.propose (el saneo del título). El stub es un mini-servidor
HTTP de la librería estándar en un puerto efímero: responde /api/tags (para
`available`) y /api/chat (la propuesta enlatada), y guarda cada payload para
poder afirmar sobre el prompt. La rama heurística se fuerza con un puerto
muerto, igual que en test_select_clips.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from aurclips import titles
from aurclips.config import Config
from aurclips.select_clips import select_clips


# --- el stub -------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # sin ruido en la salida de pytest
        pass

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # /api/tags: "sí, aquí hay un Ollama corriendo"
        self.server.hits += 1
        self._send(b'{"models": []}')

    def do_POST(self):  # /api/chat: la respuesta enlatada del test
        self.server.hits += 1
        length = int(self.headers.get("Content-Length", 0))
        self.server.chats.append(json.loads(self.rfile.read(length)))
        self._send(json.dumps(self.server.reply).encode("utf-8"))


def _chat_reply(proposal) -> dict:
    """Envuelve una propuesta (dict) o basura (str) en el formato de Ollama."""
    content = json.dumps(proposal) if isinstance(proposal, dict) else proposal
    return {"message": {"content": content}}


PROPUESTA = {
    "titulo": "El secreto del canal que nadie cuenta",
    "descripcion": "Una historia corta con truco incluido.",
    "hashtags": ["secreto", "historia"],
}


@pytest.fixture
def stub():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.hits = 0
    server.chats = []
    server.reply = _chat_reply(PROPUESTA)
    thread = threading.Thread(  # poll corto: que el teardown no pague 0.5 s
        target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


# --- entradas sintéticas -------------------------------------------------

def _cfg(tmp_path: Path, engine: str = "auto", url: str = "http://127.0.0.1:9",
         channel: dict | None = None) -> Config:
    doc = {
        "selection": {"min_clip_seconds": 15, "max_clip_seconds": 59},
        "titles": {"engine": engine, "url": url},
    }
    if channel:
        doc["channel"] = channel
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return Config(path)


def _seg(start: float, end: float, text: str) -> dict:
    tokens = text.split()
    dur = (end - start) / len(tokens)
    return {
        "start": start, "end": end, "text": text,
        "words": [{"word": w, "start": start + i * dur,
                   "end": start + (i + 1) * dur}
                  for i, w in enumerate(tokens)],
    }


ARRANQUE = "Hoy quiero contarte una historia corta sobre el canal."
REMATE = "Y ese es el secreto final que nadie te cuenta."


def _transcript() -> dict:
    """30 s con frases distintas al inicio y al final: si ambas aparecen en el
    prompt, el LLM recibió la transcripción completa, no la primera frase."""
    return {"segments": [
        _seg(0.0, 10.0, ARRANQUE),
        _seg(10.0, 20.0, "El truco importante llega justo en la mitad."),
        _seg(20.0, 30.0, REMATE),
    ]}


NO_VIDEO = "no_existe.mp4"


def _heuristica(tmp_path) -> tuple[str, str]:
    """(título, descripción) del camino sin LLM, como referencia de respaldo."""
    clips = select_clips(_cfg(tmp_path, engine="heuristic"), _transcript(),
                         "video", NO_VIDEO)
    return clips[0].title, clips[0].description


# --- integración vía select_clips ---------------------------------------

def test_la_propuesta_del_stub_se_respeta(stub, tmp_path):
    cfg = _cfg(tmp_path, url=f"http://127.0.0.1:{stub.server_port}")
    clips = select_clips(cfg, _transcript(), "video", NO_VIDEO)
    assert len(clips) == 1
    assert clips[0].title == PROPUESTA["titulo"]
    assert clips[0].description == PROPUESTA["descripcion"]
    assert clips[0].tags == PROPUESTA["hashtags"]


def test_el_prompt_lleva_angulo_ejemplos_y_transcripcion_completa(stub, tmp_path):
    cfg = _cfg(tmp_path, url=f"http://127.0.0.1:{stub.server_port}",
               channel={"angle": "análisis tranquilo de videojuegos",
                        "title_examples": ["Por qué nadie termina este juego"]})
    select_clips(cfg, _transcript(), "video", NO_VIDEO)
    prompt = stub.chats[-1]["messages"][-1]["content"]
    assert "análisis tranquilo de videojuegos" in prompt
    assert "Por qué nadie termina este juego" in prompt
    assert ARRANQUE in prompt and REMATE in prompt


def test_puerto_muerto_conserva_la_heuristica(tmp_path):
    # Ollama "instalado pero apagado" (engine auto, nadie responde): la
    # corrida no se rompe y la metadata de respaldo queda intacta
    titulo, descripcion = _heuristica(tmp_path)
    clips = select_clips(_cfg(tmp_path, engine="auto"), _transcript(),
                         "video", NO_VIDEO)
    assert clips[0].title == titulo
    assert clips[0].description == descripcion


def test_engine_heuristic_no_toca_la_red(stub, tmp_path):
    # heurística elegida a propósito: ni un GET, aunque haya Ollama vivo
    cfg = _cfg(tmp_path, engine="heuristic",
               url=f"http://127.0.0.1:{stub.server_port}")
    select_clips(cfg, _transcript(), "video", NO_VIDEO)
    assert stub.hits == 0


@pytest.mark.parametrize("reply", [
    _chat_reply("esto no es el json pedido"),          # el modelo divagó
    _chat_reply({"titulo": "   ", "descripcion": "x", "hashtags": []}),
])
def test_respuesta_invalida_cae_a_la_heuristica(stub, tmp_path, reply):
    titulo, _ = _heuristica(tmp_path)
    stub.reply = reply
    cfg = _cfg(tmp_path, url=f"http://127.0.0.1:{stub.server_port}")
    clips = select_clips(cfg, _transcript(), "video", NO_VIDEO)
    assert clips[0].title == titulo


# --- saneo de la propuesta, vía titles.propose ---------------------------

def test_titulo_largo_se_recorta_en_palabra(stub, tmp_path):
    largo = ("El secreto definitivo para que tus videos cortos retengan a la "
             "gente durante muchos mas segundos de lo normal cada vez")
    assert len(largo) > titles.MAX_TITLE_CHARS
    stub.reply = _chat_reply({"titulo": largo, "descripcion": "d",
                              "hashtags": ["uno"]})
    cfg = _cfg(tmp_path, url=f"http://127.0.0.1:{stub.server_port}")
    titulo, _, _ = titles.propose(cfg, "texto del clip")
    assert len(titulo) <= titles.MAX_TITLE_CHARS
    assert largo.startswith(titulo)  # el corte cayó en límite de palabra
    assert not titulo.endswith((".", ",", ";", ":"))
