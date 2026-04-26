---
name: en2karp-catalog
description: >
  Build and maintain a structured catalog of an Obsidian vault migrated from
  Evernote. Use when the user says "migrate my Evernote notes", "reindex my
  notes", "build the note catalog", "export catalog to webapp", or
  "scaffold the notes webapp". Handles the one-time Evernote → ENEX → Obsidian
  migration, ongoing incremental indexing into a SQLite catalog, and export of
  catalog.json + metadata.json for the companion webapp.
---

# en2karp — Note Catalog

Pipeline 01 of the Evernote-to-Karpathy workflow. Builds a local SQLite
catalog from an Obsidian vault, optionally running OCR on embedded images,
and exports the catalog as static JSON for the Pipeline 03 webapp.

## Commands

Ask the user which operation they want, then run the matching script from
`scripts/`. All scripts read defaults from `~/.config/en2karp/config.json`
if arguments are omitted. Each can be run standalone with `python3`.

| Request | Script |
|---|---|
| "migrate my Evernote", one-time import | `phase1_migrate.py` |
| "reindex my vault", "refresh the catalog" | `phase2_index.py` |
| "export catalog to webapp" | `export_json.py` |
| "scaffold the notes webapp" (first time) | `scaffold_webapp.py` |

## Prerequisites

| Tool | Check | Install |
|---|---|---|
| Python 3.10+ | `python3 --version` | comes with macOS / Homebrew |
| Pillow (image dimensions for OCR filter) | `python3 -c "import PIL"` | `pip install Pillow --break-system-packages` |
| pyobjc-framework-Vision (Apple Vision OCR) | `python3 -c "import Vision"` | `pip install pyobjc-framework-Vision --break-system-packages` |
| evernote-backup (Phase 1 only) | `evernote-backup --version` | `pipx install evernote-backup` |
| yarle (Phase 1 only) | `yarle --version` | `npm install -g yarle-evernote-to-md` |
| tesseract (optional OCR fallback) | `tesseract --version` | `brew install tesseract` |

Apple Vision OCR (the macOS default) is called via `pyobjc-framework-Vision`
(direct Python bindings). The older `osascript` approach fails with "Access not
allowed" when run from Terminal — do not use it.

## Config

On first run, if `~/.config/en2karp/config.json` is missing, ask the user for:

- **vault_path** — absolute path to their Obsidian vault root
- **catalog_path** — where `catalog.db` lives (default:
  `~/Library/Application Support/en2karp/catalog.db` on macOS)
- **webapp_path** — target for `catalog.json` / `metadata.json` (optional;
  auto-filled by `scaffold-webapp`)
- **ocr_engine** — `auto`, `apple_vision`, `tesseract`, or `none`

Write the file with `_shared.paths.save_config()` or hand-edit; the schema
is documented in `_shared/paths.py`.

## Phase 1 — One-time migration (`phase1_migrate.py`)

```
python3 scripts/phase1_migrate.py \
    --backup-dir ~/en2karp/backup \
    --enex-dir   ~/en2karp/enex   \
    --vault      ~/Obsidian/Main
```

Steps:

1. `evernote-backup init-db` (interactive device-auth login, first time only).
2. `evernote-backup sync` — resumable pull of all notebooks into SQLite.
3. `evernote-backup export` — dump ENEX files (one per notebook, trash included).
4. **yarle** — convert `.enex` files to Obsidian markdown. yarle has a known
   bug: `--configFile` and CLI `--enexSources` flags do not override the
   bundled config. Use this three-step workaround:

   **Step 4a** — Copy the sample template to `~/Desktop/` (required on startup):
   ```bash
   cp /opt/homebrew/lib/node_modules/yarle-evernote-to-md/sampleTemplate.tmpl ~/Desktop/
   ```

   **Step 4b** — Edit the bundled config at
   `/opt/homebrew/lib/node_modules/yarle-evernote-to-md/config.json` and
   replace its contents with (substituting `{username}`):
   ```json
   {
       "enexSources": ["/Users/{username}/Desktop/enex-archive/"],
       "outputDir": "/Users/{username}/Desktop/obsidian-vault/",
       "keepOriginalHierarchy": true,
       "putFrontMatterAtTop": true,
       "keepOriginalHtml": false,
       "isMetadataNeeded": false,
       "plainTextNotesOnly": false,
       "skipWebClips": false,
       "skipTags": false,
       "useHashTags": true,
       "outputFormat": "ObsidianMD",
       "taskOutputFormat": "ObsidianMD",
       "imageSizeFormat": "ObsidianMD",
       "keepImageSize": true,
       "resourcesDir": "_resources",
       "replacementChar": "_",
       "nestedTags": {
           "separatorInEN": "_",
           "replaceSeparatorWith": "/",
           "replaceSpaceWith": "-"
       },
       "dateFormat": "YYYY-MM-DD"
   }
   ```
   Key settings that differ from yarle defaults: `skipWebClips: false` (default
   is true — would skip all web clips), `resourcesDir: "_resources"` (default
   is empty — attachments go missing), `nestedTags.replaceSeparatorWith: "/"`
   (Obsidian nested tag style), `putFrontMatterAtTop: true` (required for
   `phase2_index.py` to parse notes).

   **Step 4c** — Run from `~/Desktop`, passing any real notebook subdirectory
   as `--enexSources` to unlock processing of the whole archive:
   ```bash
   cd ~/Desktop
   yarle --enexSources /Users/{username}/Desktop/enex-archive/{any-notebook-name}/
   ```
   The specific notebook path does not limit output — yarle processes all
   notebooks defined in the config.

   > ⚠️ **yarle wraps output in a `notes/` subdirectory.** Your notes live at
   > `obsidian-vault/notes/{Notebook}/`, not `obsidian-vault/{Notebook}/`.
   > All downstream steps — the config file, the indexer, Obsidian itself — must
   > point to `obsidian-vault/notes/` as the vault root, not `obsidian-vault/`.

