"""
PDF parser for Kidder Mathews quarterly market reports.

Strategies:
  - Structured: MARKET BREAKDOWN / MARKET SUMMARY tables (Boise, Inland Empire, East Bay, Orange County)
  - Dual structured: side-by-side INDUSTRIAL/WAREHOUSE breakdowns (Silicon Valley)
  - Submarket statistics: full submarket grid on pages 2-3 (East Bay, Orange County, Silicon Valley)
  - Narrative: data in sidebar callouts and prose (Seattle)
"""

import logging
import os
import re
from datetime import date as date_type

import pdfplumber

logger = logging.getLogger(__name__)

PARSER_WARNINGS: list[str] = []  # populated per-call, callers may inspect


def _warn(msg: str):
    """Record a parser warning so the dashboard can surface it."""
    PARSER_WARNINGS.append(msg)
    logger.warning(msg)


# ---------------------------------------------------------------------------
# Quarter / date helpers
# ---------------------------------------------------------------------------

def quarter_to_date(quarter_str: str) -> str | None:
    """Convert '2Q 2025' -> '2025-06-30' (end-of-quarter date as ISO string)."""
    m = re.match(r"(\d)Q\s*(\d{4})", quarter_str.strip())
    if not m:
        return None
    q, y = int(m.group(1)), int(m.group(2))
    end_dates = {1: f"{y}-03-31", 2: f"{y}-06-30", 3: f"{y}-09-30", 4: f"{y}-12-31"}
    return end_dates.get(q)


def _normalize_quarter_label(label: str) -> str | None:
    """Normalize '2Q25' or '4Q24' or '1Q2025' -> '2Q 2025'."""
    label = label.strip().upper().replace(" ", "")
    m = re.match(r"(\d)Q(\d{2,4})", label)
    if not m:
        return None
    q = m.group(1)
    yr = m.group(2)
    if len(yr) == 2:
        yr = "20" + yr
    return f"{q}Q {yr}"


