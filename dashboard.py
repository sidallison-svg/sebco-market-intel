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
import tempfile
from datetime import datetime

import pandas as pd
import plotly.express as px
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
    page_icon="📊",
    layout="wide",
)

CONFIDENCE_COLORS = {
    "high": "#2ecc71",    # >= 0.90
    "medium": "#f39c12",  # >= 0.75
    "low": "#e74c3c",     # < 0.75
}


def confidence_label(val: float | None) -> str:
    if val is None:
        return "unknown"
    if val >= 0.90:
        return "high"
    if val >= 0.75:
        return "medium"
    return "low"


def confidence_dot(val: float | None) -> str:
    label = confidence_label(val)
    color = CONFIDENCE_COLORS.get(label, "#999")
    return f'<span style="color:{color}; font-size:1.2em;">●</span> {label} ({val:.0%})'


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
)

db_path = get_db_path()
st.sidebar.markdown("---")
st.sidebar.caption(f"DB: `{os.path.basename(db_path)}`")
st.sidebar.caption(f"User: `{getpass.getuser()}`")


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

            # Show current-period data
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


def _show_metric_cards(df: pd.DataFrame, label: str):
    """Display metric values as a row of cards."""
    st.markdown(f"**{label}**")
    cols = st.columns(min(len(df), 6))
    for i, (_, row) in enumerate(df.iterrows()):
        with cols[i % len(cols)]:
            metric = row["metric_type"].replace("_", " ").title()
            val = row["metric_value"]
            unit = row["unit"]

            if unit == "percent":
                display = f"{val:.1f}%"
            elif unit == "dollar_per_sf":
                display = f"${val:.2f}/SF"
            elif unit == "sf":
                if abs(val) >= 1_000_000:
                    display = f"{val/1_000_000:.1f}M SF"
                elif abs(val) >= 1_000:
                    display = f"{val/1_000:.0f}K SF"
                else:
                    display = f"{val:,.0f} SF"
            else:
                display = f"{val:,.2f}"

            period = row["metric_period"]
            if pd.notna(period):
                period_str = pd.to_datetime(period).strftime("%b %Y")
            else:
                period_str = ""

            st.metric(
                label=metric,
                value=display,
                help=f"Confidence: {confidence_label(row['confidence'])} | {period_str}",
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

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        markets = sorted(df["market"].unique())
        sel_market = st.selectbox("Market", markets)
    with col2:
        metric_types = sorted(df[df["market"] == sel_market]["metric_type"].unique())
        sel_metric = st.selectbox("Metric", metric_types)
    with col3:
        subs = df[(df["market"] == sel_market) & (df["metric_type"] == sel_metric)]["submarket"]
        sub_options = ["(all)"] + sorted(subs.dropna().unique().tolist())
        sel_sub = st.selectbox("Submarket", sub_options)

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
    filtered["label"] = filtered["submarket"].fillna("Market-wide")

    # Plot
    unit = filtered["unit"].iloc[0]
    y_label = sel_metric.replace("_", " ").title()
    if unit == "percent":
        y_label += " (%)"
    elif unit == "dollar_per_sf":
        y_label += " ($/SF)"

    fig = px.line(
        filtered.sort_values("metric_period"),
        x="metric_period",
        y="metric_value",
        color="label",
        markers=True,
        labels={"metric_period": "Period", "metric_value": y_label, "label": "Submarket"},
    )
    fig.update_layout(height=450)
    st.plotly_chart(fig, use_container_width=True)

    # Data table below chart
    with st.expander("Show data table"):
        show = filtered[["label", "metric_period", "metric_value", "unit", "source", "confidence"]].copy()
        show.columns = ["Submarket", "Period", "Value", "Unit", "Source", "Confidence"]
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
        sub1 = st.selectbox("Submarket A", subs, index=0)
    with col2:
        sub2 = st.selectbox("Submarket B", subs, index=min(1, len(subs) - 1))

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

        def fmt(row):
            if row is None:
                return "—"
            v = row["metric_value"]
            u = row["unit"]
            if u == "percent":
                return f"{v:.1f}%"
            elif u == "dollar_per_sf":
                return f"${v:.2f}"
            elif u == "sf" and abs(v) >= 1_000_000:
                return f"{v / 1_000_000:.1f}M"
            elif u == "sf" and abs(v) >= 1_000:
                return f"{v / 1_000:.0f}K"
            return f"{v:,.0f}"

        comp_rows.append({
            "Metric": mt.replace("_", " ").title(),
            sub1: fmt(r1),
            sub2: fmt(r2),
        })

    st.table(pd.DataFrame(comp_rows))


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

    # Filters
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        markets = ["(all)"] + sorted(df["market"].unique().tolist())
        sel_market = st.selectbox("Market", markets, key="raw_market")
    with col2:
        period_types = ["(all)"] + sorted(df["period_type"].unique().tolist())
        sel_period = st.selectbox("Period Type", period_types, key="raw_period")
    with col3:
        sources = ["(all)"] + sorted(df["source"].unique().tolist())
        sel_source = st.selectbox("Source", sources, key="raw_source")
    with col4:
        search = st.text_input("Search", key="raw_search")

    filtered = df.copy()
    if sel_market != "(all)":
        filtered = filtered[filtered["market"] == sel_market]
    if sel_period != "(all)":
        filtered = filtered[filtered["period_type"] == sel_period]
    if sel_source != "(all)":
        filtered = filtered[filtered["source"] == sel_source]
    if search:
        mask = filtered.apply(
            lambda r: search.lower() in str(r.values).lower(), axis=1
        )
        filtered = filtered[mask]

    st.markdown(f"**{len(filtered)} records**")

    # Display columns
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
