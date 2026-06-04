"""
Dry-run test for the new CBRE-OC (Q4 2025 NEW format) and Andover
Puget Sound parsers. Prints every extracted record and asserts the
verified-by-the-user values match exactly. Does not write to the DB,
does not push, does not modify any sample files.

Run:
    python3 scripts/test_new_parsers.py
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from pdf_parser import (  # noqa: E402
    _detect_provider, _parse_andover, _parse_cbre_new_oc,
)
import pdfplumber  # noqa: E402


# ---------------------------------------------------------------------------
# Pretty printers
# ---------------------------------------------------------------------------

def _fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "percent":
        return f"{v:.1f}%"
    if unit == "dollar_per_sf":
        return f"${v:.2f}"
    if unit in ("sf", "number"):
        return f"{v:,.0f}"
    return f"{v}"


def _print_table(records: list[dict], title: str) -> None:
    print()
    print("=" * 92)
    print(title)
    print("=" * 92)
    if not records:
        print("  (no records)")
        return
    # Pick one source_series at a time so the by-table breakdown is visible.
    by_series: dict[str, list[dict]] = {}
    for r in records:
        by_series.setdefault(r.get("parser_strategy", "?"), []).append(r)
    for series, recs in by_series.items():
        print(f"\n  -- source_series = {series}  ({len(recs)} records) --")
        # Header
        print(f"    {'submarket':30s}  {'metric_type':28s}  "
              f"{'value':>14s}  unit            lease")
        print(f"    {'-'*30}  {'-'*28}  {'-'*14}  {'-'*14}  {'-'*8}")
        for r in recs:
            sub = (r.get("submarket") or "")[:30]
            mt  = (r.get("metric_type") or "")[:28]
            val = _fmt_value(r.get("metric_value"), r.get("unit") or "")
            unit = (r.get("unit") or "")
            lt = r.get("lease_type") or ""
            print(f"    {sub:30s}  {mt:28s}  {val:>14s}  {unit:14s}  {lt}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(parser_fn, filepath: str) -> list[dict]:
    """Open the PDF and call the per-provider parse function the same
    way ingest/_common.parse_and_upsert would, minus the upsert."""
    source = os.path.basename(filepath)
    with pdfplumber.open(filepath) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        return parser_fn(pdf, pages, source, filepath)


def _value(records: list[dict], submarket: str, metric_type: str,
           asset_class: str | None = None) -> float | None:
    """Return the first matching record's metric_value, or None."""
    for r in records:
        if (r.get("submarket") == submarket
                and r.get("metric_type") == metric_type
                and (asset_class is None
                     or r.get("asset_class") == asset_class)):
            return r.get("metric_value")
    return None


def _check(label: str, got, expected, tol: float = 0.01) -> bool:
    if got is None:
        ok = False
    elif isinstance(expected, (int, float)):
        ok = abs(got - expected) <= max(tol, abs(expected) * 1e-4)
    else:
        ok = got == expected
    marker = "OK " if ok else "!! "
    print(f"  {marker} {label:60s}  got={got!r}  expected={expected!r}")
    return ok


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cbre_new_oc() -> int:
    path = "sample_pdfs/Orange_County_Industrial_Figur.pdf"
    full = os.path.join(_REPO_ROOT, path)
    print(f"\n### CBRE new OC ({path}) ###")

    with pdfplumber.open(full) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        provider = _detect_provider(pages)
    print(f"  detected provider: {provider!r}")
    if provider != "cbre":
        print(f"  !! Expected 'cbre', got {provider!r}")
        return 1

    records = _parse(_parse_cbre_new_oc, full)
    _print_table(records, "CBRE-OC Q4 2025 — extracted records")

    print("\nFigure 6 verification (submarket-level, source_series="
          "'cbre_oc_market_statistics'):")
    fails = 0
    fig6 = [r for r in records
            if r.get("parser_strategy") == "cbre_oc_market_statistics"]
    fails += 0 if _check("North Orange County vacancy",
                         _value(fig6, "North Orange County",
                                "total_vacancy_rate"), 4.7) else 1
    fails += 0 if _check("North Orange County availability",
                         _value(fig6, "North Orange County",
                                "total_availability_rate"), 6.7) else 1
    fails += 0 if _check("North Orange County net_absorption",
                         _value(fig6, "North Orange County",
                                "net_absorption"), -73610) else 1
    fails += 0 if _check("North Orange County gross_absorption",
                         _value(fig6, "North Orange County",
                                "gross_absorption"), 509143) else 1
    fails += 0 if _check("North Orange County under_construction",
                         _value(fig6, "North Orange County",
                                "under_construction"), 604180) else 1
    fails += 0 if _check("North Orange County deliveries",
                         _value(fig6, "North Orange County",
                                "deliveries"), 0) else 1
    fails += 0 if _check("North Orange County asking_rent",
                         _value(fig6, "North Orange County",
                                "asking_rent"), 1.51) else 1

    fails += 0 if _check("Greater Airport Area vacancy",
                         _value(fig6, "Greater Airport Area",
                                "total_vacancy_rate"), 4.5) else 1
    fails += 0 if _check("Greater Airport Area availability",
                         _value(fig6, "Greater Airport Area",
                                "total_availability_rate"), 6.4) else 1
    fails += 0 if _check("Greater Airport Area net_absorption",
                         _value(fig6, "Greater Airport Area",
                                "net_absorption"), 213816) else 1
    fails += 0 if _check("Greater Airport Area gross_absorption",
                         _value(fig6, "Greater Airport Area",
                                "gross_absorption"), 607974) else 1
    fails += 0 if _check("Greater Airport Area under_construction",
                         _value(fig6, "Greater Airport Area",
                                "under_construction"), 312000) else 1
    fails += 0 if _check("Greater Airport Area deliveries",
                         _value(fig6, "Greater Airport Area",
                                "deliveries"), 246904) else 1
    fails += 0 if _check("Greater Airport Area asking_rent",
                         _value(fig6, "Greater Airport Area",
                                "asking_rent"), 1.74) else 1

    fails += 0 if _check("South Orange County vacancy",
                         _value(fig6, "South Orange County",
                                "total_vacancy_rate"), 6.8) else 1
    fails += 0 if _check("South Orange County availability",
                         _value(fig6, "South Orange County",
                                "total_availability_rate"), 9.0) else 1
    fails += 0 if _check("South Orange County net_absorption",
                         _value(fig6, "South Orange County",
                                "net_absorption"), 93366) else 1
    fails += 0 if _check("South Orange County gross_absorption",
                         _value(fig6, "South Orange County",
                                "gross_absorption"), 147941) else 1
    fails += 0 if _check("South Orange County under_construction",
                         _value(fig6, "South Orange County",
                                "under_construction"), 0) else 1
    fails += 0 if _check("South Orange County deliveries",
                         _value(fig6, "South Orange County",
                                "deliveries"), 213444) else 1
    fails += 0 if _check("South Orange County asking_rent",
                         _value(fig6, "South Orange County",
                                "asking_rent"), 1.75) else 1

    print(f"\n  Figure 6 records: "
          f"{sum(1 for r in records if r.get('parser_strategy') == 'cbre_oc_market_statistics')}")
    print(f"  Figure 9 records: "
          f"{sum(1 for r in records if r.get('parser_strategy') == 'cbre_oc_market_area_detail')}")
    print(f"  TOTAL records:    {len(records)}")

    return fails


