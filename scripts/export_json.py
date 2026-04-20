#!/usr/bin/env python3
"""Export catalog.db to catalog.json + metadata.json for the webapp.

Usage:
    export_json.py [--catalog PATH] [--out DIR]

--out defaults to the webapp_path from ~/.config/en2karp/config.json.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _shared import catalog, paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--out", type=Path, help="Directory to write catalog.json/metadata.json")
    args = parser.parse_args(argv)

    cfg = paths.load_config()
    catalog_db = args.catalog or cfg.catalog_path
    out_dir = args.out or cfg.webapp_path

    if out_dir is None:
        print("error: no output directory — pass --out or set webapp_path in config.", file=sys.stderr)
        return 2
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = catalog.connect(catalog_db)
    try:
        catalog.rebuild_tags_table(conn)
        count = catalog.export_catalog_json(conn, out_dir / "catalog.json")
        meta = catalog.export_metadata_json(conn, out_dir / "metadata.json")
        conn.commit()
    finally:
        conn.close()

    print(f"exported {count} notes · {len(meta['folders'])} folders · {len(meta['tags'])} tags → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
