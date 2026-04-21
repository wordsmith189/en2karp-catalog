"""SQLite schema and read/write helpers for the en2karp Note Catalog.

Library-style: functions take a connection and data, return values. No I/O
side effects beyond the database and the output JSON files written by the
export helpers. CLI wrappers handle logging and user messages.

Schema mirrors pipeline-01-note-catalog.md in the EN-to-Karp design docs:
three tables — notes, tags, image_ocr.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
    note_id         TEXT PRIMARY KEY,
    title           TEXT,
    folder          TEXT,
    tags            TEXT,           -- JSON array of strings
    created_date    TEXT,           -- ISO 8601
    modified_date   TEXT,           -- ISO 8601
    word_count      INTEGER,
    char_count      INTEGER,
    source_url      TEXT,
    file_path       TEXT,
    image_count     INTEGER DEFAULT 0,
    has_ocr         INTEGER DEFAULT 0,
    wiki_status     TEXT DEFAULT 'raw',
    last_processed  TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    tag             TEXT PRIMARY KEY,
    note_count      INTEGER
);

CREATE TABLE IF NOT EXISTS image_ocr (
    image_hash      TEXT PRIMARY KEY,
    file_path       TEXT,
    ocr_text        TEXT,
    ocr_engine      TEXT,
    image_width     INTEGER,
    image_height    INTEGER,
    was_skipped     INTEGER DEFAULT 0,
    skip_reason     TEXT,
    processed_at    TEXT,
    note_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder);
CREATE INDEX IF NOT EXISTS idx_notes_modified ON notes(modified_date);
CREATE INDEX IF NOT EXISTS idx_notes_wiki_status ON notes(wiki_status);
"""

VALID_WIKI_STATUSES = {"raw", "extracted", "synthesized", "deleted"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the catalog database, applying the schema on first touch."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def note_id_for_path(vault_relative_path: str | Path) -> str:
    """Stable note id: SHA-1 of the vault-relative path as a POSIX string."""
    posix = Path(vault_relative_path).as_posix()
    return hashlib.sha1(posix.encode("utf-8")).hexdigest()


def upsert_note(conn: sqlite3.Connection, note: dict[str, Any]) -> None:
    """Insert or replace a single note row. Preserves wiki_status across
    updates unless the body has changed by more than 10% (char_count)."""
    existing = conn.execute(
        "SELECT wiki_status, char_count FROM notes WHERE note_id = ?",
        (note["note_id"],),
    ).fetchone()

    wiki_status = note.get("wiki_status", "raw")
    if existing is not None:
        old_status = existing["wiki_status"]
        old_chars = existing["char_count"] or 0
        new_chars = note.get("char_count", 0)
        if old_status in ("extracted", "synthesized"):
            changed_ratio = (
                abs(new_chars - old_chars) / old_chars if old_chars else 1.0
            )
            wiki_status = "raw" if changed_ratio > 0.10 else old_status

    tags_json = json.dumps(note.get("tags") or [])

    conn.execute(
        """
        INSERT OR REPLACE INTO notes (
            note_id, title, folder, tags, created_date, modified_date,
            word_count, char_count, source_url, file_path,
            image_count, has_ocr, wiki_status, last_processed
        ) VALUES (
            :note_id, :title, :folder, :tags, :created_date, :modified_date,
            :word_count, :char_count, :source_url, :file_path,
            :image_count, :has_ocr, :wiki_status, :last_processed
        )
        """,
        {
            "note_id": note["note_id"],
            "title": note.get("title"),
            "folder": note.get("folder"),
            "tags": tags_json,
            "created_date": note.get("created_date"),
            "modified_date": note.get("modified_date"),
            "word_count": note.get("word_count", 0),
            "char_count": note.get("char_count", 0),
            "source_url": note.get("source_url"),
            "file_path": note.get("file_path"),
            "image_count": note.get("image_count", 0),
            "has_ocr": 1 if note.get("has_ocr") else 0,
            "wiki_status": wiki_status,
            "last_processed": note.get("last_processed") or _now_iso(),
        },
    )


def mark_deleted(conn: sqlite3.Connection, note_ids: Iterable[str]) -> int:
    """Mark the given note ids as deleted. Returns count marked."""
    ids = list(note_ids)
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE notes SET wiki_status = 'deleted', last_processed = ? "
        f"WHERE note_id IN ({placeholders})",
        [_now_iso(), *ids],
    )
    return cur.rowcount


def rebuild_tags_table(conn: sqlite3.Connection) -> None:
    """Aggregate tags from live notes into the tags table."""
    rows = conn.execute(
        "SELECT tags FROM notes WHERE wiki_status != 'deleted'"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        try:
            for tag in json.loads(row["tags"] or "[]"):
                counts[tag] = counts.get(tag, 0) + 1
        except json.JSONDecodeError:
            continue
    conn.execute("DELETE FROM tags")
    conn.executemany(
        "INSERT INTO tags (tag, note_count) VALUES (?, ?)",
        counts.items(),
    )


def set_wiki_status(
    conn: sqlite3.Connection,
    note_ids: Iterable[str],
    status: str,
) -> int:
    if status not in VALID_WIKI_STATUSES:
        raise ValueError(f"invalid wiki_status: {status!r}")
    ids = list(note_ids)
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE notes SET wiki_status = ?, last_processed = ? "
        f"WHERE note_id IN ({placeholders})",
        [status, _now_iso(), *ids],
    )
    return cur.rowcount


