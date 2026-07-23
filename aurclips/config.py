"""Carga de configuración (config.yaml)."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or ROOT / "config.yaml"
        with open(self.path, "r", encoding="utf-8") as f:
            self.raw: dict = yaml.safe_load(f) or {}

    # --- acceso genérico -------------------------------------------------
    def get(self, dotted: str, default=None):
        node = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def override(self, dotted: str, value) -> None:
        """Pisa una clave solo en esta corrida. No toca config.yaml.

        Para mandos de línea de comandos que valen para una ejecución y no
        deben quedarse escritos (el tope de recortes de `clip`, por ejemplo).
        """
        parts = dotted.split(".")
        node = self.raw
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value

    # --- rutas -----------------------------------------------------------
    def _dir(self, key: str, default: str) -> Path:
        p = ROOT / self.get(f"paths.{key}", default)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        return self._dir("data", "data")

    @property
    def inbox_dir(self) -> Path:
        return self._dir("inbox", "data/inbox")

    @property
    def downloads_dir(self) -> Path:
        return self._dir("downloads", "data/downloads")

    @property
    def work_dir(self) -> Path:
        return self._dir("work", "data/work")

    @property
    def output_dir(self) -> Path:
        return self._dir("output", "data/output")

    @property
    def credentials_dir(self) -> Path:
        return self._dir("credentials", "credentials")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.db"

    # --- ffmpeg ----------------------------------------------------------
    def _tool(self, name: str) -> str:
        bundled = ROOT / self.get("paths.ffmpeg", "tools/ffmpeg/bin") / f"{name}.exe"
        if bundled.exists():
            return str(bundled)
        on_path = shutil.which(name)
        if on_path:
            return on_path
        raise FileNotFoundError(
            f"No se encontró {name}. Ejecuta setup.ps1 o instala ffmpeg y agrégalo al PATH."
        )

    @property
    def ffmpeg(self) -> str:
        return self._tool("ffmpeg")

    @property
    def ffprobe(self) -> str:
        return self._tool("ffprobe")