def test_andover() -> int:
    path = "sample_pdfs/Andover-Seattle-Market-Report-Q2.pdf"
    full = os.path.join(_REPO_ROOT, path)
    print(f"\n### Andover Puget Sound ({path}) ###")

    with pdfplumber.open(full) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        provider = _detect_provider(pages)
    print(f"  detected provider: {provider!r}")
    if provider != "andover":
        print(f"  !! Expected 'andover', got {provider!r}")
        return 1

    records = _parse(_parse_andover, full)
    _print_table(records, "Andover Puget Sound Q2 2025 — extracted records")

    fails = 0
    # Industrial Logistics
    fails += 0 if _check("Industrial Logistics RBA",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Logistics", "total_inventory"),
                         250606220) else 1
    fails += 0 if _check("Industrial Logistics vacancy",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Logistics", "total_vacancy_rate"),
                         10.2) else 1
    fails += 0 if _check("Industrial Logistics rent",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Logistics", "asking_rent"), 1.10) else 1
    fails += 0 if _check("Industrial Logistics availability",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Logistics", "total_availability_rate"),
                         14.2) else 1
    # Industrial Market
    fails += 0 if _check("Industrial Market RBA",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Market", "total_inventory"),
                         365742825) else 1
    fails += 0 if _check("Industrial Market vacancy",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Market", "total_vacancy_rate"), 8.9) else 1
    fails += 0 if _check("Industrial Market rent",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Market", "asking_rent"), 1.19) else 1
    fails += 0 if _check("Industrial Market availability",
                         _value([r for r in records
                                 if r.get("asset_class") == "industrial"],
                                "Market", "total_availability_rate"),
                         11.7) else 1
    # Office 4 & 5 Star
    fails += 0 if _check("Office 4 & 5 Star RBA",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "4 & 5 Star", "total_inventory"),
                         88572339) else 1
    fails += 0 if _check("Office 4 & 5 Star vacancy",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "4 & 5 Star", "total_vacancy_rate"),
                         24.5) else 1
    fails += 0 if _check("Office 4 & 5 Star rent",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "4 & 5 Star", "asking_rent"), 41.72) else 1
    fails += 0 if _check("Office 4 & 5 Star availability",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "4 & 5 Star", "total_availability_rate"),
                         26.2) else 1
    # Office Market
    fails += 0 if _check("Office Market RBA",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "Market", "total_inventory"),
                         235954097) else 1
    fails += 0 if _check("Office Market vacancy",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "Market", "total_vacancy_rate"), 17.0) else 1
    fails += 0 if _check("Office Market rent",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "Market", "asking_rent"), 36.89) else 1
    fails += 0 if _check("Office Market availability",
                         _value([r for r in records
                                 if r.get("asset_class") == "office"],
                                "Market", "total_availability_rate"),
                         18.0) else 1
    return fails


def main() -> int:
    fails = 0
    fails += test_cbre_new_oc()
    fails += test_andover()
    print()
    print("=" * 92)
    if fails == 0:
        print("ALL CHECKS PASSED. No DB writes were attempted.")
    else:
        print(f"{fails} CHECK(S) FAILED. No DB writes were attempted.")
    print("=" * 92)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
