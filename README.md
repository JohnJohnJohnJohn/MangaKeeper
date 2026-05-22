# MangaKeeper

A Python CLI tool to organize manga and comic collections by removing duplicates and standardizing formats.

## Features

- **Exact duplicate detection** via SHA256 — files with identical content are identified and deduplicated
- **Perceptual duplicate detection** via page hashing — different scans or encodes of the same title are detected using perceptual image hashing on sampled pages
- **Quality-based selection** — when duplicates are found, the best version (page count, resolution, file size) is kept automatically
- **CBZ conversion** — PDF, EPUB, CBR, and ZIP archives are converted to CBZ (ordered ZIP of images) for a consistent library layout

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

1. **Scan** — Recursively scans the target directory for comic files (CBZ, CBR, ZIP, PDF, EPUB).

2. **Exact Dedup** — Computes SHA256 hashes for all files. Files with identical hashes are grouped, and all but one copy is marked for removal.

3. **Perceptual Dedup** — Samples pages from each archive and computes perceptual hashes. Files with similar page signatures (below the threshold) are grouped as duplicates of the same title. The highest-quality version is kept.

4. **Convert** — Converts non-CBZ files into CBZ archives with naturally sorted page images.

## Safety Features

- **Confirmation prompts** — Before deleting or converting files, you are asked to confirm
- **Trash instead of delete** — Removed files are moved to `.manga_keeper_trash/` rather than permanently deleted
- **Dry-run mode** — Preview all actions without making any changes to your files

## CLI Arguments

| Argument | Description | Default |
|---|---|---|
| `--path` | Path to the directory containing comic files | Required |
| `--dry-run` | Preview changes without modifying any files | `False` |
| `--keep-originals` | Keep original files after CBZ conversion | `False` |
| `--threshold` | Perceptual hash distance threshold for duplicate detection | `10` |
| `--log-file` | Path to write a detailed operation log | None |

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
