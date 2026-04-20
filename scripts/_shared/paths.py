"""Config loading and user-path resolution for en2karp skills.

Uses a JSON config at ~/.config/en2karp/config.json to avoid a TOML dep on
older Python runtimes. The file is plain and small:

    {
        "vault_path":   "~/iCloud Drive/Obsidian/Main",
        "catalog_path": "~/Library/Application Support/en2karp/catalog.db",
        "webapp_path":  "~/repos/en2karp-webapp-template",
        "ocr_engine":   "apple_vision"    // or "tesseract" or "auto"
    }

Callers should use load_config() and access fields through the returned
dataclass; missing-file and missing-key cases are handled here so scripts
can focus on their own logic.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field, asdict
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "en2karp"
CONFIG_PATH = CONFIG_DIR / "config.json"


@dataclass
class Config:
    vault_path: Path | None = None
    catalog_path: Path = field(default_factory=lambda: _default_catalog_path())
    webapp_path: Path | None = None
    ocr_engine: str = "auto"

    def to_dict(self) -> dict:
        d = asdict(self)
        for key, value in list(d.items()):
            if isinstance(value, Path):
                d[key] = str(value)
        return d


def _default_catalog_path() -> Path:
    # macOS: use Application Support so it's user-scoped but out of iCloud.
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "en2karp" / "catalog.db"
    return Path.home() / ".local" / "share" / "en2karp" / "catalog.db"


def load_config(path: str | Path = CONFIG_PATH) -> Config:
    """Load config. If the file is missing, return defaults with vault unset."""
    p = Path(path).expanduser()
    if not p.exists():
        return Config()
    raw = json.loads(p.read_text())
    return Config(
        vault_path=_opt_path(raw.get("vault_path")),
        catalog_path=_opt_path(raw.get("catalog_path")) or _default_catalog_path(),
        webapp_path=_opt_path(raw.get("webapp_path")),
        ocr_engine=raw.get("ocr_engine", "auto"),
    )


def save_config(cfg: Config, path: str | Path = CONFIG_PATH) -> Path:
    """Persist config, creating parent directories as needed."""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg.to_dict(), indent=2))
    return p


def resolve_ocr_engine(requested: str) -> str:
    """Turn 'auto' into a concrete engine for the current platform."""
    if requested in ("apple_vision", "tesseract"):
        return requested
    if platform.system() == "Darwin":
        return "apple_vision"
    return "tesseract"


def _opt_path(value) -> Path | None:
    if not value:
        return None
    return Path(os.path.expanduser(str(value)))
