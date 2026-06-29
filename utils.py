"""
Shared utilities for Sebco Market Intel.
"""

import json
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
_PORTFOLIO_FILE = os.path.join(_DIR, "sebco_portfolio.json")
_PORTFOLIO_LOCAL_FILE = os.path.join(_DIR, "sebco_portfolio.local.json")

# Canonical display order for the portfolio / Market Overview report.
SEBCO_PORTFOLIO_ORDER = [
    "Kent Valley", "Marysville", "San Diego",
    "LA Mid-Counties", "LA South Bay", "Orange County",
]


def _read_json(path: str) -> dict:
    """Load a JSON file and return a dict (or {} on any failure)."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def load_sebco_portfolio() -> dict:
    """Return the Sebco portfolio dict.

    Reads from `sebco_portfolio.local.json` if it exists (gitignored;
    local-only snapshot with real numbers), otherwise falls back to
    `sebco_portfolio.json` (committed placeholder, safe for the public
    cloud deploy). The local file is treated as a full snapshot — when
    present it replaces, not merges with, the base file.

    Returns {} (never raises) if neither file is loadable so the
    dashboard degrades gracefully instead of crashing.
    """
    if os.path.exists(_PORTFOLIO_LOCAL_FILE):
        return _read_json(_PORTFOLIO_LOCAL_FILE)
    return _read_json(_PORTFOLIO_FILE)


def save_sebco_portfolio(data: dict) -> None:
    """Always writes to `sebco_portfolio.local.json` (gitignored).

    Settings-page edits never overwrite the public placeholder file,
    even on the cloud deploy — that prevents an accidental save (from
    anyone who has access to the deployment) from leaking real numbers
    into git history if the .local file ever gets staged by mistake.

    To edit the public placeholder set, edit sebco_portfolio.json by
    hand and commit.
    """
    with open(_PORTFOLIO_LOCAL_FILE, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def is_using_local_portfolio() -> bool:
    """True if the load_sebco_portfolio() returns the .local override
    rather than the public placeholder. Used by the Settings page to
    show which file is currently in effect."""
    return os.path.exists(_PORTFOLIO_LOCAL_FILE)


# ---------------------------------------------------------------------------
# Sebco market -> where its data actually lives
# ---------------------------------------------------------------------------
# Some Sebco "markets" (Kent Valley, Marysville, ...) are not top-level
# markets in the metrics table — they are sub-areas reported *inside* a
# parent market's PDF. Kidder's Puget Sound report, for example, rolls Kent
# into the "Southend" submarket and Marysville into "Northend"; it has no
# literal "Kent Valley"/"Marysville" row. A portfolio entry can therefore
# carry an optional `data_source`:
#
#   "Kent Valley": {
#       ...,
#       "data_source": {
#           "market": "Seattle",
#           "submarket_aliases": ["Kent Valley", "Kent", "Southend"]
#       }
#   }
#
# `submarket_aliases` is tried in priority order, so a report that breaks out
# a literal "Kent Valley" submarket wins over the broader "Southend" rollup.
# A market with no `data_source` is assumed top-level: its data lives under
# market==<name> with a market-wide submarket ("" or "Market Total").

# Submarket values that represent a whole market (no sub-area breakdown).
MARKET_WIDE_SUBMARKETS = ["", "Market Total"]


def resolve_data_source(market: str, portfolio: dict | None = None) -> dict | None:
    """Return a Sebco market's `data_source` mapping, or None if it is a
    top-level market whose data is stored under its own name.

    The returned dict always has string `market` and a non-empty
    `submarket_aliases` list. A malformed/empty mapping resolves to None
    (treated as top-level) so a bad config degrades to current behavior
    rather than blanking the card.
    """
    if portfolio is None:
        portfolio = load_sebco_portfolio()
    ds = portfolio.get(market, {}).get("data_source")
    if isinstance(ds, dict) and ds.get("market") and ds.get("submarket_aliases"):
        return {
            "market": str(ds["market"]),
            "submarket_aliases": [str(s) for s in ds["submarket_aliases"]],
        }
    return None


def data_query_keys(market: str, portfolio: dict | None = None) -> tuple[str, list[str]]:
    """Resolve a Sebco market to the (db_market, submarkets) pair to filter
    the metrics table on. For a top-level market this is the market itself
    plus the market-wide submarkets; for a submarket-backed market it is the
    parent market plus its ordered submarket aliases.

    `submarkets` is in priority order — earlier entries should win when more
    than one has data for the same metric.
    """
    ds = resolve_data_source(market, portfolio)
    if ds is not None:
        return ds["market"], ds["submarket_aliases"]
    return market, list(MARKET_WIDE_SUBMARKETS)

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
