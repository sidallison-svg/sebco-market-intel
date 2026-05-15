"""
Streamlit dashboard for Sebco Market Intel.

Pages:
  1. Upload - drag-and-drop PDF upload with parsing
  2. Summary - latest data per submarket
  3. Trends - line charts of rent/vacancy over time
  4. Comparison - side-by-side of two submarkets
  5. Raw Data - searchable table with CSV export and manual editing
"""

import getpass
import hashlib
import io
import os
import re
import tempfile
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import get_db_path
from database import (
    STATUS_REJECTED,
    delete_by_source,
    delete_rejected_record,
    delete_uploaded_file_row,
    find_active_report,
    get_all_metrics,
    get_distinct_values,
    get_file_by_hash,
    get_metrics_for_source,
    get_rejected_records,
    get_upload_history,
    get_upload_summaries,
    init_db,
    insert_metrics,
    record_uploaded_file,
    supersede_file,
    update_metric,
)
from pdf_parser import get_warnings, parse_pdf
from utils import format_display_name

# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Sebco Market Intel",
    page_icon="\U0001f4ca",
    layout="wide",
)

# Honor any pending navigation request before the sidebar radio (which owns
# `nav_page`) is instantiated. Streamlit forbids writing to a widget's key
# AFTER the widget renders, so callers route via `pending_nav` instead and
# we apply it here, on the next run.
if "pending_nav" in st.session_state:
    st.session_state["nav_page"] = st.session_state.pop("pending_nav")

# ---------------------------------------------------------------------------
# Corporate styling (navy + grey, serif headings, SEBCO INC wordmark banner)
# ---------------------------------------------------------------------------
# To swap the placeholder wordmark for a real logo later, replace the
# .sebco-wordmark <span> below with: st.image("path/to/logo.png", width=160)
# rendered above the banner block.

st.markdown(
    """
    <style>
      /* Tighten Streamlit's default top padding so the navy banner sits high. */
      .main .block-container { padding-top: 1.25rem; padding-bottom: 2rem; max-width: 1320px; }

      /* Serif headings across the app */
      h1, h2, h3, h4 {
        font-family: "Georgia", "Times New Roman", "Droid Serif", serif !important;
        color: #0E2A47 !important;
        letter-spacing: 0.01em;
      }
      h1 { font-weight: 600 !important; }
      h2 { font-weight: 600 !important; border-bottom: 1px solid #E5E7EB; padding-bottom: 0.35rem; }
      h3 { font-weight: 500 !important; color: #1A1A2E !important; }

      /* SEBCO INC navy banner */
      .sebco-banner {
        background: linear-gradient(135deg, #0E2A47 0%, #143560 100%);
        color: #FFFFFF;
        padding: 18px 28px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 1.25rem;
        box-shadow: 0 1px 3px rgba(14, 42, 71, 0.18);
      }
      .sebco-wordmark {
        font-family: "Georgia", "Times New Roman", serif;
        font-size: 1.45rem;
        font-weight: 600;
        letter-spacing: 0.18em;
        color: #FFFFFF;
        border: 1.5px solid rgba(255, 255, 255, 0.85);
        padding: 6px 16px;
        border-radius: 3px;
      }
      .sebco-tagline {
        font-family: "Georgia", "Times New Roman", serif;
        font-size: 1.1rem;
        font-weight: 300;
        color: #E5E7EB;
        letter-spacing: 0.04em;
      }
      .sebco-divider {
        border: 0;
        border-top: 1px solid #E5E7EB;
        margin: 0.25rem 0 1.25rem 0;
      }

      /* Metric cards (Summary page) get a subtle border + light shadow */
      [data-testid="stMetric"] {
        background: #FAFBFC;
        border: 1px solid #E5E7EB;
        border-left: 3px solid #0E2A47;
        padding: 0.85rem 1rem;
        border-radius: 4px;
        box-shadow: 0 1px 2px rgba(14, 42, 71, 0.04);
      }
      [data-testid="stMetricLabel"] {
        font-family: "Georgia", "Times New Roman", serif;
        font-size: 0.85rem !important;
        color: #4B5563 !important;
        font-weight: 500;
      }
      [data-testid="stMetricValue"] {
        font-family: "Georgia", "Times New Roman", serif;
        color: #0E2A47 !important;
        font-weight: 600;
      }

      /* Sidebar polish */
      [data-testid="stSidebar"] {
        border-right: 1px solid #E5E7EB;
      }
      [data-testid="stSidebar"] .sebco-sidebar-mark {
        font-family: "Georgia", "Times New Roman", serif;
        font-size: 0.95rem;
        font-weight: 600;
        letter-spacing: 0.20em;
        color: #0E2A47;
        text-align: center;
        padding: 14px 8px 6px 8px;
        border: 1px solid #0E2A47;
        border-radius: 3px;
        margin: 0.5rem 0.25rem 0.75rem 0.25rem;
        background: #FFFFFF;
      }
      [data-testid="stSidebar"] .sebco-sidebar-sub {
        text-align: center;
        font-family: "Georgia", "Times New Roman", serif;
        font-size: 0.72rem;
        color: #6B7280;
        letter-spacing: 0.10em;
        margin: -0.5rem 0 0.75rem 0;
      }
      .sidebar-meta {
        font-family: "Georgia", "Times New Roman", serif;
        color: #6B7280 !important;
        font-size: 0.78rem;
      }

      /* Section captions on Summary etc. */
      .stMarkdown p strong { color: #0E2A47; }

      /* Hide the default Streamlit chrome that looks unfinished */
      #MainMenu { visibility: hidden; }
      footer { visibility: hidden; }
    </style>

    <div class="sebco-banner">
      <span class="sebco-wordmark">SEBCO&nbsp;&nbsp;INC</span>
      <span class="sebco-tagline">Market Intelligence</span>
    </div>
    <hr class="sebco-divider" />
    """,
    unsafe_allow_html=True,
)

CONFIDENCE_COLORS = {
    "high": "#2ecc71",    # >= 0.90
    "medium": "#f39c12",  # >= 0.75
    "low": "#e74c3c",     # < 0.75
}

LOW_CONFIDENCE_THRESHOLD = 0.85

# ---------------------------------------------------------------------------
# Metric glossary and filter help text
# ---------------------------------------------------------------------------