def _classify_period(col_quarter: str, header_quarter: str) -> str:
    """Determine if a column quarter is current, prior_quarter, or prior_year."""
    col_date = quarter_to_date(col_quarter)
    header_date = quarter_to_date(header_quarter)
    if not col_date or not header_date:
        return "historical"
    if col_date == header_date:
        return "current"
    cd = date_type.fromisoformat(col_date)
    hd = date_type.fromisoformat(header_date)
    diff = (hd - cd).days
    if 60 <= diff <= 200:
        return "prior_quarter"
    if 300 <= diff <= 400:
        return "prior_year"
    return "historical"


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def _parse_value(text: str) -> tuple[float | None, str]:
    """Parse a metric value string, return (numeric_value, unit)."""
    text = text.strip().replace(",", "")

    if text.upper().replace("/", "") == "NA":
        return None, "na"

    # Percentage (including negative)
    m = re.match(r"(-?[\d.]+)%$", text)
    if m:
        return float(m.group(1)), "percent"

    # Dollar amount
    m = re.match(r"\$(-?[\d.]+)$", text)
    if m:
        return float(m.group(1)), "dollar_per_sf"

    # Number with M/K suffix (e.g., 35.7M)
    m = re.match(r"(-?\d[\d.]*)([MKB])$", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        s = m.group(2).upper()
        if s == "M":
            val *= 1_000_000
        elif s == "K":
            val *= 1_000
        elif s == "B":
            val *= 1_000_000_000
        return val, "sf"

    # Plain number (must have at least one digit)
    m = re.match(r"(-?\d[\d.]*)$", text)
    if m:
        try:
            return float(m.group(1)), "number"
        except ValueError:
            return None, "unknown"

    return None, "unknown"


# ---------------------------------------------------------------------------
# Metric type normalization
# ---------------------------------------------------------------------------

METRIC_MAP = {
    "direct vacancy rate": "vacancy_rate",
    "vacancy rate": "vacancy_rate",
    "asking lease rate": "asking_rent",
    "asking lease rate/sf/mo": "asking_rent",
    "asking lease rate (nnn)": "asking_rent",
    "average asking rents/sf/mo": "asking_rent",
    "avg asking rents/sf/mo": "asking_rent",
    "average direct rental rate": "asking_rent",
    "direct rental rate": "asking_rent",
    "rental rate": "asking_rent",
    "average rental rate": "asking_rent",
    "average direct rental rate (nnn)": "asking_rent",
    "average rental rate (nnn)": "asking_rent",
    "cap rates": "cap_rate",
    "cap rate": "cap_rate",
    "under construction (sf)": "under_construction",
    "under construction": "under_construction",
    "under const. (sf)": "under_construction",
    "sf under construction": "under_construction",
    "deliveries (sf)": "deliveries",
    "deliveries": "deliveries",
    "new deliveries": "deliveries",
    "new deliveries (sf)": "deliveries",
    "construction deliveries": "construction_deliveries",
    "construction deliveries (sf)": "construction_deliveries",
    "direct net absorption (sf)": "net_absorption",
    "direct net absorption": "net_absorption",
    "net absorption (sf)": "net_absorption",
    "net absorption": "net_absorption",
    "1q26 direct net absorption": "net_absorption",
    "2q26 direct net absorption": "net_absorption",
    "3q26 direct net absorption": "net_absorption",
    "4q26 direct net absorption": "net_absorption",
    "ytd direct net absorption": "ytd_net_absorption",
    "2025 direct net absorption": "ytd_net_absorption",
    "2026 direct net absorption": "ytd_net_absorption",
    "1q26 direct net": "net_absorption",
    "1q26 leasing activity": "leasing_activity",
    "1q26 total leasing activity": "leasing_activity",
    "ytd total leasing activity": "ytd_leasing_activity",
    "2025 total leasing activity": "ytd_leasing_activity",
    "2026 total leasing activity": "ytd_leasing_activity",
    "2025 leasing activity": "ytd_leasing_activity",
    "1q26 gross absorption": "gross_absorption",
    "2025 gross absorption": "ytd_gross_absorption",
    "2026 gross absorption": "ytd_gross_absorption",
    "ytd gross absorption": "ytd_gross_absorption",
    "gross absorption": "gross_absorption",
    "total inventory": "total_inventory",
    "new construction (sf)": "new_construction",
    "new construction": "new_construction",
    "average sales price/sf": "avg_sales_price",
    "avg sales price/sf": "avg_sales_price",
    "availability rate": "availability_rate",
    "total available rate": "availability_rate",
    "total availability rate": "availability_rate",
    "sublet vacancy rate": "sublet_vacancy_rate",
    "sublease vacancy rate": "sublet_vacancy_rate",
    "total vacancy rate": "total_vacancy_rate",
    "leased activity (sf)": "leasing_activity",
    "leased activity": "leasing_activity",
    "leasing activity (sf)": "leasing_activity",
    "leasing activity": "leasing_activity",
    "leased sf": "leasing_activity",
    "lease transactions (sf)": "leasing_activity",
    "lease transactions": "leasing_activity",
    "direct leasing activity": "leasing_activity",
    "ytd leasing activity": "ytd_leasing_activity",
    "2025 leasing activity": "ytd_leasing_activity",
    "2026 leasing activity": "ytd_leasing_activity",
    "sales volume (sf)": "sales_volume",
    "sales volume": "sales_volume",
    "sold sf": "sales_volume",
    "sale transactions (sf)": "sales_volume",
    "sale transactions": "sales_volume",
}

UNIT_OVERRIDES = {
    "vacancy_rate": "percent",
    "availability_rate": "percent",
    "sublet_vacancy_rate": "percent",
    "total_vacancy_rate": "percent",
    "cap_rate": "percent",
    "asking_rent": "dollar_per_sf",
    "avg_sales_price": "dollar_per_sf",
    "net_absorption": "sf",
    "ytd_net_absorption": "sf",
    "gross_absorption": "sf",
    "ytd_gross_absorption": "sf",
    "under_construction": "sf",
    "deliveries": "sf",
    "construction_deliveries": "sf",
    "new_construction": "sf",
    "total_inventory": "sf",
    "leasing_activity": "sf",
    "ytd_leasing_activity": "sf",
    "sales_volume": "sf",
}


def _normalize_metric(raw_label: str) -> str | None:
    key = raw_label.strip().lower()
    if key in METRIC_MAP:
        return METRIC_MAP[key]
    for pattern, metric in METRIC_MAP.items():
        if key.startswith(pattern) or pattern.startswith(key):
            return metric
    return None


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

KNOWN_ASSET_CLASSES = ["industrial", "office", "retail", "multifamily", "warehouse"]

# Lines that should be ignored when capturing the market name block.
_HEADER_NOISE = re.compile(
    r"^(VACANCY|ABSORPTION|RENTAL\s*RATES?|CONSTRUCTION|DELIVERIES|"
    r"YEAR-OVER-YEAR|MARKET\s*DRIVERS?|ECONOMIC\s*(OVERVIEW|REVIEW)|"
    r"NEAR-TERM\s*OUTLOOK|SIGNIFICANT)",
    re.IGNORECASE,
)


def _title_case_market(raw: str) -> str:
    """Convert 'EAST BAY' / 'east bay' to 'East Bay' (preserving slashes)."""
    parts = re.split(r"(\s+|/)", raw.strip())
    return "".join(p if p.isspace() or p == "/" else p.capitalize() for p in parts)


def _market_from_filename(filepath: str) -> tuple[str | None, str | None]:
    """Extract (market, asset_class) from KM filename convention.

    Pattern: {asset}-market-research-{market-slug}-{year}-{quarter}.pdf
    Returns (None, None) if pattern doesn't match.
    """
    name = os.path.basename(filepath)
    m = re.match(
        r"^([a-z]+)-market-research-([a-z][a-z-]*?)-(\d{4})-(\dq)\.pdf$",
        name,
        re.IGNORECASE,
    )
    if not m:
        return None, None
    asset = m.group(1).lower()
    market_slug = m.group(2).replace("-", " ")
    return _title_case_market(market_slug), asset


def _detect_market_from_trends(page1_text: str) -> str | None:
    """Find 'MARKET TRENDS' and capture the market name from following lines.

    Handles three observed layouts:
      Layout A: 'MARKET TRENDS' on its own line, market name on the next 1-2 lines
                (East Bay, Orange County, Silicon Valley)
      Layout B: 'MARKET TRENDS | SEATTLE' single line (Seattle)
      Layout C: 'MARKET TRENDS' then 'BOISE INDUSTRIAL' on one line (Boise)
    """
    lines = page1_text.split("\n")

    for idx, line in enumerate(lines):
        if "MARKET TRENDS" not in line.upper():
            continue

        # Layout B: pipe-delimited on same line
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            for p in parts:
                if "MARKET TRENDS" in p.upper():
                    continue
                if p:
                    return _title_case_market(_strip_asset(p))

        # Layouts A/C: market name is in the next 1-3 non-noise lines
        collected: list[str] = []
        for j in range(idx + 1, min(idx + 5, len(lines))):
            candidate = lines[j].strip()
            if not candidate:
                if collected:
                    break
                continue
            if _HEADER_NOISE.match(candidate):
                break
            # Stop on first line that doesn't look like ALL CAPS (header is typically caps)
            if not _looks_like_header_line(candidate):
                break
            collected.append(candidate)
            if len(collected) >= 3:
                break

        if not collected:
            continue

        joined = " ".join(collected)
        return _title_case_market(_strip_asset(joined))

    return None


def _looks_like_header_line(text: str) -> bool:
    """Header lines for the market title block are short, mostly uppercase, no digits."""
    if len(text) > 60:
        return False
    if re.search(r"\d", text):
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    upper_frac = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper_frac >= 0.7


def _strip_asset(text: str) -> str:
    """Remove an asset class word from the title block (e.g. 'BOISE INDUSTRIAL' -> 'BOISE')."""
    for ac in KNOWN_ASSET_CLASSES:
        text = re.sub(rf"\b{ac}\b", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _detect_header(page1_text: str, filepath: str | None = None) -> dict:
    """Extract quarter, market, and asset class from report header.

    Strategy:
      1. Find 'MARKET TRENDS' and capture market name from following lines.
      2. Detect asset class from the same header region.
      3. Fall back to filename pattern if market or asset class still missing.
    """
    info: dict = {"quarter": None, "market": None, "asset_class": None}
    header_block = page1_text[:800]

    m = re.search(r"(\dQ)\s*(\d{4})", header_block)
    if m:
        info["quarter"] = f"{m.group(1)} {m.group(2)}"

    market = _detect_market_from_trends(page1_text)
    if market:
        info["market"] = market

    upper = header_block.upper()
    for ac in KNOWN_ASSET_CLASSES:
        if ac.upper() in upper:
            info["asset_class"] = ac
            break

    # Filename fallback
    if filepath and (not info["market"] or not info["asset_class"]):
        fn_market, fn_asset = _market_from_filename(filepath)
        if not info["market"] and fn_market:
            info["market"] = fn_market
            _warn(
                f"Market not found in PDF header for {os.path.basename(filepath)}, "
                f"using filename fallback: '{fn_market}' — verify on Raw Data page."
            )
        if not info["asset_class"] and fn_asset:
            info["asset_class"] = fn_asset
            _warn(
                f"Asset class not found in PDF header for {os.path.basename(filepath)}, "
                f"using filename fallback: '{fn_asset}'."
            )

    return info


# ---------------------------------------------------------------------------
# Strategy 1: Structured table parsing (Boise, Inland Empire style)
# ---------------------------------------------------------------------------

def _preprocess_table_text(text: str) -> str:
    """
    Fix common pdfplumber layout issues:
    - Join 'Direct Net\\nAbsorption (SF)' into one line
    - Remove chart-value noise at end of data rows
    """
    # Join split metric labels
    text = re.sub(r"Direct\s*Net\s*\n\s*Absorption", "Direct Net Absorption", text)
    return text


def _extract_column_headers(text: str) -> list[str] | None:
    """
    Find quarter column headers in text. Handles two layouts:
    1. 'MARKET 2Q25 1Q25 2Q24 YOY Change' (BREAKDOWN style)
    2. '2Q25 4Q24 1Q24 Change' preceded by 'YOY' on prior line (SUMMARY style)
    """
    # Layout 1: quarters on same line as MARKET keyword
    m = re.search(
        r"(?:MARKET\s+)?(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(?:YOY\s*)?Change",
        text, re.IGNORECASE
    )
    if m:
        return [m.group(1), m.group(2), m.group(3)]

    # Layout 2: quarters on a line that ends with 'Change'
    m = re.search(
        r"(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+Change",
        text, re.IGNORECASE
    )
    if m:
        return [m.group(1), m.group(2), m.group(3)]

    return None


# Token pattern: matches values like $1.01, 8.0%, -81.4%, 35.7M, 60906882, -99969, 0, N/A
# Note: N/A is bounded by \b so it doesn't match inside words like "Buena", "Santa", "Dana".
_VAL_TOKEN = re.compile(
    r"-?\$\d[\d,.]+"       # dollar (must start with digit after $)
    r"|-?\d[\d,.]*[MKB]"   # number with suffix
    r"|-?\d[\d,.]*%"       # percentage
    r"|-?\d[\d,.]*"        # plain number including single digits like 0
    r"|\bN/?A\b",          # N/A — word-bounded to avoid 'Ana','Dana','Buena','Laguna'
    re.IGNORECASE
)


def _parse_structured_table(text: str, header: dict, source: str, page_num: int,
                            submarket: str | None) -> list[dict]:
    """Parse MARKET BREAKDOWN or MARKET SUMMARY block line by line."""
    text = _preprocess_table_text(text)
    records = []
    report_quarter = header["quarter"]
    report_date = quarter_to_date(report_quarter)

    col_labels_raw = _extract_column_headers(text)
    if not col_labels_raw:
        return records

    col_quarters = [_normalize_quarter_label(c) for c in col_labels_raw]

    # Find where data rows start (after the BREAKDOWN/SUMMARY + column header)
    breakdown_match = re.search(r"(?:BREAKDOWN|SUMMARY)", text, re.IGNORECASE)
    col_header_match = re.search(
        r"(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(?:YOY\s*)?Change",
        text, re.IGNORECASE
    )
    if not col_header_match:
        return records

    # Start parsing from whichever comes later
    start = col_header_match.end()
    lines = text[start:].split("\n")

    # Also handle a second set of column headers (IE has two blocks)
    pending_label = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Stop if we hit chart labels or non-data sections
        # (but NOT data rows like "Direct Net Absorption (SF) 92,225...")
        if re.match(r"(LEASE RATE,|UNDER CONSTRUCTION &|Data Source|Kidder Mathews)", line, re.IGNORECASE):
            break

        # Check if this line is a second column header block
        second_cols = re.match(
            r"(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(\dQ\s*\d{2,4})\s+(?:YOY\s*)?Change",
            line, re.IGNORECASE
        )
        if second_cols:
            col_quarters = [_normalize_quarter_label(second_cols.group(i)) for i in (1, 2, 3)]
            continue

        # Skip pure noise lines (chart tick values, single short numbers)
        if re.match(r"^[\d.]+[MKB%]?$", line) and len(line) < 8:
            continue

        # Try to split line into label + values
        # Find all value tokens in the line
        tokens = list(_VAL_TOKEN.finditer(line))

        if len(tokens) >= 3:
            # Label is everything before the first value token
            label_end = tokens[0].start()
            raw_label = line[:label_end].strip()

            # Handle continuation from previous line (e.g., "Absorption (SF) values...")
            if pending_label and not raw_label:
                raw_label = pending_label
                pending_label = None
            elif pending_label:
                raw_label = pending_label + " " + raw_label
                pending_label = None

            # Clean label
            raw_label = re.sub(r"\s*(BREAKDOWN|SUMMARY)\s*", "", raw_label, flags=re.IGNORECASE).strip()

            metric_type = _normalize_metric(raw_label)
            if metric_type is None:
                continue

            unit = UNIT_OVERRIDES.get(metric_type, "unknown")
            raw_line = line

            # Take first 3 tokens as the 3 column values
            col_values = [tokens[i].group() for i in range(min(3, len(tokens)))]
            yoy_value = tokens[3].group() if len(tokens) >= 4 else None

            for i, val_str in enumerate(col_values):
                if i >= len(col_quarters) or col_quarters[i] is None:
                    continue
                val, parsed_unit = _parse_value(val_str)
                if val is None:
                    continue
                final_unit = unit if unit != "unknown" else parsed_unit
                period_type = _classify_period(col_quarters[i], report_quarter)

                records.append({
                    "source": source,
                    "source_page": page_num,
                    "report_date": report_date,
                    "quarter": report_quarter,
                    "metric_period": quarter_to_date(col_quarters[i]),
                    "period_type": period_type,
                    "market": header["market"],
                    "submarket": submarket,
                    "asset_class": header["asset_class"],
                    "metric_type": metric_type,
                    "metric_value": val,
                    "unit": final_unit,
                    "confidence": 0.95,
                    "raw_text": raw_line.strip()[:200],
                    "parser_strategy": "structured",
                    "extraction_notes": f"Column: {col_quarters[i]} ({period_type})",
                })

            # YOY change
            if yoy_value and yoy_value.upper().replace("/", "") != "NA":
                yoy_val, _ = _parse_value(yoy_value)
                if yoy_val is not None:
                    records.append({
                        "source": source,
                        "source_page": page_num,
                        "report_date": report_date,
                        "quarter": report_quarter,
                        "metric_period": report_date,
                        "period_type": "yoy_change",
                        "market": header["market"],
                        "submarket": submarket,
                        "asset_class": header["asset_class"],
                        "metric_type": metric_type,
                        "metric_value": yoy_val,
                        "unit": "percent_change",
                        "confidence": 0.90,
                        "raw_text": raw_line.strip()[:200],
                        "parser_strategy": "structured",
                        "extraction_notes": "YOY change value",
                    })

        elif len(tokens) < 3:
            # Could be a split label like "Direct Net" — save for next line
            candidate = line.strip()
            if _normalize_metric(candidate) or re.match(r"(Direct Net|Net)\s*$", candidate, re.IGNORECASE):
                pending_label = candidate

    return records


# ---------------------------------------------------------------------------
# Strategy 2: Narrative / sidebar parsing (Seattle style)
# ---------------------------------------------------------------------------

def _parse_sidebar(page_text: str, header: dict, source: str, page_num: int) -> list[dict]:
    """
    Parse sidebar callouts from page 1.

    pdfplumber interleaves sidebar values with paragraph text, so the value
    (e.g., '9.3%') and its label (e.g., 'VACANCY RATE') may be separated by
    several lines of body text. Strategy: find each label, then search
    backwards through preceding lines for a short line containing just a value.
    """
    records = []
    report_quarter = header["quarter"]
    report_date = quarter_to_date(report_quarter)

    lines = page_text.split("\n")

    # (label_regex, metric_type, unit, value_regex for the short preceding line)
    sidebar_defs = [
        (r"VACANCY\s*RATE", "vacancy_rate", "percent", r"^([\d.]+)%$"),
        (r"ASKING\s*RENT", "asking_rent", "dollar_per_sf", r"^\$([\d.]+)$"),
        (r"NET\s*ABSORPTION", "net_absorption", "sf", r"^(-?[\d,.]+[MKB]?)\s*SF$"),
    ]

    for label_re, metric_type, unit, val_re in sidebar_defs:
        # Find lines containing the label (may be at the end of a long line)
        for i, line in enumerate(lines):
            if re.search(label_re, line, re.IGNORECASE):
                # Search backwards up to 5 lines for a short value line
                for j in range(max(0, i - 5), i):
                    prev = lines[j].strip()
                    if len(prev) > 20:
                        continue  # Skip long paragraph lines
                    vm = re.match(val_re, prev, re.IGNORECASE)
                    if vm:
                        raw = vm.group(1).replace(",", "")
                        val, _ = _parse_value(
                            raw if metric_type not in ("asking_rent",) else f"${raw}"
                        )
                        if val is not None:
                            records.append({
                                "source": source,
                                "source_page": page_num,
                                "report_date": report_date,
                                "quarter": report_quarter,
                                "metric_period": report_date,
                                "period_type": "current",
                                "market": header["market"],
                                "submarket": None,
                                "asset_class": header["asset_class"],
                                "metric_type": metric_type,
                                "metric_value": val,
                                "unit": unit,
                                "confidence": 0.90,
                                "raw_text": f"{prev} ... {line.strip()[-30:]}",
                                "parser_strategy": "sidebar",
                                "extraction_notes": "Extracted from sidebar callout",
                            })
                        break
                break  # Only match the first occurrence of each label

    # Under Construction sidebar — "UNDER" and "CONSTRUCTION" are often on
    # separate lines (or CONSTRUCTION is at the end of a paragraph line).
    # Search for any line containing "CONSTRUCTION" (not preceded by "&"),
    # verify "UNDER" appears on a nearby preceding line, then look further
    # back for the value.
    for i, line in enumerate(lines):
        if re.search(r"(?<![&\w])CONSTRUCTION", line) and not re.search(r"UNDER CONSTRUCTION &", line):
            # Check that "UNDER" appears within 3 lines before
            has_under = any(
                "UNDER" in lines[j] for j in range(max(0, i - 3), i + 1)
            )
            if not has_under:
                continue
            # Search backwards for a short SF value line
            for j in range(max(0, i - 6), i):
                prev = lines[j].strip()
                if len(prev) > 20:
                    continue
                vm = re.match(r"^([\d,.]+[MKB]?)\s*SF$", prev, re.IGNORECASE)
                if vm:
                    raw = vm.group(1).replace(",", "")
                    val, _ = _parse_value(raw)
                    if val is not None:
                        records.append({
                            "source": source,
                            "source_page": page_num,
                            "report_date": report_date,
                            "quarter": report_quarter,
                            "metric_period": report_date,
                            "period_type": "current",
                            "market": header["market"],
                            "submarket": None,
                            "asset_class": header["asset_class"],
                            "metric_type": "under_construction",
                            "metric_value": val,
                            "unit": "sf",
                            "confidence": 0.90,
                            "raw_text": f"{prev} ... UNDER CONSTRUCTION",
                            "parser_strategy": "sidebar",
                            "extraction_notes": "Under construction from sidebar callout",
                        })
                    break
            break

    # Total inventory from narrative text:
    # "Total inventory was 410M SF" or "ended at 409.7M SF"
    inv_m = re.search(
        r"(?:Total\s+inventory\s+was|ended\s+at|inventory\s+of)\s+([\d,.]+[MKB]?)\s*SF",
        page_text, re.IGNORECASE
    )
    if inv_m:
        raw = inv_m.group(1).replace(",", "")
        val, _ = _parse_value(raw)
        if val is not None:
            records.append({
                "source": source,
                "source_page": page_num,
                "report_date": report_date,
                "quarter": report_quarter,
                "metric_period": report_date,
                "period_type": "current",
                "market": header["market"],
                "submarket": None,
                "asset_class": header["asset_class"],
                "metric_type": "total_inventory",
                "metric_value": val,
                "unit": "sf",
                "confidence": 0.85,
                "raw_text": inv_m.group(0).strip(),
                "parser_strategy": "narrative",
                "extraction_notes": "Total inventory from narrative text",
            })

    # Cap rate from narrative
    cap_m = re.search(
        r"average\s+capitalization\s+rate\s+of\s+([\d.]+)%",
        page_text, re.IGNORECASE
    )
    if cap_m:
        records.append({
            "source": source,
            "source_page": page_num,
            "report_date": report_date,
            "quarter": report_quarter,
            "metric_period": report_date,
            "period_type": "current",
            "market": header["market"],
            "submarket": None,
            "asset_class": header["asset_class"],
            "metric_type": "cap_rate",
            "metric_value": float(cap_m.group(1)),
            "unit": "percent",
            "confidence": 0.85,
            "raw_text": cap_m.group(0).strip(),
            "parser_strategy": "narrative",
            "extraction_notes": "Cap rate from narrative text",
        })

    return records


# Submarket section patterns for Seattle-style reports
SEATTLE_SUBMARKETS = {
    "SEATTLE CLOSE-IN REVIEW": "Seattle Close-In",
    "SOUTHEND REVIEW": "Southend",
    "NORTHEND REVIEW": "Northend",
    "PIERCE COUNTY REVIEW": "Pierce County",
    "THURSTON COUNTY REVIEW": "Thurston County",
    "EASTSIDE REVIEW": "Eastside",
    "SKAGIT / WHATCOM COUNTIES": "Skagit/Whatcom",
    "SKAGIT/WHATCOM COUNTIES": "Skagit/Whatcom",
}


def _clean_section_text(text: str) -> str:
    """
    Remove chart axis noise interleaved with narrative text by pdfplumber.
    Lines like '5M 14%', '$1.04 $1.04 $1.05', '2M 8%' are chart labels
    that break regex matching.
    """
    cleaned = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Skip lines that are purely chart data: only numbers, $, %, M, K, SF, spaces
        alpha = re.sub(r"[\d$%.,\-+\s/]", "", stripped).replace("M", "").replace("K", "").replace("SF", "").replace("PSF", "")
        if len(alpha) < 3 and len(stripped) < 30:
            continue
        # For longer lines, strip trailing chart data (sequences of dollar/pct values at end)
        stripped = re.sub(r"\s+\d+[MK]\s+\$[\d.]+(?:\s+\$[\d.]+)*\s+[\d.]+%$", "", stripped)
        stripped = re.sub(r"\s+\d+[MK]\s+[\d.]+%$", "", stripped)
        cleaned.append(stripped)
    return " ".join(cleaned)


def _parse_narrative_submarkets(full_text: str, header: dict, source: str,
                                page_breaks: list[int]) -> list[dict]:
    """Parse submarket sections from narrative-style reports."""
    records = []
    report_quarter = header["quarter"]
    report_date = quarter_to_date(report_quarter)

    # Find submarket sections
    section_starts = []
    for section_header, submarket_name in SEATTLE_SUBMARKETS.items():
        idx = full_text.find(section_header)
        if idx >= 0:
            section_starts.append((idx, section_header, submarket_name))
    section_starts.sort(key=lambda x: x[0])

    def _page_for_offset(offset):
        for i, brk in enumerate(page_breaks):
            if offset < brk:
                return i + 1
        return len(page_breaks) + 1

    for i, (start_idx, section_header, submarket_name) in enumerate(section_starts):
        end_idx = section_starts[i + 1][0] if i + 1 < len(section_starts) else len(full_text)
        section_raw = full_text[start_idx:end_idx]
        section = _clean_section_text(section_raw)
        page_num = _page_for_offset(start_idx)

        # --- Vacancy ---
        # Strategy: find percentage values near "vacancy" that look like a
        # current-quarter vacancy rate. Two-column PDFs scramble sentence
        # structure, so we anchor on the value format rather than parsing
        # full sentences.
        vacancy_val = None
        vacancy_raw = ""

        # Pattern 1: "to X.XX% in Q1" or "to X.XX%, up/down N basis"
        for vm in re.finditer(r"to\s+([\d.]+)%\s*(?:in\s+Q\d|,?\s*(?:up|down|a)\s+\d)", section):
            # Check that "vacancy" appears within 300 chars before
            pre = section[max(0, vm.start() - 300):vm.start()]
            if re.search(r"vacanc", pre, re.IGNORECASE):
                vacancy_val = float(vm.group(1))
                vacancy_raw = vm.group(0)
                break

        # Pattern 2: "basis points to X.XX%" near "vacancy"
        if vacancy_val is None:
            for vm in re.finditer(r"basis\s+points?\s+to\s+([\d.]+)%", section):
                pre = section[max(0, vm.start() - 300):vm.start()]
                if re.search(r"vacanc", pre, re.IGNORECASE):
                    vacancy_val = float(vm.group(1))
                    vacancy_raw = vm.group(0)
                    break

        # Pattern 3: "X.XX%, N basis point" near "vacancy"
        if vacancy_val is None:
            for vm in re.finditer(r"([\d.]+)%\s*,\s*\d+\s*basis\s*point", section):
                pre = section[max(0, vm.start() - 300):vm.start()]
                if re.search(r"vacanc", pre, re.IGNORECASE):
                    vacancy_val = float(vm.group(1))
                    vacancy_raw = vm.group(0)
                    break

        # Pattern 3b: "X.XX%, up/down from" near "vacancy"
        if vacancy_val is None:
            for vm in re.finditer(r"(?:is|to)\s+([\d.]+)%\s*,\s*(?:up|down)", section):
                pre = section[max(0, vm.start() - 300):vm.start()]
                if re.search(r"vacanc", pre, re.IGNORECASE):
                    vacancy_val = float(vm.group(1))
                    vacancy_raw = vm.group(0)
                    break

        # Pattern 4: direct "vacancy ... to X.XX%" with longer gap
        if vacancy_val is None:
            vm = re.search(r"[Vv]acanc\w*\s+(?:rate\s+)?(?:rose|increased|fell|declined).{0,250}?to\s+([\d.]+)%", section, re.DOTALL)
            if vm:
                vacancy_val = float(vm.group(1))
                vacancy_raw = vm.group(0)

        if vacancy_val is not None and vacancy_val < 50:  # sanity check
            records.append({
                "source": source,
                "source_page": page_num,
                "report_date": report_date,
                "quarter": report_quarter,
                "metric_period": report_date,
                "period_type": "current",
                "market": header["market"],
                "submarket": submarket_name,
                "asset_class": header["asset_class"],
                "metric_type": "vacancy_rate",
                "metric_value": vacancy_val,
                "unit": "percent",
                "confidence": 0.80,
                "raw_text": vacancy_raw.strip()[:200],
                "parser_strategy": "narrative",
                "extraction_notes": f"Vacancy from {section_header}",
            })

        # --- Rent ---
        rent_val = None
        rent_raw = ""

        rent_patterns = [
            # "blended rate of $X.XX" or "blended rent remains at $X.XX"
            r"(?:blended|average\s+blended)\s+(?:rate|rent)\s+(?:of|remains?\s+at|is|was|at)\s+\$([\d.]+)",
            # "average blended rate of $X.XX" with words between
            r"average\s+blended\s+rate\s+of\s+\$([\d.]+)",
            # "rates remain stable at $X.XX PSF"
            r"[Rr](?:ental\s+)?ates?\s+remain\s+stable\s+at\s+\$([\d.]+)\s*PSF",
            # "to $X.XX PSF" near "rent/rate" (prefer "to" values)
            r"(?:rents?|rates?)\s+(?:fell|declined|dropped|dipped).{0,100}?to.{0,80}?\$([\d.]+)\s*PSF",
            # "ending the year at $X.XX" (for rates that held through current quarter)
            r"ending\s+the\s+year\s+at\s+\$([\d.]+)",
            # "$X.XX PSF" near "blended" or "asking"
            r"(?:blended|asking).{0,60}?\$([\d.]+)\s*PSF",
            # "Rent has fallen to $X.XX PSF"
            r"[Rr]ent\s+has\s+fallen\s+to\s+\$([\d.]+)\s*PSF",
            # "rent remains at $X.XX PSF"
            r"[Rr]ent\s+(?:remains?|remained|is|was)\s+(?:at\s+)?\$([\d.]+)",
            # "rates declined/fell ... $X.XX PSF"
            r"rates?\s+(?:declined|fell|dropped|dipped).{0,60}?\$([\d.]+)\s*PSF",
        ]
        for rp in rent_patterns:
            rm = re.search(rp, section, re.IGNORECASE | re.DOTALL)
            if rm:
                rent_val = float(rm.group(1))
                rent_raw = rm.group(0)
                break

        if rent_val is not None and rent_val < 20:  # sanity check
            records.append({
                "source": source,
                "source_page": page_num,
                "report_date": report_date,
                "quarter": report_quarter,
                "metric_period": report_date,
                "period_type": "current",
                "market": header["market"],
                "submarket": submarket_name,
                "asset_class": header["asset_class"],
                "metric_type": "asking_rent",
                "metric_value": rent_val,
                "unit": "dollar_per_sf",
                "confidence": 0.75,
                "raw_text": rent_raw.strip()[:200],
                "parser_strategy": "narrative",
                "extraction_notes": f"Rent from {section_header}",
            })

    return records


# ---------------------------------------------------------------------------
# Strategy 3: SUBMARKET STATISTICS table parser
# ---------------------------------------------------------------------------

def _group_rows(words: list[dict], y_tol: float = 3.0) -> list[list[dict]]:
    """Group pdfplumber words into rows by y-coordinate."""
    rows: list[list[dict]] = []
    cur: list[dict] = []
    last_top: float | None = None
    for w in sorted(words, key=lambda x: (x["top"], x["x0"])):
        if last_top is None or abs(w["top"] - last_top) > y_tol:
            if cur:
                rows.append(cur)
            cur = [w]
            last_top = w["top"]
        else:
            cur.append(w)
            last_top = (last_top + w["top"]) / 2
    if cur:
        rows.append(cur)
    for r in rows:
        r.sort(key=lambda w: w["x0"])
    return rows


def _build_columns(header_rows: list[list[dict]],
                   data_row: list[dict] | None = None) -> list[dict]:
    """Cluster header-row words into column groups, anchored to data-row positions.

    Strategy: data values are left-aligned at consistent x0 positions across
    rows. Use the first data row's x0 positions as canonical column anchors,
    then assign each header word to the column whose anchor is the largest
    one <= word.x0 + slack. This correctly merges multi-word labels like
    'Direct Net Absorption' where 'Net' is indented within the column.

    If no data_row is given, fall back to header-only x0 clustering.

    Returns list of {"x_min", "x_max", "x_center", "label"} sorted left-to-right.
    """
    all_words = [dict(w) for row in header_rows for w in row]
    if not all_words:
        return []
    for w in all_words:
        w["x_center"] = (w["x0"] + w["x1"]) / 2

    # Anchor x0 positions — preferred source is the data row, filtered to
    # only value-shaped tokens (so submarket names with internal spaces and
    # mid-row unit labels like "SF" don't create spurious columns).
    anchor_source: list[dict]
    if data_row:
        value_words = [w for w in data_row if _VAL_TOKEN.fullmatch(w["text"])]
        # Always include the leftmost word as the submarket-name anchor.
        leftmost = min(data_row, key=lambda w: w["x0"]) if data_row else None
        if leftmost is not None and leftmost not in value_words:
            anchor_source = [leftmost] + value_words
        else:
            anchor_source = value_words or data_row
    else:
        anchor_source = all_words

    x0_tol = 3.0
    x0_anchors: list[float] = []
    for x0 in sorted(w["x0"] for w in anchor_source):
        if not x0_anchors or x0 - x0_anchors[-1] > x0_tol:
            x0_anchors.append(x0)
        else:
            x0_anchors[-1] = (x0_anchors[-1] + x0) / 2

    if len(x0_anchors) <= 1:
        return []

    assignments: dict[int, list[dict]] = {i: [] for i in range(len(x0_anchors))}
    for w in all_words:
        col_idx = 0
        for i, a in enumerate(x0_anchors):
            if a <= w["x0"] + 1:
                col_idx = i
            else:
                break
        assignments[col_idx].append(w)

    columns = []
    for i, cluster in assignments.items():
        anchor_x0 = x0_anchors[i]
        if not cluster:
            # Still emit an empty column so positional value mapping aligns
            # with the data-row anchors.
            columns.append({
                "x_center": anchor_x0,
                "x_min": anchor_x0,
                "x_max": anchor_x0,
                "label": "",
            })
            continue
        cluster.sort(key=lambda w: (w["top"], w["x0"]))
        label = " ".join(w["text"] for w in cluster)
        label = re.sub(r"\([^)]*\)", "", label)  # drop "(SF)", "(NNN)"
        label = re.sub(r"\s+", " ", label).strip()
        columns.append({
            "x_center": sum(w["x_center"] for w in cluster) / len(cluster),
            "x_min": min(w["x0"] for w in cluster),
            "x_max": max(w["x1"] for w in cluster),
            "label": label,
        })
    columns.sort(key=lambda c: c["x_min"])
    return columns


def _parse_submarket_statistics(page, header: dict, source: str,
                                page_num: int) -> list[dict]:
    """Parse a SUBMARKET STATISTICS table on a single page using word positions.

    Handles single-asset tables (East Bay, Orange County) and the dual
    industrial/warehouse layout on Silicon Valley page 3.
    """
    text = page.extract_text() or ""
    if "SUBMARKET STATISTICS" not in text.upper():
        return []

    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    rows = _group_rows(words)

    stats_idx = None
    for i, row in enumerate(rows):
        if "SUBMARKET STATISTICS" in " ".join(w["text"] for w in row).upper():
            stats_idx = i
            break
    if stats_idx is None:
        return []

    # A data row has at least one true value: percent, dollar, or number with
    # comma/decimal. Bare integers like '0' or quarter labels like '1Q26' don't
    # qualify (those appear in header rows).
    data_signal = re.compile(r"%|\$\d|[\d.],[\d.]|\d+\.\d")

    header_rows: list[list[dict]] = []
    data_start: int | None = None
    for i in range(stats_idx + 1, len(rows)):
        row = rows[i]
        line = " ".join(w["text"] for w in row)
        if data_signal.search(line):
            data_start = i
            break
        header_rows.append(row)
        if len(header_rows) >= 5:
            break

    if not header_rows or data_start is None:
        return []

    columns = _build_columns(header_rows, data_row=rows[data_start])
    if len(columns) < 2:
        return []

    # The leftmost column is the submarket-name column; the rest are metrics.
    metric_cols = columns[1:]
    metric_specs = [(_normalize_metric(c["label"]), c["label"]) for c in metric_cols]

    market = header.get("market")
    asset_class = header.get("asset_class") or "industrial"
    report_quarter = header["quarter"]
    report_date = quarter_to_date(report_quarter)

    market_pat = re.escape(market or "") if market else ""
    total_pat = re.compile(
        rf"^{market_pat}\s+(Industrial\s+Totals?|Warehouse\s+Totals?|Totals?)\b"
        if market_pat else r"^\b$",
        re.IGNORECASE,
    )

    records = []
    current_asset = asset_class

    # Stop markers: anything that signals end of the statistics block.
    stop_pat = re.compile(
        r"(BIGGEST\s+(SALE|LEASE)|NEAR-TERM\s+OUTLOOK|DATA\s+SOURCE|"
        r"COMMERCIAL\s+BROKERAGE|KIDDER\s+MATHEWS\s+IS)",
        re.IGNORECASE,
    )

    for i in range(data_start, len(rows)):
        row = rows[i]
        line = " ".join(w["text"] for w in row).strip()
        if not line:
            continue
        if stop_pat.search(line):
            break

        tokens = list(_VAL_TOKEN.finditer(line))
        if len(tokens) < 2:
            continue

        first_val_start = tokens[0].start()
        sub_name = line[:first_val_start].strip()
        if not sub_name:
            continue

        is_total = bool(total_pat.search(sub_name)) if market_pat else False
        # Silicon Valley: the "Industrial Total" row ends the industrial block.
        is_industrial_total = bool(
            market_pat and re.search(
                rf"^{market_pat}\s+Industrial\s+Total", sub_name, re.IGNORECASE
            )
        )
        is_warehouse_total = bool(
            market_pat and re.search(
                rf"^{market_pat}\s+Warehouse\s+Total", sub_name, re.IGNORECASE
            )
        )

        submarket = None if is_total else sub_name

        n = min(len(tokens), len(metric_specs))
        for j in range(n):
            metric_type, raw_label = metric_specs[j]
            if metric_type is None:
                continue
            val, parsed_unit = _parse_value(tokens[j].group())
            if val is None:
                continue
            unit = UNIT_OVERRIDES.get(metric_type, parsed_unit if parsed_unit != "unknown" else "number")
            records.append({
                "source": source,
                "source_page": page_num,
                "report_date": report_date,
                "quarter": report_quarter,
                "metric_period": report_date,
                "period_type": "current",
                "market": market,
                "submarket": submarket,
                "asset_class": current_asset,
                "metric_type": metric_type,
                "metric_value": val,
                "unit": unit,
                "confidence": 0.92,
                "raw_text": line[:200],
                "parser_strategy": "submarket_statistics",
                "extraction_notes": f"Col: {raw_label}",
            })

        # Switch asset_class context after emitting the boundary row.
        if is_industrial_total:
            current_asset = "warehouse"
        elif is_warehouse_total:
            current_asset = asset_class  # reset

    return records


# ---------------------------------------------------------------------------
# Strategy 4: Side-by-side INDUSTRIAL / WAREHOUSE breakdowns (Silicon Valley)
# ---------------------------------------------------------------------------

def _parse_dual_breakdown(page, header: dict, source: str,
                          page_num: int) -> list[dict] | None:
    """Detect and parse side-by-side INDUSTRIAL/WAREHOUSE breakdowns.

    Returns list of records, or None if this page doesn't have the dual layout.
    """
    text = page.extract_text() or ""
    if not (re.search(r"INDUSTRIAL\s+MARKET\s+BREAKDOWN", text, re.IGNORECASE)
            and re.search(r"WAREHOUSE\s+MARKET\s+BREAKDOWN", text, re.IGNORECASE)):
        return None

    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    rows = _group_rows(words)

    dual_idx = None
    waho_x = None
    for i, row in enumerate(rows):
        text_line = " ".join(w["text"] for w in row).upper()
        if "INDUSTRIAL" in text_line and "WAREHOUSE" in text_line and "BREAKDOWN" in text_line:
            dual_idx = i
            for w in row:
                if w["text"].upper() == "WAREHOUSE":
                    waho_x = w["x0"]
                    break
            break
    if dual_idx is None:
        return None

    split_x = (waho_x - 10) if waho_x else (page.width / 2)

    industrial_lines: list[str] = []
    warehouse_lines: list[str] = []
    for row in rows[dual_idx:]:
        line_upper = " ".join(w["text"] for w in row).upper()
        if re.search(r"(DATA\s+SOURCE|KIDDER\s+MATHEWS\s+IS|COMMERCIAL\s+BROKERAGE)",
                     line_upper):
            break
        left = [w for w in row if w["x0"] < split_x]
        right = [w for w in row if w["x0"] >= split_x]
        if left:
            industrial_lines.append(" ".join(w["text"] for w in left))
        if right:
            warehouse_lines.append(" ".join(w["text"] for w in right))

    records: list[dict] = []
    for side_lines, ac in [
        (industrial_lines, "industrial"),
        (warehouse_lines, "warehouse"),
    ]:
        synthetic = "\n".join(side_lines)
        # Ensure the synthetic text contains a BREAKDOWN keyword so
        # _parse_structured_table doesn't bail out searching for it.
        if "BREAKDOWN" not in synthetic.upper():
            synthetic = "MARKET BREAKDOWN\n" + synthetic
        side_header = {**header, "asset_class": ac}
        side_records = _parse_structured_table(
            synthetic, side_header, source, page_num, submarket=None,
        )
        for r in side_records:
            r["parser_strategy"] = "dual_breakdown"
            r["extraction_notes"] = f"{ac} side | " + (r.get("extraction_notes") or "")
        records.extend(side_records)

    return records


# ---------------------------------------------------------------------------
# Provider detection + CBRE / Voit grid-table parsers
# ---------------------------------------------------------------------------
#
# CBRE and Voit publish dense single-period statistics grids rather than the
# multi-quarter blocks Kidder uses, so they get their own positional parsers.
#
# Unit note: the spec calls asking-rent units "usd_per_sf_per_month" and
# inventory "msf". To stay consistent with the rest of the app (formatting,
# charts, CSV, snapshot PDF all branch on the existing vocabulary) we store
# asking_rent as "dollar_per_sf" (already monthly elsewhere) and convert MSF
# to SF so total_inventory stays unit "sf" like every other source. The
# $/SF/MO + NNN-vs-gross distinction is carried by metric_type + lease_type.


def _detect_provider(pages: list[str]) -> str:
    """Identify the report publisher from footer/branding text."""
    joined = "\n".join(pages).upper()
    if "CBRE RESEARCH" in joined:
        return "cbre"
    if "VOIT REAL ESTATE SERVICES" in joined:
        return "voit"
    return "kidder"


# A grid cell: (1,234) / (12.3)% negative, 1,234 / 12.3% / $1.41 / -123 / 0,
# N/A, or a lone dash meaning an explicit NULL cell (kept as a token so
# positional column alignment survives blank cells).
_GRID_CELL = re.compile(
    r"\(\$?[\d,]+(?:\.\d+)?\)%?"
    r"|\$?-?[\d,]*\.?\d+%?"
    r"|N/?A"
    r"|[-–—]",
    re.IGNORECASE,
)


def _grid_value(cell: str, scale: float = 1.0) -> float | None:
    """Parse a CBRE/Voit cell. (parens)=negative, dash/NA=None, strip $ , %."""
    if cell is None:
        return None
    s = str(cell).strip()
    if not s or s in ("-", "–", "—") or s.upper() in ("NA", "N/A"):
        return None
    s = s.replace(",", "")
    if s.endswith("%"):
        s = s[:-1].strip()
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1].strip()
    s = s.replace("$", "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    v = -v if neg else v
    return v * scale


def _grid_data_rows(page) -> list[tuple[str, list[str], str]]:
    """Yield (label, [cell, ...], raw_line) for each text row on a page.

    Uses the same word y-grouping as the Kidder submarket-stats parser. The
    label is everything before the first value-shaped token; cells are the
    value tokens (including dash NULL placeholders) left-to-right.
    """
    words = page.extract_words(x_tolerance=2, y_tolerance=3)
    out: list[tuple[str, list[str], str]] = []
    for row in _group_rows(words):
        line = " ".join(w["text"] for w in row).strip()
        if not line:
            continue
        tokens = list(_GRID_CELL.finditer(line))
        cells = [t.group() for t in tokens]
        label = line[:tokens[0].start()].strip() if tokens else line
        out.append((label, cells, line))
    return out


# (metric_type, unit, lease_type, scale) left-to-right after the submarket col.
_CBRE_COLS = [
    ("total_inventory", "sf", None, 1_000_000),  # Net Rentable Area (MSF)->SF
    ("total_vacancy_rate", "percent", None, 1),
    ("total_availability_rate", "percent", None, 1),
    ("direct_availability_rate", "percent", None, 1),
    ("sublease_availability_rate", "percent", None, 1),
    ("asking_rent", "dollar_per_sf", "NNN", 1),
    ("net_absorption", "sf", None, 1),
    ("deliveries", "sf", None, 1),
    ("ytd_net_absorption", "sf", None, 1),
    ("under_construction", "sf", None, 1),
]

_CBRE_HEADER_RE = re.compile(
    r"FIGURES\s*[|｜]\s*(?P<market>[A-Za-z .,'/&-]+?)\s*[|｜]\s*"
    r"Q\s*(?P<q>[1-4])\s+(?P<yr>\d{4})",
    re.IGNORECASE,
)


def _cbre_header(pages: list[str], filepath: str) -> dict:
    info = {"quarter": None, "market": None, "asset_class": None}
    head = "\n".join(pages[:2])
    m = _CBRE_HEADER_RE.search(head.replace("\n", " "))
    if m:
        info["market"] = _title_case_market(m.group("market").strip())
        info["quarter"] = f"{m.group('q')}Q {m.group('yr')}"
    if not info["quarter"]:
        qm = re.search(r"\bQ\s*([1-4])\s+(\d{4})\b", head)
        if qm:
            info["quarter"] = f"{qm.group(1)}Q {qm.group(2)}"
    up = head.upper()
    for ac in KNOWN_ASSET_CLASSES:
        if ac.upper() in up:
            info["asset_class"] = ac
            break
    # Filename fallback: CBRE-Orange_County_Industrial_Figur.pdf
    if not info["market"] or not info["asset_class"]:
        stem = re.sub(r"\.pdf$", "", os.path.basename(filepath), flags=re.I)
        stem = re.sub(r"^CBRE[-_ ]*", "", stem, flags=re.I)
        stem = re.sub(r"[-_ ]*Figur\w*$", "", stem, flags=re.I)
        kept = []
        for p in re.split(r"[-_ ]+", stem):
            if p.lower() in KNOWN_ASSET_CLASSES:
                info["asset_class"] = info["asset_class"] or p.lower()
            elif p:
                kept.append(p)
        if not info["market"] and kept:
            info["market"] = _title_case_market(" ".join(kept))
    info["asset_class"] = info["asset_class"] or "industrial"
    return info


def _parse_cbre(pdf, pages: list[str], source: str,
                filepath: str) -> list[dict]:
    """Parse the CBRE 'Market Statistics by Submarket' grid (Figure 9)."""
    header = _cbre_header(pages, filepath)
    if not header["quarter"]:
        _warn(f"{source}: CBRE — could not detect quarter; skipping.")
        return []
    if not header["market"]:
        _warn(f"{source}: CBRE — could not detect market; skipping.")
        return []
    report_quarter = header["quarter"]
    report_date = quarter_to_date(report_quarter)

    target = None
    for i, txt in enumerate(pages):
        if "MARKET STATISTICS BY SUBMARKET" in (txt or "").upper():
            target = i
            break
    if target is None:
        _warn(f"{source}: CBRE — 'Market Statistics by Submarket' table "
              f"not found; skipping.")
        return []

    n = len(_CBRE_COLS)
    records: list[dict] = []
    started = False
    for label, cells, line in _grid_data_rows(pdf.pages[target]):
        up = line.upper()
        if "MARKET STATISTICS BY SUBMARKET" in up:
            started = True
            continue
        if not started:
            continue
        # End of the submarket table: the next section ("by Size"), the
        # "Source: CBRE Research" caption, or the page footer. NB: do NOT
        # break on "FIGURE" — "Figure 9" sits between the section title
        # and the first data row, which would end parsing immediately.
        if ("MARKET STATISTICS BY SIZE" in up or "CBRE RESEARCH" in up):
            break
        if len(cells) < n or not label:
            continue
        is_total = bool(re.match(r"^total\b", label, re.IGNORECASE))
        submarket = "Market Total" if is_total else label
        for (mt, unit, lease, scale), raw in zip(_CBRE_COLS, cells[-n:]):
            val = _grid_value(raw, scale)
            if val is None:
                continue
            records.append({
                "source": source, "source_page": target + 1,
                "report_date": report_date, "quarter": report_quarter,
                "metric_period": report_date, "period_type": "current",
                "market": header["market"], "submarket": submarket,
                "asset_class": header["asset_class"], "metric_type": mt,
                "metric_value": val, "unit": unit, "lease_type": lease,
                "confidence": 0.90, "raw_text": line[:200],
                "parser_strategy": "cbre_submarket_table",
                "extraction_notes": f"CBRE col: {mt}",
            })
    if not records:
        _warn(f"{source}: CBRE — submarket table located but no data rows "
              f"parsed (column layout may need tuning for this report).")
    return records


# (metric_type, unit, lease_type) left-to-right after the submarket col.
# A None metric_type marks a real table column we deliberately don't
# store — it still occupies a position so the rest stay aligned. The live
# OC/SD Voit reports carry an Average Sales Price ($/SF) column between
# the asking rate and net absorption that has no schema home; omitting it
# shifted every column left by one (building_count was dropped).
_VOIT_COLS = [
    ("building_count", "number", None),
    ("total_inventory", "sf", None),
    ("under_construction", "sf", None),
    ("planned_construction", "sf", None),
    ("vacant_sf", "sf", None),
    ("total_vacancy_rate", "percent", None),
    ("available_sf", "sf", None),
    ("total_availability_rate", "percent", None),
    ("asking_rent", "dollar_per_sf", "industrial_gross"),
    (None, None, None),  # Average Sales Price ($/SF) — not in schema
    ("net_absorption", "sf", None),
    ("ytd_net_absorption", "sf", None),
    ("gross_absorption", "sf", None),
    ("ytd_gross_absorption", "sf", None),
]

_VOIT_MARKETS = {"SD": "San Diego", "OC": "Orange County",
                 "IE": "Inland Empire", "LA": "Los Angeles"}

_VOIT_CODE_RES = [
    re.compile(r"\b(SD|OC|IE|LA)\s*Q\s*([1-4])\s*'?(\d{2})\b", re.I),  # SDQ126
    re.compile(r"\b(SD|OC|IE|LA)\s*([1-4])\s*Q\s*'?(\d{2})\b", re.I),  # SD1Q26
]


def _voit_header(pages: list[str], filepath: str) -> dict:
    info = {"quarter": None, "market": None, "asset_class": "industrial"}
    hay = os.path.basename(filepath) + "\n" + "\n".join(pages[:3])
    for rx in _VOIT_CODE_RES:
        m = rx.search(hay)
        if m:
            info["market"] = _VOIT_MARKETS.get(m.group(1).upper())
            info["quarter"] = f"{m.group(2)}Q 20{m.group(3)}"
            break
    up = ("\n".join(pages[:3])).upper()
    for ac in KNOWN_ASSET_CLASSES:
        if ac.upper() in up:
            info["asset_class"] = ac
            break
    return info


def _parse_voit(pdf, pages: list[str], source: str,
                filepath: str) -> list[dict]:
    """Parse the Voit page-3 submarket table (skips bottom size-range rows)."""
    header = _voit_header(pages, filepath)
    if not header["market"] or not header["quarter"]:
        _warn(f"{source}: Voit — could not detect market/quarter from the "
              f"report code; skipping.")
        return []
    report_quarter = header["quarter"]
    report_date = quarter_to_date(report_quarter)
    if len(pdf.pages) < 3:
        _warn(f"{source}: Voit — expected the submarket table on page 3 "
              f"but the PDF has fewer pages; skipping.")
        return []

    mkt_low = header["market"].lower()
    mkt_first = mkt_low.split()[0]
    n = len(_VOIT_COLS)
    records: list[dict] = []
    for label, cells, line in _grid_data_rows(pdf.pages[2]):
        if len(cells) < n or not label:
            continue
        # Size-range rows like '0-9,999' or '100,000+' — skip entirely.
        if re.match(r"^\s*\d[\d,]*\s*(?:[-–]\s*\d[\d,]*|\+)", label):
            continue
        low = label.lower()
        if low.endswith("total"):
            if mkt_low in low or low.startswith(mkt_first):
                submarket = "Market Total"
            else:
                submarket = re.sub(r"\s*total\s*$", "", label,
                                   flags=re.I).strip()
        else:
            submarket = label
        for (mt, unit, lease), raw in zip(_VOIT_COLS, cells[-n:]):
            if mt is None:  # positional placeholder (e.g. sales price)
                continue
            val = _grid_value(raw)
            if val is None:
                continue
            records.append({
                "source": source, "source_page": 3,
                "report_date": report_date, "quarter": report_quarter,
                "metric_period": report_date, "period_type": "current",
                "market": header["market"], "submarket": submarket,
                "asset_class": header["asset_class"], "metric_type": mt,
                "metric_value": val, "unit": unit, "lease_type": lease,
                "confidence": 0.90, "raw_text": line[:200],
                "parser_strategy": "voit_submarket_table",
                "extraction_notes": f"Voit col: {mt}",
            })
    if not records:
        _warn(f"{source}: Voit — page-3 table produced no rows (column "
              f"layout may need tuning for this report).")
    return records


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_pdf(filepath: str) -> list[dict]:
    """Parse a market-report PDF and return a list of metric dicts.

    Dispatches by publisher: CBRE and Voit have dedicated single-period grid
    parsers; everything else falls through to the Kidder Mathews strategies
    (structured / dual-breakdown / submarket-statistics / narrative).
    """
    PARSER_WARNINGS.clear()
    source = os.path.basename(filepath)

    with pdfplumber.open(filepath) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        provider = _detect_provider(pages)
        if provider == "cbre":
            return _parse_cbre(pdf, pages, source, filepath)
        if provider == "voit":
            return _parse_voit(pdf, pages, source, filepath)
        return _parse_kidder(pdf, pages, source, filepath)


def _parse_kidder(pdf, pages: list[str], source: str,
                  filepath: str) -> list[dict]:
    """Kidder Mathews multi-strategy parser (unchanged behavior)."""
    all_records = []

    page_breaks = []  # cumulative char offsets for page boundaries
    offset = 0
    for text in pages:
        offset += len(text) + 1  # +1 for the \n join
        page_breaks.append(offset)

    full_text = "\n".join(pages)
    header = _detect_header(pages[0], filepath=filepath)

    if not header["quarter"]:
        _warn(f"{source}: could not detect quarter from header — skipping.")
        return []
    if not header["asset_class"]:
        _warn(f"{source}: could not detect asset class from header — skipping.")
        return []
    if not header["market"]:
        _warn(f"{source}: could not detect market — skipping (records would be rejected).")
        return []

    # --- Dual-breakdown (Silicon Valley page 1) ---
    # Try this BEFORE single-table structured parsing to avoid the
    # single-table parser mashing both columns together.
    dual_pages_handled: set[int] = set()
    for i, page in enumerate(pdf.pages):
        dual_recs = _parse_dual_breakdown(page, header, source, i + 1)
        if dual_recs:
            all_records.extend(dual_recs)
            dual_pages_handled.add(i)

    # --- Structured parsing (single MARKET BREAKDOWN/SUMMARY tables) ---
    has_structured = bool(dual_pages_handled)
    for i, page_text in enumerate(pages):
        if i in dual_pages_handled:
            continue
        submarket = None
        sub_match = re.search(
            r"(?:^|\n)\s*(ADA COUNTY|CANYON COUNTY)\s*(?:\n|$)",
            page_text[:200]
        )
        if sub_match:
            submarket = sub_match.group(1).strip().title()

        if re.search(r"(?:^|\n)\s*BREAKDOWN\s*(?:\n|$)", page_text, re.IGNORECASE) or \
           re.search(r"MARKET\s+SUMMARY", page_text, re.IGNORECASE) or \
           re.search(r"MARKET\s+BREAKDOWN", page_text, re.IGNORECASE):
            recs = _parse_structured_table(page_text, header, source, i + 1, submarket)
            if recs:
                has_structured = True
                all_records.extend(recs)

    # --- SUBMARKET STATISTICS table (East Bay, Orange County, Silicon Valley) ---
    for i, page in enumerate(pdf.pages):
        sub_recs = _parse_submarket_statistics(page, header, source, i + 1)
        all_records.extend(sub_recs)

    # --- Sidebar parsing (page 1 only) ---
    sidebar_records = _parse_sidebar(pages[0], header, source, 1)
    existing = {(r["metric_type"], r["period_type"], r.get("submarket"), r.get("asset_class"))
                for r in all_records}
    for sr in sidebar_records:
        key = (sr["metric_type"], sr["period_type"], sr.get("submarket"), sr.get("asset_class"))
        if key not in existing:
            all_records.append(sr)

    # --- Narrative submarket parsing ---
    if not has_structured or header.get("market") == "Seattle":
        all_records.extend(
            _parse_narrative_submarkets(full_text, header, source, page_breaks)
        )

    # --- Cap rate from later pages ---
    for i, page_text in enumerate(pages[1:], start=2):
        cap_recs = _parse_sidebar(page_text, header, source, i)
        existing_caps = {r.get("submarket") for r in all_records if r["metric_type"] == "cap_rate"}
        for cr in cap_recs:
            if cr["metric_type"] == "cap_rate" and cr.get("submarket") not in existing_caps:
                all_records.append(cr)

    return all_records


def get_warnings() -> list[str]:
    """Return warnings emitted by the most recent parse_pdf call."""
    return list(PARSER_WARNINGS)
