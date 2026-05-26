"""
Excel output formatter.

Produces a workbook with four sheets:
  1. Enriched Output  — source columns + enriched columns
  2. Audit            — per-row LLM prompt/response/parsed/confidence log
  3. Failed Rows      — rows that errored or were rejected
  4. Stats            — summary statistics

Formatting rules (strict):
  - Calibri 11, row height 13
  - Freeze top row
  - Gridlines OFF
  - No alternating shading
  - Explicit column widths only (no auto-fit)
  - index=False on every df.to_excel call
  - Header colour: scraped=light green, LLM=light blue, default=grey
  - Error cells highlighted in light red
  - URLs left-aligned
  - ISBNs formatted as text
  - Date format DD MMM YYYY
"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import (
    Alignment,
    Font,
    PatternFill,
    numbers,
)
from openpyxl.utils import get_column_letter

from config import (
    COLUMN_WIDTHS,
    ENRICHED_COLS,
    ERROR_FILL,
    FONT_NAME,
    FONT_SIZE,
    HEADER_DEFAULT_FILL,
    HEADER_LLM_FILL,
    HEADER_SCRAPED_FILL,
    LLM_COLS,
    OUTPUT_FILE,
    ROW_HEIGHT,
    SCRAPED_COLS,
    COL_GR_SERIES_URL,
    COL_GR_FIRST_BOOK_URL,
    COL_CONFIDENCE,
    COL_GR_RATING,
    COL_GR_RATING_COUNT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Openpyxl style factories
# ---------------------------------------------------------------------------

def _font(bold: bool = False) -> Font:
    return Font(name=FONT_NAME, size=FONT_SIZE, bold=bold)


def _fill(hex_colour: str) -> PatternFill:
    return PatternFill(fill_type="solid", fgColor=hex_colour)


def _left_align() -> Alignment:
    return Alignment(horizontal="left", vertical="center", wrap_text=False)


def _wrap_align() -> Alignment:
    return Alignment(horizontal="left", vertical="center", wrap_text=True)


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------

def _apply_sheet_defaults(ws: Any, freeze: bool = True) -> None:
    """Apply global formatting: font, row heights, freeze pane, gridlines off."""
    ws.sheet_view.showGridLines = False
    if freeze:
        ws.freeze_panes = "A2"

    for row in ws.iter_rows():
        for cell in row:
            cell.font = _font()
            cell.alignment = _left_align()
        ws.row_dimensions[row[0].row].height = ROW_HEIGHT


def _style_header_row(ws: Any, col_headers: list[str]) -> None:
    """Colour header row cells based on column category."""
    for col_idx, header in enumerate(col_headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = _font(bold=True)
        if header in SCRAPED_COLS:
            cell.fill = _fill(HEADER_SCRAPED_FILL)
        elif header in LLM_COLS:
            cell.fill = _fill(HEADER_LLM_FILL)
        else:
            cell.fill = _fill(HEADER_DEFAULT_FILL)
        cell.alignment = _left_align()


def _set_column_widths(ws: Any, col_headers: list[str]) -> None:
    """Apply explicit widths; fall back to 20 if not specified."""
    for col_idx, header in enumerate(col_headers, start=1):
        width = COLUMN_WIDTHS.get(header, 20)
        ws.column_dimensions[get_column_letter(col_idx)].width = width


def _highlight_error_cells(ws: Any, col_headers: list[str], error_col: str = "Audit Notes") -> None:
    """Highlight cells that contain 'ERROR' or 'failed' in audit notes."""
    if error_col not in col_headers:
        return
    err_col_idx = col_headers.index(error_col) + 1
    for row in ws.iter_rows(min_row=2):
        cell = row[err_col_idx - 1]
        val = str(cell.value or "")
        if "error" in val.lower() or "failed" in val.lower() or "rejected" in val.lower():
            for c in row:
                c.fill = _fill(ERROR_FILL)


def _format_url_cells(ws: Any, col_headers: list[str]) -> None:
    """Left-align and hyperlink URL columns."""
    url_cols = [h for h in col_headers if "URL" in h or "url" in h]
    for header in url_cols:
        col_idx = col_headers.index(header) + 1
        for row_idx in range(2, ws.max_row + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            val = str(cell.value or "")
            if val.startswith("http"):
                cell.hyperlink = val
                cell.style = "Hyperlink"
                cell.alignment = _left_align()


# ---------------------------------------------------------------------------
# DataFrame → worksheet writer
# ---------------------------------------------------------------------------

def _write_df_to_sheet(wb: Workbook, sheet_name: str, df: pd.DataFrame) -> None:
    """Write a DataFrame to a named sheet with full formatting."""
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(title=sheet_name)

    headers = list(df.columns)

    # Write header row
    for col_idx, header in enumerate(headers, start=1):
        ws.cell(row=1, column=col_idx, value=header)

    # Write data rows
    for row_idx, (_, row) in enumerate(df.iterrows(), start=2):
        for col_idx, header in enumerate(headers, start=1):
            val = row[header]
            # None → empty string for Excel safety
            if val is None or (isinstance(val, float) and pd.isna(val)):
                val = ""
            ws.cell(row=row_idx, column=col_idx, value=val)

    _apply_sheet_defaults(ws, freeze=True)
    _style_header_row(ws, headers)
    _set_column_widths(ws, headers)
    _highlight_error_cells(ws, headers)
    _format_url_cells(ws, headers)

    logger.debug("Sheet '%s' written: %d rows × %d cols", sheet_name, len(df), len(headers))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def write_output(
    enriched_df: pd.DataFrame,
    audit_records: list[dict],
    failed_df: pd.DataFrame,
    stats: dict[str, Any],
    output_path: Path = OUTPUT_FILE,
) -> Path:
    """
    Write the four-sheet workbook to *output_path*.
    Keeps default 'Sheet' removed.
    Returns the final path.
    """
    wb = Workbook()
    # Remove default sheet
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]

    # ---- Sheet 1: Enriched Output ----
    _write_df_to_sheet(wb, "Enriched Output", enriched_df)

    # ---- Sheet 2: Audit ----
    audit_df = pd.DataFrame(audit_records) if audit_records else _empty_audit_df()
    _write_df_to_sheet(wb, "Audit", audit_df)

    # ---- Sheet 3: Failed Rows ----
    _write_df_to_sheet(wb, "Failed Rows", failed_df if not failed_df.empty else _empty_failed_df())

    # ---- Sheet 4: Stats ----
    stats_df = pd.DataFrame(list(stats.items()), columns=["Metric", "Value"])
    _write_df_to_sheet(wb, "Stats", stats_df)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    logger.info("Workbook saved: %s", output_path)

    # Open automatically
    _open_file(output_path)

    return output_path


def _empty_audit_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["Row Index", "Author", "Series", "Title",
                                 "Prompt", "Raw Response", "Parsed Response",
                                 "Nationality", "Subgenre", "Tropes",
                                 "LLM Confidence", "Error"])


def _empty_failed_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["Row Index", "Series Name", "First Book Name",
                                 "Author Name", "Error", "Audit Notes"])


def _open_file(path: Path) -> None:
    """Open the output file with the system default application."""
    try:
        if sys.platform.startswith("linux"):
            subprocess.Popen(["xdg-open", str(path)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.Popen(["start", str(path)], shell=True)
    except Exception as exc:
        logger.debug("Could not auto-open file: %s", exc)
