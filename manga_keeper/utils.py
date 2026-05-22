"""Shared utility helpers for manga_keeper."""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

PathLike = Union[str, Path]

DEFAULT_TRASH_DIR_NAME = ".manga_keeper_trash"

_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)


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
        log.warning("Cannot move to trash, file not found: %s", src)
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


def get_comic_metadata(file_path: PathLike) -> Optional[Dict[str, Any]]:
    """Return page count and representative dimensions for a comic file."""
    path = Path(file_path)
    suffix = path.suffix.lower()
    log = logging.getLogger(__name__)

    if suffix in {".cbz", ".cbr", ".zip"}:
        names = list_archive_images(path)
        if not names:
            return None
        return {
            "page_count": len(names),
            "width": None,
            "height": None,
        }

    if suffix == ".pdf":
        try:
            import fitz
        except ImportError:
            log.error("pymupdf not installed; cannot read PDF metadata for %s", path)
            return None
        try:
            doc = fitz.open(path)
        except Exception as exc:
            log.warning("Cannot open PDF %s: %s", path, exc)
            return None
        try:
            page_count = doc.page_count
            width = height = None
            if page_count:
                page = doc.load_page(0)
                rect = page.rect
                width = int(rect.width)
                height = int(rect.height)
            return {
                "page_count": page_count,
                "width": width,
                "height": height,
            }
        finally:
            doc.close()

    if suffix == ".epub":
        try:
            import fitz
        except ImportError:
            log.error("pymupdf not installed; cannot read EPUB metadata for %s", path)
            return None
        try:
            doc = fitz.open(path)
        except Exception as exc:
            log.warning("Cannot open EPUB %s: %s", path, exc)
            return None
        try:
            return {"page_count": doc.page_count, "width": None, "height": None}
        finally:
            doc.close()

    return None
