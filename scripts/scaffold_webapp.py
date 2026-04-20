#!/usr/bin/env python3
"""One-shot: clone the en2karp-webapp-template and populate it with catalog JSON.

Usage:
    scaffold_webapp.py --dest DIR [--template URL] [--catalog PATH]

If --template is omitted, defaults to the public GitHub template repo.
After scaffolding, also saves webapp_path to the user's config so subsequent
'export-webapp' calls need no arguments.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _shared import paths  # noqa: E402

import export_json  # noqa: E402

DEFAULT_TEMPLATE = "https://github.com/wordsmith189/en2karp-webapp-template.git"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True,
                        help="Target directory to create (must not exist).")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE,
                        help="Template repo URL or local path.")
    parser.add_argument("--catalog", type=Path, help="Path to catalog.db.")
    args = parser.parse_args(argv)

    dest: Path = args.dest.expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"error: destination not empty: {dest}", file=sys.stderr)
        return 2

    if shutil.which("git") is None:
        print("error: git is required to clone the template.", file=sys.stderr)
        return 2

    print(f"→ cloning {args.template} → {dest}")
    subprocess.run(["git", "clone", "--depth", "1", args.template, str(dest)], check=True)
    shutil.rmtree(dest / ".git", ignore_errors=True)

    cfg = paths.load_config()
    cfg.webapp_path = dest
    paths.save_config(cfg)
    print(f"→ webapp_path saved → {paths.CONFIG_PATH}")

    export_args = ["--out", str(dest)]
    if args.catalog is not None:
        export_args += ["--catalog", str(args.catalog)]
    return export_json.main(export_args)


if __name__ == "__main__":
    raise SystemExit(main())
