#!/usr/bin/env python3
"""Phase 2 indexer: walk an Obsidian vault and refresh the note catalog.

Usage:
    phase2_index.py [--vault PATH] [--catalog PATH]
                    [--ocr-engine auto|apple_vision|tesseract|none]
                    [--sweep-resources]

If --vault / --catalog are omitted, values come from ~/.config/en2karp/config.json.
With --sweep-resources, also OCR every supported image under `_resources/`
that is not already in the cache (for orphans no note references).

Prints a short summary to stdout; raises non-zero exit on fatal errors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _shared import catalog, paths, vault  # noqa: E402

MIN_OCR_DIM = 100
MIN_OCR_CHARS = 10


def _image_dimensions(image_path: Path) -> tuple[int, int] | None:
    """Return (width, height) or None if Pillow can't open the file."""
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        print("error: Pillow not installed — run `pip install pillow`.", file=sys.stderr)
        raise
    try:
        with Image.open(image_path) as img:
            return img.size
    except Exception:
        return None


def _run_ocr(image_path: Path, engine: str) -> tuple[str, dict]:
    """Run the OS OCR engine. Returns (text, extras).

    extras contains `was_skipped` and, if skipped, a normalized `skip_reason`
    from the enum {'too_small','unsupported_type','ocr_failed','ocr_disabled'}.
    """
    if engine == "apple_vision":
        return _run_apple_vision(image_path)
    if engine == "tesseract":
        return _run_tesseract(image_path)
    print(f"warn: unknown OCR engine '{engine}' — skipping", file=sys.stderr)
    return "", {"was_skipped": 1, "skip_reason": "ocr_failed"}


