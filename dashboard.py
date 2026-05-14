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
    check_source_exists,
    delete_by_source,
    get_all_metrics,
    get_distinct_values,
    init_db,
    insert_metrics,
    update_metric,
)
from pdf_parser import parse_pdf

# ---------------------------------------------------------------------------
# App config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Sebco Market Intel",
    page_icon="\U0001f4ca",
    layout="wide",
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
        st.session_state["nav_page"] = parsed["page"]
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

page = st.sidebar.radio(
    "Navigation",
    ["Upload", "Summary", "Trends", "Comparison", "Raw Data"],
    key="nav_page",
)

db_path = get_db_path()
st.sidebar.markdown("---")
st.sidebar.caption(f"DB: `{os.path.basename(db_path)}`")
st.sidebar.caption(f"User: `{getpass.getuser()}`")

# ---------------------------------------------------------------------------
# Smart search bar (rendered at the top of every page)
# ---------------------------------------------------------------------------

_render_search_bar()


# ---------------------------------------------------------------------------
# Page: Upload
# ---------------------------------------------------------------------------

def page_upload():
    st.header("Upload Kidder Mathews Reports")
    st.markdown(
        "Upload one or more Kidder Mathews quarterly market report PDFs. "
        "The parser will extract key metrics automatically."
    )

    uploaded_files = st.file_uploader(
        "Drop PDF files here",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        for uf in uploaded_files:
            st.subheader(f"Processing: {uf.name}")

            # Check for duplicates
            if check_source_exists(uf.name):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.warning(f"'{uf.name}' has already been imported.")
                with col2:
                    if st.button(f"Re-import", key=f"reimport_{uf.name}"):
                        delete_by_source(uf.name)
                        st.rerun()
                    else:
                        continue

            # Save to temp file and parse
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(uf.getbuffer())
                tmp_path = tmp.name

            try:
                with st.spinner("Parsing PDF..."):
                    records = parse_pdf(tmp_path)
                    # Override source with original filename
                    for r in records:
                        r["source"] = uf.name
            finally:
                os.unlink(tmp_path)

            if not records:
                st.error(
                    "Could not extract any data. This may not be a supported "
                    "Kidder Mathews report format."
                )
                continue

            # Show preview
            df = pd.DataFrame(records)
            current = df[df["period_type"] == "current"]

            st.markdown(f"**Found {len(records)} records** "
                       f"({len(current)} current-period)")

            # Summary stats
            market = records[0].get("market", "Unknown")
            asset = records[0].get("asset_class", "Unknown")
            quarter = records[0].get("quarter", "Unknown")
            st.markdown(f"**{market}** | **{asset.title()}** | **{quarter}**")

            # Show current-period data (confidence kept on upload preview)
            if len(current):
                show_cols = ["submarket", "metric_type", "metric_value",
                            "unit", "confidence", "parser_strategy"]
                st.dataframe(
                    current[show_cols].fillna("(market-wide)"),
                    use_container_width=True,
                    hide_index=True,
                )

            # Confidence summary
            high = len(df[df["confidence"] >= 0.90])
            med = len(df[(df["confidence"] >= 0.75) & (df["confidence"] < 0.90)])
            low = len(df[df["confidence"] < 0.75])
            st.caption(
                f"Confidence: {high} high, {med} medium, {low} low  "
                f"| Review the Raw Data page to correct any parsing errors."
            )

            # Save button
            if st.button(f"Save to database", key=f"save_{uf.name}"):
                n = insert_metrics(records)
                st.success(f"Saved {n} records from {uf.name}")
                st.rerun()


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
        sel_source = st.selectbox("Source", sources, key="raw_source",
                                  help=FILTER_HELP["source"])
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

    # Display columns (confidence kept on Raw Data page)
    display_cols = [
        "id", "source", "quarter", "market", "submarket", "metric_type",
        "metric_value", "unit", "metric_period", "period_type", "confidence",
        "parser_strategy", "last_edited_by", "last_edited_at",
    ]
    existing_cols = [c for c in display_cols if c in filtered.columns]
    st.dataframe(
        filtered[existing_cols],
        use_container_width=True,
        hide_index=True,
    )

    # CSV export
    csv = filtered.to_csv(index=False)
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
        del_source = st.selectbox("Source to delete", sources_list, key="del_source")
        if st.button("Delete all records from this source", type="secondary"):
            n = delete_by_source(del_source)
            st.success(f"Deleted {n} records from {del_source}")
            st.rerun()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if page == "Upload":
    page_upload()
elif page == "Summary":
    page_summary()
elif page == "Trends":
    page_trends()
elif page == "Comparison":
    page_comparison()
elif page == "Raw Data":
    page_raw_data()