METRIC_GLOSSARY = {
    "vacancy_rate": "Percentage of total inventory currently unoccupied and available for lease.",
    "lease_rate": "Average asking rental rate per square foot, typically quoted as monthly NNN (triple net).",
    "net_absorption": "Net change in occupied space over the period. Positive = more space occupied, negative = more space vacated.",
    "total_inventory": "Total rentable building area (square feet) tracked in this market/submarket.",
    "under_construction": "Square footage of new buildings currently being built but not yet delivered.",
    "cap_rate": "Capitalization rate \u2014 ratio of net operating income to property value. Lower cap rate = higher property prices.",
    "sale_price_per_sf": "Average sale price per square foot for transactions in the period.",
    "yoy_rent_change": "Year-over-year percentage change in asking lease rates.",
    "yoy_vacancy_change": "Year-over-year change in vacancy rate (in percentage points).",
}

FILTER_HELP = {
    "market": "Geographic market area (e.g., Seattle, Boise, Inland Empire).",
    "metric": "The data metric to display. Hover the ? icon after selecting a metric for its definition.",
    "submarket": "A sub-area within the market. Select '(all)' to overlay all submarkets.",
    "period_type": "When the metric was measured: current = report quarter, prior_quarter, prior_year, yoy_change = year-over-year delta.",
    "source": "The PDF filename from which data was extracted.",
}


def confidence_label(val: float | None) -> str:
    if val is None:
        return "unknown"
    if val >= 0.90:
        return "high"
    if val >= 0.75:
        return "medium"
    return "low"


def _metric_help(metric_type: str) -> str:
    """Return glossary tooltip for a metric type."""
    return METRIC_GLOSSARY.get(metric_type, "")


def _format_value(val, unit: str) -> str:
    """Format a metric value with unit."""
    if val is None:
        return "\u2014"
    if unit == "percent":
        return f"{val:.1f}%"
    elif unit == "dollar_per_sf":
        return f"${val:.2f}/SF"
    elif unit == "sf":
        if abs(val) >= 1_000_000:
            return f"{val / 1_000_000:.1f}M SF"
        elif abs(val) >= 1_000:
            return f"{val / 1_000:.0f}K SF"
        else:
            return f"{val:,.0f} SF"
    return f"{val:,.2f}"


def _format_value_short(val, unit: str) -> str:
    """Format for comparison table (shorter, no SF suffix)."""
    if val is None:
        return "\u2014"
    if unit == "percent":
        return f"{val:.1f}%"
    elif unit == "dollar_per_sf":
        return f"${val:.2f}"
    elif unit == "sf" and abs(val) >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    elif unit == "sf" and abs(val) >= 1_000:
        return f"{val / 1_000:.0f}K"
    return f"{val:,.0f}"


def _warn_suffix(confidence: float | None) -> str:
    """Return warning marker if confidence is below threshold."""
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        return " \u26a0\ufe0f"
    return ""


# ---------------------------------------------------------------------------
# Smart search
# ---------------------------------------------------------------------------

METRIC_KEYWORDS = {
    "vacancy": "vacancy_rate",
    "vacant": "vacancy_rate",
    "lease": "lease_rate",
    "rent": "lease_rate",
    "rental": "lease_rate",
    "absorption": "net_absorption",
    "absorb": "net_absorption",
    "inventory": "total_inventory",
    "construction": "under_construction",
    "cap": "cap_rate",
    "capitalization": "cap_rate",
    "sale": "sale_price_per_sf",
    "price": "sale_price_per_sf",
}

PAGE_KEYWORDS = {
    "upload": "Upload",
    "import": "Upload",
    "summary": "Summary",
    "overview": "Summary",
    "latest": "Summary",
    "trend": "Trends",
    "chart": "Trends",
    "graph": "Trends",
    "history": "Trends",
    "over time": "Trends",
    "compare": "Comparison",
    "comparison": "Comparison",
    "versus": "Comparison",
    "vs": "Comparison",
    "side by side": "Comparison",
    "raw": "Raw Data",
    "export": "Raw Data",
    "csv": "Raw Data",
    "edit": "Raw Data",
    "all data": "Raw Data",
    "library": "Uploads",
    "uploads": "Uploads",
    "pdfs": "Uploads",
    "reports": "Uploads",
    "files": "Uploads",
}


def _parse_search(query: str, known_markets: list[str], known_submarkets: list[str]) -> dict:
    """Parse a natural-language search query into filter components."""
    result = {"page": None, "market": None, "submarket": None, "metric": None}
    if not query:
        return result

    q_lower = query.lower().strip()
    tokens = q_lower.split()

    # Match markets (case-insensitive substring, longest match first)
    for m in sorted(known_markets, key=len, reverse=True):
        if m.lower() in q_lower:
            result["market"] = m
            break

    # Match submarkets (longest match first)
    for s in sorted(known_submarkets, key=len, reverse=True):
        if s.lower() in q_lower:
            result["submarket"] = s
            break

    # Match metric keywords
    for token in tokens:
        if token in METRIC_KEYWORDS:
            result["metric"] = METRIC_KEYWORDS[token]
            break

    # Match page keywords (check multi-word first, then single-word)
    for phrase, page_name in sorted(PAGE_KEYWORDS.items(), key=lambda x: -len(x[0])):
        if phrase in q_lower:
            result["page"] = page_name
            break

    # Infer best page if not explicit
    if result["page"] is None:
        if result["metric"]:
            result["page"] = "Trends"
        elif result["market"] or result["submarket"]:
            result["page"] = "Summary"

    return result


def _render_search_bar():
    """Render the smart search bar at the top of the main content area."""
    query = st.text_input(
        "Quick search",
        placeholder="e.g., Boise vacancy, Q1 2026 Seattle rent, compare submarkets",
        key="smart_search_input",
        help=(
            "Type keywords to jump to relevant data. "
            "Try market names, metrics (vacancy, rent, absorption), "
            "or page names (trend, compare, raw)."
        ),
        label_visibility="collapsed",
    )

    if not query or query == st.session_state.get("_last_search_applied"):
        return

    st.session_state["_last_search_applied"] = query

    rows = get_all_metrics()
    if not rows:
        return

    df = pd.DataFrame(rows)
    known_markets = sorted(df["market"].unique().tolist())
    known_subs = sorted(df["submarket"].dropna().unique().tolist())
    parsed = _parse_search(query, known_markets, known_subs)

    need_rerun = False

    if parsed["page"]:
        # Defer the nav-page change; the consumer at the top of the script
        # writes it into `nav_page` before the sidebar radio renders.
        st.session_state["pending_nav"] = parsed["page"]
        need_rerun = True
    if parsed["market"]:
        st.session_state["_search_market"] = parsed["market"]
        need_rerun = True
    if parsed["submarket"]:
        st.session_state["_search_submarket"] = parsed["submarket"]
        need_rerun = True
    if parsed["metric"]:
        st.session_state["_search_metric"] = parsed["metric"]
        need_rerun = True

    if need_rerun:
        st.rerun()


