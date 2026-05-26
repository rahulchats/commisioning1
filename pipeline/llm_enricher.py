"""
LLM enrichment module using xAI / Grok API.

Used ONLY for:
  - Author nationality
  - Subgenre classification
  - Trope extraction

Never used for URL discovery, ratings, or factual data retrieval.
All prompts, raw responses, and parsed outputs are logged to the Audit sheet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from config import (
    LLM_SEMAPHORE_COUNT,
    XAI_API_KEY,
    XAI_BASE_URL,
    XAI_MAX_TOKENS,
    XAI_MODEL,
    XAI_TEMPERATURE,
)

logger = logging.getLogger(__name__)

# Shared semaphore (set at module level, applied per-call)
_llm_semaphore: asyncio.Semaphore | None = None


def get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(LLM_SEMAPHORE_COUNT)
    return _llm_semaphore


@dataclass
class LLMResult:
    nationality: str = ""
    subgenre: str = ""
    tropes: str = ""
    llm_confidence: float = 0.0
    # Audit fields
    prompt: str = ""
    raw_response: str = ""
    parsed_response: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _build_prompt(
    author: str,
    series: str,
    title: str,
    genres: list[str],
    description: str,
) -> str:
    genre_str = ", ".join(genres[:5]) if genres else "unknown"
    desc_snippet = description[:500].strip() if description else ""
    return f"""You are a book metadata specialist. Given the following book information, provide structured enrichment.

Book: "{title}" (Series: "{series}")
Author: {author}
Goodreads Genres: {genre_str}
Description snippet: {desc_snippet}

Return a JSON object with EXACTLY these keys:
{{
  "nationality": "<author's country of nationality, e.g. 'American', 'British', 'Polish'>",
  "subgenre": "<one specific subgenre label, e.g. 'Epic Fantasy', 'Military Sci-Fi', 'Cozy Mystery'>",
  "tropes": "<comma-separated list of up to 6 major tropes present in this series>",
  "confidence": <float 0.0-1.0 representing your confidence in these classifications>
}}

Rules:
- Use only publicly known facts; do NOT invent information.
- If uncertain about nationality, return "Unknown".
- If uncertain about subgenre, pick the closest established label.
- Tropes should be recognisable genre tropes (e.g. "Chosen One, Found Family, Dark Lord, Magic System").
- Return ONLY the JSON object, no other text.
"""


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

async def enrich_with_llm(
    author: str,
    series: str,
    title: str,
    genres: list[str],
    description: str,
    row_index: int,
) -> LLMResult:
    """
    Call xAI Grok API for nationality/subgenre/tropes enrichment.
    Respects the global LLM semaphore for concurrency control.
    """
    result = LLMResult()
    prompt = _build_prompt(author, series, title, genres, description)
    result.prompt = prompt

    client = AsyncOpenAI(api_key=XAI_API_KEY, base_url=XAI_BASE_URL)

    async with get_llm_semaphore():
        logger.info("[row %d] Calling LLM for enrichment (author=%s)", row_index, author)
        t0 = time.monotonic()
        try:
            response = await client.chat.completions.create(
                model=XAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=XAI_TEMPERATURE,
                max_tokens=XAI_MAX_TOKENS,
            )
            elapsed = time.monotonic() - t0
            raw = response.choices[0].message.content or ""
            result.raw_response = raw
            logger.debug("[row %d] LLM response in %.2fs: %s", row_index, elapsed, raw[:200])

            parsed = _parse_llm_response(raw)
            result.parsed_response = parsed
            result.nationality = parsed.get("nationality", "")
            result.subgenre = parsed.get("subgenre", "")
            result.tropes = parsed.get("tropes", "")
            raw_conf = parsed.get("confidence", 0.0)
            result.llm_confidence = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0

        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.error("[row %d] LLM call failed after %.2fs: %s", row_index, elapsed, exc)
            result.error = str(exc)

    return result


def _parse_llm_response(raw: str) -> dict[str, Any]:
    """
    Extract JSON from LLM response. Handles markdown code fences and
    partial JSON gracefully. Returns empty dict on failure.
    """
    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object with regex
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse LLM JSON: %s", raw[:300])
    return {}
