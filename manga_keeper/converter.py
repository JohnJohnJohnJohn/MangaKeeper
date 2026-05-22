"""Standardize comic folders and convert page images to size-budget PNG."""

from __future__ import annotations

import io
import logging
import math
import os
import shutil
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional, Tuple, Union

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

from .naming import folder_name_is_standard, standard_folder_name
from .utils import is_image_folder, list_archive_images, list_folder_images, move_to_trash

PathLike = Union[str, Path]

log = logging.getLogger(__name__)

TARGET_PAGE_EXTENSION = ".png"

# Converted PNG must not exceed this multiple of the source page file size.
MAX_OUTPUT_SIZE_RATIO = 2.0
# Prefer shrinking dimensions down to this scale before reducing color depth.
MIN_OUTPUT_SCALE = 0.05
_SCALE_REFINE_ITERATIONS = 5
_QUANTIZE_COLOR_STEPS = (256, 128, 64, 32)
_FAST_PNG_COMPRESS_LEVEL = 3

# PNG compress_level only affects file size and encode time, not pixel fidelity.
_PNG_COMPRESS_LARGE = 6
_PNG_COMPRESS_MEDIUM = 7
_PNG_COMPRESS_SMALL = 9
_LARGE_PAGE_PIXELS = 4_000_000
_MEDIUM_PAGE_PIXELS = 1_000_000

_ARCHIVE_SUFFIXES = frozenset({".cbz", ".cbr", ".zip", ".pdf", ".epub"})


def needs_png_conversion(comic_path: PathLike) -> bool:
    path = Path(comic_path)
    if is_image_folder(path):
        return any(
            image.suffix.lower() != TARGET_PAGE_EXTENSION
            for image in list_folder_images(path)
        )
    if path.is_file():
        return path.suffix.lower() in _ARCHIVE_SUFFIXES
    return False


def needs_folder_rename(comic_path: PathLike) -> bool:
    path = Path(comic_path)
    return is_image_folder(path) and not folder_name_is_standard(path)


def needs_conversion(comic_path: PathLike) -> bool:
    path = Path(comic_path)
    if is_image_folder(path):
        return needs_folder_rename(path) or needs_png_conversion(path)
    return needs_png_conversion(path)


def _png_compress_level(width: int, height: int) -> int:
    """Pick PNG zlib level from page size (quality is unchanged at any level)."""
    pixels = width * height
    if pixels >= _LARGE_PAGE_PIXELS:
        return _PNG_COMPRESS_LARGE
    if pixels >= _MEDIUM_PAGE_PIXELS:
        return _PNG_COMPRESS_MEDIUM
    return _PNG_COMPRESS_SMALL


def _prepare_png_image(img: Image.Image) -> Image.Image:
    """Convert to a PNG-safe mode without unnecessary resampling."""
    if img.mode in ("RGBA", "LA"):
        return img
    if img.mode == "P" and "transparency" in img.info:
        return img.convert("RGBA")
    if img.mode == "P":
        return img.convert("RGB")
    if img.mode in ("L", "RGB", "CMYK"):
        return img.convert("RGB")
    if img.mode == "I":
        return img.convert("I")
    if img.mode == "I;16":
        return img.convert("I;16")
    if img.mode == "1":
        return img.convert("L")
    return img.convert("RGB")


def _scaled_image(img: Image.Image, scale: float) -> Image.Image:
    if scale >= 0.999:
        return img
    width, height = img.size
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))
    if (new_width, new_height) == (width, height):
        return img
    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def _quantize_image(img: Image.Image, colors: int) -> Image.Image:
    if img.mode == "RGBA":
        rgb = img.convert("RGB")
        quantized = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        result = quantized.convert("RGBA")
        result.putalpha(img.getchannel("A"))
        return result
    working = img.convert("RGB") if img.mode != "RGB" else img
    return working.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGB")


