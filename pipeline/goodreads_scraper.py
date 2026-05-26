"""
Goodreads scraper using async Playwright.

Responsibilities:
  - Build search query from (title, series, author)
  - Scrape search results page, extract candidate book entries
  - Score each candidate with confidence module
  - Open best-match book page and scrape: rating, rating_count, genres, description
  - Attempt to find canonical series page URL
  - Never use LLM — deterministic extraction only
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
    async_playwright,
)

from confidence import MatchScore, score_match
from config import (
    CONFIDENCE_ACCEPT_THRESHOLD,
    GOODREADS_BASE,
    GOODREADS_SEARCH_URL,
    MAX_RETRIES,
    REQUEST_DELAY_MAX,
    REQUEST_DELAY_MIN,
    RETRY_BACKOFF,
    USER_AGENTS,
)

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    series_url: str = ""
    book_url: str = ""
    rating: str = ""
    rating_count: str = ""
    genres: list[str] = field(default_factory=list)
    description: str = ""
    confidence: float = 0.0
    match_source: str = ""
    audit_notes: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "series_url": self.series_url,
            "book_url": self.book_url,
            "rating": self.rating,
            "rating_count": self.rating_count,
            "genres": self.genres,
            "description": self.description,
            "confidence": self.confidence,
            "match_source": self.match_source,
            "audit_notes": self.audit_notes,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_ua() -> str:
    return random.choice(USER_AGENTS)


def _random_delay() -> float:
    return random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX)


def _build_search_query(title: str, series: str, author: str) -> str:
    parts = [p for p in [title, series, author] if p.strip()]
    return " ".join(parts)


def _extract_search_results(html: str) -> list[dict[str, str]]:
    """
    Parse Goodreads search results page.
    Returns list of {title, author, series, url, minirating} dicts.

    Goodreads search results are <tr> elements (no special class in current DOM)
    containing <a class="bookTitle"> and <a class="authorName">.
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # Select all book title links; each sits in a <td> → <tr>
    for title_tag in soup.select("a.bookTitle"):
        author_tag = title_tag.find_next("a", class_="authorName")
        if not author_tag:
            continue

        raw_title = title_tag.get_text(" ", strip=True)

        # Extract series hint from "(Series Name, #N)" pattern in title
        series_hint = ""
        series_match = re.search(r"\(([^)]+),\s*#[\d.]+\)", raw_title)
        if series_match:
            series_hint = series_match.group(1).strip()
            clean_title = raw_title[: series_match.start()].strip()
        else:
            clean_title = raw_title

        href = title_tag.get("href", "")
        if href and not href.startswith("http"):
            href = GOODREADS_BASE + href
        # Strip query params / tracking from URL
        href = href.split("?")[0]

        # Try to get minirating from the same row
        minirating_tag = title_tag.find_next("span", class_="minirating")
        minirating = minirating_tag.get_text(" ", strip=True) if minirating_tag else ""

        results.append(
            {
                "title": clean_title,
                "raw_title": raw_title,
                "author": author_tag.get_text(strip=True),
                "series": series_hint,
                "url": href,
                "minirating": minirating,
            }
        )

    return results


