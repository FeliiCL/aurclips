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


# --- rotación por patrón -----------------------------------------------------

def test_los_logs_de_watch_rotan_sin_tocar_los_de_run(tmp_path):
    for i in range(12):
        (tmp_path / f"watch_2026-01-{i + 1:02d}_030000.log").write_text("x")
    (tmp_path / "run_2026-01-01_0300.log").write_text("x")
    removed = prune_run_logs(tmp_path, keep=10, pattern="watch_*.log")
    assert removed == 2
    assert (tmp_path / "run_2026-01-01_0300.log").exists()
