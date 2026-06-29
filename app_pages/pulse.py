"""
Pulse — landing page. At-a-glance state of the Sebco markets.

Responsive 3-up grid of cards (any number of markets, wrapping to new
rows of three). Each card shows the two metrics principals look at most
(vacancy + asking rent), a QoQ arrow if the source PDF carried a
prior_quarter row, a tiny sparkline of rent over the historical points
the DB has (current + prior_quarter + prior_year from Kidder breakdowns;
sparser for CBRE/Voit/JLL which are single-period), and a link into the
Trends page for that market.

Markets without any uploaded data render a placeholder card with a
direct nudge to the Library. Markets render in utils.ordered_markets()
order: the canonical SEBCO_PORTFOLIO_ORDER first, then any added later.
An "Add market" button opens a dialog to append a new market (with an
optional data_source mapping) to the local portfolio file.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from components import sparkline
from db import get_all_metrics
from theme import PALETTE, RADIUS, SHADOW, SPACE, TYPE_SCALE
from utils import (
    MARKET_WIDE_SUBMARKETS,
    data_query_keys,
    load_sebco_portfolio,
    ordered_markets,
    save_sebco_portfolio,
)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Pulse")
st.markdown(
    '<p class="page-lede">Six Sebco markets, current quarter at a glance.</p>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def _frame() -> pd.DataFrame:
    df = pd.DataFrame(get_all_metrics())
    if df.empty:
        return df
    df["period_date"] = pd.to_datetime(df["period_date"])
    return df


df = _frame()
portfolio = load_sebco_portfolio()


def _market_frame(market: str) -> pd.DataFrame:
    """Rows in the metrics frame that represent this Sebco market.

    Resolves the market through the portfolio's data_source map: a
    top-level market (San Diego, Orange County) returns its market-wide
    rows; a submarket-backed market (Kent Valley -> Seattle/Southend,
    Marysville -> Seattle/Northend) returns the parent market's rows for
    the aliased submarket(s). A `_subpri` column carries the alias
    priority (0 = best) so the preferred alias wins when several match.
    """
    db_market, submarkets = data_query_keys(market, portfolio)
    sub = df[
        (df["market"] == db_market)
        & (df["asset_class"].isin(["overall", "industrial"]))
        & (df["submarket"].isin(submarkets))
    ].copy()
    if sub.empty:
        return sub
    priority = {name: i for i, name in enumerate(submarkets)}
    sub["_subpri"] = sub["submarket"].map(priority).fillna(len(submarkets))
    return sub


def _best_value(market: str, mts: list[str],
                period_type: str = "current") -> tuple[float | None, str]:
    """Highest-priority, highest-confidence, most-recent value for any of
    the candidate metric_types for this market.

    Returns (value, unit) or (None, '')."""
    sub = _market_frame(market)
    if sub.empty:
        return None, ""
    sub = sub[sub["period_type"] == period_type]
    for mt in mts:
        match = sub[sub["metric_type"] == mt]
        if match.empty:
            continue
        match = match.sort_values(["_subpri", "confidence", "period_date"],
                                  ascending=[True, False, False])
        row = match.iloc[0]
        return (float(row["value"]) if pd.notna(row["value"]) else None,
                row["unit"])
    return None, ""


def _historical(market: str, mts: list[str]) -> list[float | None]:
    """Up to four historical points (prior_year, prior_quarter, current)
    for the sparkline. Returns chronological list of values.

    When several submarket aliases match, only the highest-priority one
    that actually carries the metric is plotted, so the sparkline tracks a
    single consistent series rather than mixing sub-areas.
    """
    sub = _market_frame(market)
    if sub.empty:
        return []
    sub = sub[sub["metric_type"].isin(mts)]
    if sub.empty:
        return []
    best_pri = sub["_subpri"].min()
    sub = sub[sub["_subpri"] == best_pri].sort_values("period_date")
    return [float(v) if pd.notna(v) else None for v in sub["value"]]


# ---------------------------------------------------------------------------
# Card rendering
# ---------------------------------------------------------------------------

def _fmt_vacancy(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def _fmt_rent(v: float | None) -> str:
    return f"${v:.2f}" if v is not None else "—"


def _fmt_delta_pp(curr: float | None, prev: float | None) -> tuple[str, str]:
    """Returns (formatted_text, tone). tone in {positive, negative, neutral}."""
    if curr is None or prev is None:
        return "", "neutral"
    d = curr - prev
    if abs(d) < 0.05:
        return "—", "neutral"
    return f"{'+' if d > 0 else '−'}{abs(d):.1f}pp", \
           ("negative" if d > 0 else "positive")  # vacancy up = bad


def _fmt_delta_dollar(curr: float | None, prev: float | None) -> tuple[str, str]:
    if curr is None or prev is None:
        return "", "neutral"
    d = curr - prev
    if abs(d) < 0.01:
        return "—", "neutral"
    return f"{'+' if d > 0 else '−'}${abs(d):.2f}", \
           ("positive" if d > 0 else "negative")  # rent up = good


_DELTA_COLOR = {
    "positive": PALETTE["positive"],
    "negative": PALETTE["negative"],
    "neutral":  PALETTE["text_tertiary"],
}


def _card_html(market: str, quarter_label: str,
               vac: float | None, vac_delta: tuple[str, str],
               rent: float | None, rent_delta: tuple[str, str],
               sebco_rent: float | None,
               has_data: bool) -> str:
    """Render the static portion of a market card as one HTML block.

    Returns a single-line HTML string. Multi-line/indented HTML hits
    Streamlit's CommonMark parser, which treats lines indented by 4+
    spaces as a code block — even with unsafe_allow_html=True the inner
    content would render as literal text. Keeping the whole card on
    one line side-steps that entirely.
    """
    p = PALETTE
    if not has_data:
        body = (
            f'<div style="color:{p["text_tertiary"]};'
            f'font-size:{TYPE_SCALE["sm"]};padding:{SPACE["6"]} 0;">'
            'No reports uploaded yet for this market. '
            'Visit Library to add one.</div>'
        )
    else:
        vac_txt = _fmt_vacancy(vac)
        rent_txt = _fmt_rent(rent)
        vac_dtxt, vac_tone = vac_delta
        rent_dtxt, rent_tone = rent_delta

        sebco_line = ""
        if sebco_rent is not None and rent is not None:
            diff = sebco_rent - rent
            pct = (diff / rent * 100.0) if rent else 0
            sign = "+" if diff >= 0 else "−"
            sebco_line = (
                f'<div style="font-size:{TYPE_SCALE["xs"]};'
                f'color:{p["text_tertiary"]};margin-top:{SPACE["1"]};">'
                f'Sebco ${sebco_rent:.2f} '
                f'({sign}{abs(pct):.0f}% vs market)</div>'
            )

        def _cell(label: str, value: str, delta_txt: str, delta_tone: str,
                  trailing: str = "") -> str:
            label_div = (
                f'<div style="font-size:{TYPE_SCALE["xs"]};'
                f'font-weight:500;color:{p["text_secondary"]};'
                f'text-transform:uppercase;letter-spacing:0.04em;'
                f'margin-bottom:{SPACE["1"]};">{label}</div>'
            )
            value_div = (
                f'<div style="font-size:{TYPE_SCALE["2xl"]};'
                f'font-weight:600;color:{p["text_primary"]};'
                f'line-height:1.1;">{html.escape(value)}</div>'
            )
            delta_div = (
                f'<div style="font-size:{TYPE_SCALE["xs"]};'
                f'font-weight:500;color:{_DELTA_COLOR[delta_tone]};'
                f'margin-top:{SPACE["1"]};">'
                f'{html.escape(delta_txt) if delta_txt else "&nbsp;"}</div>'
            )
            return (
                f'<div style="flex:1;">{label_div}{value_div}{delta_div}'
                f'{trailing}</div>'
            )

        body = (
            f'<div style="display:flex;gap:{SPACE["6"]};'
            f'margin-top:{SPACE["4"]};">'
            + _cell("Vacancy", vac_txt, vac_dtxt, vac_tone)
            + _cell("Asking Rent", rent_txt, rent_dtxt, rent_tone,
                    trailing=sebco_line)
            + '</div>'
        )

    quarter_chip = (
        f'<span style="font-size:{TYPE_SCALE["xs"]};'
        f'color:{p["text_tertiary"]};margin-left:{SPACE["2"]};">'
        f'{html.escape(quarter_label)}</span>'
        if quarter_label else ""
    )
    title_div = (
        f'<div style="font-size:{TYPE_SCALE["lg"]};font-weight:600;'
        f'color:{p["text_primary"]};letter-spacing:-0.01em;">'
        f'{html.escape(market)}{quarter_chip}</div>'
    )
    return (
        f'<div style="background:{p["bg"]};border:1px solid {p["border"]};'
        f'border-radius:{RADIUS["md"]};padding:{SPACE["5"]} {SPACE["5"]};'
        f'box-shadow:{SHADOW["card"]};min-height:240px;">'
        + title_div + body + '</div>'
    )


def _render_card(market: str, col) -> None:
    pf = portfolio.get(market, {})
    sebco_rent = pf.get("sebco_asking_rent")

    vac_curr, _ = _best_value(market, ["total_vacancy_rate", "vacancy_rate"])
    vac_prev, _ = _best_value(market, ["total_vacancy_rate", "vacancy_rate"],
                              period_type="prior_quarter")
    rent_curr, _ = _best_value(market, ["asking_rent"])
    rent_prev, _ = _best_value(market, ["asking_rent"],
                               period_type="prior_quarter")

    # Quarter label for the chip — resolved through the same data_source
    # map so a submarket-backed market (Kent Valley, Marysville) still shows
    # its parent report's quarter.
    market_rows = _market_frame(market)
    market_rows = market_rows[market_rows["period_type"] == "current"]
    quarter_label = (market_rows.sort_values("period_date", ascending=False)
                     ["quarter"].iloc[0]
                     if not market_rows.empty else "")

    has_data = vac_curr is not None or rent_curr is not None

    with col:
        st.markdown(
            _card_html(
                market=market,
                quarter_label=quarter_label,
                vac=vac_curr,
                vac_delta=_fmt_delta_pp(vac_curr, vac_prev),
                rent=rent_curr,
                rent_delta=_fmt_delta_dollar(rent_curr, rent_prev),
                sebco_rent=sebco_rent,
                has_data=has_data,
            ),
            unsafe_allow_html=True,
        )
        # Rent sparkline (renders separately — can't embed in HTML above)
        if has_data:
            hist = _historical(market, ["asking_rent"])
            sparkline(hist, height=36, key=f"pulse_spark_{market}")
            st.page_link(
                "app_pages/trends.py",
                label=f"View {market} trends",
            )


# ---------------------------------------------------------------------------
# Add a market
# ---------------------------------------------------------------------------

def _market_submarkets() -> dict[str, list[str]]:
    """Map each uploaded market to its real submarket names (excluding the
    market-wide '' / 'Market Total' rows), for the Add-market data picker."""
    if df.empty:
        return {}
    out: dict[str, list[str]] = {}
    for m in sorted(df["market"].dropna().unique()):
        subs = sorted(
            s for s in df[df["market"] == m]["submarket"].dropna().unique()
            if s and s != "Market Total"
        )
        out[m] = subs
    return out


@st.dialog("Add a market")
def _add_market_dialog(market_subs: dict[str, list[str]]) -> None:
    st.caption(
        "Add a market card to Pulse. Saves to your local portfolio file "
        "(`sebco_portfolio.local.json`)."
    )
    name = st.text_input("Market name", placeholder="e.g. Tacoma")

    st.markdown("**Where does this market's data come from?**")
    none_opt = "— none (no data yet) —"
    parent_opts = [none_opt] + sorted(market_subs.keys())
    parent = st.selectbox(
        "Pull data from report / market", parent_opts,
        help="The uploaded market this card reads its numbers from. Choose "
             "'none' to add a placeholder with no data yet.",
    )
    subs: list[str] = []
    if parent != none_opt:
        subs = st.multiselect(
            "Specific submarket(s)", market_subs.get(parent, []),
            help="Leave empty to use the whole market. Pick one or more "
                 "submarkets (e.g. Southend) if this market is a sub-area "
                 "inside the report — the first match wins.",
        )

    with st.expander("Sebco position (optional)"):
        c1, c2 = st.columns(2)
        buildings = c1.number_input("Buildings", min_value=0, step=1, value=0)
        total_sf = c2.number_input("Total SF", min_value=0, step=1000, value=0)
        rent = c1.number_input("In-place asking rent ($/SF)", min_value=0.0,
                               step=0.01, value=0.0, format="%.2f")
        lease = c2.selectbox("Lease type",
                             ["NNN", "industrial_gross", "modified_gross"])

    if st.button("Add market", type="primary", width="stretch"):
        clean = name.strip()
        current = load_sebco_portfolio()
        if not clean:
            st.error("Market name is required.")
        elif clean in current:
            st.error(f"'{clean}' already exists.")
        else:
            entry: dict = {}
            if buildings:
                entry["building_count"] = int(buildings)
            if total_sf:
                entry["total_sf"] = int(total_sf)
            if rent:
                entry["sebco_asking_rent"] = float(rent)
            entry["lease_type"] = lease
            if parent != none_opt:
                entry["data_source"] = {
                    "market": parent,
                    "submarket_aliases": subs or list(MARKET_WIDE_SUBMARKETS),
                }
            current[clean] = entry
            save_sebco_portfolio(current)
            st.success(f"Added {clean}.")
            st.rerun()


# ---------------------------------------------------------------------------
# Layout — responsive 3-up grid of Sebco markets, in canonical order
# ---------------------------------------------------------------------------

_, add_col = st.columns([4, 1])
with add_col:
    if st.button("➕ Add market", width="stretch", key="pulse_add_market"):
        _add_market_dialog(_market_submarkets())

if df.empty:
    st.warning("Database is empty. Visit Library to upload your first report.")
    st.stop()

markets = ordered_markets(portfolio)
for start in range(0, len(markets), 3):
    cols = st.columns(3, gap="medium")
    for col, market in zip(cols, markets[start:start + 3]):
        _render_card(market, col)
    st.markdown("")  # gap between rows
