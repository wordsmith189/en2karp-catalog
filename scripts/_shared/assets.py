"""Copy vault image assets into a webapp tree and emit per-note JSON sidecars.

Kept separate from catalog.py because it pulls in vault + frontmatter, both
of which already import catalog — putting the export here avoids a circular
import.

Public surface:
    copy_note_assets(body, note_abs_path, vault_path, assets_dir, ocr_lookup)
        -> (rewritten_body, image_records)
    export_note_sidecars(conn, vault_path, out_dir, *, copy_assets=True)
        -> int
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

try:  # pragma: no cover - vendored import shape
    from . import vault as _vault
    from . import frontmatter as _fm
except ImportError:  # pragma: no cover
    import vault as _vault  # type: ignore
    import frontmatter as _fm  # type: ignore


_IMG_WIKILINK_RE = re.compile(r"!\[\[([^\]]+?)\]\]")
_IMG_MARKDOWN_RE = re.compile(r"(!\[[^\]]*?\]\()([^)]+?)(\))")


def _is_image_ref(ref: str) -> bool:
    cleaned = ref.split("|", 1)[0].split("#", 1)[0].strip().lower()
    if not cleaned:
        return False
    return any(cleaned.endswith(ext) for ext in _vault.IMAGE_EXTENSIONS)


def copy_note_assets(
    body: str,
    note_abs_path: Path,
    vault_path: Path,
    assets_dir: Path,
    *,
    ocr_lookup: dict[str, sqlite3.Row] | None = None,
) -> tuple[str, list[dict]]:
    """Rewrite image refs in body to point at content-addressed copies under
    assets_dir. Non-image embeds and unresolvable refs are left untouched.

    Returns (rewritten_body, image_records). image_records carries one entry
    per successfully copied reference, deduplicated by hash.
    """
    seen_hashes: set[str] = set()
    records: list[dict] = []

    def process_ref(raw_ref: str) -> str | None:
        if not _is_image_ref(raw_ref):
            return None
        abs_path = _vault.resolve_image_ref(vault_path, note_abs_path, raw_ref)
        if abs_path is None:
            return None
        ext = abs_path.suffix.lower()
        sha1 = _vault.hash_file(abs_path)
        dest = assets_dir / f"{sha1}{ext}"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest)
        asset_url = f"assets/images/{sha1}{ext}"
        if sha1 not in seen_hashes:
            seen_hashes.add(sha1)
            record = {"original_ref": raw_ref, "asset_url": asset_url}
            if ocr_lookup and sha1 in ocr_lookup:
                row = ocr_lookup[sha1]
                record["width"] = row["image_width"]
                record["height"] = row["image_height"]
                record["ocr_text"] = (
                    row["ocr_text"] if not row["was_skipped"] else None
                )
            records.append(record)
        return asset_url

    def wikilink_sub(m: re.Match) -> str:
        replacement = process_ref(m.group(1))
        if replacement is None:
            return m.group(0)
        return f"![]({replacement})"

    def markdown_sub(m: re.Match) -> str:
        replacement = process_ref(m.group(2))
        if replacement is None:
            return m.group(0)
        return f"{m.group(1)}{replacement}{m.group(3)}"

    new_body = _IMG_WIKILINK_RE.sub(wikilink_sub, body)
    new_body = _IMG_MARKDOWN_RE.sub(markdown_sub, new_body)
    return new_body, records


def export_note_sidecars(
    conn: sqlite3.Connection,
    vault_path: str | Path,
    out_dir: str | Path,
    *,
    copy_assets: bool = True,
) -> int:
    """Write one JSON sidecar per live note to <out_dir>/notes/<note_id>.json.

    Returns count written. Prunes sidecars whose note_id is no longer live.
    Asset copies go to <out_dir>/assets/images/<sha1>.<ext>; orphan assets
    are left in place (content-addressed, safe to accumulate).
    """
    vault = Path(vault_path).expanduser().resolve()
    out = Path(out_dir).expanduser().resolve()
    notes_dir = out / "notes"
    assets_dir = out / "assets" / "images"
    notes_dir.mkdir(parents=True, exist_ok=True)

    ocr_lookup: dict[str, sqlite3.Row] = {
        row["image_hash"]: row
        for row in conn.execute("SELECT * FROM image_ocr").fetchall()
    }

    rows = conn.execute(
        """
        SELECT note_id, title, folder, tags, modified_date, source_url, file_path
        FROM notes
        WHERE wiki_status != 'deleted'
        """
    ).fetchall()

    live_ids: set[str] = set()
    count = 0
    for row in rows:
        file_path = row["file_path"]
        if not file_path:
            continue
        note_abs = Path(file_path).expanduser()
        if not note_abs.is_absolute():
            note_abs = (vault / file_path).resolve()
        if not note_abs.exists():
            continue
        text = note_abs.read_text(encoding="utf-8", errors="replace")
        _meta, body = _fm.split_frontmatter(text)
        if copy_assets:
            body, image_records = copy_note_assets(
                body, note_abs, vault, assets_dir, ocr_lookup=ocr_lookup
            )
        else:
            image_records = []
        sidecar = {
            "note_id": row["note_id"],
            "title": row["title"],
            "file_path": _vault_relative(note_abs, vault),
            "body_markdown": body,
            "images": image_records,
            "source_url": row["source_url"],
            "tags": json.loads(row["tags"] or "[]"),
            "modified_date": row["modified_date"],
        }
        (notes_dir / f"{row['note_id']}.json").write_text(
            json.dumps(sidecar, ensure_ascii=False, indent=2)
        )
        live_ids.add(row["note_id"])
        count += 1

    for existing in notes_dir.glob("*.json"):
        if existing.stem not in live_ids:
            existing.unlink()

    return count


def _vault_relative(abs_path: Path, vault: Path) -> str | None:
    try:
        return abs_path.resolve().relative_to(vault).as_posix()
    except ValueError:
        return None
