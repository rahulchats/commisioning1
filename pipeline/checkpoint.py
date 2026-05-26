"""
Checkpoint manager: persist per-row enrichment results to disk so the pipeline
can resume without re-scraping rows already processed.

Storage format: one JSON file per row, named by zero-based index.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config import CHECKPOINT_DIR

logger = logging.getLogger(__name__)


def _row_path(row_index: int) -> Path:
    return CHECKPOINT_DIR / f"row_{row_index:05d}.json"


def save_checkpoint(row_index: int, data: dict[str, Any]) -> None:
    """Persist enrichment result for a single row."""
    path = _row_path(row_index)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("Checkpoint saved: row %d → %s", row_index, path.name)
    except OSError as exc:
        logger.warning("Failed to save checkpoint for row %d: %s", row_index, exc)


def load_checkpoint(row_index: int) -> dict[str, Any] | None:
    """Return persisted result for row, or None if not yet checkpointed."""
    path = _row_path(row_index)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Corrupt checkpoint for row %d: %s — re-processing.", row_index, exc)
        return None


def clear_checkpoint(row_index: int) -> None:
    path = _row_path(row_index)
    if path.exists():
        path.unlink()


def list_checkpointed_indices() -> list[int]:
    indices = []
    for p in sorted(CHECKPOINT_DIR.glob("row_*.json")):
        try:
            idx = int(p.stem.split("_")[1])
            indices.append(idx)
        except (IndexError, ValueError):
            pass
    return indices