def query_notes(
    conn: sqlite3.Connection,
    *,
    tag_filter: list[str] | None = None,
    folder_filter: str | None = None,
    exclude_deleted: bool = True,
) -> list[sqlite3.Row]:
    """Fetch notes matching all given tag strings and optional folder prefix."""
    clauses: list[str] = []
    params: list[Any] = []
    if exclude_deleted:
        clauses.append("wiki_status != 'deleted'")
    for tag in tag_filter or []:
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if folder_filter is not None:
        clauses.append("folder = ? OR folder LIKE ?")
        params.extend([folder_filter, f"{folder_filter}/%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM notes {where} ORDER BY modified_date DESC"
    return conn.execute(sql, params).fetchall()


def get_note(conn: sqlite3.Connection, note_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM notes WHERE note_id = ?", (note_id,)
    ).fetchone()


def get_notes_by_ids(
    conn: sqlite3.Connection, note_ids: list[str], *, exclude_deleted: bool = True
) -> list[sqlite3.Row]:
    """Fetch notes by id in one query, preserving the input order.

    Missing ids are silently dropped; deleted ids are dropped unless
    `exclude_deleted=False`. SQLite's parameter limit (999 by default)
    is respected by chunking.
    """
    if not note_ids:
        return []
    found: dict[str, sqlite3.Row] = {}
    chunk_size = 900
    for start in range(0, len(note_ids), chunk_size):
        chunk = note_ids[start : start + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        clauses = [f"note_id IN ({placeholders})"]
        if exclude_deleted:
            clauses.append("wiki_status != 'deleted'")
        sql = f"SELECT * FROM notes WHERE {' AND '.join(clauses)}"
        for row in conn.execute(sql, chunk):
            found[row["note_id"]] = row
    return [found[nid] for nid in note_ids if nid in found]


def all_note_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT note_id FROM notes WHERE wiki_status != 'deleted'"
    ).fetchall()
    return {row["note_id"] for row in rows}


def upsert_ocr(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO image_ocr (
            image_hash, file_path, ocr_text, ocr_engine,
            image_width, image_height, was_skipped, skip_reason,
            processed_at, note_id
        ) VALUES (
            :image_hash, :file_path, :ocr_text, :ocr_engine,
            :image_width, :image_height, :was_skipped, :skip_reason,
            :processed_at, :note_id
        )
        """,
        {
            "image_hash": record["image_hash"],
            "file_path": record.get("file_path"),
            "ocr_text": record.get("ocr_text"),
            "ocr_engine": record.get("ocr_engine"),
            "image_width": record.get("image_width"),
            "image_height": record.get("image_height"),
            "was_skipped": 1 if record.get("was_skipped") else 0,
            "skip_reason": record.get("skip_reason"),
            "processed_at": record.get("processed_at") or _now_iso(),
            "note_id": record.get("note_id"),
        },
    )


def get_ocr_by_hash(conn: sqlite3.Connection, image_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM image_ocr WHERE image_hash = ?", (image_hash,)
    ).fetchone()


def export_catalog_json(conn: sqlite3.Connection, output_path: str | Path) -> int:
    """Write Pipeline 03's `catalog.json`. Returns the row count written."""
    rows = conn.execute(
        """
        SELECT note_id, title, folder, tags, created_date, modified_date,
               word_count, source_url, file_path, image_count, has_ocr, wiki_status
        FROM notes
        WHERE wiki_status != 'deleted'
        ORDER BY modified_date DESC
        """
    ).fetchall()
    payload = []
    for row in rows:
        payload.append(
            {
                "note_id": row["note_id"],
                "title": row["title"],
                "folder": row["folder"],
                "tags": json.loads(row["tags"] or "[]"),
                "created_date": row["created_date"],
                "modified_date": row["modified_date"],
                "word_count": row["word_count"],
                "source_url": row["source_url"],
                "file_path": row["file_path"],
                "image_count": row["image_count"],
                "has_ocr": bool(row["has_ocr"]),
                "wiki_status": row["wiki_status"],
            }
        )
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return len(payload)


def export_metadata_json(conn: sqlite3.Connection, output_path: str | Path) -> dict:
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM notes WHERE wiki_status != 'deleted'"
    ).fetchone()["c"]
    folders = [
        row["folder"]
        for row in conn.execute(
            "SELECT DISTINCT folder FROM notes "
            "WHERE wiki_status != 'deleted' AND folder IS NOT NULL "
            "ORDER BY folder"
        ).fetchall()
    ]
    tag_rows = conn.execute(
        "SELECT tag, note_count FROM tags ORDER BY note_count DESC, tag ASC"
    ).fetchall()
    meta = {
        "generated_at": _now_iso(),
        "total_notes": total,
        "folders": folders,
        "tags": [{"tag": r["tag"], "count": r["note_count"]} for r in tag_rows],
    }
    Path(output_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta
