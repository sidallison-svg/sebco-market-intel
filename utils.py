"""
Shared utilities for Sebco Market Intel.
"""

import os
import re


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
