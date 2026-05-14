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
- **Summary**: Latest metrics per market and submarket at a glance. Hover any metric's ? icon for its definition.
- **Trends**: Line charts of any metric over time, filterable by market and submarket. Series with fewer than 4 data points show dots only (no connecting line). Gaps longer than 6 months are not connected. Each series shows its data point count in the legend.
- **Comparison**: Side-by-side view of two submarkets. Expand "Metric definitions" for a glossary of all metrics.
- **Raw Data**: Searchable table of all records with CSV export and manual value correction. This is the only page that shows raw confidence scores.

## Smart Search

A search bar at the top of every page lets you jump to relevant data quickly. Type keywords like:

- **Market names**: "Boise", "Seattle", "Inland Empire"
- **Metrics**: "vacancy", "rent", "absorption", "construction", "cap rate"
- **Page names**: "trend", "compare", "raw", "export"
- **Combinations**: "Boise vacancy", "Seattle rent trend", "compare submarkets"

The search parses your keywords, selects the best page, and pre-fills the filters.

## Supported Report Formats

The parser handles two Kidder Mathews report styles:

- **Structured** (e.g., Boise, Inland Empire): Reports with MARKET BREAKDOWN tables. Extraction accuracy is very high.
- **Narrative** (e.g., Seattle): Reports with data in prose text and sidebar callouts. Accuracy is good but some values may need manual correction via the Raw Data page.

## Data Quality Indicators

Values extracted with lower confidence are flagged across the dashboard:

- A small warning icon appears next to any value where parser confidence is below 85%.
- Full confidence scores and parser strategy details are visible on the **Raw Data** page.
- Use the Raw Data page to correct any parsing errors. All edits are tracked with your username and timestamp.

## Metric Glossary

Hover the ? icon on any metric card or filter dropdown for its definition. Key metrics include:

- **Vacancy Rate**: Percentage of total inventory currently unoccupied and available for lease.
- **Lease Rate**: Average asking rental rate per square foot (typically monthly NNN).
- **Net Absorption**: Net change in occupied space. Positive = more occupied, negative = more vacated.
- **Total Inventory**: Total rentable building area tracked in the market/submarket.
- **Under Construction**: Square footage of new buildings being built but not yet delivered.
- **Cap Rate**: Capitalization rate (net operating income / property value). Lower = higher prices.

## Editing Data

On the Raw Data page, enter a record ID and new value to correct any parsing errors. All edits are tracked with your username and timestamp.
