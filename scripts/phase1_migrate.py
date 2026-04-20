#!/usr/bin/env python3
"""Phase 1 one-time migration: Evernote → ENEX → Obsidian markdown → catalog.

Wraps `evernote-backup` (export from Evernote Cloud API) and `yarle`
(ENEX → markdown), then delegates to phase2_index for the first catalog build.

Both tools must be on PATH; the script errors with an install hint if either
is missing. Re-running is safe — evernote-backup's db is resumable and yarle
re-emits markdown idempotently.

Usage:
    phase1_migrate.py --backup-dir DIR --enex-dir DIR --vault DIR [--catalog PATH]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _shared import paths  # noqa: E402

import phase2_index  # noqa: E402


def _require(tool: str, hint: str) -> None:
    if shutil.which(tool) is None:
        print(f"error: '{tool}' not found on PATH — {hint}", file=sys.stderr)
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", type=Path, required=True,
                        help="Where evernote-backup stores its SQLite db + ENEX exports.")
    parser.add_argument("--enex-dir", type=Path, required=True,
                        help="Directory for ENEX files (one per note, nested by notebook).")
    parser.add_argument("--vault", type=Path, required=True,
                        help="Target Obsidian vault directory.")
    parser.add_argument("--catalog", type=Path, help="Path to catalog.db (defaults to config).")
    parser.add_argument("--skip-evernote", action="store_true",
                        help="Skip evernote-backup step (ENEX already present).")
    parser.add_argument("--skip-yarle", action="store_true",
                        help="Skip yarle step (markdown already present).")
    args = parser.parse_args(argv)

    backup_dir: Path = args.backup_dir.expanduser().resolve()
    enex_dir: Path = args.enex_dir.expanduser().resolve()
    vault: Path = args.vault.expanduser().resolve()

    backup_dir.mkdir(parents=True, exist_ok=True)
    enex_dir.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)

    if not args.skip_evernote:
        _require("evernote-backup",
                 "install with: pipx install evernote-backup  (or pip install evernote-backup)")
        db_path = backup_dir / "en_backup.db"
        if not db_path.exists():
            print(f"→ evernote-backup init-db (interactive login) → {db_path}")
            subprocess.run(
                ["evernote-backup", "init-db", "--database", str(db_path)],
                check=True,
            )
        print("→ evernote-backup sync")
        subprocess.run(
            ["evernote-backup", "sync", "--database", str(db_path)],
            check=True,
        )
        print(f"→ evernote-backup export --single-notes → {enex_dir}")
        subprocess.run(
            [
                "evernote-backup", "export",
                "--database", str(db_path),
                "--include-trash",
                "--single-notes",
                str(enex_dir),
            ],
            check=True,
        )

    if not args.skip_yarle:
        _require("yarle",
                 "install with: npm install -g yarle-evernote-to-md")
        print(f"→ yarle {enex_dir} → {vault}")
        subprocess.run(
            [
                "yarle",
                "--enexSources", str(enex_dir),
                "--outputDir", str(vault),
                "--outputFormat", "ObsidianMD",
                "--useHashedFilenames", "false",
                "--keepOriginalHierarchy", "true",
                "--putFrontMatterAtTop", "true",
                "--dateFormat", "YYYY-MM-DD",
                "--keepOriginalHtml", "false",
                "--isMetadataNeeded", "true",
            ],
            check=True,
        )

    cfg = paths.load_config()
    catalog_db = args.catalog or cfg.catalog_path
    print(f"→ phase2 index (with --sweep-resources) → {catalog_db}")
    return phase2_index.main([
        "--vault", str(vault),
        "--catalog", str(catalog_db),
        "--sweep-resources",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