5. `phase2_index` — build the initial `catalog.db` (see Phase 2 below).

Warn the user that step 1 opens a browser for Evernote login and the full
sync can take 10+ minutes on a large account.

## Phase 2 — Incremental reindex (`phase2_index.py`)

Before the first run, create the config file if it does not already exist:

```bash
mkdir -p ~/.config/en2karp
cat > ~/.config/en2karp/config.json << 'EOF'
{
  "vault_path": "~/Desktop/obsidian-vault/notes",
  "catalog_path": "~/Library/Application Support/en2karp/catalog.db",
  "webapp_path": "~/repos/en2karp-webapp-template",
  "ocr_engine": "apple_vision"
}
EOF
```

The indexer reads this config on startup. Without it the script will error or
use wrong default paths. `catalog_path` is set to `~/Library/Application
Support/` deliberately — this keeps the database out of iCloud sync. Adjust
`webapp_path` to the actual Pipeline 03 repo location.

> **Do not run `--ocr-engine none` as a preliminary pass** if you intend to
> run OCR afterward. Doing so writes stub rows to the `image_ocr` cache that
> block real OCR on all subsequent runs. Run with `--ocr-engine apple_vision`
> directly. If you need a fast structural index first, omit `--sweep-resources`
> on the first pass rather than disabling OCR.

```
python3 scripts/phase2_index.py [--vault PATH] [--catalog PATH] \
    [--ocr-engine auto|apple_vision|tesseract|none] \
    [--sweep-resources]
```

Walks the vault, parses YAML frontmatter + inline `#tags`, collects image
references (both `![[wikilink]]` and `![alt](path)` forms), resolves them
against `_resources/` folders, and upserts each note row.

Notes present in the DB but missing from the vault are marked `wiki_status =
'deleted'`. Notes whose body changes by > 10 % (char_count) lose their
`extracted` / `synthesized` status and revert to `raw`.

OCR is content-keyed by SHA-1 of the image file; already-seen hashes are
skipped. Images under 100×100 px are logged with `skip_reason='too_small'`
and not OCR'd; OCR results under 10 chars are treated as empty.
`skip_reason` values are normalized to the enum `too_small` |
`unsupported_type` | `ocr_failed` | `ocr_disabled` | NULL.

`--sweep-resources` additionally walks every `_resources/` tree and OCRs
any supported image whose hash is not already cached. `phase1_migrate.py`
passes this flag automatically on the initial run so orphan images (never
referenced in a note) still populate `image_ocr`. Safe to re-run nightly.

## Export (`export_json.py`)

```
python3 scripts/export_json.py [--catalog PATH] [--out DIR]
```

Writes `catalog.json` (one entry per live note) and `metadata.json`
(`generated_at`, `total_notes`, `folders[]`, `tags[{tag,count}]`) into
`--out`. Meant to run whenever the vault changes and the webapp needs a
refresh.

## Scaffold webapp (`scaffold_webapp.py`)

```
python3 scripts/scaffold_webapp.py --dest ~/repos/my-notes-webapp
```

Clones the public `en2karp-webapp-template` repo (no `.git/` kept), saves
`webapp_path` to the user's config, then runs `export_json.py` into the
target directory. After this, the user can `cd ~/repos/my-notes-webapp &&
python3 -m http.server 8000` to browse.

## Scheduling Phase 2

Out of scope for the skill itself. `assets/en2karp-reindex.plist.template`
is a ready-to-edit `launchd` job for macOS users who want a nightly refresh;
installation is the user's choice.

## Troubleshooting

- **"no vault path set"** — config is missing or `vault_path` is null. Ask
  the user for the path; write it via `_shared.paths.save_config()`.
- **"tesseract not installed"** — OCR engine fell through to Tesseract but
  the binary is absent. Either `brew install tesseract` or set
  `ocr_engine = "apple_vision"` (macOS) / `"none"` (skip OCR entirely).
- **yarle emits HTML in markdown** — pass `--keepOriginalHtml false` (the
  migrate script already does this). If it persists, update yarle.
- **image_count looks wrong** — check the note's raw markdown; wikilinks
  without an image extension (`![[topic]]` with no `.png`) are intentionally
  filtered out in `_shared.vault.find_image_references`.

## Layout

```
en2karp-catalog/
├── SKILL.md
├── scripts/
│   ├── phase1_migrate.py
│   ├── phase2_index.py
│   ├── export_json.py
│   ├── scaffold_webapp.py
│   └── _shared/                 # vendored from _en2karp-shared/shared/
└── assets/
    └── en2karp-reindex.plist.template
```