def _consume_search_filters() -> dict:
    """Pop and return any pending search filter values from session state."""
    return {
        "market": st.session_state.pop("_search_market", None),
        "submarket": st.session_state.pop("_search_submarket", None),
        "metric": st.session_state.pop("_search_metric", None),
    }


# ---------------------------------------------------------------------------
# Initialize DB
# ---------------------------------------------------------------------------

@st.cache_resource
def setup_db():
    init_db()
    return True

setup_db()


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.markdown(
    """
    <div class="sebco-sidebar-mark">SEBCO&nbsp;&nbsp;INC</div>
    <div class="sebco-sidebar-sub">MARKET&nbsp;&nbsp;INTELLIGENCE</div>
    """,
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    ["Upload", "Uploads", "Summary", "Trends", "Comparison", "Raw Data"],
    key="nav_page",
)

db_path = get_db_path()
st.sidebar.markdown("---")
st.sidebar.markdown(
    f"<div class='sidebar-meta'>DB: <code>{os.path.basename(db_path)}</code></div>",
    unsafe_allow_html=True,
)
st.sidebar.markdown(
    f"<div class='sidebar-meta'>User: <code>{getpass.getuser()}</code></div>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Smart search bar (rendered at the top of every page)
# ---------------------------------------------------------------------------

_render_search_bar()


# ---------------------------------------------------------------------------
# Page: Upload
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _report_identity(records: list[dict]) -> dict:
    """Identity tuple for Layer 2. Deterministic: uses the first record,
    which is stable per file given parse_pdf's fixed strategy order.
    """
    r0 = records[0]
    return {
        "market": r0.get("market"),
        "asset_class": r0.get("asset_class"),
        "report_date": r0.get("report_date"),
        "quarter": r0.get("quarter"),
    }


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "an earlier date"
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y at %H:%M")
    except ValueError:
        return iso[:19]


def page_upload():
    st.header("Upload Kidder Mathews Reports")
    st.markdown(
        "Upload one or more Kidder Mathews quarterly market report PDFs. "
        "Duplicate detection runs automatically: identical files are blocked, "
        "and a different file for an already-loaded report prompts you to "
        "replace or cancel."
    )

    uploaded_files = st.file_uploader(
        "Drop PDF files here",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if not uploaded_files:
        return

    for uf in uploaded_files:
        file_bytes = bytes(uf.getbuffer())
        file_hash = _sha256(file_bytes)
        st.subheader(f"Processing: {uf.name}")

        # ---- Layer 1: exact-bytes duplicate ----
        existing = get_file_by_hash(file_hash)
        if existing:
            when = _fmt_dt(existing.get("uploaded_at"))
            orig = existing.get("original_filename") or uf.name
            if existing.get("status") == STATUS_REJECTED:
                st.error(
                    f"This exact file was uploaded on {when} as '{orig}' and "
                    f"produced **0 records** (marked rejected). It's recorded "
                    f"so you don't keep retrying a broken file. If the parser "
                    f"has since improved, delete the rejected entry from the "
                    f"Uploads → Upload History section first."
                )
            else:
                st.error(
                    f"This file was already uploaded on {when} as '{orig}'. "
                    f"No changes detected."
                )
            continue

        # ---- Parse (cache by hash so button reruns don't re-parse) ----
        cache_key = f"_parse_{file_hash}"
        if cache_key not in st.session_state:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            try:
                with st.spinner("Parsing PDF..."):
                    records = parse_pdf(tmp_path)
                    parser_warnings = get_warnings()
                    for r in records:
                        r["source"] = uf.name
            finally:
                os.unlink(tmp_path)
            st.session_state[cache_key] = {
                "records": records,
                "warnings": parser_warnings,
            }

        cached = st.session_state[cache_key]
        records = cached["records"]
        for w in cached["warnings"]:
            st.warning(w)

        file_meta_base = {
            "file_hash": file_hash,
            "original_filename": uf.name,
            "display_name": format_display_name(uf.name),
            "file_size_bytes": len(file_bytes),
        }

        # ---- Edge case 9: zero records → record hash as rejected ----
        if not records:
            record_uploaded_file({
                **file_meta_base,
                "market": None,
                "asset_class": None,
                "report_date": None,
                "quarter": None,
                "record_count": 0,
                "parser_strategy": None,
                "status": STATUS_REJECTED,
            })
            st.session_state.pop(cache_key, None)
            st.error(
                "Could not extract any data — this may not be a supported "
                "Kidder Mathews format. The file hash has been recorded and "
                "marked **rejected** so you don't keep retrying it."
            )
            continue

        identity = _report_identity(records)
        strategies = ",".join(sorted({
            r.get("parser_strategy") for r in records if r.get("parser_strategy")
        }))

        # ---- Preview ----
        df = pd.DataFrame(records)
        current = df[df["period_type"] == "current"]
        st.markdown(
            f"**Found {len(records)} records** ({len(current)} current-period)"
        )
        st.markdown(
            f"**{identity['market']}** | "
            f"**{(identity['asset_class'] or '').title()}** | "
            f"**{identity['quarter']}**"
        )
        if len(current):
            show_cols = ["submarket", "metric_type", "metric_value",
                         "unit", "confidence", "parser_strategy"]
            st.dataframe(
                current[show_cols].fillna("(market-wide)"),
                use_container_width=True,
                hide_index=True,
            )
        high = len(df[df["confidence"] >= 0.90])
        med = len(df[(df["confidence"] >= 0.75) & (df["confidence"] < 0.90)])
        low = len(df[df["confidence"] < 0.75])
        st.caption(
            f"Confidence: {high} high, {med} medium, {low} low  "
            f"| Review the Raw Data page to correct any parsing errors."
        )

        # ---- Layer 2: same report identity, different file ----
        conflict = find_active_report(
            identity["market"], identity["asset_class"],
            identity["report_date"], identity["quarter"],
        )

        if conflict:
            strat_changed = (conflict.get("parser_strategy") or "") != strategies
            st.warning(
                f"**A report for "
                f"{identity['market']} "
                f"{(identity['asset_class'] or '').title()} "
                f"{identity['quarter']} is already loaded.**\n\n"
                f"- **Existing:** `{conflict.get('original_filename')}` — "
                f"{conflict.get('record_count')} records, uploaded "
                f"{_fmt_dt(conflict.get('uploaded_at'))}\n"
                f"- **New:** `{uf.name}` — {len(records)} records"
                + (
                    f"\n- ⚙️ Parser strategy changed since the existing "
                    f"upload (`{conflict.get('parser_strategy')}` → "
                    f"`{strategies}`) — replacing will re-extract with the "
                    f"current parser."
                    if strat_changed else ""
                )
            )
            st.caption(
                "Keeping both is intentionally not offered — two copies of "
                "the same report would double-count every metric in Trends, "
                "Summary, and Comparison."
            )
            c1, c2, _ = st.columns([1, 1, 3])
            with c1:
                if st.button("Replace existing data",
                             type="primary", key=f"replace_{file_hash}"):
                    deleted = supersede_file(conflict["id"])
                    new_id = record_uploaded_file({
                        **file_meta_base,
                        **identity,
                        "record_count": len(records),
                        "parser_strategy": strategies,
                        "status": "active",
                    })
                    result = insert_metrics(records, source_file_id=new_id)
                    st.session_state.pop(cache_key, None)
                    st.success(
                        f"Replaced. Removed {deleted} old records from "
                        f"'{conflict.get('original_filename')}' (kept as a "
                        f"superseded entry in Upload History) and inserted "
                        f"{result['inserted']} new records"
                        + (f"; {result['rejected']} rejected."
                           if result['rejected'] else ".")
                    )
                    st.rerun()
            with c2:
                if st.button("Cancel upload", key=f"cancel_{file_hash}"):
                    st.session_state.pop(cache_key, None)
                    st.info("Upload cancelled. No changes were made.")
                    st.rerun()
            continue

        # ---- Normal path: no conflict ----
        if st.button("Save to database", key=f"save_{file_hash}"):
            new_id = record_uploaded_file({
                **file_meta_base,
                **identity,
                "record_count": len(records),
                "parser_strategy": strategies,
                "status": "active",
            })
            result = insert_metrics(records, source_file_id=new_id)
            inserted = result["inserted"]
            rejected = result["rejected"]
            st.session_state.pop(cache_key, None)
            if rejected:
                st.warning(
                    f"Saved {inserted} records from {uf.name}, "
                    f"{rejected} rejected — view details on the Raw Data "
                    f"page under “Rejected Records”."
                )
            else:
                st.success(f"Saved {inserted} records from {uf.name}")
            st.rerun()


# ---------------------------------------------------------------------------
# Page: Uploads (library of processed PDFs)
# ---------------------------------------------------------------------------

UPLOAD_SORT_OPTIONS = {
    "Most recent upload": ("latest_upload", True),
    "Oldest upload":      ("latest_upload", False),
    "Market A–Z":         ("market", False),
    "Most records":       ("record_count", True),
    "Highest confidence": ("avg_confidence", True),
}


def _format_upload_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y · %H:%M")
    except ValueError:
        return iso[:19]


def _year_from_quarter(q: str | None) -> str | None:
    if not q:
        return None
    m = re.search(r"(\d{4})", q)
    return m.group(1) if m else None


def page_uploads():
    """Library of all processed PDFs.

    Two modes:
      - List (default): cards with filter / sort / search
      - Detail: drill-down for a single source, plus delete/reparse controls
    """
    selected = st.session_state.get("_upload_detail")
    if selected:
        _render_upload_detail(selected)
        return

    summaries = get_upload_summaries()
    if not summaries:
        st.header("Library")
        st.info(
            "No PDFs uploaded yet. Go to the **Upload** page to add your "
            "first market report."
        )
        if st.button("Go to Upload page"):
            st.session_state["pending_nav"] = "Upload"
            st.rerun()
        return

    # Build a display-name field for each entry up front
    for s in summaries:
        s["display_name"] = format_display_name(s["source"])
        s["year"] = _year_from_quarter(s.get("quarter"))

    st.header("Library")
    st.caption(
        f"{len(summaries)} processed report"
        f"{'s' if len(summaries) != 1 else ''}. "
        "Click any card to view details, delete, or re-parse."
    )

    # --- Filter / sort row ---
    fcol1, fcol2, fcol3, fcol4, fcol5 = st.columns([3, 2, 2, 2, 2.5])
    with fcol1:
        search = st.text_input(
            "Search by name", key="uploads_search",
            placeholder="e.g., Orange County, Boise…",
            label_visibility="visible",
        )
    with fcol2:
        markets = ["(all)"] + sorted({s["market"] for s in summaries if s.get("market")})
        sel_market = st.selectbox("Market", markets, key="uploads_market")
    with fcol3:
        asset_classes = ["(all)"] + sorted({
            (s["asset_class"] or "").title() for s in summaries if s.get("asset_class")
        })
        sel_asset = st.selectbox("Asset class", asset_classes, key="uploads_asset")
    with fcol4:
        years = ["(all)"] + sorted({s["year"] for s in summaries if s.get("year")}, reverse=True)
        sel_year = st.selectbox("Year", years, key="uploads_year")
    with fcol5:
        sort_label = st.selectbox(
            "Sort by", list(UPLOAD_SORT_OPTIONS.keys()), key="uploads_sort"
        )

    # Apply filters
    filtered = summaries
    if search:
        q = search.lower()
        filtered = [s for s in filtered if q in s["display_name"].lower()]
    if sel_market != "(all)":
        filtered = [s for s in filtered if s.get("market") == sel_market]
    if sel_asset != "(all)":
        filtered = [s for s in filtered
                    if (s.get("asset_class") or "").title() == sel_asset]
    if sel_year != "(all)":
        filtered = [s for s in filtered if s.get("year") == sel_year]

    # Apply sort
    sort_key, descending = UPLOAD_SORT_OPTIONS[sort_label]
    def _sort_value(row):
        v = row.get(sort_key)
        if v is None:
            return ("" if isinstance(sort_key, str) and sort_key == "market" else -1)
        return v
    filtered = sorted(filtered, key=_sort_value, reverse=descending)

    st.markdown("---")
    if not filtered:
        st.info("No uploads match these filters.")
    else:
        for s in filtered:
            _render_upload_card(s)

    _render_upload_history()


def _render_upload_history():
    """Audit trail: every uploaded_files row, including superseded and
    rejected, so the user can see exactly what was replaced and when.
    """
    history = get_upload_history()
    if not history:
        return

    superseded = [h for h in history if h["status"] == "superseded"]
    rejected = [h for h in history if h["status"] == "rejected"]
    label = f"Upload History ({len(history)} total"
    if superseded:
        label += f", {len(superseded)} superseded"
    if rejected:
        label += f", {len(rejected)} rejected"
    label += ")"

    with st.expander(label):
        st.caption(
            "Full audit trail of every file processed. **Superseded** rows "
            "were replaced by a newer upload of the same report; their "
            "metric records were deleted but the file record is kept here. "
            "**Rejected** rows produced zero records and are remembered so "
            "the same broken file isn't retried."
        )
        hist_df = pd.DataFrame(history)
        show_cols = [
            "status", "display_name", "original_filename", "market",
            "asset_class", "quarter", "record_count", "uploaded_at",
            "uploaded_by", "parser_strategy",
        ]
        existing = [c for c in show_cols if c in hist_df.columns]
        st.dataframe(
            hist_df[existing],
            use_container_width=True,
            hide_index=True,
            column_config={
                "status": st.column_config.TextColumn(
                    "Status", help="active · superseded · rejected"
                ),
                "display_name": st.column_config.TextColumn("Report"),
                "original_filename": st.column_config.TextColumn("Source file"),
                "uploaded_at": st.column_config.TextColumn("Uploaded"),
            },
        )

        # Allow clearing a rejected entry so an improved parser can retry it.
        if rejected:
            st.markdown("**Clear a rejected file** (lets you re-upload it):")
            rej_opts = {
                f"{h['original_filename']} — rejected {_fmt_dt(h['uploaded_at'])}": h["id"]
                for h in rejected
            }
            choice = st.selectbox(
                "Rejected file", list(rej_opts.keys()),
                key="clear_rejected_sel",
            )
            if st.button("Clear this rejected entry", key="clear_rejected_btn"):
                delete_uploaded_file_row(rej_opts[choice])
                st.success(
                    "Rejected entry cleared. You can now re-upload this file."
                )
                st.rerun()


def _render_upload_card(s: dict):
    """One row in the Uploads library."""
    asset = (s.get("asset_class") or "").title() or "—"
    market = s.get("market") or "—"
    quarter = s.get("quarter") or "—"
    conf = s.get("avg_confidence")
    conf_str = f"{conf * 100:.0f}% avg confidence" if conf is not None else "no confidence data"
    date_str = _format_upload_date(s.get("latest_upload"))
    rejected = s.get("rejected_count") or 0
    rej_str = f" · {rejected} rejected" if rejected else ""

    with st.container():
        c1, c2 = st.columns([6, 1])
        with c1:
            st.markdown(f"### {s['display_name']}")
            st.caption(f"{market} · {asset} · {quarter}")
            st.caption(
                f"Uploaded {date_str} · {s['record_count']:,} records · "
                f"{conf_str}{rej_str}"
            )
        with c2:
            st.markdown("&nbsp;", unsafe_allow_html=True)  # vertical alignment
            if st.button("View details", key=f"vd_{s['source']}"):
                st.session_state["_upload_detail"] = s["source"]
                st.rerun()
    st.markdown("---")


def _render_upload_detail(source: str):
    """Detail view for one source PDF."""
    if st.button("← Back to library", key="back_to_lib"):
        st.session_state.pop("_upload_detail", None)
        st.rerun()

    display_name = format_display_name(source)
    st.header(display_name)
    st.caption(
        f"Original filename: `{source}`"
    )

    records = get_metrics_for_source(source)
    if not records:
        st.warning(
            "No records found for this source. It may have been deleted."
        )
        rej = get_rejected_records(source=source)
        if rej:
            st.markdown(f"**{len(rej)} rejected record(s)** still on file:")
            st.dataframe(pd.DataFrame(rej), use_container_width=True, hide_index=True)
        return

    df = pd.DataFrame(records)

    # Metadata strip
    parser_strategies = sorted(df["parser_strategy"].dropna().unique().tolist())
    upload_dates = sorted(df["date_ingested"].dropna().unique().tolist())
    if upload_dates:
        upload_date_str = _format_upload_date(max(upload_dates))
    else:
        upload_date_str = "—"
    high = int((df["confidence"] >= 0.90).sum())
    med = int(((df["confidence"] >= 0.75) & (df["confidence"] < 0.90)).sum())
    low = int((df["confidence"] < 0.75).sum())

    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    mcol1.metric("Total records", f"{len(df):,}")
    mcol2.metric("Confidence (H/M/L)", f"{high} / {med} / {low}")
    mcol3.metric("Strategies", ", ".join(parser_strategies) or "—")
    mcol4.metric("Last ingested", upload_date_str)

    rej = get_rejected_records(source=source)
    if rej:
        st.warning(
            f"{len(rej)} record(s) from this PDF were rejected — see the "
            "expander below."
        )

    # Filters
    st.markdown("---")
    f1, f2, f3 = st.columns(3)
    with f1:
        metric_options = sorted(df["metric_type"].dropna().unique().tolist())
        sel_metrics = st.multiselect(
            "Metric type", metric_options, default=[], key=f"det_mt_{source}"
        )
    with f2:
        submarket_options = sorted(df["submarket"].dropna().unique().tolist())
        sel_subs = st.multiselect(
            "Submarket", submarket_options, default=[], key=f"det_sub_{source}"
        )
    with f3:
        period_options = sorted(df["period_type"].dropna().unique().tolist())
        sel_periods = st.multiselect(
            "Period type", period_options, default=[], key=f"det_pt_{source}"
        )

    view = df.copy()
    if sel_metrics:
        view = view[view["metric_type"].isin(sel_metrics)]
    if sel_subs:
        view = view[view["submarket"].isin(sel_subs)]
    if sel_periods:
        view = view[view["period_type"].isin(sel_periods)]

    st.markdown(f"**{len(view):,} of {len(df):,} records shown**")
    show_cols = [
        "submarket", "metric_type", "metric_period", "period_type",
        "metric_value", "unit", "confidence",
    ]
    existing = [c for c in show_cols if c in view.columns]
    st.dataframe(
        view[existing].fillna({"submarket": "(market-wide)"}),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Show raw text (first 200 chars per record)"):
        if not view.empty:
            raw_df = view[["submarket", "metric_type", "raw_text"]].copy()
            raw_df["raw_text"] = raw_df["raw_text"].fillna("").str.slice(0, 200)
            st.dataframe(raw_df, use_container_width=True, hide_index=True)

    if rej:
        with st.expander(f"Show {len(rej)} rejected record(s) from this PDF"):
            st.dataframe(pd.DataFrame(rej), use_container_width=True, hide_index=True)

    # --- Actions ---
    st.markdown("---")
    a1, a2, a3 = st.columns([1, 1, 3])
    with a1:
        confirm_key = f"confirm_del_{source}"
        if st.session_state.get(confirm_key):
            if st.button("Confirm delete", type="primary", key=f"del_yes_{source}"):
                n = delete_by_source(source)
                st.session_state.pop(confirm_key, None)
                st.session_state.pop("_upload_detail", None)
                st.success(f"Deleted {n} records from {display_name}")
                st.rerun()
        else:
            if st.button("Delete this upload", key=f"del_ask_{source}"):
                st.session_state[confirm_key] = True
                st.rerun()
    with a2:
        st.button(
            "Re-parse (coming soon)",
            disabled=True,
            help=(
                "Will re-run the parser against the saved PDF if it's "
                "still in the uploads folder. Not yet implemented."
            ),
            key=f"reparse_{source}",
        )
    with a3:
        if st.session_state.get(f"confirm_del_{source}"):
            st.caption(
                "Click **Confirm delete** to permanently remove this "
                "upload's records, or **← Back to library** to cancel."
            )


# ---------------------------------------------------------------------------
# Page: Summary
# ---------------------------------------------------------------------------

def page_summary():
    st.header("Market Summary")
    st.markdown("Latest extracted values per market and submarket.")

    _consume_search_filters()  # consume to avoid stale state

    rows = get_all_metrics()
    if not rows:
        st.info("No data yet. Upload some PDFs first.")
        return

    df = pd.DataFrame(rows)

    # Filter to current-period only
    current = df[df["period_type"] == "current"].copy()
    if current.empty:
        st.info("No current-period data found.")
        return

    # For each market/submarket/metric, keep the most recent metric_period
    current["metric_period"] = pd.to_datetime(current["metric_period"])
    idx = current.groupby(["market", "submarket", "metric_type"])["metric_period"].idxmax()
    latest = current.loc[idx].copy()

    has_warnings = (latest["confidence"] < LOW_CONFIDENCE_THRESHOLD).any()

    # Pivot for display
    for market in latest["market"].unique():
        st.subheader(market)
        mkt_data = latest[latest["market"] == market]

        # Market-wide data
        mkt_wide = mkt_data[mkt_data["submarket"].isna()]
        if not mkt_wide.empty:
            _show_metric_cards(mkt_wide, "Market-wide")

        # Per-submarket
        for sub in sorted(mkt_data["submarket"].dropna().unique()):
            sub_data = mkt_data[mkt_data["submarket"] == sub]
            _show_metric_cards(sub_data, sub)

    if has_warnings:
        st.caption("\u26a0\ufe0f = confidence below 85%. Verify on the Raw Data page.")


def _show_metric_cards(df: pd.DataFrame, label: str):
    """Display metric values as a row of cards."""
    st.markdown(f"**{label}**")
    cols = st.columns(min(len(df), 6))
    for i, (_, row) in enumerate(df.iterrows()):
        with cols[i % len(cols)]:
            metric_type = row["metric_type"]
            metric_display = metric_type.replace("_", " ").title()
            val = row["metric_value"]
            unit = row["unit"]
            confidence = row["confidence"]

            display = _format_value(val, unit)
            warn = _warn_suffix(confidence)

            period = row["metric_period"]
            if pd.notna(period):
                period_str = pd.to_datetime(period).strftime("%b %Y")
            else:
                period_str = ""

            # Build help text: glossary + period
            help_parts = []
            glossary = _metric_help(metric_type)
            if glossary:
                help_parts.append(glossary)
            if period_str:
                help_parts.append(f"Period: {period_str}")
            if warn:
                help_parts.append("Confidence below 85% \u2014 verify in Raw Data")

            st.metric(
                label=metric_display,
                value=display + warn,
                help=" | ".join(help_parts) if help_parts else None,
            )
    st.markdown("---")


# ---------------------------------------------------------------------------
# Page: Trends
# ---------------------------------------------------------------------------

def page_trends():
    st.header("Trend Analysis")

    rows = get_all_metrics()
    if not rows:
        st.info("No data yet. Upload some PDFs first.")
        return

    df = pd.DataFrame(rows)

    # Only show actual period data, not yoy_change
    df = df[df["period_type"].isin(["current", "prior_quarter", "prior_year", "historical"])]
    df["metric_period"] = pd.to_datetime(df["metric_period"])

    # Consume search filters
    search = _consume_search_filters()

    # Filters
    col1, col2, col3 = st.columns(3)

    markets = sorted(df["market"].unique())

    # Apply search market before widget renders
    if search["market"] and search["market"] in markets:
        st.session_state["trend_market"] = search["market"]

    with col1:
        sel_market = st.selectbox("Market", markets, key="trend_market",
                                  help=FILTER_HELP["market"])

    metric_types = sorted(df[df["market"] == sel_market]["metric_type"].unique())

    # Apply search metric before widget renders
    if search["metric"] and search["metric"] in metric_types:
        st.session_state["trend_metric"] = search["metric"]
    elif "trend_metric" in st.session_state and st.session_state["trend_metric"] not in metric_types:
        del st.session_state["trend_metric"]

    # Build metric help text showing selected metric's definition
    current_metric = st.session_state.get("trend_metric", metric_types[0] if metric_types else "")
    metric_help = FILTER_HELP["metric"]
    glossary = _metric_help(current_metric)
    if glossary:
        metric_help += f"\n\n{current_metric.replace('_', ' ').title()}: {glossary}"

    with col2:
        sel_metric = st.selectbox("Metric", metric_types, key="trend_metric",
                                  help=metric_help)

    subs = df[(df["market"] == sel_market) & (df["metric_type"] == sel_metric)]["submarket"]
    sub_options = ["(all)"] + sorted(subs.dropna().unique().tolist())

    if search["submarket"] and search["submarket"] in sub_options:
        st.session_state["trend_sub"] = search["submarket"]
    elif "trend_sub" in st.session_state and st.session_state["trend_sub"] not in sub_options:
        del st.session_state["trend_sub"]

    with col3:
        sel_sub = st.selectbox("Submarket", sub_options, key="trend_sub",
                               help=FILTER_HELP["submarket"])

    # Filter data
    filtered = df[(df["market"] == sel_market) & (df["metric_type"] == sel_metric)]
    if sel_sub != "(all)":
        filtered = filtered[filtered["submarket"] == sel_sub]

    if filtered.empty:
        st.info("No data for this selection.")
        return

    # Deduplicate: keep one value per (submarket, metric_period)
    filtered = filtered.sort_values("confidence", ascending=False).drop_duplicates(
        subset=["submarket", "metric_period"], keep="first"
    )

    # Create label for chart
    filtered = filtered.copy()
    filtered["label"] = filtered["submarket"].fillna("Market-wide")

    # Build chart with per-series control
    unit = filtered["unit"].iloc[0]
    y_label = sel_metric.replace("_", " ").title()
    if unit == "percent":
        y_label += " (%)"
    elif unit == "dollar_per_sf":
        y_label += " ($/SF)"

    fig = go.Figure()

    for label_name in sorted(filtered["label"].unique()):
        series = filtered[filtered["label"] == label_name].sort_values("metric_period")
        n_points = len(series)
        legend_name = f"{label_name} (n={n_points})"

        dates = series["metric_period"].tolist()
        values = series["metric_value"].tolist()

        if n_points < 4:
            # Dots only for sparse data
            fig.add_trace(go.Scatter(
                x=dates,
                y=values,
                mode="markers",
                name=legend_name,
                marker=dict(size=9),
                hovertemplate="%{x|%b %Y}: %{y}<extra>%{fullData.name}</extra>",
            ))
        else:
            # Lines with markers, break connections where gap > 6 months
            x_vals = []
            y_vals = []
            for i in range(len(dates)):
                if i > 0 and (dates[i] - dates[i - 1]).days > 180:
                    x_vals.append(dates[i - 1] + (dates[i] - dates[i - 1]) / 2)
                    y_vals.append(None)
                x_vals.append(dates[i])
                y_vals.append(values[i])

            fig.add_trace(go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines+markers",
                name=legend_name,
                marker=dict(size=6),
                connectgaps=False,
                hovertemplate="%{x|%b %Y}: %{y}<extra>%{fullData.name}</extra>",
            ))

    # X-axis range: cover all data with padding
    all_dates = filtered["metric_period"]
    min_date = all_dates.min()
    max_date = all_dates.max()
    padding = timedelta(days=45)
    fig.update_xaxes(range=[min_date - padding, max_date + padding])

    fig.update_layout(
        height=450,
        xaxis_title="Period",
        yaxis_title=y_label,
        legend_title="Submarket",
        hovermode="closest",
    )
    st.plotly_chart(fig, use_container_width=True)

    # Metric definition caption
    help_text = _metric_help(sel_metric)
    if help_text:
        st.caption(f"**{sel_metric.replace('_', ' ').title()}**: {help_text}")

    # Low-confidence warning
    has_warnings = (filtered["confidence"] < LOW_CONFIDENCE_THRESHOLD).any()
    if has_warnings:
        st.caption("\u26a0\ufe0f Some data points have confidence below 85%. Check the Raw Data page to verify.")

    # Data table below chart (confidence hidden)
    with st.expander("Show data table"):
        show = filtered[["label", "metric_period", "metric_value", "unit", "source"]].copy()
        show.columns = ["Submarket", "Period", "Value", "Unit", "Source"]
        show = show.sort_values(["Submarket", "Period"])
        st.dataframe(show, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page: Comparison
# ---------------------------------------------------------------------------

def page_comparison():
    st.header("Submarket Comparison")

    rows = get_all_metrics()
    if not rows:
        st.info("No data yet. Upload some PDFs first.")
        return

    df = pd.DataFrame(rows)
    df = df[df["period_type"] == "current"]
    df["metric_period"] = pd.to_datetime(df["metric_period"])

    _consume_search_filters()

    # Build submarket list with market prefix
    df["full_sub"] = df.apply(
        lambda r: f"{r['market']} - {r['submarket']}" if pd.notna(r["submarket"]) else f"{r['market']} (market-wide)",
        axis=1
    )

    subs = sorted(df["full_sub"].unique())
    if len(subs) < 2:
        st.info("Need at least 2 submarkets for comparison. Upload more data.")
        return

    col1, col2 = st.columns(2)
    with col1:
        sub1 = st.selectbox("Submarket A", subs, index=0,
                            help="First submarket to compare.")
    with col2:
        sub2 = st.selectbox("Submarket B", subs, index=min(1, len(subs) - 1),
                            help="Second submarket to compare.")

    if sub1 == sub2:
        st.warning("Select two different submarkets to compare.")
        return

    d1 = df[df["full_sub"] == sub1]
    d2 = df[df["full_sub"] == sub2]

    # Get latest values per metric
    def latest_metrics(d):
        if d.empty:
            return {}
        idx = d.groupby("metric_type")["metric_period"].idxmax()
        latest = d.loc[idx]
        return {row["metric_type"]: row for _, row in latest.iterrows()}

    m1 = latest_metrics(d1)
    m2 = latest_metrics(d2)

    all_metrics = sorted(set(list(m1.keys()) + list(m2.keys())))

    if not all_metrics:
        st.info("No comparable metrics found.")
        return

    # Comparison table
    comp_rows = []
    for mt in all_metrics:
        r1 = m1.get(mt)
        r2 = m2.get(mt)

        def _fmt(row):
            if row is None:
                return "\u2014"
            display = _format_value_short(row["metric_value"], row["unit"])
            display += _warn_suffix(row["confidence"])
            return display

        comp_rows.append({
            "Metric": mt.replace("_", " ").title(),
            sub1: _fmt(r1),
            sub2: _fmt(r2),
        })

    st.table(pd.DataFrame(comp_rows))

    # Check if any values have low confidence
    all_rows = list(m1.values()) + list(m2.values())
    has_warnings = any(
        r["confidence"] is not None and r["confidence"] < LOW_CONFIDENCE_THRESHOLD
        for r in all_rows
    )
    if has_warnings:
        st.caption("\u26a0\ufe0f = confidence below 85%. Verify on the Raw Data page.")

    # Metric glossary
    with st.expander("Metric definitions"):
        for mt in all_metrics:
            glossary = _metric_help(mt)
            if glossary:
                st.markdown(f"**{mt.replace('_', ' ').title()}**: {glossary}")


# ---------------------------------------------------------------------------
# Page: Raw Data
# ---------------------------------------------------------------------------

def page_raw_data():
    st.header("Raw Data")

    rows = get_all_metrics()
    if not rows:
        st.info("No data yet. Upload some PDFs first.")
        return

    df = pd.DataFrame(rows)
    df["display_name"] = df["source"].apply(format_display_name)

    search = _consume_search_filters()

    # Filters
    col1, col2, col3, col4 = st.columns(4)

    markets = ["(all)"] + sorted(df["market"].unique().tolist())
    if search["market"] and search["market"] in markets:
        st.session_state["raw_market"] = search["market"]

    with col1:
        sel_market = st.selectbox("Market", markets, key="raw_market",
                                  help=FILTER_HELP["market"])
    with col2:
        period_types = ["(all)"] + sorted(df["period_type"].unique().tolist())
        sel_period = st.selectbox("Period Type", period_types, key="raw_period",
                                  help=FILTER_HELP["period_type"])
    with col3:
        sources = ["(all)"] + sorted(df["source"].unique().tolist())
        sel_source = st.selectbox(
            "Source",
            sources,
            key="raw_source",
            help=FILTER_HELP["source"],
            format_func=lambda s: s if s == "(all)" else format_display_name(s),
        )
    with col4:
        search_text = st.text_input("Search", key="raw_search",
                                    help="Filter rows by text match across all columns.")

    filtered = df.copy()
    if sel_market != "(all)":
        filtered = filtered[filtered["market"] == sel_market]
    if sel_period != "(all)":
        filtered = filtered[filtered["period_type"] == sel_period]
    if sel_source != "(all)":
        filtered = filtered[filtered["source"] == sel_source]
    if search_text:
        mask = filtered.apply(
            lambda r: search_text.lower() in str(r.values).lower(), axis=1
        )
        filtered = filtered[mask]

    st.markdown(f"**{len(filtered)} records**")

    # Display columns: pretty display_name first, raw source kept as reference
    display_cols = [
        "id", "display_name", "source", "quarter", "market", "submarket",
        "metric_type", "metric_value", "unit", "metric_period", "period_type",
        "confidence", "parser_strategy", "last_edited_by", "last_edited_at",
    ]
    existing_cols = [c for c in display_cols if c in filtered.columns]
    st.dataframe(
        filtered[existing_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "display_name": st.column_config.TextColumn(
                "Report", help="Pretty display name derived from filename"
            ),
            "source": st.column_config.TextColumn(
                "Source file", help="Original PDF filename"
            ),
        },
    )

    # CSV export — keep raw `source` AND include `display_name` for spreadsheets
    export_df = filtered.copy()
    if "display_name" in export_df.columns and "source" in export_df.columns:
        cols = list(export_df.columns)
        cols.remove("display_name")
        src_idx = cols.index("source")
        cols.insert(src_idx + 1, "display_name")
        export_df = export_df[cols]
    csv = export_df.to_csv(index=False)
    st.download_button(
        "Export CSV",
        csv,
        "sebco_market_data.csv",
        "text/csv",
    )

    # Manual editing
    st.markdown("---")
    st.subheader("Edit a Record")
    st.markdown("Correct parsing errors by updating metric values.")

    col1, col2 = st.columns(2)
    with col1:
        edit_id = st.number_input("Record ID", min_value=1, step=1, key="edit_id")
    with col2:
        new_val = st.number_input("New Value", format="%.4f", key="edit_val")

    if st.button("Update Record"):
        try:
            update_metric(int(edit_id), float(new_val))
            st.success(
                f"Record {edit_id} updated to {new_val} by {getpass.getuser()}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

    # Delete source
    st.markdown("---")
    st.subheader("Delete Source")
    sources_list = get_distinct_values("source")
    if sources_list:
        del_source = st.selectbox(
            "Source to delete",
            sources_list,
            key="del_source",
            format_func=format_display_name,
        )
        if st.button("Delete all records from this source", type="secondary"):
            n = delete_by_source(del_source)
            st.success(f"Deleted {n} records from {format_display_name(del_source)}")
            st.rerun()

    # Rejected records
    st.markdown("---")
    st.subheader("Rejected Records")
    rejected = get_rejected_records()
    if not rejected:
        st.caption("No rejected records. Records with missing required fields "
                   "(market, asset_class, metric_type, etc.) would appear here.")
    else:
        rej_df = pd.DataFrame(rejected)
        rej_df["display_name"] = rej_df["source"].apply(format_display_name)
        st.caption(
            f"{len(rej_df)} record(s) were skipped because required fields "
            "were missing. Fix the source PDF and re-import, or delete the "
            "rejection after manually adding the record."
        )
        show_rej_cols = [
            "id", "display_name", "source", "source_page", "reason",
            "missing_fields", "raw_text", "parser_strategy", "date_rejected",
        ]
        existing_rej_cols = [c for c in show_rej_cols if c in rej_df.columns]
        st.dataframe(
            rej_df[existing_rej_cols],
            use_container_width=True,
            hide_index=True,
            column_config={
                "display_name": st.column_config.TextColumn(
                    "Report", help="Pretty display name derived from filename"
                ),
                "source": st.column_config.TextColumn(
                    "Source file", help="Original PDF filename"
                ),
            },
        )
        col1, col2 = st.columns([1, 3])
        with col1:
            rej_id = st.number_input(
                "Rejected record ID", min_value=1, step=1, key="rej_id"
            )
        with col2:
            if st.button("Delete rejected record"):
                n = delete_rejected_record(int(rej_id))
                if n:
                    st.success(f"Deleted rejected record {int(rej_id)}")
                    st.rerun()
                else:
                    st.error(f"No rejected record with ID {int(rej_id)}")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if page == "Upload":
    page_upload()
elif page == "Summary":
    page_summary()
elif page == "Uploads":
    page_uploads()
elif page == "Trends":
    page_trends()
elif page == "Comparison":
    page_comparison()
elif page == "Raw Data":
    page_raw_data()
