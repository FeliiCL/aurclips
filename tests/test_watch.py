"""Tests del modo continuo: el gate de estabilidad, las cadencias y la parada.

Seams bajo test: ``_is_settled`` (un archivo a medio escribir no entra),
``cadence_due`` (los trabajos con cadencia), ``meta_get/meta_set`` (dónde se
recuerdan), y el ``keep_going`` de cmd_process (la parada ordenada respeta la
transición en curso y deja el resto en cola). El loop del demonio en sí es
pegamento (señales, sleep) y se verifica a mano.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from aurclips.config import Config
from aurclips.ingest import _is_settled, scan_inbox
from aurclips.runner import cadence_due, prune_run_logs
from aurclips.state import State


# --- el gate de estabilidad --------------------------------------------------

def test_un_archivo_recien_tocado_no_esta_asentado(tmp_path):
    f = tmp_path / "grabando.mp4"
    f.write_bytes(b"x")
    assert not _is_settled(f, min_age_seconds=60)


def test_un_archivo_viejo_si_esta_asentado(tmp_path):
    f = tmp_path / "terminado.mp4"
    f.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert _is_settled(f, min_age_seconds=60)


def test_un_archivo_abierto_para_escritura_no_entra(tmp_path):
    """La señal de Windows: mientras la grabadora tenga el handle, no entra."""
    f = tmp_path / "obs_grabando.mp4"
    with open(f, "wb") as handle:
        handle.write(b"x")
        handle.flush()
        old = time.time() - 3600
        os.utime(f, (old, old))  # mtime viejo, pero el handle sigue abierto
        if os.name == "nt":  # en POSIX renombrar con handle abierto sí funciona
            assert not _is_settled(f, min_age_seconds=60)
    assert _is_settled(f, min_age_seconds=60)  # cerrado: ya puede entrar


def test_un_fallo_ajeno_del_probe_no_veta_el_archivo(tmp_path, monkeypatch):
    """Un antivirus o una ACL sin permiso de rename NO pueden bloquear un
    archivo para siempre: solo 'abierto por otro proceso' (winerror 32/33)
    cuenta; para lo demás, el mtime quieto es el juez."""
    import pathlib

    f = tmp_path / "con_acl_rara.mp4"
    f.write_bytes(b"x")
    old = time.time() - 3600
    os.utime(f, (old, old))

    denied = OSError("acceso denegado por politica")
    denied.winerror = 5  # no es sharing violation
    monkeypatch.setattr(pathlib.Path, "rename",
                        lambda self, target: (_ for _ in ()).throw(denied))
    monkeypatch.setattr(os, "name", "nt", raising=False)
    assert _is_settled(f, min_age_seconds=60)


def test_scan_inbox_no_registra_lo_que_aun_se_escribe(tmp_path):
    doc = {"paths": {"inbox": str(tmp_path / "inbox")},
           "watch": {"settle_seconds": 60}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")

    fresco = cfg.inbox_dir / "grabando.mp4"
    fresco.write_bytes(b"x")
    viejo = cfg.inbox_dir / "listo.mp4"
    viejo.write_bytes(b"x")
    stamp = time.time() - 3600
    os.utime(viejo, (stamp, stamp))

    assert scan_inbox(cfg, db) == 1  # solo el asentado
    assert db.video_known(str(viejo.resolve()))
    assert not db.video_known(str(fresco.resolve()))

    # el siguiente escaneo lo toma ya completo, sin estado extra
    os.utime(fresco, (stamp, stamp))
    assert scan_inbox(cfg, db) == 1
    assert db.video_known(str(fresco.resolve()))


# --- cadencias ---------------------------------------------------------------

def test_sin_registro_previo_la_cadencia_toca():
    assert cadence_due(None, 12, datetime(2026, 7, 23, 12, 0))


def test_antes_de_cumplirse_no_toca():
    now = datetime(2026, 7, 23, 12, 0)
    hace_una_hora = (now - timedelta(hours=1)).isoformat()
    assert not cadence_due(hace_una_hora, 12, now)


def test_cumplida_la_cadencia_toca():
    now = datetime(2026, 7, 23, 12, 0)
    hace_trece_horas = (now - timedelta(hours=13)).isoformat()
    assert cadence_due(hace_trece_horas, 12, now)


def test_un_timestamp_roto_cuenta_como_nunca():
    assert cadence_due("esto no es una fecha", 12, datetime(2026, 7, 23))


def test_la_cadencia_se_recuerda_en_meta():
    db = State(":memory:")
    assert db.meta_get("last_auto_retry") is None
    db.meta_set("last_auto_retry", "2026-07-23T12:00:00")
    assert db.meta_get("last_auto_retry") == "2026-07-23T12:00:00"
    db.meta_set("last_auto_retry", "2026-07-24T12:00:00")  # upsert
    assert db.meta_get("last_auto_retry") == "2026-07-24T12:00:00"


# --- la parada ordenada ------------------------------------------------------

def test_keep_going_falso_deja_los_videos_en_cola(tmp_path):
    from aurclips.__main__ import cmd_process

    doc = {"paths": {"data": str(tmp_path / "data"),
                     "work": str(tmp_path / "work")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")
    db.add_video("local", "a.mp4", "a", "a.mp4", 100.0)
    db.add_video("local", "b.mp4", "b", "b.mp4", 100.0)

    cmd_process(cfg, db, keep_going=lambda: False)
    # nada se procesó ni se marcó fallido: la cola quedó intacta
    assert len(db.videos_to_process()) == 2
    assert all(v["status"] == "new" for v in db.recent_videos())


# --- la subida en watch va con cadencia, no por ciclo ------------------------

def test_watch_no_sube_en_cada_ciclo(tmp_path, monkeypatch):
    """Cada ciclo subiendo convertiría max_uploads_per_run (tope por corrida
    DIARIA) en un tope por minuto y reventaría la cuota de YouTube."""
    from types import SimpleNamespace

    from aurclips import upload as upload_mod
    from aurclips.__main__ import _watch_cycle

    doc = {"paths": {"data": str(tmp_path / "data"),
                     "inbox": str(tmp_path / "inbox")},
           "upload": {"enabled": True}, "review": {"enabled": False},
           "watch": {"retry_hours": 0, "upload_hours": 6}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")
    vid = db.add_video("local", "v.mp4", "v", "v.mp4", 100.0)
    db.video_finished(vid)
    clip_id = db.add_clip(vid, 0, SimpleNamespace(
        start=0.0, end=30.0, title="t", description="", tags=[],
        score=1.0, marked=False), "texto")
    db.clip_rendered(clip_id, "salida.mp4")

    rondas = []
    monkeypatch.setattr(upload_mod, "upload_pending",
                        lambda c, d: rondas.append(1) or 0)

    _watch_cycle(cfg, db, lambda: True)
    _watch_cycle(cfg, db, lambda: True)  # mismo "día": no toca otra ronda
    assert len(rondas) == 1


# --- lo transitorio no se registra como decisión -----------------------------

def test_una_descarga_fallida_no_queda_vetada(monkeypatch, tmp_path):
    """Un corte de red en la corrida nocturna no puede blacklistear el video:
    no se registra nada y la próxima corrida lo intenta de nuevo."""
    from aurclips import ingest as ingest_mod

    doc = {"channels": ["https://youtube.com/@canal/videos"],
           "paths": {"downloads": str(tmp_path / "descargas"),
                     "logs": str(tmp_path / "logs")}}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    cfg = Config(tmp_path / "config.yaml")
    db = State(":memory:")

    monkeypatch.setattr(ingest_mod, "list_channel_videos",
                        lambda c, u: ["vid_transitorio"])
    monkeypatch.setattr(ingest_mod, "download_video",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("sin conexión a YouTube")))
    ingest_mod.check_channels(cfg, db)
    assert not db.video_known("vid_transitorio")  # libre para reintentarse


# --- el demonio vivo no es una corrida incompleta ----------------------------

def test_con_el_lock_tomado_el_estado_dice_en_marcha(tmp_path):
    from aurclips.runner import last_run_line, single_instance

    (tmp_path / "watch_2026-07-23_120000.log").write_text(
        "vigilando...", encoding="utf-8")  # sesión viva: sin línea de cierre
    with single_instance(tmp_path / "run.lock") as acquired:
        assert acquired
        assert last_run_line(tmp_path) == "en marcha ahora mismo"
    # soltado el lock, el log sin cierre sí es una corrida incompleta
    assert "INCOMPLETA" in last_run_line(tmp_path)


# --- rotación por patrón -----------------------------------------------------

def test_los_logs_de_watch_rotan_sin_tocar_los_de_run(tmp_path):
    for i in range(12):
        (tmp_path / f"watch_2026-01-{i + 1:02d}_030000.log").write_text("x")
    (tmp_path / "run_2026-01-01_0300.log").write_text("x")
    removed = prune_run_logs(tmp_path, keep=10, pattern="watch_*.log")
    assert removed == 2
    assert (tmp_path / "run_2026-01-01_0300.log").exists()
