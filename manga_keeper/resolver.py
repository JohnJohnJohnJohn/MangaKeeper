"""Quality-based duplicate resolution for comic files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple, Union

from .utils import folder_content_size, get_comic_metadata

PathLike = Union[str, Path]

log = logging.getLogger(__name__)

QualityScore = Tuple[int, int, int]


def get_quality_score(file_path: PathLike) -> QualityScore:
    """Return ``(page_count, pixel_area, file_size)`` for ranking."""
    path = Path(file_path)
    meta = get_comic_metadata(path) or {}

    page_count = int(meta.get("page_count") or 0)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    pixel_area = width * height

    try:
        if path.is_dir():
            file_size = folder_content_size(path)
        else:
            file_size = path.stat().st_size if path.exists() else 0
    except OSError:
        file_size = 0

    return (page_count, pixel_area, file_size)


def resolve_duplicates(
    duplicate_groups: Iterable[Sequence[PathLike]],
) -> List[Tuple[Path, List[Path]]]:
    resolved: List[Tuple[Path, List[Path]]] = []

    for raw_group in duplicate_groups:
        group: List[Path] = [Path(p) for p in raw_group]
        if not group:
            continue
        if len(group) == 1:
            resolved.append((group[0], []))
            continue

        scored = []
        for path in group:
            score = get_quality_score(path)
            scored.append((score, path))

        scored.sort(
            key=lambda t: (-t[0][0], -t[0][1], -t[0][2], str(t[1]).lower())
        )
        keep = scored[0][1]
        remove = [s[1] for s in scored[1:]]
        log.info(
            "Group of %d -> keep %s (%d pages)",
            len(group),
            keep,
            scored[0][0][0],
        )
        resolved.append((keep, remove))

    return resolved
