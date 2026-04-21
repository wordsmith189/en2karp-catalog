#!/usr/bin/env python3
"""Export catalog.db to catalog.json + metadata.json + per-note sidecars for the webapp.

Usage:
    export_json.py [--catalog PATH] [--out DIR] [--vault PATH]
                   [--skip-sidecars] [--skip-assets]

--out defaults to the webapp_path from ~/.config/en2karp/config.json.
--vault defaults to vault_path from the same config.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _shared import assets, catalog, paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--out", type=Path, help="Directory to write catalog.json/metadata.json/notes/assets")
    parser.add_argument("--vault", type=Path, help="Vault root (required for sidecar export)")
    parser.add_argument("--skip-sidecars", action="store_true",
                        help="Skip per-note sidecar JSON export")
    parser.add_argument("--skip-assets", action="store_true",
                        help="Emit sidecars but leave image refs unrewritten and don't copy assets")
    args = parser.parse_args(argv)

    cfg = paths.load_config()
    catalog_db = args.catalog or cfg.catalog_path
    out_dir = args.out or cfg.webapp_path
    vault_path = args.vault or cfg.vault_path

    if out_dir is None:
        print("error: no output directory — pass --out or set webapp_path in config.", file=sys.stderr)
        return 2
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = catalog.connect(catalog_db)
    sidecar_count = 0
    try:
        catalog.rebuild_tags_table(conn)
        count = catalog.export_catalog_json(conn, out_dir / "catalog.json")
        meta = catalog.export_metadata_json(conn, out_dir / "metadata.json")
        if not args.skip_sidecars:
            if vault_path is None:
                print("warning: no vault path — skipping sidecar export. "
                      "Pass --vault or set vault_path in config.", file=sys.stderr)
            else:
                sidecar_count = assets.export_note_sidecars(
                    conn,
                    vault_path,
                    out_dir,
                    copy_assets=not args.skip_assets,
                )
        conn.commit()
    finally:
        conn.close()

    msg = (f"exported {count} notes · {len(meta['folders'])} folders · "
           f"{len(meta['tags'])} tags → {out_dir}")
    if sidecar_count:
        msg += f" · {sidecar_count} sidecars"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
