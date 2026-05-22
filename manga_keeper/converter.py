"""Convert assorted comic formats to CBZ (ZIP of ordered images)."""

from __future__ import annotations

import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from .utils import list_archive_images, move_to_trash

PathLike = Union[str, Path]

log = logging.getLogger(__name__)

TARGET_EXTENSION = ".cbz"

_IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
)


def _natural_key(name: str) -> List:
    import re

    parts = re.split(r"(\d+)", name.lower())
    return [int(p) if p.isdigit() else p for p in parts]


def needs_conversion(file_path: PathLike) -> bool:
    path = Path(file_path)
    return path.suffix.lower() != TARGET_EXTENSION


def _write_cbz(image_paths: List[Path], output: Path) -> bool:
    try:
        with zipfile.ZipFile(
            output, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for index, image in enumerate(image_paths, start=1):
                arcname = f"{index:04d}{image.suffix.lower()}"
                archive.write(image, arcname=arcname)
        return output.exists() and output.stat().st_size > 0
    except OSError as exc:
        log.error("Failed writing CBZ %s: %s", output, exc)
        return False


def _extract_images_to_temp(file_path: Path, temp_dir: Path) -> List[Path]:
    suffix = file_path.suffix.lower()
    extracted: List[Path] = []

    if suffix in {".cbz", ".cbr", ".zip"}:
        names = list_archive_images(file_path)
        if suffix == ".cbr":
            try:
                import rarfile
            except ImportError:
                log.error("rarfile not installed; cannot convert %s", file_path)
                return []
            try:
                with rarfile.RarFile(file_path) as archive:
                    for index, name in enumerate(names):
                        data = archive.read(name)
                        out = temp_dir / f"{index:04d}{Path(name).suffix.lower()}"
                        out.write_bytes(data)
                        extracted.append(out)
            except (rarfile.Error, OSError) as exc:
                log.error("Failed extracting CBR %s: %s", file_path, exc)
                return []
        else:
            try:
                with zipfile.ZipFile(file_path) as archive:
                    for index, name in enumerate(names):
                        data = archive.read(name)
                        out = temp_dir / f"{index:04d}{Path(name).suffix.lower()}"
                        out.write_bytes(data)
                        extracted.append(out)
            except (zipfile.BadZipFile, OSError) as exc:
                log.error("Failed extracting archive %s: %s", file_path, exc)
                return []
        return sorted(extracted, key=lambda p: _natural_key(p.name))

    if suffix in {".pdf", ".epub"}:
        try:
            import fitz
        except ImportError:
            log.error("pymupdf not installed; cannot convert %s", file_path)
            return []
        try:
            doc = fitz.open(file_path)
        except Exception as exc:
            log.error("Cannot open document %s: %s", file_path, exc)
            return []
        try:
            for index in range(doc.page_count):
                page = doc.load_page(index)
                pix = page.get_pixmap()
                out = temp_dir / f"{index:04d}.png"
                pix.save(str(out))
                extracted.append(out)
        finally:
            doc.close()
        return extracted

    return []


def convert_to_cbz(
    input_path: PathLike,
    output_dir: Optional[PathLike] = None,
) -> Optional[Path]:
    src = Path(input_path)
    if not src.exists() or not src.is_file():
        log.warning("Cannot convert missing file: %s", src)
        return None

    if not needs_conversion(src):
        return src

    target_dir = Path(output_dir) if output_dir is not None else src.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    output = target_dir / f"{src.stem}{TARGET_EXTENSION}"
    counter = 1
    while output.exists():
        output = target_dir / f"{src.stem}.converted_{counter}{TARGET_EXTENSION}"
        counter += 1

    temp_dir = Path(tempfile.mkdtemp(prefix="manga_keeper_convert_"))
    try:
        images = _extract_images_to_temp(src, temp_dir)
        if not images:
            log.error("No pages extracted from %s", src)
            return None

        print(f"Converting: {src.name} -> {output.name} ({len(images)} pages)")
        if not _write_cbz(images, output):
            return None
        return output
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def convert_all(
    file_paths: Iterable[PathLike],
    dry_run: bool = False,
    keep_originals: bool = False,
    trash_dir: Optional[PathLike] = None,
) -> List[Tuple[Path, Optional[Path]]]:
    results: List[Tuple[Path, Optional[Path]]] = []
    paths = [Path(p) for p in file_paths]
    print(f"Evaluating {len(paths)} file(s) for conversion ...")

    for index, path in enumerate(paths, start=1):
        prefix = f"[{index}/{len(paths)}]"
        if not path.exists():
            log.warning("%s missing, skipping: %s", prefix, path)
            continue

        if not needs_conversion(path):
            print(f"{prefix} OK (already CBZ): {path.name}")
            results.append((path, None))
            continue

        if dry_run:
            print(f"{prefix} DRY RUN — would convert: {path.name}")
            results.append((path, None))
            continue

        output = convert_to_cbz(path)
        if output is None:
            print(f"{prefix} FAILED to convert: {path.name}")
            results.append((path, None))
            continue

        print(f"{prefix} converted -> {output.name}")
        if not keep_originals and output.resolve() != path.resolve():
            move_to_trash(path, trash_dir=trash_dir)
        results.append((path, output))

    return results
