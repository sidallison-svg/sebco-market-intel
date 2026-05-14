"""
PDF parser for Kidder Mathews quarterly market reports.

Two strategies:
  - Structured: reports with MARKET BREAKDOWN / MARKET SUMMARY tables (Boise, Inland Empire)
  - Narrative: reports with data in sidebar callouts and prose (Seattle)
"""

import os
import re
from datetime import date as date_type

import pdfplumber


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
    "asking lease rate (nnn)": "asking_rent",
    "average asking rents/sf/mo": "asking_rent",
    "avg asking rents/sf/mo": "asking_rent",
    "cap rates": "cap_rate",
    "cap rate": "cap_rate",
    "under construction (sf)": "under_construction",
    "under construction": "under_construction",
    "under const. (sf)": "under_construction",
    "deliveries (sf)": "deliveries",
    "deliveries": "deliveries",
    "direct net absorption (sf)": "net_absorption",
    "direct net absorption": "net_absorption",
    "net absorption (sf)": "net_absorption",
    "net absorption": "net_absorption",
    "total inventory": "total_inventory",
    "new construction (sf)": "new_construction",
    "new construction": "new_construction",
    "average sales price/sf": "avg_sales_price",
    "avg sales price/sf": "avg_sales_price",
}

UNIT_OVERRIDES = {
    "vacancy_rate": "percent",
    "cap_rate": "percent",
    "asking_rent": "dollar_per_sf",
    "avg_sales_price": "dollar_per_sf",
    "net_absorption": "sf",
    "under_construction": "sf",
    "deliveries": "sf",
    "new_construction": "sf",
    "total_inventory": "sf",
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

KNOWN_MARKETS = [
    "Inland Empire",  # Must be before shorter names
    "Seattle", "Boise", "Portland", "San Francisco",
    "Los Angeles", "Phoenix", "Reno", "Tucson", "Sacramento", "San Diego",
    "East Bay", "Silicon Valley", "Bellevue",
]

KNOWN_ASSET_CLASSES = ["industrial", "office", "retail", "multifamily"]


def _detect_header(page1_text: str) -> dict:
    """Extract quarter, market, and asset class from report header."""
    info = {"quarter": None, "market": None, "asset_class": None}
    header_block = page1_text[:500]

    m = re.search(r"(\dQ)\s*(\d{4})", header_block)
    if m:
        info["quarter"] = f"{m.group(1)} {m.group(2)}"

    upper = header_block.upper()
    for name in KNOWN_MARKETS:
        if name.upper() in upper:
            info["market"] = name
            break

    for ac in KNOWN_ASSET_CLASSES:
        if ac.upper() in upper:
            info["asset_class"] = ac
            break

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
_VAL_TOKEN = re.compile(
    r"-?\$\d[\d,.]+"      # dollar (must start with digit after $)
    r"|-?\d[\d,.]*[MKB]"  # number with suffix
    r"|-?\d[\d,.]*%"      # percentage
    r"|-?\d[\d,.]*"       # plain number including single digits like 0
    r"|N/?A",             # N/A
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
# Main entry point
# ---------------------------------------------------------------------------

def parse_pdf(filepath: str) -> list[dict]:
    """
    Parse a Kidder Mathews PDF and return a list of metric dicts.
    Auto-detects structured vs narrative strategy.
    """
    source = os.path.basename(filepath)
    all_records = []

    with pdfplumber.open(filepath) as pdf:
        pages = []
        page_breaks = []  # cumulative char offsets for page boundaries
        offset = 0
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)
            offset += len(text) + 1  # +1 for the \n join
            page_breaks.append(offset)

        full_text = "\n".join(pages)
        header = _detect_header(pages[0])

        if not header["quarter"] or not header["asset_class"]:
            return []

        # --- Structured parsing ---
        has_structured = False
        for i, page_text in enumerate(pages):
            submarket = None
            # Detect submarket from page header
            sub_match = re.search(
                r"(?:^|\n)\s*(ADA COUNTY|CANYON COUNTY)\s*(?:\n|$)",
                page_text[:200]
            )
            if sub_match:
                submarket = sub_match.group(1).strip().title()

            # Check for BREAKDOWN or SUMMARY — these may be on separate lines
            # from "MARKET" due to column headers between them
            if re.search(r"(?:^|\n)\s*BREAKDOWN\s*(?:\n|$)", page_text, re.IGNORECASE) or \
               re.search(r"MARKET\s+SUMMARY", page_text, re.IGNORECASE):
                recs = _parse_structured_table(page_text, header, source, i + 1, submarket)
                if recs:
                    has_structured = True
                    all_records.extend(recs)

        # --- Sidebar parsing (page 1 only) ---
        sidebar_records = _parse_sidebar(pages[0], header, source, 1)
        existing = {(r["metric_type"], r["period_type"], r.get("submarket")) for r in all_records}
        for sr in sidebar_records:
            key = (sr["metric_type"], sr["period_type"], sr.get("submarket"))
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
