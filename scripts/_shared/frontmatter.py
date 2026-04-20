"""Parse and strip YAML frontmatter from Obsidian markdown notes.

Uses a small hand-rolled YAML subset rather than depending on PyYAML, since
Obsidian frontmatter is a narrow shape (scalars, ISO dates, simple string
lists). If a note has unusual frontmatter the parser leaves the offending
field as a raw string rather than erroring.

Public surface:
    split_frontmatter(text) -> (meta: dict, body: str)
    extract_inline_tags(body) -> list[str]
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_FRONTMATTER_OPEN_RE = re.compile(r"\A---\s*\n")
_FRONTMATTER_CLOSE_RE = re.compile(r"\n---\s*(?:\n|\Z)")
_INLINE_TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_][A-Za-z0-9_/\-]*)")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(:\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (metadata dict, body string). If no frontmatter, meta is {}."""
    if not _FRONTMATTER_OPEN_RE.match(text):
        return {}, text
    after_open = _FRONTMATTER_OPEN_RE.sub("", text, count=1)
    close = _FRONTMATTER_CLOSE_RE.search(after_open)
    if not close:
        return {}, text
    raw = after_open[: close.start()]
    body = after_open[close.end():]
    return _parse_yaml_subset(raw), body


def extract_inline_tags(body: str) -> list[str]:
    """Find Obsidian-style #tags inside the note body (outside code fences)."""
    stripped = _strip_code_blocks(body)
    return list(dict.fromkeys(_INLINE_TAG_RE.findall(stripped)))


def _strip_code_blocks(text: str) -> str:
    out: list[str] = []
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            continue
        out.append(line)
    return "\n".join(out)


def _parse_yaml_subset(raw: str) -> dict[str, Any]:
    """Handle the narrow Obsidian frontmatter cases: scalars, dates, inline or
    block string lists. Unknown constructs are preserved as raw strings."""
    lines = raw.splitlines()
    meta: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Block list: next lines indented and starting with '- '
        if value == "" and i + 1 < len(lines) and _is_block_list_item(lines[i + 1]):
            items: list[str] = []
            j = i + 1
            while j < len(lines) and _is_block_list_item(lines[j]):
                items.append(lines[j].lstrip()[2:].strip().strip("'\""))
                j += 1
            meta[key] = items
            i = j
            continue

        # Inline list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                meta[key] = []
            else:
                meta[key] = [p.strip().strip("'\"") for p in inner.split(",")]
            i += 1
            continue

        # Scalar
        meta[key] = _coerce_scalar(value)
        i += 1

    return meta


def _is_block_list_item(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("- ") and len(line) - len(stripped) >= 2


def _coerce_scalar(value: str) -> Any:
    s = value.strip().strip("'\"")
    if not s:
        return None
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    if s.lower() in ("null", "none", "~"):
        return None
    if _DATE_RE.match(s):
        return _try_parse_datetime(s)
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _try_parse_datetime(s: str) -> str | datetime | date:
    try:
        if len(s) == 10:
            return date.fromisoformat(s).isoformat()
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return s