def _encode_png_bytes(
    img: Image.Image,
    *,
    colors: Optional[int] = None,
    fast: bool = False,
) -> bytes:
    buffer = io.BytesIO()
    to_save = _quantize_image(img, colors) if colors is not None else img
    if fast:
        compress_level = _FAST_PNG_COMPRESS_LEVEL
        optimize = False
    else:
        compress_level = _png_compress_level(to_save.width, to_save.height)
        optimize = True
    to_save.save(
        buffer,
        format="PNG",
        compress_level=compress_level,
        optimize=optimize,
    )
    return buffer.getvalue()


def _estimate_scale_for_budget(full_bytes: int, max_bytes: int) -> float:
    if full_bytes <= 0:
        return 1.0
    ratio = max_bytes / full_bytes
    return max(MIN_OUTPUT_SCALE, min(1.0, math.sqrt(ratio) * 0.95))


def _refine_scale_with_budget(
    img: Image.Image,
    max_bytes: int,
    initial_scale: float,
) -> Tuple[float, bytes]:
    low = MIN_OUTPUT_SCALE
    high = initial_scale
    best_scale = MIN_OUTPUT_SCALE
    best_data = _encode_png_bytes(_scaled_image(img, MIN_OUTPUT_SCALE), fast=True)

    if len(best_data) > max_bytes:
        return MIN_OUTPUT_SCALE, best_data

    for _ in range(_SCALE_REFINE_ITERATIONS):
        scale = (low + high) / 2
        candidate = _scaled_image(img, scale)
        data = _encode_png_bytes(candidate, fast=True)
        if len(data) <= max_bytes:
            best_scale = scale
            best_data = data
            low = scale
        else:
            high = scale

    return best_scale, best_data


def _fit_png_within_budget(
    img: Image.Image,
    max_bytes: int,
    *,
    source_label: str,
) -> Tuple[bytes, float, Optional[int]]:
    """Return PNG bytes within ``max_bytes``, preferring resize over color reduction."""
    full_size = _encode_png_bytes(img)
    if len(full_size) <= max_bytes:
        return full_size, 1.0, None

    probe_size = _encode_png_bytes(img, fast=True)
    estimated_scale = _estimate_scale_for_budget(len(probe_size), max_bytes)
    best_scale, _ = _refine_scale_with_budget(img, max_bytes, estimated_scale)

    final = _encode_png_bytes(_scaled_image(img, best_scale))
    if len(final) <= max_bytes:
        if best_scale < 0.999:
            fitted = _scaled_image(img, best_scale)
            log.debug(
                "Scaled %s to %.0f%% (%dx%d) to stay within size budget",
                source_label,
                best_scale * 100,
                fitted.width,
                fitted.height,
            )
        return final, best_scale, None

    smaller_scale = max(MIN_OUTPUT_SCALE, best_scale * 0.9)
    final = _encode_png_bytes(_scaled_image(img, smaller_scale))
    if len(final) <= max_bytes:
        return final, smaller_scale, None

    smallest = _scaled_image(img, MIN_OUTPUT_SCALE)
    for colors in _QUANTIZE_COLOR_STEPS:
        data = _encode_png_bytes(smallest, colors=colors)
        if len(data) <= max_bytes:
            log.warning(
                "Reduced colors to %s for %s after resize (%.0f%%) to meet size budget",
                colors,
                source_label,
                MIN_OUTPUT_SCALE * 100,
            )
            return data, MIN_OUTPUT_SCALE, colors

    raise ValueError(
        f"Could not fit {source_label} within {max_bytes} bytes "
        f"using resize-first heuristics"
    )


def _max_output_bytes(source: Path, max_page_size_mb: Optional[float] = None) -> int:
    try:
        original_size = source.stat().st_size
    except OSError:
        original_size = 0

    budgets: List[int] = []
    if original_size > 0:
        budgets.append(max(int(original_size * MAX_OUTPUT_SIZE_RATIO), 1024))
    if max_page_size_mb is not None and max_page_size_mb > 0:
        budgets.append(max(int(max_page_size_mb * 1024 * 1024), 1024))

    if not budgets:
        return 512 * 1024
    return min(budgets)


