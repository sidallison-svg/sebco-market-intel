"""
Compare — two markets / submarkets, side by side, same metric set.

Each side picks a (market, submarket) tuple; the asset_class is shared
(defaults to industrial since that's all Sebco's data today). Six key
metrics are pulled for each side from the latest period_type='current'
row, with the delta column colored by domain semantics (vacancy up =
red, rent up = green, etc.).

PDF download per side reuses pdf_export.render_market_snapshot — the
same one-page report the v1 Snapshot Report page rendered.
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd
import streamlit as st

from components import kpi_card
from config import get_db_path
from db import get_all_metrics
from pdf_export import PdfExportError, render_market_snapshot, snapshot_filename
from theme import PALETTE


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Compare")
st.markdown(
    '<p class="page-lede">Two markets or submarkets, side by side.</p>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Metric spec — what to compare + how to interpret a delta
# ---------------------------------------------------------------------------

# (label, ordered metric_type fallbacks, direction)
# direction: 'lower_better' (vacancy, availability), 'higher_better' (rent,
# absorption), 'neutral' (inventory, building counts — no value judgment).
_METRIC_ROWS = [
    ("Total Vacancy",       ("total_vacancy_rate", "vacancy_rate"),  "lower_better"),
    ("Asking Rent",         ("asking_rent",),                         "higher_better"),
    ("Net Absorption",      ("net_absorption",),                      "higher_better"),
    ("Total Availability",  ("total_availability_rate",
                             "availability_rate"),                    "lower_better"),
    ("Under Construction",  ("under_construction",),                  "neutral"),
    ("Total Inventory",     ("total_inventory",),                     "neutral"),
]


def _fmt(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit in ("percent", "percent_change"):
        return f"{v:.1f}%"
    if unit == "dollar_per_sf":
        return f"${v:.2f}"
    if unit == "sf":
        if abs(v) >= 1_000_000:
            return f"{v / 1_000_000:.1f}M SF"
        if abs(v) >= 1_000:
            return f"{v / 1_000:.0f}K SF"
        return f"{v:,.0f} SF"
    if unit == "number":
        return f"{v:,.0f}"
    return f"{v:.2f}"


def _fmt_delta(d: float | None, unit: str) -> str:
    if d is None:
        return "—"
    sign = "+" if d > 0 else ("" if d == 0 else "−")
    mag = abs(d)
    if unit in ("percent", "percent_change"):
        return f"{sign}{mag:.1f}pp"
    if unit == "dollar_per_sf":
        return f"{sign}${mag:.2f}"
    if unit == "sf":
        if mag >= 1_000_000:
            return f"{sign}{mag / 1_000_000:.1f}M"
        if mag >= 1_000:
            return f"{sign}{mag / 1_000:.0f}K"
        return f"{sign}{mag:,.0f}"
    return f"{sign}{mag:.2f}"


def _delta_color(delta: float | None, direction: str) -> str:
    """Return a hex color for the delta. Domain semantics applied:
    a positive delta on a 'lower_better' metric is bad (red), and so on.
    'neutral' metrics always render in the secondary text color."""
    if delta is None or delta == 0 or direction == "neutral":
        return PALETTE["text_secondary"]
    if direction == "higher_better":
        return PALETTE["positive"] if delta > 0 else PALETTE["negative"]
    if direction == "lower_better":
        return PALETTE["negative"] if delta > 0 else PALETTE["positive"]
    return PALETTE["text_secondary"]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _all_current() -> pd.DataFrame:
    df = pd.DataFrame(get_all_metrics())
    if df.empty:
        return df
    df = df[df["period_type"] == "current"].copy()
    df["period_date"] = pd.to_datetime(df["period_date"])
    return df


def _pick(df: pd.DataFrame, market: str, submarket: str,
          asset_class: str, mts: Iterable[str]) -> tuple[float | None, str]:
    """Most recent value for (market, submarket, asset_class) trying each
    metric_type in order. Returns (value, unit) or (None, '')."""
    sub_df = df[
        (df["market"] == market)
        & (df["submarket"] == submarket)
        & (df["asset_class"] == asset_class)
    ]
    for mt in mts:
        match = sub_df[sub_df["metric_type"] == mt]
        if match.empty:
            continue
        # Prefer highest confidence then most recent period_date
        match = match.sort_values(
            ["confidence", "period_date"], ascending=[False, False],
        )
        row = match.iloc[0]
        return float(row["value"]) if pd.notna(row["value"]) else None, row["unit"]
    return None, ""


df_all = _all_current()
if df_all.empty:
    st.info("No data yet — upload a report on the Library page first.")
    st.stop()


# ---------------------------------------------------------------------------
# Pickers (two columns, mirrored)
# ---------------------------------------------------------------------------

# Asset class up top — applies to both sides.
asset_classes = sorted(df_all["asset_class"].dropna().unique().tolist())
default_ac = (asset_classes.index("industrial")
              if "industrial" in asset_classes else 0)
sel_ac = st.selectbox("Asset class", asset_classes, index=default_ac,
                      key="cmp_ac")
df_ac = df_all[df_all["asset_class"] == sel_ac]

markets = sorted(df_ac["market"].dropna().unique().tolist())
if not markets:
    st.warning(f"No data for asset class '{sel_ac}'.")
    st.stop()

# Defaults: two distinct markets so the comparison is useful immediately.
def_a = markets[0]
def_b = markets[1] if len(markets) > 1 else markets[0]


def _picker(side: str, default_market: str) -> tuple[str, str]:
    m_key = f"cmp_{side}_market"
    s_key = f"cmp_{side}_sub"
    mkt = st.selectbox("Market", markets,
                       index=markets.index(default_market),
                       key=m_key)
    subs = sorted(s for s in df_ac[df_ac["market"] == mkt]["submarket"].unique()
                  if s)
    sub_opts = ["(Market total)"] + subs
    sub = st.selectbox("Submarket", sub_opts, key=s_key)
    return mkt, "" if sub == "(Market total)" else sub


pa_col, pb_col = st.columns(2)
with pa_col:
    st.markdown("##### Side A")
    a_market, a_sub = _picker("a", def_a)
with pb_col:
    st.markdown("##### Side B")
    b_market, b_sub = _picker("b", def_b)


# ---------------------------------------------------------------------------
# KPI strip (key metrics, side-by-side cards)
# ---------------------------------------------------------------------------

a_label = a_market + (f" · {a_sub}" if a_sub else "")
b_label = b_market + (f" · {b_sub}" if b_sub else "")

st.markdown("")  # vertical breathing room

# Render the same metric on both sides as a row of (card-A, delta-cell, card-B)
def _delta_cell_html(delta: float | None, unit: str, direction: str) -> str:
    color = _delta_color(delta, direction)
    txt = _fmt_delta(delta, unit)
    return (
        f'<div style="display:flex;align-items:center;justify-content:center;'
        f'height:100%;font-weight:600;color:{color};font-size:16px;'
        f'padding-top:32px;">{txt}</div>'
    )


for label, mts, direction in _METRIC_ROWS:
    a_val, a_unit = _pick(df_ac, a_market, a_sub, sel_ac, mts)
    b_val, b_unit = _pick(df_ac, b_market, b_sub, sel_ac, mts)
    # Pick a non-empty unit so delta formatting works even if one side missing
    unit = a_unit or b_unit
    delta = (a_val - b_val) if (a_val is not None and b_val is not None) else None

    ca, cd, cb = st.columns([4, 2, 4])
    with ca:
        kpi_card(label, _fmt(a_val, a_unit or unit), sub=a_label)
    with cd:
        st.markdown(_delta_cell_html(delta, unit, direction),
                    unsafe_allow_html=True)
    with cb:
        kpi_card(label, _fmt(b_val, b_unit or unit), sub=b_label)
    st.markdown("")  # gap


# ---------------------------------------------------------------------------
# Download per-side PDF (reuses the existing snapshot renderer)
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown("### Download single-side PDF snapshot")

# The snapshot is market-level; submarket selection doesn't affect it.
# Quarter comes from the most recent current row on that side.
def _latest_quarter(market: str) -> str | None:
    rows = df_ac[(df_ac["market"] == market)
                 & (df_ac["asset_class"] == sel_ac)]
    if rows.empty:
        return None
    return rows.sort_values("period_date", ascending=False)["quarter"].iloc[0]


def _pdf_button(side: str, market: str) -> None:
    quarter = _latest_quarter(market)
    if not quarter:
        st.caption(f"No quarter detected for {market}.")
        return
    fname = snapshot_filename(market, sel_ac, quarter)
    try:
        pdf_bytes = render_market_snapshot(market, sel_ac, quarter,
                                           get_db_path())
    except PdfExportError as e:
        st.error(f"PDF export failed: {e}")
        return
    st.download_button(
        label=f"Download {market} · {quarter}",
        data=pdf_bytes,
        file_name=fname,
        mime="application/pdf",
        key=f"cmp_pdf_{side}",
    )


dl_a, dl_b = st.columns(2)
with dl_a:
    _pdf_button("a", a_market)
with dl_b:
    _pdf_button("b", b_market)
