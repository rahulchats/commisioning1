"""
Pipeline configuration: constants, column definitions, API settings,
rate-limiting, formatting specs. Edit here to tune behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR: Final[Path] = Path(__file__).parent
INPUT_FILE: Final[Path] = BASE_DIR / "sample_input.xlsx"
INPUT_SHEET: Final[str] = "Books"
CHECKPOINT_DIR: Final[Path] = BASE_DIR / "checkpoints"
LOG_DIR: Final[Path] = BASE_DIR / "logs"

CHECKPOINT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Output file name  (YYMMDD UK CMT Funnel Enriched - vPOC_1.xlsx)
# ---------------------------------------------------------------------------
import datetime
_today = datetime.date.today()
OUTPUT_FILENAME: Final[str] = f"{_today.strftime('%y%m%d')} UK CMT Funnel Enriched - vPOC_1.xlsx"
OUTPUT_FILE: Final[Path] = BASE_DIR / OUTPUT_FILENAME

# ---------------------------------------------------------------------------
# POC settings
# ---------------------------------------------------------------------------
POC_ROW_LIMIT: Final[int] = 20        # 0 = all rows
PLAYWRIGHT_WORKERS: Final[int] = 2    # concurrent browser tabs
LLM_SEMAPHORE_COUNT: Final[int] = 3   # concurrent LLM calls

# ---------------------------------------------------------------------------
# xAI / Grok
# Set XAI_API_KEY environment variable before running the pipeline.
# ---------------------------------------------------------------------------
XAI_API_KEY: Final[str] = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL: Final[str] = "https://api.x.ai/v1"
XAI_MODEL: Final[str] = "grok-3-mini"
XAI_TEMPERATURE: Final[float] = 0.1
XAI_MAX_TOKENS: Final[int] = 600

# ---------------------------------------------------------------------------
# Goodreads scraping
# ---------------------------------------------------------------------------
GOODREADS_SEARCH_URL: Final[str] = "https://www.goodreads.com/search?q={query}&search_type=books"
GOODREADS_BASE: Final[str] = "https://www.goodreads.com"

REQUEST_DELAY_MIN: Final[float] = 2.5   # seconds between page loads
REQUEST_DELAY_MAX: Final[float] = 5.0
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF: Final[float] = 2.0       # exponential backoff base

# Minimum confidence to accept a Goodreads match
CONFIDENCE_ACCEPT_THRESHOLD: Final[float] = 0.55

# ---------------------------------------------------------------------------
# Rotating user-agent pool (desktop browsers)
# ---------------------------------------------------------------------------
USER_AGENTS: Final[list[str]] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]

# ---------------------------------------------------------------------------
# Input column names (must match source sheet exactly)
# ---------------------------------------------------------------------------
COL_SERIES: Final[str] = "Series Name"
COL_FIRST_BOOK: Final[str] = "First Book Name"
COL_AUTHOR: Final[str] = "Author Name"

# ---------------------------------------------------------------------------
# Enriched output columns
# ---------------------------------------------------------------------------
COL_GR_SERIES_URL: Final[str] = "Goodreads Series URL"
COL_GR_FIRST_BOOK_URL: Final[str] = "Goodreads First Book URL"
COL_GR_RATING: Final[str] = "Goodreads Rating"
COL_GR_RATING_COUNT: Final[str] = "Goodreads Rating Count"
COL_AUTHOR_NATIONALITY: Final[str] = "Author Nationality"
COL_GENRE: Final[str] = "Genre (normalized)"
COL_SUBGENRE: Final[str] = "Subgenre"
COL_TROPES: Final[str] = "Tropes"
COL_AUTHOR_EMAIL: Final[str] = "Author Email"
COL_CONFIDENCE: Final[str] = "Confidence Score"
COL_MATCH_SOURCE: Final[str] = "Match Source"
COL_AUDIT_NOTES: Final[str] = "Audit Notes"

ENRICHED_COLS: Final[list[str]] = [
    COL_GR_SERIES_URL,
    COL_GR_FIRST_BOOK_URL,
    COL_GR_RATING,
    COL_GR_RATING_COUNT,
    COL_AUTHOR_NATIONALITY,
    COL_GENRE,
    COL_SUBGENRE,
    COL_TROPES,
    COL_AUTHOR_EMAIL,
    COL_CONFIDENCE,
    COL_MATCH_SOURCE,
    COL_AUDIT_NOTES,
]

# Scraped columns (light green header)
SCRAPED_COLS: Final[set[str]] = {
    COL_GR_SERIES_URL,
    COL_GR_FIRST_BOOK_URL,
    COL_GR_RATING,
    COL_GR_RATING_COUNT,
    COL_GENRE,
    COL_AUTHOR_EMAIL,
}

# LLM columns (light blue header)
LLM_COLS: Final[set[str]] = {
    COL_AUTHOR_NATIONALITY,
    COL_SUBGENRE,
    COL_TROPES,
}

# ---------------------------------------------------------------------------
# Excel formatting
# ---------------------------------------------------------------------------
FONT_NAME: Final[str] = "Calibri"
FONT_SIZE: Final[int] = 11
ROW_HEIGHT: Final[float] = 13.0
URL_COL_WIDTH: Final[int] = 50

# Header fill colours
HEADER_SCRAPED_FILL: Final[str] = "C6EFCE"   # light green
HEADER_LLM_FILL: Final[str] = "BDD7EE"       # light blue
HEADER_DEFAULT_FILL: Final[str] = "D9D9D9"   # light grey

# Error cell fill
ERROR_FILL: Final[str] = "FFC7CE"            # light red

# Explicit column widths  {column_header: width}
COLUMN_WIDTHS: Final[dict[str, float]] = {
    "Series Name": 35,
    "First Book Name": 35,
    "Author Name": 25,
    "Publisher": 25,
    "Year Published": 14,
    "ISBN": 18,
    "Language": 12,
    "Format": 14,
    "Pages": 8,
    "Notes": 30,
    COL_GR_SERIES_URL: URL_COL_WIDTH,
    COL_GR_FIRST_BOOK_URL: URL_COL_WIDTH,
    COL_GR_RATING: 16,
    COL_GR_RATING_COUNT: 20,
    COL_AUTHOR_NATIONALITY: 22,
    COL_GENRE: 20,
    COL_SUBGENRE: 22,
    COL_TROPES: 45,
    COL_AUTHOR_EMAIL: 35,
    COL_CONFIDENCE: 16,
    COL_MATCH_SOURCE: 18,
    COL_AUDIT_NOTES: 50,
}
