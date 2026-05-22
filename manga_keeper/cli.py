"""Command line entry point for manga_keeper."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set

from manga_keeper.artist import (
    build_artist_profiles,
    collect_comic_signatures,
    extract_artist_tag,
    is_untagged_comic,
    proposed_tagged_name,
    suggest_artists_for_untagged,
)
from manga_keeper.index import ComicIndex, index_file_for
from manga_keeper.scanner import scan_directory
from manga_keeper.hasher import find_exact_duplicates, select_file_to_keep
from manga_keeper.perceptual import find_perceptual_duplicates
from manga_keeper.resolver import get_quality_score
from manga_keeper.converter import (
    default_worker_count,
    needs_conversion,
    standardize_comic,
    standardize_comic_with_cleanup,
)
from manga_keeper.episodes import (
    EpisodeMergeGroup,
    find_episode_merge_groups,
    merge_episode_group,
)
from manga_keeper.utils import (
    setup_logging,
    move_to_trash,
    format_size,
    folder_content_size,
    get_comic_metadata,
    is_image_folder,
    list_folder_images,
)

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
}


def _color(text: str, *styles: str) -> str:
    if not sys.stdout.isatty():
        return text
    prefix = "".join(_ANSI[s] for s in styles if s in _ANSI)
    if not prefix:
        return text
    return f"{prefix}{text}{_ANSI['reset']}"


def _header(title: str) -> None:
    bar = "=" * 3
    print()
    print(_color(f"{bar} {title} {bar}", "bold", "cyan"))


def _ok(text: str) -> None:
    print(_color(text, "green"))


def _warn(text: str) -> None:
    print(_color(text, "yellow"))


def _err(text: str) -> None:
    print(_color(text, "red"))


def _confirm(prompt: str, *, default: bool = True) -> bool:
    try:
        answer = input(f"{prompt} ").strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    if answer in ("n", "no"):
        return False
    return answer in ("y", "yes")


def _comic_page_count(path: Path) -> int:
    if is_image_folder(path):
        return len(list_folder_images(path))
    meta = get_comic_metadata(path) or {}
    return int(meta.get("page_count") or 0)


def _format_comic_label(path: Path, *, recommended: bool = False) -> str:
    pages = _comic_page_count(path)
    size = format_size(_safe_size(path))
    suffix = "  (recommended)" if recommended else ""
    if path.is_dir():
        detail = f"{pages} pages, {size}"
    else:
        detail = f"{pages} pages, {size} file"
    return f"{path} ({detail}){suffix}"


def _rank_comic_group(group: List[Path]) -> List[Path]:
    scored = [(get_quality_score(path), path) for path in group]
    scored.sort(key=lambda item: (-item[0][0], -item[0][1], -item[0][2], str(item[1]).lower()))
    return [path for _, path in scored]


def _prompt_keep_choice(group: List[Path]) -> Optional[Path]:
    ordered = _rank_comic_group(group)
    if not ordered:
        return None

    print("  Choose which version to keep:")
    for number, path in enumerate(ordered, start=1):
        print(f"    {number}. {_format_comic_label(path, recommended=(number == 1))}")

    default_choice = 1
    while True:
        try:
            raw = input(
                _color(f"  Enter number to keep [{default_choice}]: ", "yellow")
            ).strip()
        except EOFError:
            return ordered[default_choice - 1]

        if not raw:
            return ordered[default_choice - 1]

        try:
            choice = int(raw)
        except ValueError:
            _warn("  Invalid choice; enter a number from the list.")
            continue

        if 1 <= choice <= len(ordered):
            return ordered[choice - 1]

        _warn(f"  Invalid choice; enter a number between 1 and {len(ordered)}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manga-keeper",
        description=(
            "Scan a directory of manga/comics, deduplicate them (exact + perceptual), "
            "and normalize pages to size-budget PNG."
        ),
    )
    parser.add_argument(
        "--path",
        required=True,
        help="Root directory to scan for comics.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without deleting or converting anything.",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=10,
        help="Perceptual hash similarity threshold (lower = stricter). Default: 10.",
    )
    parser.add_argument(
        "--keep-originals",
        action="store_true",
        help="Keep the original files after PNG normalization (do not move to trash).",
    )
    parser.add_argument(
        "--max-page-size-mb",
        type=float,
        default=2.0,
        metavar="MB",
        help=(
            "Maximum output PNG size per page in megabytes during phase 4. "
            "The converter uses the stricter of this limit and 2x the source page size. "
            "Default: 2."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help=(
            "Parallel workers for phase 4 page conversion. "
            "Default: number of CPU cores."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to write a detailed operation log.",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Ignore and rebuild the local scan/hash index.",
    )
    parser.add_argument(
        "--suggest-artists",
        action="store_true",
        help="Suggest artist tags for untagged comics using tagged library styles.",
    )
    parser.add_argument(
        "--artists-only",
        action="store_true",
        help="Scan the library and run artist suggestions only.",
    )
    parser.add_argument(
        "--standardize-only",
        action="store_true",
        help="Scan the library and run folder/PNG standardization (phase 4) only.",
    )
    parser.add_argument(
        "--skip-combine-episodes",
        action="store_true",
        help="Skip merging contiguous episode folders (phase 5) during the normal pipeline.",
    )
    parser.add_argument(
        "--combine-episodes-only",
        action="store_true",
        help="Scan the library and merge contiguous episode folders only.",
    )
    parser.add_argument(
        "--artist-min-samples",
        type=int,
        default=3,
        help="Minimum tagged comics required to learn an artist profile. Default: 3.",
    )
    parser.add_argument(
        "--artist-threshold",
        type=int,
        default=12,
        help="Visual similarity threshold for artist suggestions. Default: 12.",
    )
    parser.add_argument(
        "--apply-artist-tags",
        action="store_true",
        help="Prompt to rename untagged comics when an artist match is suggested.",
    )
    return parser


def _safe_size(path: Path) -> int:
    try:
        if not path.exists():
            return 0
        if is_image_folder(path):
            return folder_content_size(path)
        if path.is_dir():
            return 0
        return path.stat().st_size
    except OSError:
        return 0


def _trash_files(
    files_to_remove: Iterable[Path],
    trash_dir: Path,
    log: logging.Logger,
    *,
    library_root: Optional[Path] = None,
) -> int:
    reclaimed = 0
    for victim in files_to_remove:
        size = _safe_size(victim)
        try:
            moved = move_to_trash(
                victim,
                trash_dir=trash_dir,
                library_root=library_root,
            )
        except Exception as exc:
            log.error("Unexpected error trashing %s: %s", victim, exc)
            continue
        if moved is not None:
            reclaimed += size
            print(f"  {_color('trashed', 'yellow')} {victim.name}")
        else:
            _err(f"  failed to trash {victim.name}")
    return reclaimed


def _phase_exact_duplicates(
    files: List[Path],
    trash_dir: Path,
    dry_run: bool,
    log: logging.Logger,
    index: Optional[ComicIndex] = None,
    *,
    library_root: Optional[Path] = None,
    use_cache: bool = True,
) -> tuple[List[Path], int, int]:
    _header("Phase 2: Removing Exact Duplicates")
    try:
        groups = find_exact_duplicates(files, index=index, use_cache=use_cache)
    except Exception as exc:
        log.error("Exact duplicate detection failed: %s", exc)
        return files, 0, 0

    if not groups:
        _ok("No exact duplicates found.")
        return files, 0, 0

    removed: Set[Path] = set()
    reclaimed = 0
    group_count = 0

    for digest, group in groups.items():
        try:
            keep, remove = select_file_to_keep(group)
        except ValueError:
            continue

        if not remove:
            continue

        group_count += 1
        print()
        print(_color(f"Exact duplicate group {group_count}:", "bold"))
        print(f"  {_color('KEEP', 'green')}   {keep}")
        for victim in remove:
            print(f"  {_color('REMOVE', 'red')} {victim}")

        if dry_run:
            _warn("  Dry run: would prompt to remove this group.")
            continue

        if not _confirm(
            _color("  Remove the duplicate(s) in this group? [Y/n]", "yellow")
        ):
            _warn("  Skipped this group.")
            continue

        reclaimed += _trash_files(
            remove, trash_dir, log, library_root=library_root
        )
        removed.update(remove)
        if index is not None:
            index.remove_many(remove)

    if group_count == 0:
        _ok("No exact duplicates found.")
    elif dry_run:
        _warn("Dry run: no files were moved.")
    elif removed:
        _ok(
            f"Removed {len(removed)} exact duplicate(s); "
            f"reclaimed {format_size(reclaimed)}."
        )
    else:
        _warn("No exact duplicates were removed.")

    remaining = [p for p in files if p not in removed]
    return remaining, len(removed), reclaimed


def _phase_perceptual_duplicates(
    files: List[Path],
    threshold: int,
    trash_dir: Path,
    dry_run: bool,
    log: logging.Logger,
    index: Optional[ComicIndex] = None,
    *,
    library_root: Optional[Path] = None,
    use_cache: bool = True,
) -> tuple[List[Path], int, int]:
    _header("Phase 3: Removing Perceptual Duplicates")
    try:
        groups = find_perceptual_duplicates(
            files,
            threshold=threshold,
            index=index,
            use_cache=use_cache,
        )
    except Exception as exc:
        log.error("Perceptual duplicate detection failed: %s", exc)
        return files, 0, 0

    if not groups:
        _ok("No perceptual duplicates found.")
        return files, 0, 0

    removed: Set[Path] = set()
    reclaimed = 0
    group_count = 0

    for group in groups:
        paths = [Path(path) for path in group]
        if len(paths) < 2:
            continue

        group_count += 1
        print()
        print(_color(f"Perceptual duplicate group {group_count}:", "bold"))

        if dry_run:
            for number, path in enumerate(_rank_comic_group(paths), start=1):
                print(f"    {number}. {_format_comic_label(path, recommended=(number == 1))}")
            _warn("  Dry run: would prompt to choose which version to keep.")
            continue

        keep = _prompt_keep_choice(paths)
        if keep is None:
            _warn("  Skipped this group.")
            continue

        remove = [path for path in paths if path != keep]
        print(f"  {_color('KEEP', 'green')}   {_format_comic_label(keep)}")
        for victim in remove:
            print(f"  {_color('REMOVE', 'red')} {_format_comic_label(victim)}")

        if not _confirm(
            _color("  Remove the other duplicate(s) in this group? [Y/n]", "yellow")
        ):
            _warn("  Skipped this group.")
            continue

        reclaimed += _trash_files(
            remove, trash_dir, log, library_root=library_root
        )
        removed.update(remove)
        if index is not None:
            index.remove_many(remove)

    if group_count == 0:
        _ok("No perceptual duplicates found.")
    elif dry_run:
        _warn("Dry run: no files were moved.")
    elif removed:
        _ok(
            f"Removed {len(removed)} perceptual duplicate(s); "
            f"reclaimed {format_size(reclaimed)}."
        )
    else:
        _warn("No perceptual duplicates were removed.")

    remaining = [p for p in files if p not in removed]
    return remaining, len(removed), reclaimed


def _phase_conversion(
    files: List[Path],
    trash_dir: Path,
    dry_run: bool,
    keep_originals: bool,
    log: logging.Logger,
    comic_index: Optional[ComicIndex] = None,
    *,
    library_root: Optional[Path] = None,
    max_page_size_mb: float = 2.0,
    workers: int = 1,
) -> tuple[int, int]:
    _header("Phase 4: Standardizing Comics")

    candidates: List[Path] = []
    for path in files:
        try:
            if needs_conversion(path):
                candidates.append(path)
        except Exception as exc:
            log.error("needs_conversion failed for %s: %s", path, exc)

    if not candidates:
        _ok("Every remaining comic already matches the standard folder convention.")
        return 0, 0

    print(
        _color(
            f"Automatically standardizing {len(candidates)} comic(s) "
            f"(folder rename + PNG pages; max {max_page_size_mb:g} MB/page; "
            f"{workers} worker(s); no prompts) ...",
            "bold",
        )
    )

    changed = 0
    bytes_delta = 0

    for step, path in enumerate(candidates, start=1):
        if dry_run:
            _warn(f"  [{step}/{len(candidates)}] Dry run: would standardize {path.name}")
            continue

        pre_size = _safe_size(path)
        try:
            if is_image_folder(path):
                output = standardize_comic(
                    path,
                    trash_dir=trash_dir,
                    library_root=library_root,
                    keep_originals=keep_originals,
                    max_page_size_mb=max_page_size_mb,
                    workers=workers,
                )
            else:
                output = standardize_comic_with_cleanup(
                    path,
                    keep_originals=keep_originals,
                    trash_dir=trash_dir,
                    library_root=library_root,
                    max_page_size_mb=max_page_size_mb,
                    workers=workers,
                )
        except Exception as exc:
            log.error("Standardization failed for %s: %s", path, exc)
            _err(f"  [{step}/{len(candidates)}] Failed to standardize {path.name}")
            continue

        if output is None:
            _err(f"  [{step}/{len(candidates)}] Failed to standardize {path.name}")
            continue

        changed += 1
        bytes_delta += pre_size - _safe_size(output)
        pages = _comic_page_count(output)
        if output.name != path.name:
            _ok(
                f"  [{step}/{len(candidates)}] {path.name} -> {output.name} "
                f"({pages} pages)"
            )
        else:
            _ok(f"  [{step}/{len(candidates)}] {path.name} ({pages} pages)")
        if comic_index is not None:
            comic_index.remove(path)
            comic_index.ensure_metadata(output)

    if dry_run:
        _warn("Dry run: no standardizations performed.")
    elif changed:
        _ok(
            f"Standardized {changed} comic(s) automatically; net size change: "
            f"{format_size(bytes_delta)} "
            f"{'saved' if bytes_delta >= 0 else 'grew'}."
        )
    else:
        _warn("No comics were standardized.")

    return changed, bytes_delta


def _unique_folder_path(parent: Path, folder_name: str) -> Path:
    candidate = parent / folder_name
    counter = 2
    while candidate.exists():
        candidate = parent / f"{folder_name}__{counter}"
        counter += 1
    return candidate


def _format_episode_merge_group(group: EpisodeMergeGroup) -> str:
    parts = []
    for info in group.episodes:
        pages = len(list_folder_images(info.folder))
        parts.append(f"{info.folder.name} ({pages} pages)")
    return " + ".join(parts)


def _phase_combine_episodes(
    files: List[Path],
    trash_dir: Path,
    dry_run: bool,
    log: logging.Logger,
    comic_index: Optional[ComicIndex] = None,
    *,
    library_root: Optional[Path] = None,
) -> tuple[List[Path], int]:
    _header("Phase 5: Combining Contiguous Episodes")

    groups = find_episode_merge_groups(files)
    if not groups:
        _ok("No contiguous episode groups found.")
        return files, 0

    merged_paths: List[Path] = []
    removed: Set[Path] = set()
    group_count = 0

    for group in groups:
        group_count += 1
        merged_name = group.merged_name()
        total_pages = group.total_pages()

        print()
        print(_color(f"Episode merge group {group_count}:", "bold"))
        print(f"  {_format_episode_merge_group(group)}")
        print(
            f"  {_color('MERGE', 'magenta')} -> {merged_name} "
            f"({total_pages} pages total; pages prefixed eNN_...)"
        )

        if dry_run:
            _warn("  Dry run: would prompt to merge this group.")
            continue

        if not _confirm(
            _color("  Merge these contiguous episodes? [Y/n]", "yellow")
        ):
            _warn("  Skipped this group.")
            continue

        try:
            output = merge_episode_group(
                group,
                trash_dir=trash_dir,
                library_root=library_root,
                dry_run=False,
            )
        except Exception as exc:
            log.error("Episode merge failed for %s: %s", merged_name, exc)
            _err(f"  Failed to merge into {merged_name}")
            continue

        if output is None:
            _err(f"  Failed to merge into {merged_name}")
            continue

        merged_paths.append(output)
        removed.update(group.source_folders)
        if comic_index is not None:
            comic_index.remove_many(group.source_folders)
            comic_index.ensure_metadata(output)

        _ok(f"  Merged -> {output}")

    if group_count == 0:
        _ok("No contiguous episode groups found.")
    elif dry_run:
        _warn("Dry run: no episode folders were merged.")
    elif merged_paths:
        _ok(f"Merged {len(merged_paths)} episode group(s).")
    else:
        _warn("No episode groups were merged.")

    remaining = [path for path in files if path not in removed]
    remaining.extend(merged_paths)
    return remaining, len(merged_paths)


def _phase_artist_suggestions(
    files: List[Path],
    dry_run: bool,
    log: logging.Logger,
    comic_index: Optional[ComicIndex] = None,
    *,
    use_cache: bool = True,
    min_samples: int = 3,
    threshold: int = 12,
    apply_tags: bool = False,
) -> int:
    _header("Phase 6: Suggesting Artist Tags")

    tagged = [path for path in files if extract_artist_tag(path.name)]
    untagged = [path for path in files if is_untagged_comic(path)]
    print(
        f"Tagged comics: {len(tagged)} | Untagged comics: {len(untagged)} | "
        f"Learning from artists with at least {min_samples} tagged work(s)."
    )

    if len(tagged) < min_samples:
        _warn("Not enough tagged comics to learn artist styles.")
        return 0

    try:
        signatures = collect_comic_signatures(
            files,
            comic_index,
            use_cache=use_cache,
        )
    except Exception as exc:
        log.error("Artist signature collection failed: %s", exc)
        _err(f"Artist signature collection failed: {exc}")
        return 0

    profiles = build_artist_profiles(signatures, min_samples=min_samples)
    if not profiles:
        _warn("No artist profiles met the minimum sample count.")
        return 0

    print(_color(f"Learned {len(profiles)} artist profile(s).", "bold"))
    suggestions = suggest_artists_for_untagged(
        untagged,
        signatures,
        profiles,
        threshold=threshold,
    )

    if not suggestions:
        _ok("No confident artist matches found for untagged comics.")
        return 0

    applied = 0
    for path, suggestion in suggestions:
        proposed = proposed_tagged_name(path.name, suggestion.artist)
        print()
        print(_color("Untagged comic:", "bold"))
        print(f"  {path.name}")
        print(
            f"  {_color('SUGGEST', 'magenta')} [{suggestion.artist}] "
            f"(confidence: {suggestion.confidence}, "
            f"distance: {suggestion.avg_distance:.1f}, "
            f"from {suggestion.sample_count} tagged work(s), "
            f"margin: {suggestion.margin:.1f})"
        )
        print(f"  proposed: {proposed}")

        if dry_run:
            _warn("  Dry run: would suggest this artist tag.")
            continue

        if not apply_tags:
            continue

        if not _confirm(
            _color(f"  Rename to [{suggestion.artist}] ...? [Y/n]", "yellow")
        ):
            _warn("  Skipped.")
            continue

        destination = _unique_folder_path(path.parent, proposed)
        try:
            path.rename(destination)
        except OSError as exc:
            log.error("Failed renaming %s -> %s: %s", path, destination, exc)
            _err(f"  Failed to rename {path.name}")
            continue

        applied += 1
        if comic_index is not None:
            comic_index.remove(path)
            comic_index.ensure_metadata(destination)
        _ok(f"  Renamed -> {destination.name}")

    if dry_run:
        _warn("Dry run: no folders were renamed.")
    elif apply_tags:
        _ok(f"Applied {applied} artist tag(s).")
    else:
        _ok(
            f"Found {len(suggestions)} artist suggestion(s). "
            "Re-run with --apply-artist-tags to rename matched folders."
        )

    return len(suggestions)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log = setup_logging(args.log_file)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    exclusive_modes = sum(
        bool(flag)
        for flag in (
            args.artists_only,
            args.standardize_only,
            args.combine_episodes_only,
        )
    )
    if exclusive_modes > 1:
        _err(
            "Choose only one of --artists-only, --standardize-only, "
            "or --combine-episodes-only."
        )
        return 2

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        _err(f"Path does not exist: {root}")
        return 2
    if not root.is_dir():
        _err(f"Path is not a directory: {root}")
        return 2
    if args.max_page_size_mb <= 0:
        _err("--max-page-size-mb must be greater than 0.")
        return 2
    workers = args.workers if args.workers > 0 else default_worker_count()
    if workers < 1:
        _err("--workers must be at least 1.")
        return 2

    trash_dir = root / ".manga_keeper_trash"
    index_path = index_file_for(root)
    use_cache = not args.rebuild_cache
    index = ComicIndex.load(root, rebuild=not use_cache)

    print(_color("manga_keeper", "bold", "magenta"))
    print(f"  root:      {root}")
    print(f"  trash:     {trash_dir}")
    print(f"  index:     {index_path}")
    print(f"  threshold: {args.threshold}")
    print(f"  dry-run:   {args.dry_run}")
    print(f"  keep-originals: {args.keep_originals}")
    print(f"  max-page-size: {args.max_page_size_mb:g} MB")
    print(f"  workers:   {workers}")
    print(f"  cache:     {'rebuild' if not use_cache else f'{len(index)} record(s)'}")
    if args.suggest_artists or args.artists_only:
        print(f"  artists:   min-samples={args.artist_min_samples}, threshold={args.artist_threshold}")

    _header("Phase 1: Scanning Directory")
    try:
        files = scan_directory(root, index=index, use_cache=use_cache)
    except Exception as exc:
        log.error("Scan failed: %s", exc)
        _err(f"Scan failed: {exc}")
        return 1

    initial_count = len(files)
    print(_color(f"Discovered {initial_count} comic(s).", "bold"))
    if initial_count == 0:
        if not args.dry_run:
            index.save()
        _warn("Nothing to do.")
        return 0

    exact_removed = perceptual_removed = 0
    exact_bytes = perceptual_bytes = 0
    converted = converted_bytes_delta = 0
    episodes_merged = 0
    artist_suggestions = 0
    run_combine_episodes = args.combine_episodes_only or (
        not args.skip_combine_episodes and not args.artists_only
    )

    try:
        if args.combine_episodes_only:
            files, episodes_merged = _phase_combine_episodes(
                files,
                trash_dir,
                args.dry_run,
                log,
                comic_index=index,
                library_root=root,
            )
        elif args.standardize_only:
            converted, converted_bytes_delta = _phase_conversion(
                files,
                trash_dir,
                args.dry_run,
                args.keep_originals,
                log,
                comic_index=index,
                library_root=root,
                max_page_size_mb=args.max_page_size_mb,
                workers=workers,
            )
            if run_combine_episodes:
                files, episodes_merged = _phase_combine_episodes(
                    files,
                    trash_dir,
                    args.dry_run,
                    log,
                    comic_index=index,
                    library_root=root,
                )
        elif not args.artists_only:
            files, exact_removed, exact_bytes = _phase_exact_duplicates(
                files,
                trash_dir,
                args.dry_run,
                log,
                index,
                library_root=root,
                use_cache=use_cache,
            )

            files, perceptual_removed, perceptual_bytes = _phase_perceptual_duplicates(
                files,
                args.threshold,
                trash_dir,
                args.dry_run,
                log,
                index,
                library_root=root,
                use_cache=use_cache,
            )

            converted, converted_bytes_delta = _phase_conversion(
                files,
                trash_dir,
                args.dry_run,
                args.keep_originals,
                log,
                comic_index=index,
                library_root=root,
                max_page_size_mb=args.max_page_size_mb,
                workers=workers,
            )

            if run_combine_episodes:
                files, episodes_merged = _phase_combine_episodes(
                    files,
                    trash_dir,
                    args.dry_run,
                    log,
                    comic_index=index,
                    library_root=root,
                )

        if args.suggest_artists or args.artists_only:
            artist_suggestions = _phase_artist_suggestions(
                files,
                args.dry_run,
                log,
                index,
                use_cache=use_cache,
                min_samples=args.artist_min_samples,
                threshold=args.artist_threshold,
                apply_tags=args.apply_artist_tags,
            )
    except KeyboardInterrupt:
        print()
        _warn("Interrupted by user. Saving index before exit.")
        if not args.dry_run:
            index.save()
        return 130

    if not args.dry_run:
        index.save()

    _header("Summary")
    total_reclaimed = exact_bytes + perceptual_bytes + max(converted_bytes_delta, 0)
    print(f"  Files discovered:         {initial_count}")
    print(
        f"  Exact duplicates removed: {exact_removed} ({format_size(exact_bytes)})"
    )
    print(
        f"  Perceptual duplicates:    {perceptual_removed} "
        f"({format_size(perceptual_bytes)})"
    )
    print(
        f"  Comics standardized:      {converted} "
        f"(net {format_size(converted_bytes_delta)})"
    )
    if run_combine_episodes:
        print(f"  Episode groups merged:    {episodes_merged}")
    if args.suggest_artists or args.artists_only:
        print(f"  Artist suggestions:       {artist_suggestions}")
    print(
        _color(f"  Approx. space saved:      {format_size(total_reclaimed)}", "green", "bold")
    )
    if args.dry_run:
        _warn("This was a dry run — no files were modified.")
    else:
        print(f"  Trash directory:          {trash_dir}")
        print(f"  Index file:               {index_path}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        _warn("Interrupted by user. Exiting cleanly.")
        sys.exit(130)
