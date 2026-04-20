<p align="center"><img src="banner.png" width="620" height="413" alt="en2karp"></p>

# en2karp-catalog

**Pipeline 01** of the **en2karp** workflow — a three-part setup for
LLM-maintained personal wikis, inspired by Andrej Karpathy's
[gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
It came out of exploring what Claude Code can do to support novel,
interesting modes of personal information management — while also
escaping the fangs of Bending Spoons / Evernote.

This skill is the *catalog* layer: it migrates an Evernote account into
an Obsidian vault, builds a SQLite index over every note (frontmatter, inline
tags, image references, OCR text), and exports the catalog as JSON for
downstream consumers.

It is installed as a Claude Code **skill** — it lives in `~/.claude/skills/`
and is invoked by talking to Claude Code rather than by running a CLI directly.

## Where it fits

```
Evernote account
    │  evernote-backup sync → ENEX
    ▼
Pipeline 01 (this repo) ─→  Obsidian vault + catalog.db (SQLite)
    │
    ├── catalog.json + metadata.json ─→ en2karp-webapp-template (Pipeline 03)
    └── used by ──────────────────────→ en2karp-wiki             (Pipeline 02)
```

## Background

Karpathy's gist sketches a three-layer model:

1. **Raw sources** — immutable input documents the LLM reads but never edits.
2. **The wiki** — LLM-owned markdown (summaries, entity pages, overviews).
3. **The schema** — a config doc specifying wiki structure.

en2karp specializes this model for Evernote: the **catalog** is the
structured index over the raw sources — not the wiki itself, but the
substrate a wiki builder (Pipeline 02) or a browsing UI (Pipeline 03) can
query. Splitting "index the corpus" from "synthesize the wiki" keeps each
stage cheap, debuggable, and independently rerunnable.

## What's in the box

| Script | Purpose |
|---|---|
| `scripts/phase1_migrate.py` | One-time Evernote → ENEX → Obsidian migration via `evernote-backup` + `yarle` |
| `scripts/phase2_index.py` | Incremental reindex: frontmatter, tags, image refs, OCR |
| `scripts/export_json.py` | Emits `catalog.json` + `metadata.json` for the Pipeline 03 webapp |
| `scripts/scaffold_webapp.py` | Clones `en2karp-webapp-template` + seeds it with the current catalog |
| `assets/en2karp-reindex.plist.template` | Optional `launchd` job for nightly reindexing on macOS |

Implementation notes, flags, and troubleshooting live in
[`SKILL.md`](SKILL.md).

## Install (as a Claude Code skill)

```sh
git clone https://github.com/wordsmith189/en2karp-catalog.git ~/repos/en2karp-catalog
ln -s ~/repos/en2karp-catalog ~/.claude/skills/en2karp-catalog
```

Then, inside a Claude Code session, the skill is auto-discovered; trigger it
with phrases like *"migrate my Evernote"*, *"reindex my vault"*, or
*"export catalog to webapp"*.

## Prerequisites

| Tool | Needed for | Install |
|---|---|---|
| Python 3.10+ | all scripts | ships with macOS / Homebrew |
| Pillow | image dimensions | `pip install pillow` |
| `evernote-backup` | Phase 1 only | `pipx install evernote-backup` |
| `yarle` | Phase 1 only | `npm install -g yarle-evernote-to-md` |
| `tesseract` | optional OCR fallback | `brew install tesseract` |

Apple Vision OCR (the macOS default) needs no install.

## Related

- [en2karp-wiki](https://github.com/wordsmith189/en2karp-wiki) — Pipeline 02, the LLM wiki builder that reads this catalog
- [en2karp-webapp-template](https://github.com/wordsmith189/en2karp-webapp-template) — Pipeline 03, a PWA for browsing the catalog

## License

MIT.
