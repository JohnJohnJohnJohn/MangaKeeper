"""Filesystem scanning for manga / comic archive files."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Union

from .utils import is_image_file

if TYPE_CHECKING:
    from .index import ComicIndex

PathLike = Union[str, Path]

COMIC_EXTENSIONS = frozenset(
    {
        ".cbz",
        ".cbr",
        ".zip",
        ".pdf",
        ".epub",
    }
)

_BAR_WIDTH = 28
_UPDATE_INTERVAL_S = 0.1
_LOG_INTERVAL_DIRS = 500


def _is_comic_file(path: Path) -> bool:
    return path.suffix.lower() in COMIC_EXTENSIONS


class _ScanProgress:
    """Live scan progress for TTY consoles; periodic logs otherwise."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.dirs_scanned = 0
        self.comics_found = 0
        self.files_seen = 0
        self.cache_hits = 0
        self._interactive = sys.stdout.isatty()
        self._last_render = 0.0
        self._last_logged_dirs = 0
        self._log = logging.getLogger(__name__)

    def visit_dir(self) -> None:
        self.dirs_scanned += 1
        self._maybe_update()

    def add_comic(self, *, cached: bool = False) -> None:
        self.comics_found += 1
        if cached:
            self.cache_hits += 1
        self._maybe_update(force=True)

    def add_files(self, count: int) -> None:
        if count:
            self.files_seen += count
            self._maybe_update()

    def _bar(self) -> str:
        position = self.dirs_scanned % _BAR_WIDTH
        return f"[{'=' * position}>{' ' * (_BAR_WIDTH - position - 1)}]"

    def _message(self) -> str:
        parts = [
            self._bar(),
            f"{self.dirs_scanned:,} folders",
            f"{self.comics_found:,} comics",
            f"{self.files_seen:,} files",
        ]
        if self.cache_hits:
            parts.append(f"{self.cache_hits:,} cached")
        return " | ".join(parts)

    def _maybe_update(self, force: bool = False) -> None:
        now = time.monotonic()
        if self._interactive:
            if force or now - self._last_render >= _UPDATE_INTERVAL_S:
                self._last_render = now
                sys.stdout.write(f"\rScanning {self.root} ... {self._message()}")
                sys.stdout.flush()
            return

        if (
            force
            or self.dirs_scanned - self._last_logged_dirs >= _LOG_INTERVAL_DIRS
        ):
            self._last_logged_dirs = self.dirs_scanned
            self._log.info("Scan progress: %s", self._message())

    def finish(self) -> None:
        if self._interactive:
            sys.stdout.write(f"\rScanning {self.root} ... {self._message()}\n")
            sys.stdout.flush()


def scan_directory(
    root_path: PathLike,
    index: Optional["ComicIndex"] = None,
    *,
    use_cache: bool = True,
) -> List[Path]:
    """Recursively scan ``root_path`` for supported comic files and image folders.

    A directory containing image files as direct children is treated as one comic.
    Hidden files and directories (prefixed with ``.``) are skipped. Returns a
    list of absolute paths.
    """
    log = logging.getLogger(__name__)
    root = Path(root_path).expanduser().resolve()

    if not root.exists():
        log.error("Scan root does not exist: %s", root)
        return []
    if not root.is_dir():
        log.error("Scan root is not a directory: %s", root)
        return []

    results: List[Path] = []
    total_seen = 0
    progress: Optional[_ScanProgress] = _ScanProgress(root)

    stack: List[Path] = [root]
    while stack:
        current = stack.pop()

        if use_cache and index is not None:
            record = index.get(current)
            if (
                record
                and record.kind == "folder"
                and index.is_scan_cache_hit(current)
            ):
                resolved = current.resolve()
                results.append(resolved)
                total_seen += record.page_count
                progress.add_comic(cached=True)
                progress.add_files(record.page_count)
                continue

        progress.visit_dir()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError) as exc:
            log.warning("Cannot read %s: %s", current, exc)
            continue

        visible_entries = [
            entry
            for entry in entries
            if not entry.name.startswith(".") and not entry.is_symlink()
        ]

        has_direct_images = any(
            entry.is_file() and is_image_file(entry) for entry in visible_entries
        )

        if has_direct_images:
            file_count = sum(1 for entry in visible_entries if entry.is_file())
            results.append(current.resolve())
            total_seen += file_count
            progress.add_comic()
            progress.add_files(file_count)
            continue

        dir_files = 0
        for entry in visible_entries:
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                dir_files += 1
                if _is_comic_file(entry):
                    results.append(entry.resolve())
                    progress.add_comic()

        total_seen += dir_files
        progress.add_files(dir_files)

    progress.finish()
    print(
        f"Found {len(results)} comic(s) "
        f"(out of {total_seen} files inspected)."
    )
    if progress.cache_hits:
        print(f"Reused scan cache for {progress.cache_hits} comic(s).")

    if index is not None:
        for comic in results:
            index.ensure_metadata(comic)
        pruned = index.prune_to(results)
        if pruned:
            log.info("Pruned %d stale index record(s).", pruned)

    return results
