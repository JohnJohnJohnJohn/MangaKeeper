"""Shared utility helpers for manga_keeper."""

from __future__ import annotations

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

PathLike = Union[str, Path]

DEFAULT_TRASH_DIR_NAME = ".manga_keeper_trash"

IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)
_IMAGE_SUFFIXES = IMAGE_SUFFIXES


def _natural_key(name: str) -> List:
    parts = re.split(r"(\d+)", name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def is_image_file(path: PathLike) -> bool:
    return Path(path).suffix.lower() in _IMAGE_SUFFIXES


def is_image_folder(path: PathLike) -> bool:
    """Return True if ``path`` is a directory with image files as direct children."""
    folder = Path(path)
    if not folder.is_dir():
        return False
    try:
        for entry in folder.iterdir():
            if entry.is_file() and is_image_file(entry):
                return True
    except OSError:
        return False
    return False


def list_folder_images(folder_path: PathLike) -> List[Path]:
    """Return image files in ``folder_path`` (direct children only), naturally sorted."""
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    images = [
        entry.resolve()
        for entry in folder.iterdir()
        if entry.is_file() and is_image_file(entry)
    ]
    return sorted(images, key=lambda p: _natural_key(p.name))


def folder_content_size(folder_path: PathLike) -> int:
    total = 0
    for image in list_folder_images(folder_path):
        try:
            total += image.stat().st_size
        except OSError:
            continue
    return total


def setup_logging(log_file: Optional[PathLike] = None) -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def move_to_trash(file_path: PathLike, trash_dir: Optional[PathLike] = None) -> Optional[Path]:
    src = Path(file_path)
    log = logging.getLogger(__name__)

    if not src.exists():
        log.warning("Cannot move to trash, path not found: %s", src)
        return None

    if trash_dir is None:
        trash_path = src.parent / DEFAULT_TRASH_DIR_NAME
    else:
        trash_path = Path(trash_dir)

    trash_path.mkdir(parents=True, exist_ok=True)

    target = trash_path / src.name
    counter = 1
    while target.exists():
        target = trash_path / f"{src.stem}__{counter}{src.suffix}"
        counter += 1

    try:
        shutil.move(str(src), str(target))
        log.info("Moved %s -> %s", src, target)
        return target
    except OSError as exc:
        log.error("Failed to move %s to trash: %s", src, exc)
        return None


def format_size(num_bytes: float) -> str:
    if num_bytes is None:
        return "0 B"

    size = float(num_bytes)
    sign = "-" if size < 0 else ""
    size = abs(size)

    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{sign}{int(size)} {unit}"
            return f"{sign}{size:.2f} {unit}"
        size /= 1024.0

    return f"{sign}{size:.2f} {units[-1]}"


def _is_image_member(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_SUFFIXES


def list_archive_images(file_path: PathLike) -> List[str]:
    """Return sorted image member names inside a CBZ/CBR/ZIP archive."""
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".cbr":
        try:
            import rarfile
        except ImportError:
            logging.getLogger(__name__).error(
                "rarfile not installed; cannot read %s", path
            )
            return []
        try:
            with rarfile.RarFile(path) as archive:
                names = [
                    info.filename
                    for info in archive.infolist()
                    if not info.isdir() and _is_image_member(info.filename)
                ]
        except (rarfile.Error, OSError) as exc:
            logging.getLogger(__name__).warning("Cannot read CBR %s: %s", path, exc)
            return []
        return sorted(names, key=lambda n: n.lower())

    if suffix in {".cbz", ".zip"}:
        try:
            with zipfile.ZipFile(path) as archive:
                names = [
                    info.filename
                    for info in archive.infolist()
                    if not info.filename.endswith("/")
                    and _is_image_member(info.filename)
                ]
        except (zipfile.BadZipFile, OSError) as exc:
            logging.getLogger(__name__).warning("Cannot read ZIP %s: %s", path, exc)
            return []
        return sorted(names, key=lambda n: n.lower())

    return []


def _archive_first_image_bytes(path: Path) -> Optional[bytes]:
    """Read the first image member from a CBZ/CBR/ZIP archive."""
    names = list_archive_images(path)
    if not names:
        return None

    suffix = path.suffix.lower()
    first = names[0]

    if suffix == ".cbr":
        try:
            import rarfile
        except ImportError:
            return None
        try:
            with rarfile.RarFile(path) as archive:
                return archive.read(first)
        except (rarfile.Error, OSError):
            return None

    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(first)
    except (zipfile.BadZipFile, OSError):
        return None


def _image_dimensions_from_bytes(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    if Image is None:
        return None, None
    try:
        with Image.open(io.BytesIO(data)) as img:
            return int(img.width), int(img.height)
    except (OSError, ValueError):
        return None, None


def _document_first_page_dimensions(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        import fitz
    except ImportError:
        return None, None
    try:
        doc = fitz.open(path)
    except Exception:
        return None, None
    try:
        if doc.page_count == 0:
            return None, None
        rect = doc.load_page(0).rect
        return int(rect.width), int(rect.height)
    finally:
        doc.close()


def get_comic_metadata(file_path: PathLike) -> Optional[Dict[str, Any]]:
    """Return page count and representative dimensions for a comic file or image folder."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    log = logging.getLogger(__name__)

    if is_image_folder(path):
        images = list_folder_images(path)
        if not images:
            return None
        width = height = None
        try:
            data = images[0].read_bytes()
            width, height = _image_dimensions_from_bytes(data)
        except OSError as exc:
            log.warning("Cannot read first image in folder %s: %s", path, exc)
        return {
            "page_count": len(images),
            "width": width,
            "height": height,
        }

    if suffix in {".cbz", ".cbr", ".zip"}:
        names = list_archive_images(path)
        if not names:
            return None
        width = height = None
        data = _archive_first_image_bytes(path)
        if data:
            width, height = _image_dimensions_from_bytes(data)
        return {
            "page_count": len(names),
            "width": width,
            "height": height,
        }

    if suffix in {".pdf", ".epub"}:
        try:
            import fitz  # noqa: F401
        except ImportError:
            log.error("pymupdf not installed; cannot read %s metadata for %s", suffix, path)
            return None
        try:
            doc = fitz.open(path)
        except Exception as exc:
            log.warning("Cannot open %s %s: %s", suffix, path, exc)
            return None
        try:
            width, height = _document_first_page_dimensions(path)
            return {
                "page_count": doc.page_count,
                "width": width,
                "height": height,
            }
        finally:
            doc.close()

    return None
