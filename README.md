# Goodreads Book Series Enrichment Pipeline

A deterministic scraping-first enrichment pipeline for book series datasets.
Reads an input Excel workbook, enriches each row with Goodreads metadata and
LLM-inferred classifications, and writes a fully-formatted output workbook.

---

## Architecture

```
Input Excel (read-only)
       │
       ▼
┌─────────────────────────────────────────────────────┐
│  pipeline.py  —  async orchestrator                 │
│                                                     │
│  ┌─────────────────────┐   ┌─────────────────────┐  │
│  │  goodreads_scraper  │   │   llm_enricher      │  │
│  │  (Playwright async) │   │   (xAI / Grok API)  │  │
│  │                     │   │                     │  │
│  │  Semaphore:         │   │  Semaphore:         │  │
│  │  PLAYWRIGHT_WORKERS │   │  LLM_SEMAPHORE_COUNT│  │
│  └─────────────────────┘   └─────────────────────┘  │
│              │                       │               │
│              ▼                       ▼               │
│         confidence.py          (audit log)           │
│         (fuzzy scoring)                              │
│              │                                       │
│              ▼                                       │
│        checkpoint.py  (per-row JSON snapshots)       │
└─────────────────────────────────────────────────────┘
       │
       ▼
  excel_writer.py
       │
       ▼
Output Excel (4 sheets):
  1. Enriched Output
  2. Audit
  3. Failed Rows
  4. Stats
```

---

## Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `config.py` | All constants, column names, API settings, formatting specs |
| `goodreads_scraper.py` | Async Playwright scraper — search + book page extraction |
| `confidence.py` | Weighted fuzzy similarity scoring for match validation |
| `llm_enricher.py` | xAI/Grok calls for nationality, subgenre, tropes |
| `checkpoint.py` | Per-row JSON checkpointing for resume-safe execution |
| `excel_writer.py` | 4-sheet workbook formatting with strict style rules |
| `pipeline.py` | Main orchestrator — ties all modules together |
| `create_sample_input.py` | Generates a 25-row sample input Excel for testing |

---

## Enriched Columns Added

| Column | Source |
|--------|--------|
| Goodreads Series URL | Scraped (deterministic) |
| Goodreads First Book URL | Scraped (deterministic) |
| Goodreads Rating | Scraped (deterministic) |
| Goodreads Rating Count | Scraped (deterministic) |
| Author Nationality | LLM (Grok) |
| Genre (normalized) | Scraped (deterministic) |
| Subgenre | LLM (Grok) |
| Tropes | LLM (Grok) |
| Author Email | Not auto-scraped (left blank) |
| Confidence Score | Computed (fuzzy match) |
| Match Source | Computed |
| Audit Notes | Pipeline-generated |

---

## Excel Output Spec

- Font: Calibri 11  
- Row height: 13  
- Freeze top row: YES  
- Gridlines: OFF  
- Header colours:
  - Scraped columns → light green (`#C6EFCE`)
  - LLM columns → light blue (`#BDD7EE`)
  - Other columns → light grey (`#D9D9D9`)
- Error rows → light red highlight  
- URL columns → width 50, left-aligned, hyperlinked  
- No alternating shading  
- No auto-fit  

---

## Usage

### Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### Set API key (required)

```bash
export XAI_API_KEY="xai-your-key-here"
# or copy .env.example to .env and fill in the value
```

### Run POC (first 20 rows)

```bash
cd pipeline
python3 pipeline.py
```

### Run with custom input / row limit

```bash
python3 pipeline.py --input /path/to/your_data.xlsx --limit 20
```

### Run full dataset (after POC validation)

```bash
python3 pipeline.py --limit 0
```

---

## Concurrency Controls

| Parameter | Default | Config key |
|-----------|---------|------------|
| Playwright browser workers | 2 | `PLAYWRIGHT_WORKERS` |
| LLM concurrent calls | 3 | `LLM_SEMAPHORE_COUNT` |
| Request delay (s) | 2.5–5.0 | `REQUEST_DELAY_MIN/MAX` |
| Max retries per page | 3 | `MAX_RETRIES` |

---

## Checkpointing

Each processed row is saved as a JSON file in `pipeline/checkpoints/`.
Re-running the pipeline automatically skips rows that already have a checkpoint.

To force a full re-run, delete the checkpoints directory:

```bash
rm -rf pipeline/checkpoints/
```

---

## Matching Logic

Goodreads matches are validated using weighted fuzzy similarity:

| Component | Weight |
|-----------|--------|
| Title similarity | 45% |
| Author similarity | 35% |
| Series similarity | 20% |

Matches below **0.55 confidence** are rejected.
Low-quality editions (audiobook, abridged, boxed set, graphic novel) receive a –0.25 penalty.

---

## Output File Naming

```
YYMMDD UK CMT Funnel Enriched - vPOC_1.xlsx
```

Example: `260526 UK CMT Funnel Enriched - vPOC_1.xlsx`

---

## Phase Progression

| Phase | Description |
|-------|-------------|
| **POC (Phase 1)** | First 20 rows only (`--limit 20`) |
| **Full run (Phase 2)** | Entire dataset (`--limit 0`), after POC validation |
