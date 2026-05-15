"""
Shared utilities for Sebco Market Intel.
"""

import os
import re
from datetime import datetime

import pandas as pd

# Spec column set for CSV exports. Internal IDs, source_file_id and raw_text
# are deliberately excluded; confidence is appended for Raw Data only.
CSV_BASE_COLS = [
    "market", "submarket", "asset_class", "metric_type", "metric_value",
    "unit", "quarter", "metric_period", "source", "parser_strategy",
]


def _slug(value) -> str:
    s = str(value if value is not None else "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def build_csv_export(df: pd.DataFrame, page: str, filter_bits: list,
                     include_confidence: bool = False,
                     today: str | None = None) -> tuple[str, str]:
    """Return (filename, csv_text) for an already-filtered frame.

    Filename: sebco_{page}_{filters}_{YYYY-MM-DD}.csv
    """
    cols = list(CSV_BASE_COLS) + (["confidence"] if include_confidence else [])
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = None
    out = out[cols]

    slugged = [_slug(b) for b in filter_bits
               if b not in (None, "", "(all)") and _slug(b)]
    parts = "_".join(slugged) if slugged else "all"
    date = today or datetime.now().strftime("%Y-%m-%d")
    fname = f"sebco_{page}_{parts}_{date}.csv"
    return fname, out.to_csv(index=False)


# Anchored on "-market-research-" so we can pick the asset class out of the
# prefix and the market slug + year + quarter out of the suffix.
_FILENAME_RE = re.compile(
    r"^(?P<asset>[a-z]+)-market-research-"
    r"(?P<market>[a-z][a-z-]*?)-"
    r"(?P<year>\d{4})-"
    r"(?P<quarter>\dq)\.pdf$",
    re.IGNORECASE,
)


def format_display_name(filename: str) -> str:
    """Convert a Kidder Mathews filename into a clean display name.

    >>> format_display_name("industrial-market-research-orange-county-2026-1q.pdf")
    'Orange County Industrial — 1Q 2026'

    Falls back to the raw filename (sans path) if the pattern doesn't match.
    """
    if not filename:
        return filename
    name = os.path.basename(filename)
    m = _FILENAME_RE.match(name)
    if not m:
        return name

    asset = m.group("asset").capitalize()
    market = _title_case_slug(m.group("market"))
    year = m.group("year")
    quarter = m.group("quarter").upper()  # '1q' -> '1Q'
    return f"{market} {asset} — {quarter} {year}"


def display_name_for_source(source: str | None) -> str:
    """Alias used at DB call sites for readability."""
    if not source:
        return ""
    return format_display_name(source)


def _title_case_slug(slug: str) -> str:
    """'orange-county' -> 'Orange County', 'east-bay' -> 'East Bay'."""
    return " ".join(part.capitalize() for part in slug.split("-") if part)


if __name__ == "__main__":  # pragma: no cover
    samples = [
        "industrial-market-research-orange-county-2026-1q.pdf",
        "industrial-market-research-east-bay-2026-1q.pdf",
        "office-market-research-inland-empire-2026-1q.pdf",
        "industrial-market-research-boise-2025-2q.pdf",
        "industrial-market-research-silicon-valley-2026-1q.pdf",
        "industrial-market-research-seattle-2026-1q.pdf",
        "foo.pdf",
        "",
    ]
    for s in samples:
        print(f"{s!r:70s} -> {format_display_name(s)!r}")
