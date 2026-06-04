# Sebco Market Intel

A local dashboard for extracting and tracking commercial real estate market data from quarterly PDF reports (Kidder Mathews, CBRE, Voit Real Estate Services, JLL).

## Quick Start

### 1. Install Python

**Mac**: Open Terminal and run:
```
brew install python
```
If you don't have Homebrew, install it first from https://brew.sh

**Windows**: Download Python from https://www.python.org/downloads/ and install. Check "Add Python to PATH" during installation.

### 2. Install Dependencies

Open a terminal/command prompt in this folder and run:
```
pip install -r requirements.txt
```

### 3. Run the Dashboard

```
streamlit run app.py
```

A browser window will open at http://localhost:8501 with the dashboard.

### 4. Upload PDFs

1. Click **Library** in the top tab bar
2. Expand the **Upload a report** section and drop in a PDF
3. Review the extracted preview and click **Save to database**
4. Use Pulse / Compare / Trends to explore the data

## Sharing the Database via OneDrive

By default, data is stored locally in `market_data.db`. To share across computers:

1. Create a folder in your shared OneDrive, e.g., `OneDrive/sebco-market-intel/`
2. Create a file called `config.json` in this app's folder with:

**Mac**:
```json
{"db_path": "/Users/YourName/Library/CloudStorage/OneDrive/sebco-market-intel/market_data.db"}
```

**Windows**:
```json
{"db_path": "C:\\Users\\YourName\\OneDrive\\sebco-market-intel\\market_data.db"}
```

3. Copy the same `config.json` to every computer that runs the app
4. Make sure the OneDrive folder is synced on all computers before running

**Note**: Avoid having two people upload PDFs at the exact same time. The app handles brief lock conflicts automatically, but simultaneous heavy writes may fail. Reading and viewing data works fine concurrently.

## Dashboard Pages

- **Pulse** — Landing page. Six Sebco markets at a glance with vacancy, asking rent, QoQ deltas, and rent sparklines.
- **Compare** — Side-by-side KPI comparison of any two markets or submarkets. Per-side PDF snapshot download.
- **Trends** — Multi-quarter line chart for one market + one metric, with optional submarket breakdown and Sebco rent overlay.
- **Library** — Every uploaded report, freshness badges, drill-in to view/edit records, upload new PDFs, rejected records.
- **Settings** — Edit `sebco_portfolio.json` (markets, building counts, in-place rents, lease type).

## Supported Report Formats

- **Kidder Mathews** — structured tables, dual industrial/warehouse breakdowns, submarket statistics grids, narrative + sidebar callouts.
- **CBRE** — page-level "Market Statistics by Submarket" grids.
- **Voit Real Estate Services** — page-3 submarket statistics with auto-detected layout variants.
- **JLL** — page-2 submarket tables (W&D / Manufacturing / Overall) plus single-page "Fundamentals" box.

## Data Quality

The Library page surfaces parser confidence per source and lists any records that failed validation (missing required fields). To correct an extraction error, drill into a source from Library, expand "Edit a record", and update the value by record ID.

## Architecture

- `app.py` — Streamlit entry point + custom top tab bar
- `app_pages/` — one Python file per page (pulse, compare, trends, library, settings)
- `components/` — shared UI primitives (kpi_card, sparkline, freshness_badge)
- `theme.py` — color palette + Inter typography + injected CSS + Plotly template
- `db.py` — v2 normalized schema, upsert_metrics, all DB helpers
- `pdf_parser.py` — provider detection + per-provider parsers (Kidder, CBRE, Voit, JLL)
- `ingest/` — thin per-provider ingestion modules that wrap parsers + upsert
- `pdf_export.py` — WeasyPrint one-page snapshot renderer (used by Compare)
- `scripts/` — migration + diagnostic utilities
