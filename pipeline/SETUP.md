# Local Setup & Execution Guide

## Requirements

- Python 3.10 or higher
- pip
- Internet access (to reach Goodreads and xAI API)

---

## 1. Install Python dependencies

```bash
cd pipeline
pip install -r ../requirements.txt
```

Or install directly:

```bash
pip install pandas openpyxl beautifulsoup4 playwright aiohttp rapidfuzz python-dotenv openai
```

---

## 2. Install Playwright browser

```bash
playwright install chromium
```

If `playwright` is not on PATH, use:

```bash
python -m playwright install chromium
# or
~/.local/bin/playwright install chromium
```

---

## 3. Configure your API key

The `.env` file in this directory already contains the xAI API key (pre-populated in the ZIP).

The pipeline auto-loads `.env` on startup via `python-dotenv`.  
Alternatively, export it manually:

```bash
export XAI_API_KEY="<your-key-from-the-.env-file>"
```

---

## 4. Prepare your input file

The pipeline reads `pipeline/sample_input.xlsx` by default (25 book series already included).

To use your own data, either:
- Replace `sample_input.xlsx` with your file (same column structure), or
- Pass `--input /path/to/your_file.xlsx` on the command line

Required columns in the input sheet (`Books`):
| Column | Example |
|--------|---------|
| Series Name | A Song of Ice and Fire |
| First Book Name | A Game of Thrones |
| Author Name | George R.R. Martin |

Other columns (Publisher, ISBN, etc.) are preserved as-is.

---

## 5. Run the POC (first 20 rows)

```bash
cd pipeline
python pipeline.py
```

Or explicitly:

```bash
python pipeline.py --limit 20
```

---

## 6. Run on your own file

```bash
python pipeline.py --input /path/to/your_data.xlsx --limit 20
```

---

## 7. Run the full dataset (Phase 2)

After validating POC results:

```bash
python pipeline.py --limit 0
```

---

## 8. Resume after interruption

Checkpoints are saved per-row in `pipeline/checkpoints/`. If the pipeline is interrupted,
simply re-run the same command — already-processed rows will be loaded from checkpoint
and skipped automatically.

To force a full re-run from scratch:

```bash
rm -rf pipeline/checkpoints/*
```

---

## Output

The output file is saved in the `pipeline/` directory with naming:

```
YYMMDD UK CMT Funnel Enriched - vPOC_1.xlsx
```

Example: `260526 UK CMT Funnel Enriched - vPOC_1.xlsx`

The file opens automatically after completion (on macOS, Windows, and Linux with `xdg-open`).

### Output sheets

| Sheet | Contents |
|-------|----------|
| Enriched Output | Source columns + 12 enriched columns |
| Audit | Full LLM prompt, raw response, parsed JSON, confidence per row |
| Failed Rows | Rows that errored or were rejected (Goodreads match too weak) |
| Stats | Run summary: match rate, confidence, row counts |

---

## Concurrency controls (in `config.py`)

| Setting | Default | Effect |
|---------|---------|--------|
| `PLAYWRIGHT_WORKERS` | 2 | Max simultaneous Goodreads browser tabs |
| `LLM_SEMAPHORE_COUNT` | 3 | Max simultaneous xAI API calls |
| `REQUEST_DELAY_MIN/MAX` | 2.5–5.0s | Random delay between Goodreads page loads |
| `POC_ROW_LIMIT` | 20 | Default row cap (0 = unlimited) |

---

## Troubleshooting

**`playwright: command not found`**  
Use `python -m playwright install chromium` instead.

**`ModuleNotFoundError`**  
Run `pip install -r requirements.txt` from the project root.

**Goodreads returns 0 candidates for a row**  
This is usually transient rate-limiting. The row is saved to `Failed Rows` sheet.
Delete its checkpoint file and re-run to retry:
```bash
rm pipeline/checkpoints/row_00013.json
python pipeline.py
```

**LLM calls failing**  
Check that `XAI_API_KEY` is set and valid. Verify connectivity to `api.x.ai`.

---

## File structure

```
project_root/
├── requirements.txt
├── README.md
└── pipeline/
    ├── .env                  ← API key (auto-loaded)
    ├── SETUP.md              ← this file
    ├── config.py             ← all settings
    ├── pipeline.py           ← main entry point
    ├── goodreads_scraper.py  ← Playwright scraper
    ├── llm_enricher.py       ← xAI/Grok enrichment
    ├── confidence.py         ← fuzzy match scoring
    ├── checkpoint.py         ← resume-safe persistence
    ├── excel_writer.py       ← 4-sheet output formatter
    ├── create_sample_input.py← generates sample_input.xlsx
    ├── sample_input.xlsx     ← 25-row test dataset
    ├── checkpoints/          ← per-row JSON snapshots
    ├── logs/                 ← pipeline run logs
    └── YYMMDD UK CMT Funnel Enriched - vPOC_1.xlsx  ← output
```
