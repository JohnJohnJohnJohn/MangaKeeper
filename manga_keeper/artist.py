"""Artist tag extraction and style-based artist suggestions."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .index import DEFAULT_PERCEPTUAL_PAGES, compute_content_fingerprint
from .perceptual import _best_match_details, _deserialize_phash, compute_comic_signature

if TYPE_CHECKING:
    from .index import ComicIndex

PathLike = Union[str, Path]

_BRACKET_PAIRS = {
    "[": "]",
    "(": ")",
    "（": "）",
    "【": "】",
    "［": "］",
}

_METADATA_MARKERS = (
    "chinese",
    "english",
    "digital",
    "complete",
    "ongoing",
    "pixiv",
    "twitter",
    "ai generated",
    "中国",
    "翻译",
    "翻訳",
    "汉化",
    "漢化",
    "dl版",
)


@dataclass(frozen=True)
class ArtistSuggestion:
    artist: str
    avg_distance: float
    good_ratio: float
    sample_count: int
    confidence: str
    margin: float


def is_metadata_tag(tag: str) -> bool:
    lowered = tag.casefold()
    return any(marker.casefold() in lowered for marker in _METADATA_MARKERS)


def _extract_next_bracket_tag(name: str) -> tuple[Optional[str], str]:
    stripped = name.lstrip()
    if not stripped:
        return None, name

    opener = stripped[0]
    closer = _BRACKET_PAIRS.get(opener)
    if closer is None:
        return None, name

    close_index = stripped.find(closer, 1)
    if close_index == -1:
        return None, name

    tag = stripped[1:close_index].strip()
    remainder = stripped[close_index + 1 :].lstrip()
    return tag or None, remainder


def extract_artist_tag(name: str) -> Optional[str]:
    """Return the leading non-metadata bracket tag from a folder name, if any."""
    remaining = name.strip()
    while remaining:
        tag, remaining = _extract_next_bracket_tag(remaining)
        if tag is None:
            return None
        if not is_metadata_tag(tag):
            return tag
    return None


def comic_title_without_tag(name: str) -> str:
    remaining = name.strip()
    while remaining:
        tag, remaining = _extract_next_bracket_tag(remaining)
        if tag is None:
            break
    return remaining or name.strip()


def is_untagged_comic(path: PathLike) -> bool:
    return extract_artist_tag(Path(path).name) is None


def collect_comic_signatures(
    comics: Iterable[PathLike],
    index: Optional["ComicIndex"] = None,
    *,
    use_cache: bool = True,
    num_pages: int = DEFAULT_PERCEPTUAL_PAGES,
) -> Dict[Path, List]:
    signatures: Dict[Path, List] = {}
    for path in comics:
        resolved = Path(path).resolve()
        record = index.get(resolved) if index is not None else None
        signature: Optional[List] = None

        if (
            use_cache
            and index is not None
            and record
            and record.perceptual_hashes
            and record.perceptual_num_pages == num_pages
            and index.is_content_cache_hit(resolved)
        ):
            signature = [
                deserialized
                for value in record.perceptual_hashes
                if (deserialized := _deserialize_phash(value)) is not None
            ]

        if not signature:
            signature = compute_comic_signature(resolved, num_pages=num_pages)
            if signature and index is not None:
                from .utils import get_comic_metadata

                meta = get_comic_metadata(resolved) or {}
                index.upsert(
                    resolved,
                    content_fingerprint=compute_content_fingerprint(resolved),
                    page_count=int(meta.get("page_count") or 0),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    perceptual_hashes=[str(value) for value in signature],
                    perceptual_num_pages=num_pages,
                )

        if signature:
            signatures[resolved] = signature

    return signatures


def build_artist_profiles(
    signatures: Dict[Path, Sequence],
    *,
    min_samples: int = 3,
) -> Dict[str, List[Sequence]]:
    profiles: Dict[str, List[Sequence]] = {}
    for path, signature in signatures.items():
        artist = extract_artist_tag(path.name)
        if not artist:
            continue
        profiles.setdefault(artist, []).append(signature)

    return {
        artist: profile_signatures
        for artist, profile_signatures in profiles.items()
        if len(profile_signatures) >= min_samples
    }


def _score_against_profile(
    candidate: Sequence,
    profile_signatures: Sequence[Sequence],
) -> Tuple[float, float]:
    distances: List[float] = []
    ratios: List[float] = []
    for reference in profile_signatures:
        avg_distance, good_ratio = _best_match_details(candidate, reference)
        distances.append(avg_distance)
        ratios.append(good_ratio)
    return statistics.median(distances), statistics.median(ratios)


def _confidence_label(
    avg_distance: float,
    good_ratio: float,
    sample_count: int,
    margin: float,
    threshold: int,
) -> str:
    if (
        avg_distance <= max(8, threshold - 4)
        and good_ratio >= 0.6
        and sample_count >= 5
        and margin >= 2.0
    ):
        return "high"
    if avg_distance <= threshold and good_ratio >= 0.45 and margin >= 1.5:
        return "medium"
    if avg_distance <= threshold and good_ratio >= 0.35:
        return "low"
    return "none"


def suggest_artist_for_comic(
    signature: Sequence,
    profiles: Dict[str, List[Sequence]],
    *,
    threshold: int = 12,
) -> Optional[ArtistSuggestion]:
    if not profiles:
        return None

    scored: List[Tuple[str, float, float, int]] = []
    for artist, profile_signatures in profiles.items():
        avg_distance, good_ratio = _score_against_profile(signature, profile_signatures)
        scored.append((artist, avg_distance, good_ratio, len(profile_signatures)))

    scored.sort(key=lambda item: (item[1], -item[2], -item[3], item[0].lower()))
    best_artist, best_distance, best_ratio, sample_count = scored[0]
    second_distance = scored[1][1] if len(scored) > 1 else float("inf")
    margin = second_distance - best_distance

    confidence = _confidence_label(
        best_distance,
        best_ratio,
        sample_count,
        margin,
        threshold,
    )
    if confidence == "none":
        return None

    return ArtistSuggestion(
        artist=best_artist,
        avg_distance=best_distance,
        good_ratio=best_ratio,
        sample_count=sample_count,
        confidence=confidence,
        margin=margin,
    )


def suggest_artists_for_untagged(
    comics: Iterable[PathLike],
    signatures: Dict[Path, Sequence],
    profiles: Dict[str, List[Sequence]],
    *,
    threshold: int = 12,
) -> List[Tuple[Path, ArtistSuggestion]]:
    suggestions: List[Tuple[Path, ArtistSuggestion]] = []
    for path in comics:
        resolved = Path(path).resolve()
        if not is_untagged_comic(resolved):
            continue
        signature = signatures.get(resolved)
        if not signature:
            continue
        suggestion = suggest_artist_for_comic(
            signature,
            profiles,
            threshold=threshold,
        )
        if suggestion is not None:
            suggestions.append((resolved, suggestion))

    suggestions.sort(
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}.get(item[1].confidence, 3),
            item[1].avg_distance,
            item[0].name.lower(),
        )
    )
    return suggestions


def proposed_tagged_name(folder_name: str, artist: str) -> str:
    title = comic_title_without_tag(folder_name)
    if title == folder_name.strip():
        return f"[{artist}] {title}"
    return f"[{artist}] {title}"
