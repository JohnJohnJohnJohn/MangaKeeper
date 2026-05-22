# MangaKeeper

A Python CLI tool to organize manga and comic collections by removing duplicates and standardizing formats.

## Features

- **Exact duplicate detection** via SHA256 — files with identical content are identified and deduplicated
- **Perceptual duplicate detection** via page hashing — different scans or encodes of the same title are detected using perceptual image hashing on sampled pages
- **Quality-based selection** — when duplicates are found, the best version (page count, resolution, file size) is kept automatically
- **PNG pages + folder naming** — page images are converted to lossless PNG (keeping their original filenames), and comic folders are renamed to a cleaned standard convention

## Prerequisites

- Python 3.9+
- Optional: `unrar` on PATH for CBR extraction (via the `rarfile` package)

## Installation

```bash
git clone https://github.com/JohnJohnJohnJohn/MangaKeeper.git
cd MangaKeeper

pip install -r requirements.txt
```

## Usage

### Dry-run mode (preview changes without making them)

```bash
python3 -m manga_keeper.cli --path /path/to/manga --dry-run
```

### Normal run

```bash
python3 -m manga_keeper.cli --path /path/to/manga
```

### With options

```bash
python3 -m manga_keeper.cli --path /path/to/manga --keep-originals --threshold 12
```

## How It Works

MangaKeeper processes your collection in four phases:

1. **Scan** — Recursively scans the target directory for comic files (CBZ, CBR, ZIP, PDF, EPUB) and folders containing page images (JPG, PNG, WebP, etc.).

2. **Exact Dedup** — Computes SHA256 hashes for all files. Files with identical hashes are grouped, and all but one copy is marked for removal.

3. **Perceptual Dedup** — Samples pages from each archive and computes perceptual hashes. Files with similar page signatures (below the threshold) are grouped as duplicates of the same title. The highest-quality version is kept.

4. **Standardize** — Converts page images to lossless PNG (original filenames preserved) and renames comic folders to a cleaned standard convention: bracket variants normalized to ASCII `[]` / `()`, no double spaces, spaces around bracket groups, duplicate markers removed. Archives are extracted into a standardized folder.

5. **Suggest artists** *(optional)* — Learns visual style profiles from tagged folders like `[Artist A] Title` and suggests likely artists for untagged comics based on page similarity.

## Artist suggestions

If many folders follow a tag pattern such as `[artist_a] manga_b`, MangaKeeper can compare untagged comics against learned artist style profiles built from your tagged library.

```bash
python3 -m manga_keeper.cli --path /path/to/manga --artists-only
python3 -m manga_keeper.cli --path /path/to/manga --suggest-artists --apply-artist-tags
```

Requirements and caveats:

- An artist needs at least `--artist-min-samples` tagged works (default: 3) before MangaKeeper will suggest them
- Suggestions are visual-style matches, not proof — scan groups, magazines, AI prompts, and collaborators can create false positives
- Use suggestions as a starting point; review before applying tags with `--apply-artist-tags`

## Index cache

MangaKeeper stores a local index at `<library>/.manga_keeper/index.json` so repeat runs on the same parent directory are faster. The index remembers:

- Known comic folders/archives from previous scans
- Exact content hashes
- Perceptual page signatures and metadata

Unchanged comics skip re-listing their page files during scan and skip re-hashing during duplicate detection. Use `--rebuild-cache` to ignore the index and refresh everything.

## Safety Features

- **Confirmation prompts** — Phases 2 and 3 ask before removing duplicates (Enter accepts the default). Phase 3 lets you pick which version to keep, with page counts shown for each copy. Phase 4 automatically renames folders and converts pages to PNG without prompting.
- **Trash instead of delete** — Removed files are moved to `.manga_keeper_trash/` rather than permanently deleted
- **Dry-run mode** — Preview all actions without making any changes to your files

## CLI Arguments

| Argument | Description | Default |
|---|---|---|
| `--path` | Path to the directory containing comic files | Required |
| `--dry-run` | Preview changes without modifying any files | `False` |
| `--keep-originals` | Keep original files after PNG normalization | `False` |
| `--threshold` | Perceptual hash distance threshold for duplicate detection | `10` |
| `--log-file` | Path to write a detailed operation log | None |
| `--rebuild-cache` | Ignore and rebuild the local scan/hash index | `False` |
| `--suggest-artists` | Suggest artist tags for untagged comics after the normal pipeline | `False` |
| `--artists-only` | Scan and run artist suggestions only | `False` |
| `--standardize-only` | Scan and run folder/PNG standardization (phase 4) only | `False` |
| `--artist-min-samples` | Minimum tagged works required to learn an artist profile | `3` |
| `--artist-threshold` | Visual similarity threshold for artist suggestions | `12` |
| `--apply-artist-tags` | Prompt to rename untagged folders when a match is suggested | `False` |

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
