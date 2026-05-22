"""Persistent index for scanned and processed comics."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Union

from .utils import get_comic_metadata, list_folder_images

PathLike = Union[str, Path]

INDEX_VERSION = 1
INDEX_DIR_NAME = ".manga_keeper"
INDEX_FILE_NAME = "index.json"
DEFAULT_PERCEPTUAL_PAGES = 12

log = logging.getLogger(__name__)


def index_dir_for(root: PathLike) -> Path:
    return Path(root).resolve() / INDEX_DIR_NAME


def index_file_for(root: PathLike) -> Path:
    return index_dir_for(root) / INDEX_FILE_NAME


def compute_quick_fingerprint(path: PathLike) -> str:
    """Cheap change detector based on filesystem metadata only."""
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return ""

    if target.is_dir():
        payload = f"dir:{stat.st_mtime_ns}"
    else:
        payload = f"file:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_content_fingerprint(path: PathLike) -> str:
    """Stronger change detector used to validate cached hashes/signatures."""
    target = Path(path)
    if not target.exists():
        return ""

    if target.is_dir():
        lines: List[str] = []
        for image in list_folder_images(target):
            try:
                stat = image.stat()
            except OSError:
                continue
            lines.append(f"{image.name.lower()}:{stat.st_size}:{stat.st_mtime_ns}")
        payload = "\n".join(lines)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    try:
        stat = target.stat()
    except OSError:
        return ""
    return hashlib.sha256(f"{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")).hexdigest()


def comic_kind(path: Path) -> str:
    return "folder" if Path(path).is_dir() else "archive"


@dataclass
class ComicRecord:
    path: str
    kind: str
    quick_fingerprint: str
    content_fingerprint: str
    exact_hash: Optional[str] = None
    page_count: int = 0
    width: Optional[int] = None
    height: Optional[int] = None
    perceptual_hashes: List[str] = field(default_factory=list)
    perceptual_num_pages: int = DEFAULT_PERCEPTUAL_PAGES
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ComicRecord:
        return cls(
            path=data["path"],
            kind=data.get("kind", "archive"),
            quick_fingerprint=data.get("quick_fingerprint", ""),
            content_fingerprint=data.get("content_fingerprint", ""),
            exact_hash=data.get("exact_hash"),
            page_count=int(data.get("page_count") or 0),
            width=data.get("width"),
            height=data.get("height"),
            perceptual_hashes=list(data.get("perceptual_hashes") or []),
            perceptual_num_pages=int(
                data.get("perceptual_num_pages") or DEFAULT_PERCEPTUAL_PAGES
            ),
            updated_at=data.get("updated_at", ""),
        )


class ComicIndex:
    def __init__(self, root: Path, records: Optional[Dict[str, ComicRecord]] = None) -> None:
        self.root = root.resolve()
        self._records: Dict[str, ComicRecord] = records or {}

    @classmethod
    def load(cls, root: PathLike, *, rebuild: bool = False) -> ComicIndex:
        root_path = Path(root).resolve()
        if rebuild:
            return cls(root_path)

        index_path = index_file_for(root_path)
        if not index_path.exists():
            return cls(root_path)

        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read index %s: %s", index_path, exc)
            return cls(root_path)

        if payload.get("version") != INDEX_VERSION:
            log.warning("Index version mismatch; rebuilding cache.")
            return cls(root_path)

        if payload.get("root") != str(root_path):
            log.warning("Index root mismatch; rebuilding cache.")
            return cls(root_path)

        records = {
            key: ComicRecord.from_dict(value)
            for key, value in (payload.get("comics") or {}).items()
        }
        return cls(root_path, records)

    def get(self, path: PathLike) -> Optional[ComicRecord]:
        return self._records.get(str(Path(path).resolve()))

    def upsert(
        self,
        path: PathLike,
        *,
        kind: Optional[str] = None,
        quick_fingerprint: Optional[str] = None,
        content_fingerprint: Optional[str] = None,
        exact_hash: Optional[str] = None,
        page_count: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        perceptual_hashes: Optional[List[str]] = None,
        perceptual_num_pages: Optional[int] = None,
    ) -> ComicRecord:
        resolved = Path(path).resolve()
        key = str(resolved)
        record = self._records.get(key)

        if record is None:
            record = ComicRecord(
                path=key,
                kind=kind or comic_kind(resolved),
                quick_fingerprint=quick_fingerprint or compute_quick_fingerprint(resolved),
                content_fingerprint=content_fingerprint
                or compute_content_fingerprint(resolved),
            )
            self._records[key] = record

        if kind is not None:
            record.kind = kind
        if quick_fingerprint is not None:
            record.quick_fingerprint = quick_fingerprint
        if content_fingerprint is not None:
            record.content_fingerprint = content_fingerprint
        if exact_hash is not None:
            record.exact_hash = exact_hash
        if page_count is not None:
            record.page_count = page_count
        if width is not None:
            record.width = width
        if height is not None:
            record.height = height
        if perceptual_hashes is not None:
            record.perceptual_hashes = perceptual_hashes
        if perceptual_num_pages is not None:
            record.perceptual_num_pages = perceptual_num_pages

        record.updated_at = datetime.now(timezone.utc).isoformat()
        return record

    def remove(self, path: PathLike) -> None:
        self._records.pop(str(Path(path).resolve()), None)

    def remove_many(self, paths: Iterable[PathLike]) -> None:
        for path in paths:
            self.remove(path)

    def prune_to(self, discovered: Iterable[PathLike]) -> int:
        keep = {str(Path(path).resolve()) for path in discovered}
        stale = [key for key in self._records if key not in keep]
        for key in stale:
            del self._records[key]
        return len(stale)

    def save(self) -> None:
        index_path = index_file_for(self.root)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_VERSION,
            "root": str(self.root),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "comics": {key: asdict(record) for key, record in self._records.items()},
        }
        temp_path = index_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(index_path)

    def ensure_metadata(self, path: PathLike, *, force: bool = False) -> ComicRecord:
        resolved = Path(path).resolve()
        record = self.get(resolved)
        quick = compute_quick_fingerprint(resolved)
        if (
            not force
            and record
            and record.quick_fingerprint == quick
            and record.page_count > 0
        ):
            return record

        meta = get_comic_metadata(resolved) or {}
        return self.upsert(
            resolved,
            kind=comic_kind(resolved),
            quick_fingerprint=quick,
            content_fingerprint=compute_content_fingerprint(resolved),
            page_count=int(meta.get("page_count") or (record.page_count if record else 0)),
            width=meta.get("width") if meta.get("width") is not None else (record.width if record else None),
            height=meta.get("height") if meta.get("height") is not None else (record.height if record else None),
        )

    def is_scan_cache_hit(self, path: PathLike) -> bool:
        record = self.get(path)
        if record is None:
            return False
        return record.quick_fingerprint == compute_quick_fingerprint(path)

    def is_content_cache_hit(self, path: PathLike) -> bool:
        record = self.get(path)
        if record is None:
            return False
        return record.content_fingerprint == compute_content_fingerprint(path)

    def cached_paths(self) -> Set[str]:
        return set(self._records.keys())

    def __len__(self) -> int:
        return len(self._records)