def _extract_book_page_data(html: str) -> dict[str, Any]:
    """
    Parse a Goodreads book page (current React DOM, verified May 2026).
    Returns: rating, rating_count, genres, description, series_url.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, Any] = {
        "rating": "",
        "rating_count": "",
        "genres": [],
        "description": "",
        "series_url": "",
    }

    # --- Rating ---
    # Current DOM: <div class="RatingStatistics__rating"> e.g. "4.45"
    rating_tag = soup.select_one(".RatingStatistics__rating")
    if rating_tag:
        data["rating"] = rating_tag.get_text(strip=True)

    # --- Rating count ---
    # Current DOM: <span data-testid="ratingsCount"> e.g. "2,777,280ratings"
    count_tag = soup.select_one("[data-testid='ratingsCount']")
    if count_tag:
        text = count_tag.get_text(strip=True)
        # Parse "2,777,280ratings" → "2,777,280"
        m = re.match(r"([\d,]+)", text)
        if m:
            data["rating_count"] = m.group(1)
    if not data["rating_count"]:
        # Fallback: .RatingStatistics__meta e.g. "2,777,280ratings73,961reviews"
        meta_tag = soup.select_one(".RatingStatistics__meta")
        if meta_tag:
            text = meta_tag.get_text(strip=True)
            m = re.match(r"([\d,]+)", text)
            if m:
                data["rating_count"] = m.group(1)

    # --- Genres ---
    # Current DOM: [data-testid="genresList"] .Button__labelItem
    genre_tags = soup.select("[data-testid='genresList'] .Button__labelItem")
    if not genre_tags:
        genre_tags = soup.select(".BookPageMetadataSection__genres .Button--tag .Button__labelItem")
    data["genres"] = list(dict.fromkeys(
        g.get_text(strip=True) for g in genre_tags if g.get_text(strip=True)
    ))

    # --- Description ---
    # Current DOM: [data-testid="description"]
    desc_tag = soup.select_one("[data-testid='description']")
    if not desc_tag:
        desc_tag = soup.select_one(".BookPageMetadataSection__description")
    if desc_tag:
        data["description"] = desc_tag.get_text(" ", strip=True)[:1000]

    # --- Series URL ---
    for a_tag in soup.select("a[href*='/series/']"):
        href = a_tag.get("href", "")
        if href:
            if not href.startswith("http"):
                href = GOODREADS_BASE + href
            data["series_url"] = href.split("?")[0]
            break

    return data


# ---------------------------------------------------------------------------
# Main scraper class
# ---------------------------------------------------------------------------

class GoodreadsScraper:
    """Async Playwright-based scraper for Goodreads."""

    def __init__(self, browser: Browser):
        self._browser = browser
        self._ua_index = 0

    def _next_ua(self) -> str:
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        return ua

    async def _new_context(self) -> BrowserContext:
        return await self._browser.new_context(
            user_agent=self._next_ua(),
            locale="en-GB",
            timezone_id="Europe/London",
            viewport={"width": 1366, "height": 768},
        )

    async def _fetch_page(
        self,
        context: BrowserContext,
        url: str,
        wait_selector: str | None = None,
    ) -> str:
        """
        Fetch URL with retry + exponential backoff.
        Uses domcontentloaded then waits for wait_selector (if given) or a
        fixed delay, allowing JS/React renders to complete. Returns page HTML.
        """
        for attempt in range(1, MAX_RETRIES + 1):
            page: Page | None = None
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)

                # Wait for rendered content
                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=10_000)
                    except PWTimeout:
                        pass  # proceed anyway — content may still be usable

                await asyncio.sleep(_random_delay())
                content = await page.content()
                return content
            except PWTimeout as exc:
                logger.warning("Timeout on %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, exc)
            except Exception as exc:
                logger.warning("Error fetching %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, exc)
            finally:
                if page:
                    await page.close()
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF ** attempt)
        return ""

    async def scrape_row(
        self,
        row_index: int,
        title: str,
        series: str,
        author: str,
    ) -> ScrapeResult:
        """
        Full scrape cycle for one book row.
        1. Build search query → fetch search results page
        2. Score candidates → pick best match above threshold
        3. Fetch book page → extract rating, count, genres, description, series URL
        """
        result = ScrapeResult()
        audit: list[str] = []

        query = _build_search_query(title, series, author)
        search_url = GOODREADS_SEARCH_URL.format(
            query=urllib.parse.quote_plus(query)
        )
        logger.info("[row %d] Search query: %s", row_index, query)

        context = await self._new_context()
        try:
            # --- Step 1: search results ---
            # Wait for book title links to appear (confirms results are rendered)
            search_html = await self._fetch_page(
                context, search_url, wait_selector="a.bookTitle"
            )
            if not search_html:
                result.error = "search page fetch failed"
                result.audit_notes = "Search page unreachable after retries"
                return result

            candidates = _extract_search_results(search_html)
            audit.append(f"Found {len(candidates)} candidates on search page")

            if not candidates:
                result.error = "no candidates"
                result.audit_notes = "; ".join(audit)
                return result

            # --- Step 2: score candidates ---
            best_candidate = None
            best_score: MatchScore | None = None

            for c in candidates[:10]:  # inspect top-10 only
                ms = score_match(
                    query_title=title,
                    query_author=author,
                    query_series=series,
                    result_title=c["title"],
                    result_author=c["author"],
                    result_series=c["series"],
                )
                logger.debug(
                    "[row %d] Candidate '%s' by '%s' → score=%.3f",
                    row_index, c["title"], c["author"], ms.final_score,
                )
                if best_score is None or ms.final_score > best_score.final_score:
                    best_score = ms
                    best_candidate = c

            if best_score is None or best_score.final_score < CONFIDENCE_ACCEPT_THRESHOLD:
                score_val = best_score.final_score if best_score else 0.0
                audit.append(
                    f"Best score {score_val:.3f} below threshold "
                    f"{CONFIDENCE_ACCEPT_THRESHOLD} — match rejected"
                )
                result.confidence = round(score_val, 3)
                result.match_source = "search"
                result.audit_notes = "; ".join(audit)
                return result

            result.book_url = best_candidate["url"]  # type: ignore[index]
            result.confidence = best_score.final_score
            result.match_source = "goodreads_search"
            if best_score.notes:
                audit.append("Match notes: " + ", ".join(best_score.notes))
            audit.append(
                f"Matched '{best_candidate['title']}' by '{best_candidate['author']}' "  # type: ignore[index]
                f"(score={best_score.final_score:.3f})"
            )

            # --- Step 3: book page ---
            book_html = await self._fetch_page(
                context, result.book_url, wait_selector=".RatingStatistics__rating"
            )
            if not book_html:
                result.error = "book page fetch failed"
                result.audit_notes = "; ".join(audit)
                return result

            page_data = _extract_book_page_data(book_html)
            result.rating = page_data["rating"]
            result.rating_count = page_data["rating_count"]
            result.genres = page_data["genres"]
            result.description = page_data["description"]
            result.series_url = page_data["series_url"]

            audit.append(
                f"Book page scraped: rating={result.rating!r}, "
                f"count={result.rating_count!r}, "
                f"genres={result.genres[:3]}"
            )

        finally:
            await context.close()

        result.audit_notes = "; ".join(audit)
        return result


# ---------------------------------------------------------------------------
# Context manager for creating / tearing down the shared browser
# ---------------------------------------------------------------------------

class PlaywrightSession:
    """Async context manager that owns the Playwright + Browser lifecycle."""

    def __init__(self, headless: bool = True):
        self._headless = headless
        self._pw = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "PlaywrightSession":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    @property
    def browser(self) -> Browser:
        if self._browser is None:
            raise RuntimeError("Browser not started — use async with PlaywrightSession()")
        return self._browser

    def new_scraper(self) -> GoodreadsScraper:
        return GoodreadsScraper(self.browser)
