"""CLI de aurclips.

Uso:
    python -m aurclips run       # pipeline completo (ingesta -> proceso -> subida)
    python -m aurclips ingest    # solo buscar/descargar contenido nuevo
    python -m aurclips process   # solo transcribir + seleccionar + renderizar
    python -m aurclips upload    # solo subir clips renderizados
    python -m aurclips auth      # iniciar sesión de YouTube (una sola vez)
    python -m aurclips status    # ver estado de videos y clips
    python -m aurclips report    # métricas de los Shorts publicados y cola
    python -m aurclips retry     # reencolar videos/clips fallidos
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .config import Config
from .state import State


def _load() -> tuple[Config, State]:
    cfg = Config()
    db = State(cfg.db_path)
    return cfg, db


def cmd_ingest(cfg: Config, db: State):
    from .ingest import ingest
    ingest(cfg, db)


def cmd_process(cfg: Config, db: State):
    from .render import render_clip
    from .safety import check_text, is_duplicate
    from .select_clips import clip_words, select_clips
    from .transcribe import transcribe

    videos = db.videos_with_status("new", "transcribed", "selected")
    if not videos:
        print("[2/4] No hay videos pendientes por procesar")
        return
    max_videos = cfg.get("limits.max_videos_per_run", 3)
    if len(videos) > max_videos:
        print(f"[2/4] {len(videos)} pendientes; se procesan {max_videos} "
              f"(limits.max_videos_per_run)")
        videos = videos[:max_videos]
    for video in videos:
        vid = video["id"]
        title = video["title"] or f"video_{vid}"
        workdir = cfg.work_dir / f"video_{vid}"
        transcript_path = workdir / "transcript.json"
        try:
            # --- transcribir -------------------------------------------
            if video["status"] == "new":
                print(f"[2/4] Transcribiendo: {title}")
                transcript = transcribe(cfg, video["path"], transcript_path)
                db.update_video(vid, status="transcribed")
            else:
                transcript = json.loads(transcript_path.read_text(encoding="utf-8"))

            # --- seleccionar clips -------------------------------------
            if video["status"] in ("new", "transcribed"):
                print(f"[3/4] Seleccionando clips: {title}")
                clips = select_clips(cfg, transcript, title, video["path"])
                if not clips:
                    print("  sin clips útiles; video marcado como terminado")
                    db.update_video(vid, status="done")
                    continue
                added = 0
                for i, c in enumerate(clips):
                    text = " ".join(
                        w["word"] for w in clip_words(transcript, c.start_s, c.end_s)
                    )
                    status = "pending"
                    # filtro de contenido no apto
                    if cfg.get("safety.enabled", True):
                        flagged = check_text(cfg, text)
                        if flagged:
                            action = cfg.get("safety.action", "skip")
                            print(f"  [filtro] clip {c.title!r} contiene: "
                                  f"{', '.join(flagged[:5])} -> {action}")
                            if action == "skip":
                                continue
                            status = "flagged"
                    # limpieza de duplicados
                    if cfg.get("dedup.enabled", True):
                        dup, dup_id = is_duplicate(
                            db, text, cfg.get("dedup.similarity", 0.8))
                        if dup:
                            print(f"  [dedup] clip {c.title!r} es casi idéntico "
                                  f"al clip #{dup_id}; se omite")
                            continue
                    db.add_clip(vid, i, c.start_s, c.end_s, c.title,
                                c.description, c.hashtags, text=text,
                                score=c.score, status=status)
                    added += 1
                if not added:
                    print("  todos los clips fueron filtrados; video terminado")
                    db.update_video(vid, status="done")
                    continue
                db.update_video(vid, status="selected")

            # --- renderizar --------------------------------------------
            pending = [c for c in db.clips_for_video(vid) if c["status"] == "pending"]
            for clip in pending:
                words = clip_words(transcript, clip["start"], clip["end"])
                out = render_clip(cfg, video["path"], clip["start"], clip["end"],
                                  clip["title"], words, clip["id"])
                db.update_clip(clip["id"], status="rendered", path=str(out))
            db.update_video(vid, status="done")
        except Exception as e:  # noqa: BLE001 — un video fallido no detiene el resto
            print(f"  [error] video {vid} ({title}): {e}")
            traceback.print_exc()
            db.update_video(vid, status="failed", error=str(e)[:500])
            from .notify import notify
            notify(cfg, "error", f"Falló el video '{title}': {str(e)[:200]}")


def cmd_upload(cfg: Config, db: State):
    from .upload import upload_pending
    upload_pending(cfg, db)


def cmd_auth(cfg: Config, db: State):
    from .upload import get_credentials
    get_credentials(cfg, interactive=True)
    print("Autenticación de YouTube completada.")


def cmd_status(cfg: Config, db: State):
    print("== Videos ==")
    rows = db.conn.execute(
        "SELECT id, status, source, title, duration FROM videos ORDER BY id DESC LIMIT 20"
    ).fetchall()
    if not rows:
        print("  (ninguno)")
    for r in rows:
        dur = f"{r['duration']:.0f}s" if r["duration"] else "?"
        print(f"  #{r['id']:<4} {r['status']:<12} [{r['source']}] {r['title'] or ''} ({dur})")
    print("\n== Clips ==")
    rows = db.conn.execute(
        "SELECT id, status, title, publish_at, youtube_id FROM clips ORDER BY id DESC LIMIT 20"
    ).fetchall()
    if not rows:
        print("  (ninguno)")
    for r in rows:
        extra = ""
        if r["youtube_id"]:
            extra = f" -> https://youtu.be/{r['youtube_id']} @ {r['publish_at'] or '?'}"
        print(f"  #{r['id']:<4} {r['status']:<10} {r['title'] or ''}{extra}")


def cmd_report(cfg: Config, db: State):
    from .stats import build_report, fetch_stats
    if cfg.get("stats.enabled", True):
        n = fetch_stats(cfg, db)
        if n:
            print(f"(métricas actualizadas para {n} clip(s))\n")
    print(build_report(db))


def cmd_retry(cfg: Config, db: State):
    """Reencola videos y clips fallidos."""
    n = 0
    for video in db.videos_with_status("failed"):
        transcript = cfg.work_dir / f"video_{video['id']}" / "transcript.json"
        new_status = "transcribed" if transcript.exists() else "new"
        db.update_video(video["id"], status=new_status, error=None)
        n += 1
    for clip in db.clips_with_status("failed"):
        new_status = "rendered" if clip["path"] else "pending"
        db.update_clip(clip["id"], status=new_status, error=None)
        n += 1
    print(f"{n} elemento(s) reencolados. Corre 'run' para reintentarlos.")


def cmd_run(cfg: Config, db: State):
    from .notify import notify
    cmd_ingest(cfg, db)
    cmd_process(cfg, db)
    cmd_upload(cfg, db)
    uploaded = len(db.clips_with_status("uploaded"))
    queued = len(db.clips_with_status("rendered"))
    notify(cfg, "run",
           f"Corrida completa: {uploaded} clips subidos en total, {queued} en cola")
    print("\nCorrida completa.")


COMMANDS = {
    "run": cmd_run,
    "ingest": cmd_ingest,
    "process": cmd_process,
    "upload": cmd_upload,
    "auth": cmd_auth,
    "status": cmd_status,
    "report": cmd_report,
    "retry": cmd_retry,
}


def main():
    parser = argparse.ArgumentParser(prog="aurclips", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS.keys())
    args = parser.parse_args()
    cfg, db = _load()
    try:
        COMMANDS[args.command](cfg, db)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        sys.exit(130)


if __name__ == "__main__":
    main()
