"""Filesystem scanning for manga / comic archive files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Union

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


def scan_directory(root_path: PathLike) -> List[Path]:
    """Recursively scan ``root_path`` for supported comic files.

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

    print(f"Scanning {root} ...")
    results: List[Path] = []
    total_seen = 0

    stack: List[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError) as exc:
            log.warning("Cannot read %s: %s", current, exc)
            continue

        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                total_seen += 1
                if entry.suffix.lower() in COMIC_EXTENSIONS:
                    results.append(entry.resolve())

    print(
        f"Found {len(results)} comic file(s) "
        f"(out of {total_seen} files inspected)."
    )
    return results
