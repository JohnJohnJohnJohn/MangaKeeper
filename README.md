# MangaKeeper

A Python CLI tool to organize manga and comic collections by removing duplicates and standardizing formats.

## Features

- **Exact duplicate detection** via SHA256 — files with identical content are identified and deduplicated
- **Perceptual duplicate detection** via page hashing — different scans or encodes of the same title are detected using perceptual image hashing on sampled pages
- **Quality-based selection** — when duplicates are found, the best version (page count, resolution, file size) is kept automatically
- **Standardized WebP pages + folder naming** — page images are converted to WebP (default quality 95, full resolution), keeping their original filenames; comic folders are renamed to a cleaned standard convention

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

MangaKeeper processes your collection in six phases (episode combining runs by default after standardization):

1. **Scan** — Recursively scans the target directory for comic files (CBZ, CBR, ZIP, PDF, EPUB) and folders containing page images (JPG, PNG, WebP, etc.).

2. **Exact Dedup** — Computes SHA256 hashes for all files. Files with identical hashes are grouped, and all but one copy is marked for removal.

3. **Perceptual Dedup** — Samples pages from each archive and computes perceptual hashes. Files with similar page signatures (below the threshold) are grouped as duplicates of the same title. The highest-quality version is kept.

4. **Standardize** — Converts page images to **WebP** (original filenames preserved, default quality 95, full resolution). Folder names are cleaned to a standard convention: bracket variants normalized to ASCII `[]` / `()`, no double spaces, spaces around bracket groups, duplicate markers removed. Archives are extracted into a standardized folder. Converted page originals are moved to trash by default.

   Use `--page-quality` to adjust WebP quality (1–100). Use `--max-page-size-mb` to optionally cap per-page file size; when set, pages may be downscaled to fit.

5. **Combine episodes** — Detects contiguous episode folders (for example `manga_a_episode_1` + `manga_a_episode_2`) and merges them into a single folder such as `manga_a_episode_1to2`. Pages are prefixed by episode (`e01_001.webp`, `e02_001.webp`, …) to avoid filename collisions. Non-contiguous gaps (for example episodes 1 and 3 without 2) are skipped. Prompts per group; Enter accepts the default. Use `--skip-combine-episodes` to skip this phase.

6. **Suggest artists** *(optional)* — Learns visual style profiles from tagged folders like `[Artist A] Title` and suggests likely artists for untagged comics based on page similarity.

## Episode combining

When a series is split into per-episode folders, MangaKeeper can merge **contiguous** runs into one folder:

```bash
python3 -m manga_keeper.cli --path /path/to/manga --combine-episodes-only --dry-run
python3 -m manga_keeper.cli --path /path/to/manga --skip-combine-episodes
```

Supported naming patterns include `_episode_N`, `_ep_N`, `_ch_N`, trailing ` N`, `-N`, `#N`, and CJK forms like `第N话`. Already-merged names such as `_episode_1to2` are skipped.

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

- **Confirmation prompts** — Phases 2, 3, and 5 ask before removing or merging (Enter accepts the default). Phase 3 lets you pick which version to keep, with page counts shown for each copy. Phase 4 automatically renames folders and converts pages without prompting.
- **Trash instead of delete** — Removed duplicates, merged episode folders, converted page originals, and extracted archives are moved to `.manga_keeper_trash/` preserving their library-relative folder structure
- **Dry-run mode** — Preview all actions without making any changes to your files

## CLI Arguments

| Argument | Description | Default |
|---|---|---|
| `--path` | Path to the directory containing comic files | Required |
| `--dry-run` | Preview changes without modifying any files | `False` |
| `--keep-originals` | Keep original page files and archives after page conversion | `False` |
| `--page-quality` | WebP quality for phase 4 page conversion (1–100) | `95` |
| `--max-page-size-mb` | Optional per-page WebP size cap; `0` disables downscaling | `0` |
| `--workers` | Parallel workers for phase 4 page conversion | CPU core count |
| `--threshold` | Perceptual hash distance threshold for duplicate detection | `10` |
| `--log-file` | Path to write a detailed operation log | None |
| `--rebuild-cache` | Ignore and rebuild the local scan/hash index | `False` |
| `--suggest-artists` | Suggest artist tags for untagged comics after the normal pipeline | `False` |
| `--artists-only` | Scan and run artist suggestions only | `False` |
| `--standardize-only` | Scan and run folder/page standardization (phase 4) only | `False` |
| `--skip-combine-episodes` | Skip merging contiguous episode folders (phase 5) | `False` |
| `--combine-episodes-only` | Scan and merge contiguous episode folders only | `False` |
| `--artist-min-samples` | Minimum tagged works required to learn an artist profile | `3` |
| `--artist-threshold` | Visual similarity threshold for artist suggestions | `12` |
| `--apply-artist-tags` | Prompt to rename untagged folders when a match is suggested | `False` |

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