def _run_apple_vision(image_path: Path) -> tuple[str, dict]:
    import subprocess

    script = (
        'use framework "Vision"\n'
        'use framework "Foundation"\n'
        'on run argv\n'
        '  set imgPath to item 1 of argv\n'
        '  set nsURL to current application\'s |NSURL|\'s fileURLWithPath:imgPath\n'
        '  set req to current application\'s VNRecognizeTextRequest\'s alloc()\'s init()\n'
        '  req\'s setRecognitionLevel:(current application\'s VNRequestTextRecognitionLevelAccurate)\n'
        '  set handler to current application\'s VNImageRequestHandler\'s alloc()\'s initWithURL:nsURL options:(current application\'s NSDictionary\'s dictionary())\n'
        '  handler\'s performRequests:{req} |error|:(missing value)\n'
        '  set results to req\'s results()\n'
        '  set outText to ""\n'
        '  repeat with obs in results\n'
        '    set top to (obs\'s topCandidates:1)\n'
        '    if (count of top) > 0 then\n'
        '      set outText to outText & ((item 1 of top)\'s |string|() as string) & linefeed\n'
        '    end if\n'
        '  end repeat\n'
        '  return outText\n'
        'end run\n'
    )
    try:
        result = subprocess.run(
            ["osascript", "-l", "AppleScript", "-e", script, str(image_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "osascript failed").strip()
            print(f"warn: apple_vision failed on {image_path.name}: {stderr[:200]}",
                  file=sys.stderr)
            return "", {"was_skipped": 1, "skip_reason": "ocr_failed"}
        return result.stdout.strip(), {"was_skipped": 0}
    except Exception as exc:  # pragma: no cover - hardware-dependent
        print(f"warn: apple_vision error on {image_path.name}: {exc}", file=sys.stderr)
        return "", {"was_skipped": 1, "skip_reason": "ocr_failed"}


def _run_tesseract(image_path: Path) -> tuple[str, dict]:
    import shutil
    import subprocess

    if shutil.which("tesseract") is None:
        print("warn: tesseract not installed — skipping OCR", file=sys.stderr)
        return "", {"was_skipped": 1, "skip_reason": "ocr_failed"}
    try:
        result = subprocess.run(
            ["tesseract", str(image_path), "-", "-l", "eng"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "tesseract failed").strip()
            print(f"warn: tesseract failed on {image_path.name}: {stderr[:200]}",
                  file=sys.stderr)
            return "", {"was_skipped": 1, "skip_reason": "ocr_failed"}
        return result.stdout.strip(), {"was_skipped": 0}
    except Exception as exc:  # pragma: no cover
        print(f"warn: tesseract error on {image_path.name}: {exc}", file=sys.stderr)
        return "", {"was_skipped": 1, "skip_reason": "ocr_failed"}


def _index_image_at_path(
    conn,
    resolved: Path,
    engine: str,
    note_id: str | None,
) -> tuple[bool, bool]:
    """Hash + (maybe) OCR an already-resolved image path.

    Returns (indexed, has_text):
      indexed  — True if the image was inserted OR already cached.
      has_text — True if a non-empty, post-filter `ocr_text` is on the row.
    """
    if not resolved.is_file():
        return False, False

    image_hash = vault.hash_file(resolved)
    existing = catalog.get_ocr_by_hash(conn, image_hash)
    if existing is not None:
        return True, bool(existing["ocr_text"])

    record: dict = {
        "image_hash": image_hash,
        "file_path": str(resolved),
        "ocr_text": None,
        "ocr_engine": engine,
        "note_id": note_id,
        "was_skipped": 0,
        "skip_reason": None,
        "image_width": None,
        "image_height": None,
    }

    if engine == "none":
        record.update(was_skipped=1, skip_reason="ocr_disabled")
        catalog.upsert_ocr(conn, record)
        return True, False

    if resolved.suffix.lower() not in vault.OCR_SUPPORTED_EXTENSIONS:
        record.update(was_skipped=1, skip_reason="unsupported_type")
        catalog.upsert_ocr(conn, record)
        return True, False

    dims = _image_dimensions(resolved)
    if dims is None:
        record.update(was_skipped=1, skip_reason="unsupported_type")
        catalog.upsert_ocr(conn, record)
        return True, False
    width, height = dims
    record["image_width"] = width
    record["image_height"] = height
    if width < MIN_OCR_DIM or height < MIN_OCR_DIM:
        record.update(was_skipped=1, skip_reason="too_small")
        catalog.upsert_ocr(conn, record)
        return True, False

    text, extras = _run_ocr(resolved, engine)
    if extras.get("was_skipped"):
        record.update(extras)
        catalog.upsert_ocr(conn, record)
        return True, False

    if len((text or "").strip()) < MIN_OCR_CHARS:
        record.update(was_skipped=1, skip_reason="ocr_failed")
        catalog.upsert_ocr(conn, record)
        return True, False

    record["ocr_text"] = text
    catalog.upsert_ocr(conn, record)
    return True, True


def _index_image(
    conn,
    vault_root: Path,
    note_path: Path,
    ref: str,
    engine: str,
    note_id: str,
) -> tuple[bool, bool]:
    """Resolve an in-note image reference, then delegate to _index_image_at_path."""
    resolved = vault.resolve_image_ref(vault_root, note_path, ref)
    if resolved is None:
        return False, False
    return _index_image_at_path(conn, resolved, engine, note_id)


def _sweep_resources(conn, vault_root: Path, engine: str) -> tuple[int, int]:
    """Walk every `_resources/` tree in the vault, OCR unseen images.

    Returns (touched, newly_with_text).
    """
    touched = 0
    with_text = 0
    for resources in vault_root.rglob("_resources"):
        if not resources.is_dir():
            continue
        for image in resources.rglob("*"):
            if not image.is_file():
                continue
            if image.suffix.lower() not in vault.OCR_SUPPORTED_EXTENSIONS:
                continue
            indexed, has_text = _index_image_at_path(conn, image, engine, note_id=None)
            if indexed:
                touched += 1
                if has_text:
                    with_text += 1
    return touched, with_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reindex an Obsidian vault into catalog.db.")
    parser.add_argument("--vault", type=Path, help="Path to Obsidian vault root.")
    parser.add_argument("--catalog", type=Path, help="Path to catalog.db.")
    parser.add_argument(
        "--ocr-engine",
        choices=["auto", "apple_vision", "tesseract", "none"],
        help="OCR engine. 'none' skips OCR entirely.",
    )
    parser.add_argument(
        "--sweep-resources",
        action="store_true",
        help="After the note walk, OCR every unseen image under _resources/.",
    )
    args = parser.parse_args(argv)

    cfg = paths.load_config()
    vault_root = (args.vault or cfg.vault_path)
    catalog_db = (args.catalog or cfg.catalog_path)
    engine_arg = args.ocr_engine or cfg.ocr_engine
    engine = "none" if engine_arg == "none" else paths.resolve_ocr_engine(engine_arg)

    if vault_root is None:
        print("error: no vault path set — pass --vault or run config setup.", file=sys.stderr)
        return 2
    vault_root = Path(vault_root).expanduser().resolve()
    if not vault_root.is_dir():
        print(f"error: vault path does not exist: {vault_root}", file=sys.stderr)
        return 2

    conn = catalog.connect(catalog_db)
    try:
        before_ids = catalog.all_note_ids(conn)
        seen_ids: set[str] = set()
        note_count = 0
        resolved_images = 0

        for note_path in vault.iter_notes(vault_root):
            parsed = vault.parse_note(vault_root, note_path)
            body = parsed.pop("body", "")
            refs = vault.find_image_references(body)

            any_ocr = False
            for ref in refs:
                indexed, has_text = _index_image(
                    conn, vault_root, note_path, ref, engine, parsed["note_id"]
                )
                if indexed:
                    resolved_images += 1
                if has_text:
                    any_ocr = True

            parsed["image_count"] = len(refs)  # total refs incl. missing, per spec
            parsed["has_ocr"] = any_ocr
            catalog.upsert_note(conn, parsed)
            seen_ids.add(parsed["note_id"])
            note_count += 1

        sweep_touched = sweep_with_text = 0
        if args.sweep_resources:
            sweep_touched, sweep_with_text = _sweep_resources(conn, vault_root, engine)

        deleted = catalog.mark_deleted(conn, before_ids - seen_ids)
        catalog.rebuild_tags_table(conn)
        conn.commit()

        summary = (
            f"indexed {note_count} notes · {resolved_images} resolved images · "
            f"{deleted} removed · catalog={catalog_db}"
        )
        if args.sweep_resources:
            summary += f" · sweep: {sweep_touched} images ({sweep_with_text} with text)"
        print(summary)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
