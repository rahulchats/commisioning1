"""
Confidence scoring for Goodreads match quality.

Weighted similarity across title, author, and series fields.
Penalty applied for known low-quality indicators (audiobook, abridged, etc.).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

# Weight distribution (must sum to 1.0)
_W_TITLE: float = 0.45
_W_AUTHOR: float = 0.35
_W_SERIES: float = 0.20

_LOW_QUALITY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\baudiobook\b", re.I),
    re.compile(r"\babridged\b", re.I),
    re.compile(r"\bboxed\s+set\b", re.I),
    re.compile(r"\bomnibus\b", re.I),
    re.compile(r"\bcollection\b", re.I),
    re.compile(r"\bcompendium\b", re.I),
    re.compile(r"\bgraphic\s+novel\b", re.I),
    re.compile(r"\bcomic\b", re.I),
]


def _normalise(text: str) -> str:
    """Lower-case, strip punctuation/articles for fuzzy comparison."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _similarity(a: str, b: str) -> float:
    """Token-set ratio in [0, 1]."""
    if not a or not b:
        return 0.0
    return fuzz.token_set_ratio(_normalise(a), _normalise(b)) / 100.0


def _has_low_quality_flag(text: str) -> bool:
    return any(p.search(text) for p in _LOW_QUALITY_PATTERNS)


@dataclass
class MatchScore:
    title_sim: float
    author_sim: float
    series_sim: float
    penalty: float
    raw_score: float
    final_score: float
    notes: list[str]


def score_match(
    query_title: str,
    query_author: str,
    query_series: str,
    result_title: str,
    result_author: str,
    result_series: str,
) -> MatchScore:
    """
    Compute weighted confidence for a Goodreads search result against query.

    Returns a MatchScore dataclass with individual components.
    """
    title_sim = _similarity(query_title, result_title)
    author_sim = _similarity(query_author, result_author)
    series_sim = _similarity(query_series, result_series) if query_series else 0.5

    raw = (
        title_sim * _W_TITLE
        + author_sim * _W_AUTHOR
        + series_sim * _W_SERIES
    )

    penalty = 0.0
    notes: list[str] = []

    combined_text = f"{result_title} {result_series}"
    if _has_low_quality_flag(combined_text):
        penalty += 0.25
        notes.append("low-quality edition flag")

    if title_sim < 0.3:
        notes.append(f"low title similarity ({title_sim:.2f})")
    if author_sim < 0.3:
        notes.append(f"low author similarity ({author_sim:.2f})")

    final = max(0.0, raw - penalty)

    return MatchScore(
        title_sim=round(title_sim, 3),
        author_sim=round(author_sim, 3),
        series_sim=round(series_sim, 3),
        penalty=round(penalty, 3),
        raw_score=round(raw, 3),
        final_score=round(final, 3),
        notes=notes,
    )
