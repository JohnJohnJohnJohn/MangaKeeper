"""Detect and merge contiguous episode folders into combined comics."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .naming import standardize_folder_name
from .utils import is_image_folder, list_folder_images, move_to_trash

log = logging.getLogger(__name__)

# Skip folders that already represent a merged episode range.
_ALREADY_MERGED = re.compile(
    r"(?:_episode_|_ep_|_chapter_|_ch_|_vol_|\s)(\d+)to(\d+)\s*$",
    re.IGNORECASE,
)

_EPISODE_PATTERNS = (
    re.compile(r"^(?P<prefix>.+?_episode_)(?P<num>\d+)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.+?_ep_)(?P<num>\d+)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.+?_chapter_)(?P<num>\d+)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.+?_ch_)(?P<num>\d+)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.+?_vol_)(?P<num>\d+)$", re.IGNORECASE),
    re.compile(r"^(?P<prefix>.+ )(?P<num>\d+)$"),
    re.compile(r"^(?P<prefix>.+-)(?P<num>\d+)$"),
    re.compile(r"^(?P<prefix>.+#)(?P<num>\d+)$"),
    re.compile(r"^(?P<prefix>.+第)(?P<num>\d+)(?P<suffix>[话話集卷])$"),
)


@dataclass(frozen=True)
class EpisodeNameParts:
    prefix: str
    suffix: str
    episode: int


@dataclass(frozen=True)
class EpisodeInfo:
    folder: Path
    prefix: str
    suffix: str
    episode: int

    @property
    def parent(self) -> Path:
        return self.folder.parent

    @property
    def group_key(self) -> Tuple[str, str, str]:
        return (str(self.parent.resolve()), self.prefix, self.suffix)


@dataclass(frozen=True)
class EpisodeMergeGroup:
    episodes: Tuple[EpisodeInfo, ...]

    @property
    def parent(self) -> Path:
        return self.episodes[0].parent

    @property
    def start(self) -> int:
        return self.episodes[0].episode

    @property
    def end(self) -> int:
        return self.episodes[-1].episode

    @property
    def source_folders(self) -> List[Path]:
        return [info.folder for info in self.episodes]

    def raw_merged_name(self) -> str:
        prefix = self.episodes[0].prefix
        suffix = self.episodes[0].suffix
        return f"{prefix}{self.start}to{self.end}{suffix}"

    def merged_name(self) -> str:
        return standardize_folder_name(self.raw_merged_name())

    def total_pages(self) -> int:
        return sum(len(list_folder_images(info.folder)) for info in self.episodes)


def parse_episode_name(name: str) -> Optional[EpisodeNameParts]:
    """Return parsed episode naming parts when ``name`` looks like one episode."""
    if _ALREADY_MERGED.search(name):
        return None

    for pattern in _EPISODE_PATTERNS:
        match = pattern.match(name)
        if not match:
            continue
        suffix = match.groupdict().get("suffix") or ""
        return EpisodeNameParts(
            prefix=match.group("prefix"),
            suffix=suffix,
            episode=int(match.group("num")),
        )

    return None


def parse_episode_folder(folder: Path) -> Optional[EpisodeInfo]:
    """Return episode metadata when ``folder`` looks like a single episode."""
    if not is_image_folder(folder):
        return None

    parsed = parse_episode_name(folder.name)
    if parsed is None:
        return None

    return EpisodeInfo(
        folder=folder.resolve(),
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        episode=parsed.episode,
    )


def _find_contiguous_runs(episodes: Sequence[EpisodeInfo]) -> List[List[EpisodeInfo]]:
    if len(episodes) < 2:
        return []

    ordered = sorted(episodes, key=lambda item: item.episode)
    runs: List[List[EpisodeInfo]] = []
    current = [ordered[0]]

    for item in ordered[1:]:
        if item.episode == current[-1].episode + 1:
            current.append(item)
            continue
        if len(current) >= 2:
            runs.append(current)
        current = [item]

    if len(current) >= 2:
        runs.append(current)
    return runs


def find_episode_merge_groups(comics: Iterable[Path]) -> List[EpisodeMergeGroup]:
    """Find contiguous episode folders that can be merged."""
    grouped: dict[Tuple[str, str, str], List[EpisodeInfo]] = {}

    for comic in comics:
        info = parse_episode_folder(Path(comic))
        if info is None:
            continue
        grouped.setdefault(info.group_key, []).append(info)

    merge_groups: List[EpisodeMergeGroup] = []
    for episodes in grouped.values():
        for run in _find_contiguous_runs(episodes):
            merge_groups.append(EpisodeMergeGroup(episodes=tuple(run)))

    merge_groups.sort(
        key=lambda group: (
            str(group.parent).lower(),
            group.episodes[0].prefix.lower(),
            group.episodes[0].suffix.lower(),
            group.start,
        )
    )
    return merge_groups


def unique_folder_path(parent: Path, folder_name: str) -> Path:
    candidate = parent / folder_name
    counter = 2
    while candidate.exists():
        candidate = parent / f"{folder_name}__{counter}"
        counter += 1
    return candidate


def _prefixed_page_name(episode: int, original_name: str) -> str:
    return f"e{episode:02d}_{original_name}"


def _unique_file_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 2
    while True:
        candidate = directory / f"{stem}__{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def merge_episode_group(
    group: EpisodeMergeGroup,
    *,
    trash_dir: Path,
    library_root: Optional[Path] = None,
    dry_run: bool = False,
) -> Optional[Path]:
    """Merge contiguous episode folders into one combined folder."""
    merged_name = group.merged_name()
    output_dir = unique_folder_path(group.parent, merged_name)

    if dry_run:
        return output_dir

    output_dir.mkdir(parents=False, exist_ok=False)
    moved_sources: List[Path] = []

    try:
        for info in group.episodes:
            for image in list_folder_images(info.folder):
                dest_name = _prefixed_page_name(info.episode, image.name)
                destination = _unique_file_path(output_dir, dest_name)
                shutil.move(str(image), str(destination))

        for source in group.source_folders:
            moved = move_to_trash(
                source,
                trash_dir=trash_dir,
                library_root=library_root,
            )
            if moved is None:
                raise OSError(f"failed to trash source folder: {source}")
            moved_sources.append(source)
    except Exception:
        log.exception("Episode merge failed for %s", group.raw_merged_name())
        if output_dir.exists():
            for leftover in list_folder_images(output_dir):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            try:
                output_dir.rmdir()
            except OSError:
                pass
        raise

    return output_dir
