"""
Pulse — landing page. At-a-glance state of all six Sebco markets.

3-column × 2-row grid of cards. Each card shows the two metrics
principals look at most (vacancy + asking rent), a QoQ arrow if the
source PDF carried a prior_quarter row, a tiny sparkline of rent over
the historical points the DB has (current + prior_quarter + prior_year
from Kidder breakdowns; sparser for CBRE/Voit/JLL which are single-
period), and a link into the Trends page for that market.

Markets without any uploaded data render a placeholder card with a
direct nudge to the Library. The 6 Sebco markets are pinned in
utils.SEBCO_PORTFOLIO_ORDER so the grid layout stays stable even when
some are empty.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from components import sparkline
from db import get_all_metrics
from theme import PALETTE, RADIUS, SHADOW, SPACE, TYPE_SCALE
from utils import SEBCO_PORTFOLIO_ORDER, load_sebco_portfolio


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


def _best_value(market: str, mts: list[str],
                period_type: str = "current") -> tuple[float | None, str]:
    """Highest-confidence, most-recent value for any of the candidate
    metric_types for this market (industrial, market-wide submarket).

    Returns (value, unit) or (None, '')."""
    if df.empty:
        return None, ""
    sub = df[
        (df["market"] == market)
        & (df["asset_class"] == "industrial")
        & (df["submarket"] == "")
        & (df["period_type"] == period_type)
    ]
    if sub.empty:
        # JLL stores LA/Seattle/etc. as asset_class='overall' for Format B
        sub = df[
            (df["market"] == market)
            & (df["asset_class"].isin(["overall", "industrial"]))
            & (df["submarket"].isin(["", "Market Total"]))
            & (df["period_type"] == period_type)
        ]
    for mt in mts:
        match = sub[sub["metric_type"] == mt]
        if match.empty:
            continue
        match = match.sort_values(["confidence", "period_date"],
                                  ascending=[False, False])
        row = match.iloc[0]
        return (float(row["value"]) if pd.notna(row["value"]) else None,
                row["unit"])
    return None, ""


def _historical(market: str, mts: list[str]) -> list[float | None]:
    """Up to four historical points (prior_year, prior_quarter, current)
    for the sparkline. Returns chronological list of values."""
    if df.empty:
        return []
    sub = df[
        (df["market"] == market)
        & (df["asset_class"].isin(["overall", "industrial"]))
        & (df["submarket"].isin(["", "Market Total"]))
        & (df["metric_type"].isin(mts))
    ]
    if sub.empty:
        return []
    sub = sub.sort_values("period_date")
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

    # Quarter label for the chip
    if not df.empty:
        market_rows = df[(df["market"] == market)
                         & (df["period_type"] == "current")]
        quarter_label = (market_rows.sort_values("period_date",
                                                 ascending=False)
                         ["quarter"].iloc[0]
                         if not market_rows.empty else "")
    else:
        quarter_label = ""

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
# Layout — 3 columns × 2 rows of Sebco markets, in canonical order
# ---------------------------------------------------------------------------

if df.empty:
    st.warning("Database is empty. Visit Library to upload your first report.")
    st.stop()

# Pair up six markets into two rows of three.
for row_start in (0, 3):
    cols = st.columns(3, gap="medium")
    for i, col in enumerate(cols):
        if row_start + i >= len(SEBCO_PORTFOLIO_ORDER):
            continue
        _render_card(SEBCO_PORTFOLIO_ORDER[row_start + i], col)
    st.markdown("")  # gap between rows
