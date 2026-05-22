"""Standard folder naming for manga libraries."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
TRAILING_COPY_NUMBER = re.compile(r"\(\s*\d+\s*\)\s*$")
STANDALONE_COPY = re.compile(r"(?:^|\s)copy(?:\s|$)", re.IGNORECASE)
STANDALONE_DUPLICATE = re.compile(r"(?:^|\s)duplicate(?:\s|$)", re.IGNORECASE)

# Map fullwidth and CJK bracket variants to ASCII square brackets / parentheses.
_BRACKET_CHAR_MAP = str.maketrans(
    {
        "【": "[",
        "】": "]",
        "［": "[",
        "］": "]",
        "「": "[",
        "」": "]",
        "『": "[",
        "』": "]",
        "（": "(",
        "）": ")",
    }
)

_BRACKET_OPEN = "(["
_BRACKET_CLOSE = ")]"
_BRACKET_OPEN_RE = re.compile(
    rf"(?<=[^{re.escape(_BRACKET_OPEN)}\s])([{re.escape(_BRACKET_OPEN)}])"
)
_BRACKET_CLOSE_RE = re.compile(
    rf"([{re.escape(_BRACKET_CLOSE)}])(?=[^{re.escape(_BRACKET_CLOSE + _BRACKET_OPEN)}\s])"
)

_TAG_DELIMITED = re.compile(r"(\[[^\]]*\]|\([^)]*\))")

_TAG_OPTIONAL_SUFFIXES = (
    re.compile(r"\s+version\.?$", re.IGNORECASE),
    re.compile(r"\s+ver\.?$", re.IGNORECASE),
    re.compile(r"\s+版$"),
)

_LANGUAGE_TAG_CODES = {
    "chinese": "chn",
    "china": "chn",
    "chs": "chn",
    "cht": "chn",
    "chn": "chn",
    "cn": "chn",
    "中文": "chn",
    "中国语": "chn",
    "english": "eng",
    "eng": "eng",
    "en": "eng",
}

_DROPPABLE_BRACKET_TAGS = frozenset(
    {
        "decensored",
        "decensor",
        "uncensored",
        "censored",
        "censorship",
        "digital",
        "digital ver",
        "digital version",
        "scan",
        "scanned",
        "scanlation",
        "complete",
        "completed",
        "ongoing",
        "end",
        "raw",
        "pixiv",
        "twitter",
        "fanbox",
        "patreon",
        "ai generated",
        "ai-generated",
        "dl",
        "dl版",
        "mtl",
    }
)

_TRANSLATOR_MARKERS = (
    "汉化",
    "漢化",
    "翻译",
    "翻訳",
    "嵌字",
    "扫图",
    "掃圖",
    "修图",
    "修圖",
    "校对",
    "校對",
)

# Match episode suffixes so title casing leaves markers/numbers untouched.
_EPISODE_SUFFIX_PATTERNS = (
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

_EPISODE_MARKERS = ("_episode_", "_ep_", "_chapter_", "_ch_", "_vol_")


def _normalize_bracket_characters(name: str) -> str:
    """Convert bracket variants to ASCII [] and ()."""
    return name.translate(_BRACKET_CHAR_MAP)


def _normalize_bracket_spacing(name: str) -> str:
    """Ensure a single space around bracket and parenthesis groups."""
    spaced = _BRACKET_OPEN_RE.sub(r" \1", name)
    spaced = _BRACKET_CLOSE_RE.sub(r"\1 ", spaced)
    return spaced


def _remove_noise_tokens(name: str) -> str:
    cleaned = STANDALONE_COPY.sub(" ", name)
    return STANDALONE_DUPLICATE.sub(" ", cleaned)


def _collapse_spaces(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip(" .")


def _strip_optional_tag_suffix(tag: str) -> str:
    """Remove optional trailing suffixes like 'version' or 'ver' from tag text."""
    stripped = tag.strip()
    while True:
        updated = stripped
        for pattern in _TAG_OPTIONAL_SUFFIXES:
            updated = pattern.sub("", updated).strip()
        if updated == stripped:
            break
        stripped = updated
    return stripped


def _shorten_language_tag(tag: str) -> Optional[str]:
    """Map language bracket tags to short codes like chn/eng."""
    stripped = _strip_optional_tag_suffix(tag.strip())
    if not stripped:
        return None

    lowered = stripped.casefold()
    if lowered in _LANGUAGE_TAG_CODES:
        return _LANGUAGE_TAG_CODES[lowered]
    if stripped in _LANGUAGE_TAG_CODES:
        return _LANGUAGE_TAG_CODES[stripped]
    return None


def _is_translator_tag(tag: str) -> bool:
    return any(marker in tag for marker in _TRANSLATOR_MARKERS)


def _should_drop_bracket_tag(tag: str) -> bool:
    candidates = {tag.strip().casefold(), _strip_optional_tag_suffix(tag).casefold()}
    for lowered in candidates:
        if not lowered:
            return True
        if lowered in _DROPPABLE_BRACKET_TAGS:
            return True
        if _is_translator_tag(tag):
            return True
    return False


def _normalize_bracket_tag_content(tag: str) -> Optional[str]:
    """Return normalized tag text, or None when the tag should be removed."""
    stripped = tag.strip()
    if not stripped:
        return None

    core = _strip_optional_tag_suffix(stripped)

    language_code = _shorten_language_tag(stripped)
    if language_code is not None:
        return language_code
    if _should_drop_bracket_tag(stripped):
        return None
    return core.casefold()


def _normalize_delimited_tags(name: str) -> str:
    """Normalize [...] and (...) tags with the same rules."""

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        opener, closer = token[0], token[-1]
        normalized = _normalize_bracket_tag_content(token[1:-1])
        if normalized is None:
            return " "
        return f"{opener}{normalized}{closer}"

    return _TAG_DELIMITED.sub(replace, name)


def _title_case_ascii_word(word: str) -> str:
    chars = list(word)
    for index, char in enumerate(chars):
        if char.isalpha() and char.isascii():
            chars[index] = char.upper()
            for rest in range(index + 1, len(chars)):
                if chars[rest].isalpha() and chars[rest].isascii():
                    chars[rest] = chars[rest].lower()
            break
    return "".join(chars)


def _title_case_token(token: str) -> str:
    if not token:
        return token
    if "_" in token:
        return "_".join(_title_case_ascii_word(part) for part in token.split("_"))
    return _title_case_ascii_word(token)


def _title_case_text(text: str) -> str:
    if not text:
        return text
    return " ".join(_title_case_token(token) for token in text.split(" "))


def _title_case_title_portion(prefix: str) -> str:
    lowered = prefix.casefold()
    for marker in _EPISODE_MARKERS:
        if lowered.endswith(marker):
            title = prefix[: -len(marker)]
            return f"{_title_case_text(title)}{marker.lower()}"
    return _title_case_text(prefix)


def _title_case_episode_aware(text: str) -> str:
    for pattern in _EPISODE_SUFFIX_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        prefix = match.group("prefix")
        suffix = text[len(prefix) :]
        return f"{_title_case_title_portion(prefix)}{suffix}"
    return _title_case_text(text)


def _title_case_segment(segment: str) -> str:
    if not segment or not segment.strip():
        return segment

    leading = segment[: len(segment) - len(segment.lstrip(" "))]
    trailing = segment[len(segment.rstrip(" ")) :]
    core = segment.strip()
    return f"{leading}{_title_case_episode_aware(core)}{trailing}"


def _title_case_outside_tags(name: str) -> str:
    """Title-case manga words outside [...] and (...) tags."""
    parts = _TAG_DELIMITED.split(name)
    return "".join(
        part if part and part[0] in "[(" else _title_case_segment(part)
        for part in parts
    )


def standardize_folder_name(name: str) -> str:
    """Return a cleaned, filesystem-safe folder name."""
    cleaned = INVALID_FILENAME_CHARS.sub("", name.strip())
    cleaned = TRAILING_COPY_NUMBER.sub("", cleaned).strip()
    cleaned = _remove_noise_tokens(cleaned)
    cleaned = _normalize_bracket_characters(cleaned)
    cleaned = _normalize_bracket_spacing(cleaned)
    cleaned = _collapse_spaces(cleaned)
    cleaned = _normalize_delimited_tags(cleaned)
    cleaned = _title_case_outside_tags(cleaned)
    cleaned = _collapse_spaces(cleaned)
    return cleaned or name.strip()


def standard_folder_name(path: PathLike) -> str:
    target = Path(path)
    raw_name = target.name if target.is_dir() else target.stem
    return standardize_folder_name(raw_name)


def folder_name_is_standard(path: PathLike) -> bool:
    target = Path(path)
    if not target.is_dir():
        return True
    return target.name == standard_folder_name(target)
