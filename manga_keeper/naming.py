"""Standard folder naming for manga libraries."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')
TRAILING_COPY_NUMBER = re.compile(r"\(\s*\d+\s*\)\s*$")
STANDALONE_COPY = re.compile(r"(?:^|\s)copy(?:\s|$)", re.IGNORECASE)
STANDALONE_DUPLICATE = re.compile(r"(?:^|\s)duplicate(?:\s|$)", re.IGNORECASE)

_BRACKET_OPEN = "([（［【"
_BRACKET_CLOSE = ")]）］】"
_BRACKET_OPEN_RE = re.compile(
    rf"(?<=[^{re.escape(_BRACKET_OPEN)}\s])([{re.escape(_BRACKET_OPEN)}])"
)
_BRACKET_CLOSE_RE = re.compile(
    rf"([{re.escape(_BRACKET_CLOSE)}])(?=[^{re.escape(_BRACKET_CLOSE + _BRACKET_OPEN)}\s])"
)


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


def standardize_folder_name(name: str) -> str:
    """Return a cleaned, filesystem-safe folder name."""
    cleaned = INVALID_FILENAME_CHARS.sub("", name.strip())
    cleaned = TRAILING_COPY_NUMBER.sub("", cleaned).strip()
    cleaned = _remove_noise_tokens(cleaned)
    cleaned = _normalize_bracket_spacing(cleaned)
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
