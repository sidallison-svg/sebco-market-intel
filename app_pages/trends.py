"""
Trends — one market, one metric, multi-quarter line chart.

Charts every period_date the DB has for the chosen (market, asset_class,
metric_type) tuple. By default just the market-wide row (submarket='')
is plotted; toggle "Show submarkets" to break it out per-submarket.

Today's data is mostly 1Q26 with a few prior_quarter / prior_year rows
from the Kidder breakdowns, so charts look sparse — that's a data
coverage limit, not the page's fault. As more quarterly reports get
uploaded, lines fill out automatically.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from db import get_all_metrics
from theme import PALETTE
from utils import load_sebco_portfolio


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Trends")
st.markdown(
    '<p class="page-lede">Multi-quarter movement for any market and '
    'metric.</p>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Metric label + formatting
# ---------------------------------------------------------------------------

# Curated label map. Anything not listed falls back to title-cased
# underscores, which is fine for the long tail.
_METRIC_LABELS = {
    "asking_rent":               "Asking Rent",
    "vacancy_rate":              "Vacancy Rate",
    "total_vacancy_rate":        "Total Vacancy Rate",
    "availability_rate":         "Availability Rate",
    "total_availability_rate":   "Total Availability Rate",
    "direct_availability_rate":  "Direct Availability Rate",
    "sublease_availability_rate": "Sublease Availability Rate",
    "net_absorption":            "Net Absorption",
    "ytd_net_absorption":        "YTD Net Absorption",
    "gross_absorption":          "Gross Absorption",
    "ytd_gross_absorption":      "YTD Gross Absorption",
    "under_construction":        "Under Construction",
    "deliveries":                "Deliveries",
    "ytd_deliveries":            "YTD Deliveries",
    "total_inventory":           "Total Inventory",
    "leasing_activity":          "Leasing Activity",
    "ytd_leasing_activity":      "YTD Leasing Activity",
    "cap_rate":                  "Cap Rate",
    "sales_volume":              "Sales Volume",
    "building_count":            "Building Count",
    "available_sf":              "Available SF",
    "vacant_sf":                 "Vacant SF",
    "planned_construction":      "Planned Construction",
}


def _metric_label(mt: str) -> str:
    return _METRIC_LABELS.get(mt, mt.replace("_", " ").title())


def _fmt_value(v: float | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "percent" or unit == "percent_change":
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


def _y_axis_tickformat(unit: str) -> str | None:
    if unit == "percent" or unit == "percent_change":
        return ".1f"
    if unit == "dollar_per_sf":
        return "$,.2f"
    if unit == "sf":
        return ",.0f"
    return None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _all_metrics() -> pd.DataFrame:
    df = pd.DataFrame(get_all_metrics())
    if df.empty:
        return df
    # Only current-period rows participate in the trend line — prior_year
    # / prior_quarter rows are "this report's view of past quarters" and
    # would double-plot the same period.
    df = df[df["period_type"] == "current"].copy()
    df["period_date"] = pd.to_datetime(df["period_date"])
    return df


df_all = _all_metrics()

if df_all.empty:
    st.info("No data yet — upload a report on the Library page first.")
    st.stop()


# ---------------------------------------------------------------------------
# Pickers
# ---------------------------------------------------------------------------

c1, c2, c3 = st.columns(3)

with c1:
    markets = sorted(df_all["market"].dropna().unique().tolist())
    sel_market = st.selectbox("Market", markets, key="trends_market")

df_market = df_all[df_all["market"] == sel_market]

with c2:
    asset_classes = sorted(df_market["asset_class"].dropna().unique().tolist())
    default_ac_idx = (asset_classes.index("industrial")
                      if "industrial" in asset_classes else 0)
    sel_ac = st.selectbox("Asset class", asset_classes, index=default_ac_idx,
                          key="trends_ac")

df_ma = df_market[df_market["asset_class"] == sel_ac]

with c3:
    metrics_avail = sorted(df_ma["metric_type"].dropna().unique().tolist())
    default_mt_idx = (metrics_avail.index("asking_rent")
                      if "asking_rent" in metrics_avail
                      else (metrics_avail.index("total_vacancy_rate")
                            if "total_vacancy_rate" in metrics_avail else 0))
    sel_metric = st.selectbox("Metric", metrics_avail, index=default_mt_idx,
                              key="trends_metric",
                              format_func=_metric_label)

df_chart = df_ma[df_ma["metric_type"] == sel_metric].copy()

# Sub-options
o1, o2, _ = st.columns([1, 1, 4])
with o1:
    show_submarkets = st.toggle("Show submarkets", value=False,
                                key="trends_show_subs")
with o2:
    portfolio = load_sebco_portfolio()
    sebco_overlay_available = (
        sel_metric == "asking_rent"
        and portfolio.get(sel_market, {}).get("sebco_asking_rent") is not None
    )
    show_sebco = st.toggle(
        "Show Sebco", value=sebco_overlay_available,
        disabled=not sebco_overlay_available,
        key="trends_show_sebco",
        help=("Overlays Sebco's in-place asking rent for this market. "
              "Only available for asking_rent metric when the market is "
              "configured in sebco_portfolio.json.")
        if sebco_overlay_available else
        "Sebco overlay only available for asking_rent in configured markets.",
    )


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

if df_chart.empty:
    st.warning(f"No {_metric_label(sel_metric)} data for {sel_market} "
               f"({sel_ac}).")
    st.stop()


def _quarter_label_from_date(d) -> str:
    """'2026-03-31' -> '1Q 2026'."""
    s = str(d)[:10]
    if len(s) < 7:
        return s
    yr, mo = s[:4], s[5:7]
    q = {"03": "1Q", "06": "2Q", "09": "3Q", "12": "4Q"}.get(mo)
    return f"{q} {yr}" if q else s


# Limit to the most recent 4 quarters of data — past that the chart gets
# cluttered and Sebco principals are looking at trajectory, not history.
df_chart["quarter_label"] = df_chart["period_date"].apply(
    _quarter_label_from_date)
recent_qs_sorted = (df_chart[["period_date", "quarter_label"]]
                    .drop_duplicates()
                    .sort_values("period_date")
                    .tail(4))
keep_labels = recent_qs_sorted["quarter_label"].tolist()
df_chart = df_chart[df_chart["quarter_label"].isin(keep_labels)]

# Decide which submarkets to draw
if show_submarkets:
    series_keys = sorted(df_chart["submarket"].fillna("").unique().tolist(),
                         key=lambda s: ("" if s == "" else s))
    series_labels = {s: ("Market total" if s == "" else s)
                     for s in series_keys}
else:
    if (df_chart["submarket"] == "").any():
        series_keys = [""]
    else:
        series_keys = [sorted(df_chart["submarket"].unique().tolist())[0]]
    series_labels = {s: ("Market total" if s == "" else s)
                     for s in series_keys}

unit = df_chart["unit"].iloc[0]

fig = go.Figure()
palette_cycle = [PALETTE["accent"], PALETTE["positive"], PALETTE["warning"],
                 PALETTE["negative"], PALETTE["neutral"],
                 "#7C3AED", "#0891B2", "#EA580C", "#65A30D", "#9333EA"]
for i, sk in enumerate(series_keys):
    sub_df = (df_chart[df_chart["submarket"].fillna("") == sk]
              .sort_values("period_date"))
    if sub_df.empty:
        continue
    fig.add_trace(go.Scatter(
        x=sub_df["quarter_label"],
        y=sub_df["value"],
        mode="lines+markers",
        name=series_labels[sk],
        line={"color": palette_cycle[i % len(palette_cycle)], "width": 2},
        marker={"size": 7},
        hovertemplate=(
            "<b>%{fullData.name}</b><br>"
            "%{x}<br>"
            "%{y}<extra></extra>"
        ),
    ))

# Sebco overlay
if show_sebco and sebco_overlay_available:
    sebco_rent = portfolio[sel_market]["sebco_asking_rent"]
    fig.add_hline(
        y=sebco_rent,
        line={"color": PALETTE["text_secondary"], "width": 1, "dash": "dash"},
        annotation_text=f"Sebco · ${sebco_rent:.2f}",
        annotation_position="bottom right",
        annotation_font={"color": PALETTE["text_secondary"], "size": 11},
    )

tickfmt = _y_axis_tickformat(unit)
fig.update_layout(
    height=420,
    showlegend=show_submarkets,
    xaxis_title=None,
    yaxis_title=f"{_metric_label(sel_metric)} ({unit})",
    xaxis={"type": "category", "categoryorder": "array",
           "categoryarray": keep_labels},
)
if tickfmt:
    fig.update_yaxes(tickformat=tickfmt)

st.plotly_chart(fig, width="stretch",
                config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Underlying values table
# ---------------------------------------------------------------------------

st.markdown("## Underlying data")

table_df = df_chart[[
    "period_date", "submarket", "value", "unit", "lease_type",
    "source", "source_series", "confidence",
]].copy()
table_df["submarket"] = table_df["submarket"].replace("", "Market total")
table_df = table_df.sort_values(["period_date", "submarket"], ascending=[False, True])
table_df["formatted"] = table_df.apply(
    lambda r: _fmt_value(r["value"], r["unit"]), axis=1,
)

st.dataframe(
    table_df[["period_date", "submarket", "formatted", "lease_type",
              "source", "source_series", "confidence"]]
        .rename(columns={"formatted": _metric_label(sel_metric)}),
    width="stretch", hide_index=True,
    column_config={
        "period_date": st.column_config.DateColumn("Period"),
        "confidence":  st.column_config.NumberColumn("Conf", format="%.2f"),
        "source_series": st.column_config.TextColumn("Parser"),
    },
)