def _save_image_as_png(
    source: Path,
    destination: Path,
    *,
    max_page_size_mb: Optional[float] = None,
) -> bool:
    if Image is None:
        log.error("Pillow not installed; cannot write PNG")
        return False
    try:
        max_bytes = _max_output_bytes(source, max_page_size_mb)
        with Image.open(source) as img:
            prepared = _prepare_png_image(img)
            png_bytes, scale, colors = _fit_png_within_budget(
                prepared,
                max_bytes,
                source_label=source.name,
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(png_bytes)
        output_size = destination.stat().st_size
        if output_size > max_bytes:
            raise ValueError(
                f"Converted PNG {destination.name} is {output_size} bytes, "
                f"over budget of {max_bytes} bytes"
            )
        if scale < 0.999 or colors is not None:
            log.debug(
                "Converted %s -> %s (%d -> %d bytes, scale=%.2f, colors=%s)",
                source.name,
                destination.name,
                source.stat().st_size,
                output_size,
                scale,
                colors,
            )
        return output_size > 0
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


def _conversion_destination(image: Path) -> Path:
    destination = image.with_suffix(TARGET_PAGE_EXTENSION)
    if destination.exists() and destination.resolve() != image.resolve():
        destination = _unique_path(image.parent, destination.name)
    return destination


def _convert_page_task(
    args: Tuple[str, str, Optional[float]],
) -> Tuple[str, bool, Optional[str]]:
    """Worker entry point for parallel page conversion."""
    source_str, destination_str, max_page_size_mb = args
    source = Path(source_str)
    destination = Path(destination_str)
    try:
        ok = _save_image_as_png(
            source,
            destination,
            max_page_size_mb=max_page_size_mb,
        )
        if not ok:
            return source_str, False, "conversion failed"
        return source_str, True, None
    except Exception as exc:
        return source_str, False, str(exc)


def default_worker_count() -> int:
    return max(1, os.cpu_count() or 4)


def _convert_image_to_png(
    image: Path,
    *,
    trash_dir: Optional[Path] = None,
    library_root: Optional[Path] = None,
    keep_original: bool = False,
    max_page_size_mb: Optional[float] = None,
) -> Optional[Path]:
    if image.suffix.lower() == TARGET_PAGE_EXTENSION:
        return image

    destination = _conversion_destination(image)

    if not _save_image_as_png(
        image,
        destination,
        max_page_size_mb=max_page_size_mb,
    ):
        return None

    if image.resolve() != destination.resolve() and not keep_original:
        if trash_dir is not None:
            if (
                move_to_trash(
                    image,
                    trash_dir=trash_dir,
                    library_root=library_root,
                )
                is None
            ):
                log.error("Failed moving original %s to trash", image)
                return None
        else:
            try:
                image.unlink()
            except OSError as exc:
                log.error("Failed removing original %s: %s", image, exc)
                return None

    return destination


def _convert_folder_images_to_png(
    folder: Path,
    *,
    trash_dir: Optional[Path] = None,
    library_root: Optional[Path] = None,
    keep_originals: bool = False,
    max_page_size_mb: Optional[float] = None,
    workers: int = 1,
) -> bool:
    images = [
        image
        for image in list_folder_images(folder)
        if image.suffix.lower() != TARGET_PAGE_EXTENSION
    ]
    if not images:
        return True

    if workers <= 1:
        for image in images:
            if (
                _convert_image_to_png(
                    image,
                    trash_dir=trash_dir,
                    library_root=library_root,
                    keep_original=keep_originals,
                    max_page_size_mb=max_page_size_mb,
                )
                is None
            ):
                return False
        return True

    tasks = [
        (
            str(image.resolve()),
            str(_conversion_destination(image).resolve()),
            max_page_size_mb,
        )
        for image in images
    ]

    failed: List[str] = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_convert_page_task, task) for task in tasks]
        for future in as_completed(futures):
            source_str, ok, error = future.result()
            if not ok:
                failed.append(f"{source_str}: {error or 'conversion failed'}")

    if failed:
        for message in failed[:5]:
            log.error("Parallel conversion failed: %s", message)
        return False

    if not keep_originals and trash_dir is not None:
        for image in images:
            if (
                move_to_trash(
                    image,
                    trash_dir=trash_dir,
                    library_root=library_root,
                )
                is None
            ):
                log.error("Failed moving original %s to trash", image)
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

    return destination


