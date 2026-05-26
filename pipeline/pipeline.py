"""
Main pipeline orchestrator.

Flow:
  1. Read input Excel (read-only, never modified)
  2. For each row (up to POC_ROW_LIMIT):
     a. Check checkpoint — skip if already done
     b. Scrape Goodreads (async Playwright worker)
     c. Call LLM for nationality/subgenre/tropes
     d. Merge results → enriched row dict
     e. Save checkpoint
  3. Assemble DataFrames
  4. Write 4-sheet Excel output

Concurrency model:
  - asyncio.Semaphore(PLAYWRIGHT_WORKERS) gates Playwright browser contexts
  - asyncio.Semaphore(LLM_SEMAPHORE_COUNT) gates LLM calls (inside llm_enricher)
  - Rows are processed concurrently up to PLAYWRIGHT_WORKERS at a time
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Ensure pipeline directory is on path when run directly
sys.path.insert(0, str(Path(__file__).parent))

from checkpoint import load_checkpoint, save_checkpoint
from config import (
    COL_AUDIT_NOTES,
    COL_AUTHOR,
    COL_AUTHOR_EMAIL,
    COL_AUTHOR_NATIONALITY,
    COL_CONFIDENCE,
    COL_FIRST_BOOK,
    COL_GENRE,
    COL_GR_FIRST_BOOK_URL,
    COL_GR_RATING,
    COL_GR_RATING_COUNT,
    COL_GR_SERIES_URL,
    COL_MATCH_SOURCE,
    COL_SERIES,
    COL_SUBGENRE,
    COL_TROPES,
    ENRICHED_COLS,
    INPUT_FILE,
    INPUT_SHEET,
    LOG_DIR,
    OUTPUT_FILE,
    PLAYWRIGHT_WORKERS,
    POC_ROW_LIMIT,
)
from excel_writer import write_output
from goodreads_scraper import PlaywrightSession, ScrapeResult
from llm_enricher import LLMResult, enrich_with_llm

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    log_path = LOG_DIR / f"pipeline_{datetime.date.today().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-row processing
# ---------------------------------------------------------------------------

_playwright_semaphore: asyncio.Semaphore | None = None


def get_playwright_semaphore() -> asyncio.Semaphore:
    global _playwright_semaphore
    if _playwright_semaphore is None:
        _playwright_semaphore = asyncio.Semaphore(PLAYWRIGHT_WORKERS)
    return _playwright_semaphore


async def _process_row(
    row_index: int,
    source_row: dict[str, Any],
    session: "PlaywrightSession",
) -> dict[str, Any]:
    """
    Process a single row end-to-end.
    Returns a merged dict: source fields + enriched fields + audit fields.
    """
    # -- Resume from checkpoint if available --
    cached = load_checkpoint(row_index)
    if cached:
        logger.info("[row %d] Loaded from checkpoint.", row_index)
        return cached

    title = str(source_row.get(COL_FIRST_BOOK, "")).strip()
    series = str(source_row.get(COL_SERIES, "")).strip()
    author = str(source_row.get(COL_AUTHOR, "")).strip()

    logger.info("[row %d] Processing: '%s' by %s", row_index, title, author)

    # -- Goodreads scrape --
    scrape: ScrapeResult = ScrapeResult()
    async with get_playwright_semaphore():
        scraper = session.new_scraper()
        scrape = await scraper.scrape_row(row_index, title, series, author)

    # -- LLM enrichment (always attempted; falls back gracefully) --
    llm: LLMResult = LLMResult()
    if title or author:
        llm = await enrich_with_llm(
            author=author,
            series=series,
            title=title,
            genres=scrape.genres,
            description=scrape.description,
            row_index=row_index,
        )

    # -- Assemble merged row --
    genre_str = "; ".join(scrape.genres[:3]) if scrape.genres else ""

    enriched: dict[str, Any] = {
        **{k: _safe_str(v) for k, v in source_row.items()},
        COL_GR_SERIES_URL: scrape.series_url,
        COL_GR_FIRST_BOOK_URL: scrape.book_url,
        COL_GR_RATING: scrape.rating,
        COL_GR_RATING_COUNT: scrape.rating_count,
        COL_AUTHOR_NATIONALITY: llm.nationality,
        COL_GENRE: genre_str,
        COL_SUBGENRE: llm.subgenre,
        COL_TROPES: llm.tropes,
        COL_AUTHOR_EMAIL: "",   # not publicly discoverable via scraping
        COL_CONFIDENCE: scrape.confidence,
        COL_MATCH_SOURCE: scrape.match_source,
        COL_AUDIT_NOTES: scrape.audit_notes or scrape.error,
        # Internal — used for audit sheet only
        "_row_index": row_index,
        "_llm_prompt": llm.prompt,
        "_llm_raw": llm.raw_response,
        "_llm_parsed": str(llm.parsed_response),
        "_llm_confidence": llm.llm_confidence,
        "_llm_error": llm.error,
        "_scrape_error": scrape.error,
    }

    save_checkpoint(row_index, enriched)
    return enriched


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, float) and pd.isna(val):
        return ""
    return str(val)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def run_pipeline(
    input_file: Path = INPUT_FILE,
    poc_limit: int = POC_ROW_LIMIT,
) -> Path:
    """
    Full async pipeline. Returns path to the output workbook.
    """
    _setup_logging()
    logger.info("=" * 60)
    logger.info("Pipeline starting — POC limit: %d rows", poc_limit)
    logger.info("Input: %s", input_file)
    logger.info("Output: %s", OUTPUT_FILE)
    logger.info("=" * 60)

    # -- Read input (read-only) --
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    df_input = pd.read_excel(input_file, sheet_name=INPUT_SHEET, dtype=str)
    logger.info("Input loaded: %d rows × %d columns", len(df_input), len(df_input.columns))

    rows = df_input.to_dict(orient="records")
    if poc_limit > 0:
        rows = rows[:poc_limit]
        logger.info("POC mode: processing first %d rows", len(rows))

    # -- Run all rows concurrently under Playwright session --
    results: list[dict[str, Any]] = []

    async with PlaywrightSession(headless=True) as session:
        tasks = [
            _process_row(idx, row, session)
            for idx, row in enumerate(rows)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # -- Separate successes from failures --
    enriched_records: list[dict] = []
    failed_records: list[dict] = []
    audit_records: list[dict] = []

    for idx, res in enumerate(results):
        if isinstance(res, Exception):
            logger.error("[row %d] Unhandled exception: %s", idx, res)
            src = rows[idx]
            failed_records.append({
                "Row Index": idx,
                COL_SERIES: src.get(COL_SERIES, ""),
                COL_FIRST_BOOK: src.get(COL_FIRST_BOOK, ""),
                COL_AUTHOR: src.get(COL_AUTHOR, ""),
                "Error": str(res),
                COL_AUDIT_NOTES: "",
            })
            continue

        enriched_records.append(res)

        has_error = bool(res.get("_scrape_error") or res.get("_llm_error"))
        if has_error:
            src = rows[idx]
            failed_records.append({
                "Row Index": idx,
                COL_SERIES: src.get(COL_SERIES, ""),
                COL_FIRST_BOOK: src.get(COL_FIRST_BOOK, ""),
                COL_AUTHOR: src.get(COL_AUTHOR, ""),
                "Error": res.get("_scrape_error", "") or res.get("_llm_error", ""),
                COL_AUDIT_NOTES: res.get(COL_AUDIT_NOTES, ""),
            })

        audit_records.append({
            "Row Index": res.get("_row_index", idx),
            "Author": res.get(COL_AUTHOR, ""),
            "Series": res.get(COL_SERIES, ""),
            "Title": res.get(COL_FIRST_BOOK, ""),
            "Prompt": res.get("_llm_prompt", ""),
            "Raw Response": res.get("_llm_raw", ""),
            "Parsed Response": res.get("_llm_parsed", ""),
            "Nationality": res.get(COL_AUTHOR_NATIONALITY, ""),
            "Subgenre": res.get(COL_SUBGENRE, ""),
            "Tropes": res.get(COL_TROPES, ""),
            "LLM Confidence": res.get("_llm_confidence", ""),
            "LLM Error": res.get("_llm_error", ""),
        })

    # -- Build enriched DataFrame (source cols + enriched cols only) --
    source_cols = list(df_input.columns)
    output_cols = source_cols + ENRICHED_COLS

    enriched_df = pd.DataFrame(enriched_records)
    # Keep only known output columns, fill missing with ""
    for col in output_cols:
        if col not in enriched_df.columns:
            enriched_df[col] = ""
    enriched_df = enriched_df[output_cols].fillna("")

    failed_df = pd.DataFrame(failed_records) if failed_records else pd.DataFrame()

    # -- Stats --
    total = len(rows)
    matched = sum(
        1 for r in enriched_records
        if isinstance(r, dict) and r.get(COL_GR_FIRST_BOOK_URL)
    )
    failed_count = len(failed_records)
    avg_conf = (
        sum(
            float(r.get(COL_CONFIDENCE, 0))
            for r in enriched_records
            if isinstance(r, dict)
        ) / max(len(enriched_records), 1)
    )

    stats = {
        "Run Date": datetime.date.today().strftime("%d %b %Y"),
        "Input File": str(input_file.name),
        "Output File": str(OUTPUT_FILE.name),
        "Total Rows Processed": total,
        "Goodreads Matched": matched,
        "Failed / No Match": failed_count,
        "Match Rate (%)": f"{100 * matched / max(total, 1):.1f}%",
        "Average Confidence Score": f"{avg_conf:.3f}",
        "POC Mode": f"First {poc_limit} rows" if poc_limit else "Full run",
    }

    logger.info("Pipeline complete: %d/%d matched (%.1f%%)", matched, total, 100 * matched / max(total, 1))

    output_path = write_output(
        enriched_df=enriched_df,
        audit_records=audit_records,
        failed_df=failed_df,
        stats=stats,
    )

    return output_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Goodreads Enrichment Pipeline (POC)")
    parser.add_argument("--input", type=Path, default=INPUT_FILE, help="Input Excel file")
    parser.add_argument("--limit", type=int, default=POC_ROW_LIMIT,
                        help="Max rows to process (0=all). Default=20 for POC.")
    args = parser.parse_args()

    output = asyncio.run(run_pipeline(input_file=args.input, poc_limit=args.limit))
    print(f"\nDone. Output: {output}")
