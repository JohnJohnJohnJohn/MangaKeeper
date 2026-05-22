"""Perceptual duplicate detection using sampled page pHash signatures."""

from __future__ import annotations

import io
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Sequence, Tuple, Union

try:
    import imagehash
    from PIL import Image
except ImportError:
    imagehash = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from .index import ComicIndex

from .index import DEFAULT_PERCEPTUAL_PAGES, compute_content_fingerprint
from .utils import get_comic_metadata, is_image_folder, list_archive_images, list_folder_images

PathLike = Union[str, Path]

log = logging.getLogger(__name__)

_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)


class UnionFind:
    def __init__(self, items: Optional[Iterable] = None) -> None:
        self._parent: Dict = {}
        self._rank: Dict = {}
        if items:
            for item in items:
                self.add(item)

    def add(self, item) -> None:
        if item not in self._parent:
            self._parent[item] = item
            self._rank[item] = 0

    def find(self, item):
        self.add(item)
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        cur = item
        while self._parent[cur] != root:
            self._parent[cur], cur = root, self._parent[cur]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1

    def groups(self) -> List[List]:
        buckets: Dict = {}
        for item in self._parent:
            buckets.setdefault(self.find(item), []).append(item)
        return list(buckets.values())


def _page_indices(total: int, num_pages: int) -> List[int]:
    if total <= 0 or num_pages <= 0:
        return []
    if num_pages >= total:
        return list(range(total))
    if num_pages == 1:
        return [total // 2]
    start = max(0, int(total * 0.10))
    end = max(start, int(total * 0.90) - 1)
    if end <= start:
        start, end = 0, total - 1
    step = (end - start) / (num_pages - 1)
    return sorted({min(total - 1, int(start + step * i)) for i in range(num_pages)})


def _load_image_bytes(data: bytes) -> Optional[Image.Image]:
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.convert("RGB")
    except (OSError, ValueError) as exc:
        log.warning("Failed to decode image bytes: %s", exc)
        return None


def _hash_image(img: Image.Image):
    if imagehash is None:
        return None
    return imagehash.phash(img)


def _extract_from_folder(folder_path: Path, num_pages: int) -> List:
    images = list_folder_images(folder_path)
    if not images:
        return []

    indices = _page_indices(len(images), num_pages)
    hashes = []
    for idx in indices:
        try:
            data = images[idx].read_bytes()
        except OSError as exc:
            log.warning("Failed reading %s: %s", images[idx], exc)
            continue
        img = _load_image_bytes(data)
        if img is None:
            continue
        page_hash = _hash_image(img)
        if page_hash is not None:
            hashes.append(page_hash)
    return hashes


def _extract_from_archive(file_path: Path, num_pages: int) -> List:
    suffix = file_path.suffix.lower()
    names = list_archive_images(file_path)
    if not names:
        return []

    indices = _page_indices(len(names), num_pages)
    hashes = []

    if suffix == ".cbr":
        try:
            import rarfile
        except ImportError:
            log.error("rarfile not installed; cannot sample pages from %s", file_path)
            return []
        try:
            with rarfile.RarFile(file_path) as archive:
                for idx in indices:
                    data = archive.read(names[idx])
                    img = _load_image_bytes(data)
                    if img is None:
                        continue
                    page_hash = _hash_image(img)
                    if page_hash is not None:
                        hashes.append(page_hash)
        except (rarfile.Error, OSError) as exc:
            log.warning("Failed reading CBR %s: %s", file_path, exc)
        return hashes

    try:
        with zipfile.ZipFile(file_path) as archive:
            for idx in indices:
                data = archive.read(names[idx])
                img = _load_image_bytes(data)
                if img is None:
                    continue
                page_hash = _hash_image(img)
                if page_hash is not None:
                    hashes.append(page_hash)
    except (zipfile.BadZipFile, OSError) as exc:
        log.warning("Failed reading archive %s: %s", file_path, exc)

    return hashes


def _extract_from_document(file_path: Path, num_pages: int) -> List:
    try:
        import fitz
    except ImportError:
        log.error("pymupdf not installed; cannot sample pages from %s", file_path)
        return []

    hashes = []
    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        log.warning("Cannot open document %s: %s", file_path, exc)
        return []

    try:
        indices = _page_indices(doc.page_count, num_pages)
        for idx in indices:
            try:
                page = doc.load_page(idx)
                pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
                img = _load_image_bytes(pix.tobytes("png"))
                if img is None:
                    continue
                page_hash = _hash_image(img)
                if page_hash is not None:
                    hashes.append(page_hash)
            except Exception as exc:
                log.warning("Failed rendering page %d of %s: %s", idx, file_path, exc)
    finally:
        doc.close()

    return hashes


def extract_sample_pages(file_path: PathLike, num_pages: int = 12) -> List:
    """Extract perceptual hashes from evenly spaced pages."""
    if imagehash is None or Image is None:
        log.error("imagehash / Pillow not installed; cannot compute perceptual hash")
        return []

    src = Path(file_path)

    if is_image_folder(src):
        return _extract_from_folder(src, num_pages)

    suffix = src.suffix.lower()

    if suffix in {".cbz", ".cbr", ".zip"}:
        return _extract_from_archive(src, num_pages)
    if suffix in {".pdf", ".epub"}:
        return _extract_from_document(src, num_pages)

    return []


def compute_comic_signature(file_path: PathLike, num_pages: int = 12) -> Optional[List]:
    hashes = extract_sample_pages(file_path, num_pages=num_pages)
    return hashes or None


def compute_similarity(sig1: Sequence, sig2: Sequence) -> float:
    if not sig1 or not sig2:
        return float("inf")

    if len(sig1) <= len(sig2):
        query, target = sig1, sig2
    else:
        query, target = sig2, sig1

    total_best = 0
    for q_hash in query:
        best = min(q_hash - t_hash for t_hash in target)
        total_best += best
    return total_best / len(query)


def _best_match_details(sig1: Sequence, sig2: Sequence) -> Tuple[float, float]:
    if not sig1 or not sig2:
        return float("inf"), 0.0

    if len(sig1) <= len(sig2):
        query, target = sig1, sig2
    else:
        query, target = sig2, sig1

    best_distances: List[int] = []
    for q_hash in query:
        best = min(q_hash - t_hash for t_hash in target)
        best_distances.append(best)

    avg_distance = sum(best_distances) / len(best_distances)
    good_matches = sum(1 for d in best_distances if d <= 8)
    good_ratio = good_matches / len(best_distances)
    return avg_distance, good_ratio


def _serialize_phash(page_hash) -> str:
    return str(page_hash)


def _deserialize_phash(value: str):
    if imagehash is None:
        return None
    return imagehash.hex_to_hash(value)


def find_perceptual_duplicates(
    file_paths: Iterable[PathLike],
    threshold: int = 10,
    page_tolerance: int = 5,
    index: Optional["ComicIndex"] = None,
    *,
    use_cache: bool = True,
    num_pages: int = DEFAULT_PERCEPTUAL_PAGES,
) -> List[List[Path]]:
    """Find perceptual duplicate groups across comic files."""
    paths = [Path(p) for p in file_paths]
    if len(paths) < 2:
        return []

    print(f"Computing page signatures for {len(paths)} file(s) ...")
    signatures: Dict[Path, List] = {}
    page_counts: Dict[Path, int] = {}
    cache_hits = 0

    for step, path in enumerate(paths, start=1):
        sig: Optional[List] = None
        record = index.get(path) if index is not None else None

        if (
            use_cache
            and index is not None
            and record
            and record.perceptual_hashes
            and record.perceptual_num_pages == num_pages
            and index.is_content_cache_hit(path)
        ):
            sig = [
                deserialized
                for value in record.perceptual_hashes
                if (deserialized := _deserialize_phash(value)) is not None
            ]
            if sig:
                cache_hits += 1
                print(f"  [{step}/{len(paths)}] cached signature {path.name}")
                page_counts[path] = record.page_count
                signatures[path] = sig
                continue

        print(f"  [{step}/{len(paths)}] sampling pages from {path.name}")
        sig = compute_comic_signature(path, num_pages=num_pages)
        meta = get_comic_metadata(path) or {}
        page_count = int(meta.get("page_count") or 0)
        page_counts[path] = page_count

        if sig:
            signatures[path] = sig
            if index is not None:
                index.upsert(
                    path,
                    content_fingerprint=compute_content_fingerprint(path),
                    page_count=page_count,
                    width=meta.get("width"),
                    height=meta.get("height"),
                    perceptual_hashes=[_serialize_phash(value) for value in sig],
                    perceptual_num_pages=num_pages,
                )

    if cache_hits:
        print(f"Reused perceptual cache for {cache_hits} comic(s).")

    if len(signatures) < 2:
        print("Not enough signatures to compare.")
        return []

    uf = UnionFind(signatures.keys())
    items = list(signatures.keys())

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            pages_a = page_counts.get(a, 0)
            pages_b = page_counts.get(b, 0)
            if pages_a and pages_b and abs(pages_a - pages_b) > page_tolerance:
                continue

            avg_dist, good_ratio = _best_match_details(
                signatures[a], signatures[b]
            )
            if avg_dist <= threshold or good_ratio >= 0.6:
                uf.union(a, b)

    groups = [g for g in uf.groups() if len(g) > 1]
    print(f"Found {len(groups)} perceptual-duplicate group(s).")
    return groups