def _extract_archive_to_folder(
    archive_path: Path,
    output_dir: Path,
    *,
    max_page_size_mb: Optional[float] = None,
) -> List[Path]:
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
                            png_path = _convert_image_to_png(
                                temp_file,
                                max_page_size_mb=max_page_size_mb,
                            )
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
                            png_path = _convert_image_to_png(
                                temp_file,
                                max_page_size_mb=max_page_size_mb,
                            )
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


def _standardize_image_folder(
    folder: Path,
    *,
    trash_dir: Optional[Path] = None,
    library_root: Optional[Path] = None,
    keep_originals: bool = False,
    max_page_size_mb: Optional[float] = None,
    workers: int = 1,
) -> Optional[Path]:
    if needs_png_conversion(folder):
        if not _convert_folder_images_to_png(
            folder,
            trash_dir=trash_dir,
            library_root=library_root,
            keep_originals=keep_originals,
            max_page_size_mb=max_page_size_mb,
            workers=workers,
        ):
            return None
    return _rename_folder_to_standard(folder)


def rename_comic_folder_if_needed(folder: PathLike) -> Path:
    """Rename an image folder to the standard convention without prompting."""
    path = Path(folder)
    if not is_image_folder(path):
        return path
    return _rename_folder_to_standard(path)


def standardize_comic(
    input_path: PathLike,
    output_dir: Optional[PathLike] = None,
    *,
    trash_dir: Optional[PathLike] = None,
    library_root: Optional[PathLike] = None,
    keep_originals: bool = False,
    max_page_size_mb: Optional[float] = None,
    workers: int = 1,
) -> Optional[Path]:
    """Convert page images to size-budget PNG and rename comic folders to the standard convention."""
    src = Path(input_path)
    trash_path = Path(trash_dir) if trash_dir is not None else None
    library_path = Path(library_root).resolve() if library_root is not None else None
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
        return _standardize_image_folder(
            src,
            trash_dir=trash_path,
            library_root=library_path,
            keep_originals=keep_originals,
            max_page_size_mb=max_page_size_mb,
            workers=workers,
        )

    parent = Path(output_dir) if output_dir is not None else src.parent
    output = _unique_output_dir(parent, standard_folder_name(src))
    output.mkdir(parents=True, exist_ok=True)

    written = _extract_archive_to_folder(
        src,
        output,
        max_page_size_mb=max_page_size_mb,
    )
    if not written:
        shutil.rmtree(output, ignore_errors=True)
        return None

    return output


def standardize_comic_with_cleanup(
    input_path: PathLike,
    keep_originals: bool = False,
    trash_dir: Optional[PathLike] = None,
    library_root: Optional[PathLike] = None,
    max_page_size_mb: Optional[float] = None,
    workers: int = 1,
) -> Optional[Path]:
    src = Path(input_path)
    if not src.exists():
        return None

    output = standardize_comic(
        src,
        keep_originals=keep_originals,
        trash_dir=trash_dir,
        library_root=library_root,
        max_page_size_mb=max_page_size_mb,
        workers=workers,
    )
    if output is None:
        return None

    if keep_originals or src.resolve() == output.resolve():
        return output

    move_to_trash(src, trash_dir=trash_dir, library_root=library_root)
    return output


# Backwards-compatible aliases used by older imports.
normalize_comic_to_png = standardize_comic
normalize_comic_to_png_with_cleanup = standardize_comic_with_cleanup
