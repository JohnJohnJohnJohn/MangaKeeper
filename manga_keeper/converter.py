"""Standardize comic folders and convert page images to lossless PNG."""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional, Union

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

from .naming import folder_name_is_standard, standard_folder_name
from .utils import is_image_folder, list_archive_images, list_folder_images, move_to_trash

PathLike = Union[str, Path]

log = logging.getLogger(__name__)

TARGET_PAGE_EXTENSION = ".png"

_ARCHIVE_SUFFIXES = frozenset({".cbz", ".cbr", ".zip", ".pdf", ".epub"})


def needs_conversion(comic_path: PathLike) -> bool:
    path = Path(comic_path)
    if is_image_folder(path):
        if not folder_name_is_standard(path):
            return True
        return any(
            image.suffix.lower() != TARGET_PAGE_EXTENSION
            for image in list_folder_images(path)
        )
    if path.is_file():
        return path.suffix.lower() in _ARCHIVE_SUFFIXES
    return False


def _save_image_as_png(source: Path, destination: Path) -> bool:
    if Image is None:
        log.error("Pillow not installed; cannot write PNG")
        return False
    try:
        with Image.open(source) as img:
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            img.save(destination, format="PNG", compress_level=0)
        return destination.exists() and destination.stat().st_size > 0
    except (OSError, ValueError) as exc:
        log.error("Failed converting %s to PNG: %s", source, exc)
        return False


def _unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _unique_output_dir(parent: Path, folder_name: str) -> Path:
    output = parent / folder_name
    counter = 2
    while output.exists():
        output = parent / f"{folder_name}__{counter}"
        counter += 1
    return output


def _convert_image_to_png(image: Path) -> Optional[Path]:
    if image.suffix.lower() == TARGET_PAGE_EXTENSION:
        return image

    destination = image.with_suffix(TARGET_PAGE_EXTENSION)
    if destination.exists() and destination.resolve() != image.resolve():
        destination = _unique_path(image.parent, destination.name)

    if not _save_image_as_png(image, destination):
        return None

    if image.resolve() != destination.resolve():
        try:
            image.unlink()
        except OSError as exc:
            log.error("Failed removing original %s: %s", image, exc)
            return None

    return destination


def _convert_folder_images_to_png(folder: Path) -> bool:
    for image in list_folder_images(folder):
        if _convert_image_to_png(image) is None:
            return False
    return True


def _rename_folder_to_standard(folder: Path) -> Path:
    target_name = standard_folder_name(folder)
    if folder.name == target_name:
        return folder

    destination = _unique_output_dir(folder.parent, target_name)
    try:
        folder.rename(destination)
    except OSError as exc:
        log.error("Failed renaming folder %s -> %s: %s", folder, destination, exc)
        return folder

    print(f"Renamed folder: {folder.name} -> {destination.name}")
    return destination


def _extract_archive_to_folder(archive_path: Path, output_dir: Path) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = archive_path.suffix.lower()
    written: List[Path] = []
    temp_root = Path(tempfile.mkdtemp(prefix="manga_keeper_extract_"))

    try:
        if suffix in {".cbz", ".cbr", ".zip"}:
            names = list_archive_images(archive_path)
            if suffix == ".cbr":
                try:
                    import rarfile
                except ImportError:
                    log.error("rarfile not installed; cannot convert %s", archive_path)
                    return []
                try:
                    with rarfile.RarFile(archive_path) as archive:
                        for name in names:
                            temp_file = temp_root / Path(name).name
                            temp_file.write_bytes(archive.read(name))
                            png_path = _convert_image_to_png(temp_file)
                            if png_path is None:
                                return []
                            final_path = _unique_path(output_dir, png_path.name)
                            shutil.move(str(png_path), str(final_path))
                            written.append(final_path)
                except (rarfile.Error, OSError) as exc:
                    log.error("Failed extracting CBR %s: %s", archive_path, exc)
                    return []
            else:
                try:
                    with zipfile.ZipFile(archive_path) as archive:
                        for name in names:
                            temp_file = temp_root / Path(name).name
                            temp_file.write_bytes(archive.read(name))
                            png_path = _convert_image_to_png(temp_file)
                            if png_path is None:
                                return []
                            final_path = _unique_path(output_dir, png_path.name)
                            shutil.move(str(png_path), str(final_path))
                            written.append(final_path)
                except (zipfile.BadZipFile, OSError) as exc:
                    log.error("Failed extracting archive %s: %s", archive_path, exc)
                    return []
            return written

        if suffix in {".pdf", ".epub"}:
            try:
                import fitz
            except ImportError:
                log.error("pymupdf not installed; cannot convert %s", archive_path)
                return []
            try:
                doc = fitz.open(archive_path)
            except Exception as exc:
                log.error("Cannot open document %s: %s", archive_path, exc)
                return []
            try:
                for index in range(doc.page_count):
                    page = doc.load_page(index)
                    pix = page.get_pixmap()
                    temp_file = temp_root / f"page_{index + 1}.png"
                    pix.save(str(temp_file))
                    final_path = _unique_path(output_dir, temp_file.name)
                    shutil.move(str(temp_file), str(final_path))
                    written.append(final_path)
            finally:
                doc.close()
            return written
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)

    return []


def _standardize_image_folder(folder: Path) -> Optional[Path]:
    if not _convert_folder_images_to_png(folder):
        return None
    return _rename_folder_to_standard(folder)


def standardize_comic(
    input_path: PathLike,
    output_dir: Optional[PathLike] = None,
) -> Optional[Path]:
    """Convert page images to PNG and rename comic folders to the standard convention."""
    src = Path(input_path)
    if not src.exists():
        log.warning("Cannot standardize missing path: %s", src)
        return None
    if src.is_dir():
        if not is_image_folder(src):
            log.warning("Cannot standardize non-comic directory: %s", src)
            return None
    elif not src.is_file():
        log.warning("Cannot standardize invalid path: %s", src)
        return None

    if not needs_conversion(src):
        return src

    if is_image_folder(src):
        print(
            f"Standardizing folder: {src.name} "
            f"({len(list_folder_images(src))} pages)"
        )
        return _standardize_image_folder(src)

    parent = Path(output_dir) if output_dir is not None else src.parent
    output = _unique_output_dir(parent, standard_folder_name(src))
    output.mkdir(parents=True, exist_ok=True)

    written = _extract_archive_to_folder(src, output)
    if not written:
        shutil.rmtree(output, ignore_errors=True)
        return None

    print(
        f"Extracted archive to folder: {src.name} -> {output.name} "
        f"({len(written)} pages)"
    )
    return output


def standardize_comic_with_cleanup(
    input_path: PathLike,
    keep_originals: bool = False,
    trash_dir: Optional[PathLike] = None,
) -> Optional[Path]:
    src = Path(input_path)
    if not src.exists():
        return None

    output = standardize_comic(src)
    if output is None:
        return None

    if keep_originals or src.resolve() == output.resolve():
        return output

    move_to_trash(src, trash_dir=trash_dir)
    return output


# Backwards-compatible aliases used by older imports.
normalize_comic_to_png = standardize_comic
normalize_comic_to_png_with_cleanup = standardize_comic_with_cleanup
