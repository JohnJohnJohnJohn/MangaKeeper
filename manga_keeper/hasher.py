"""Exact-duplicate detection using SHA256 hashing."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple, Union

from .index import compute_content_fingerprint
from .utils import is_image_folder, list_folder_images

if TYPE_CHECKING:
    from .index import ComicIndex

PathLike = Union[str, Path]

CHUNK_SIZE = 1024 * 1024  # 1 MiB


def compute_sha256(file_path: PathLike) -> Optional[str]:
    """Return the hex SHA256 digest for ``file_path`` or ``None`` on error."""
    log = logging.getLogger(__name__)
    path = Path(file_path)

    if not path.exists() or not path.is_file():
        log.warning("Skipping missing file for hashing: %s", path)
        return None

    sha = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
                sha.update(chunk)
    except OSError as exc:
        log.warning("Could not read %s for hashing: %s", path, exc)
        return None

    return sha.hexdigest()


def compute_comic_hash(comic_path: PathLike) -> Optional[str]:
    """Return a deterministic SHA256 digest for a comic file or image folder."""
    path = Path(comic_path)
    if path.is_dir():
        if not is_image_folder(path):
            return None
        images = list_folder_images(path)
        if not images:
            return None
        sha = hashlib.sha256()
        for image in images:
            sha.update(image.name.lower().encode("utf-8"))
            digest = compute_sha256(image)
            if digest is None:
                return None
            sha.update(digest.encode("ascii"))
        return sha.hexdigest()
    if path.is_file():
        return compute_sha256(path)
    return None


def find_exact_duplicates(
    file_paths: Iterable[PathLike],
    index: Optional["ComicIndex"] = None,
    *,
    use_cache: bool = True,
) -> Dict[str, List[Path]]:
    """Group files by SHA256 hash; return groups containing more than one file."""
    paths = [Path(p) for p in file_paths]
    print(f"Hashing {len(paths)} file(s) for exact duplicate detection ...")

    by_hash: Dict[str, List[Path]] = {}
    cache_hits = 0

    for step, path in enumerate(paths, start=1):
        digest: Optional[str] = None
        record = index.get(path) if index is not None else None

        if (
            use_cache
            and index is not None
            and record
            and record.exact_hash
            and index.is_content_cache_hit(path)
        ):
            digest = record.exact_hash
            cache_hits += 1
            print(f"  [{step}/{len(paths)}] cached hash {path.name}")
        else:
            print(f"  [{step}/{len(paths)}] hashing {path.name}")
            digest = compute_comic_hash(path)
            if digest is not None and index is not None:
                index.upsert(
                    path,
                    content_fingerprint=compute_content_fingerprint(path),
                    exact_hash=digest,
                )

        if digest is None:
            continue
        by_hash.setdefault(digest, []).append(path)

    if cache_hits:
        print(f"Reused exact-hash cache for {cache_hits} comic(s).")

    duplicates = {h: ps for h, ps in by_hash.items() if len(ps) > 1}
    print(f"Found {len(duplicates)} exact-duplicate group(s).")
    return duplicates


_NOISE_PATTERNS = [
    re.compile(r"\(\s*\d+\s*\)"),
    re.compile(r"\bcopy\b", re.IGNORECASE),
    re.compile(r"\bduplicate\b", re.IGNORECASE),
]


def _cleanliness_score(path: Path) -> Tuple[int, int, int, str]:
    name = path.stem
    noise_hits = sum(len(p.findall(name)) for p in _NOISE_PATTERNS)
    non_alnum = sum(
        1 for ch in name if not ch.isalnum() and ch not in (" ", "-", "_", ".")
    )
    return (noise_hits, len(name), non_alnum, str(path).lower())


def select_file_to_keep(duplicates_list: Iterable[PathLike]) -> Tuple[Path, List[Path]]:
    """Pick the cleanest filename from the list of duplicates."""
    paths = [Path(p) for p in duplicates_list]
    if not paths:
        raise ValueError("select_file_to_keep requires at least one path")

    ranked = sorted(paths, key=_cleanliness_score)
    keep = ranked[0]
    remove = [p for p in paths if p != keep]
    return keep, remove
