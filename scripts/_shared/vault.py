"""Walk an Obsidian vault, parse notes, resolve image references.

Functions here are pure and stateless; they return structured dicts ready
to feed into catalog.upsert_note / catalog.upsert_ocr.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

try:  # pragma: no cover - test environments vendor _shared sibling-style
    from . import frontmatter as _fm
    from . import catalog as _catalog
except ImportError:  # pragma: no cover
    import frontmatter as _fm  # type: ignore
    import catalog as _catalog  # type: ignore

SKIP_DIRS = {".obsidian", "_resources", "_templates"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".gif", ".svg", ".bmp"}
OCR_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp"}

_IMG_WIKILINK_RE = re.compile(r"!\[\[([^\]]+?)\]\]")
_IMG_MARKDOWN_RE = re.compile(r"!\[[^\]]*?\]\(([^)]+?)\)")


def iter_notes(vault_root: str | Path) -> Iterator[Path]:
    """Yield absolute paths to every markdown note in the vault, skipping
    Obsidian internals, resource folders, and underscore-prefixed files."""
    root = Path(vault_root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".md") or name.startswith("_"):
                continue
            yield Path(dirpath) / name


def parse_note(vault_root: str | Path, note_path: str | Path) -> dict:
    """Read and parse a single note file. Returns a dict shaped for
    catalog.upsert_note (minus image-derived fields and note_id, which
    callers fill in after Step 2.2b)."""
    root = Path(vault_root).resolve()
    path = Path(note_path).resolve()
    rel = path.relative_to(root)
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _fm.split_frontmatter(text)

    tags_frontmatter = meta.get("tags") or []
    if isinstance(tags_frontmatter, str):
        tags_frontmatter = [tags_frontmatter]
    tags_inline = _fm.extract_inline_tags(body)
    tags = list(dict.fromkeys([*tags_frontmatter, *tags_inline]))

    title = meta.get("title") or _deslugify(path.stem)
    created = _as_iso(meta.get("created")) or _file_ctime(path)
    modified = (
        _as_iso(meta.get("updated"))
        or _as_iso(meta.get("modified"))
        or _file_mtime(path)
    )
    source_url = meta.get("source")

    folder = rel.parent.as_posix() if rel.parent.as_posix() != "." else "/"

    return {
        "note_id": _catalog.note_id_for_path(rel),
        "title": title,
        "folder": folder,
        "tags": tags,
        "created_date": created,
        "modified_date": modified,
        "word_count": len(body.split()),
        "char_count": len(body),
        "source_url": source_url,
        "file_path": str(path),
        "body": body,
    }


def find_image_references(body: str) -> list[str]:
    """Return raw image reference strings from a note body (both Obsidian
    wikilink and standard markdown forms). Duplicates preserved so callers
    can count 'image_count' accurately."""
    refs: list[str] = []
    for match in _IMG_WIKILINK_RE.finditer(body):
        refs.append(match.group(1).strip())
    for match in _IMG_MARKDOWN_RE.finditer(body):
        refs.append(match.group(1).strip())
    return [r for r in refs if _looks_like_image(r)]


def resolve_image_ref(
    vault_root: str | Path,
    note_path: str | Path,
    ref: str,
) -> Path | None:
    """Resolve an image reference to an absolute path, or None if not found.

    Wikilink references are searched inside any `_resources/` folder in the
    vault. Markdown references are resolved relative to the note's own
    directory, then against the vault root as a fallback.
    """
    root = Path(vault_root).resolve()
    note_dir = Path(note_path).resolve().parent
    cleaned = ref.split("|", 1)[0].split("#", 1)[0].strip()
    if not cleaned:
        return None

    if "/" in cleaned or "\\" in cleaned:
        for base in (note_dir, root):
            candidate = (base / cleaned).resolve()
            if candidate.exists():
                return candidate
        return None

    name = Path(cleaned).name
    for resources_dir in root.rglob("_resources"):
        if not resources_dir.is_dir():
            continue
        for hit in resources_dir.rglob(name):
            if hit.is_file():
                return hit
    # Fallback: search anywhere in the vault.
    for hit in root.rglob(name):
        if hit.is_file():
            return hit
    return None


def hash_file(path: str | Path) -> str:
    """SHA-1 of a file's binary content, suitable for image_ocr.image_hash."""
    hasher = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _deslugify(stem: str) -> str:
    return stem.replace("-", " ").replace("_", " ").strip()


def _file_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _file_ctime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_ctime, tz=timezone.utc).isoformat(timespec="seconds")


def _as_iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _looks_like_image(ref: str) -> bool:
    cleaned = ref.split("|", 1)[0].split("#", 1)[0].strip().lower()
    if not cleaned:
        return False
    # Treat bare filenames with no extension as non-images (Obsidian wikilinks
    # to other notes use the same `[[...]]` syntax without the leading `!`,
    # but defensive filtering helps for unusual cases).
    return any(cleaned.endswith(ext) for ext in IMAGE_EXTENSIONS)
