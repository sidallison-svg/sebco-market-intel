# Sebco Market Intel

A local dashboard for extracting and tracking commercial real estate market data from Kidder Mathews quarterly PDF reports.

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
streamlit run dashboard.py
```

A browser window will open at http://localhost:8501 with the dashboard.

### 4. Upload PDFs

1. Click **Upload** in the sidebar
2. Drag and drop one or more Kidder Mathews quarterly market report PDFs
3. Review the extracted data and click **Save to database**
4. Use the other pages to view summaries, trends, and comparisons

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

- **Upload**: Import Kidder Mathews PDF reports. Shows extracted data preview and confidence scores before saving.
- **Summary**: Latest metrics per market and submarket at a glance.
- **Trends**: Line charts of any metric over time, filterable by market and submarket.
- **Comparison**: Side-by-side view of two submarkets.
- **Raw Data**: Searchable table of all records with CSV export and manual value correction.

## Supported Report Formats

The parser handles two Kidder Mathews report styles:

- **Structured** (e.g., Boise, Inland Empire): Reports with MARKET BREAKDOWN tables. Extraction accuracy is very high.
- **Narrative** (e.g., Seattle): Reports with data in prose text and sidebar callouts. Accuracy is good but some values may need manual correction via the Raw Data page.

## Confidence Indicators

Each extracted value has a confidence score:

- **High (>= 90%)**: Extracted from structured tables. Very reliable.
- **Medium (75-89%)**: Extracted from sidebar callouts or clear prose patterns.
- **Low (< 75%)**: Extracted from complex narrative text. Review recommended.

## Editing Data

On the Raw Data page, enter a record ID and new value to correct any parsing errors. All edits are tracked with your username and timestamp.
