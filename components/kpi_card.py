"""
Big-number KPI card.

    kpi_card("Total Vacancy", value="6.5%", delta="+0.3pp",
             delta_tone="negative", sub="Q1 2026")

A card has:
  - label      (small, secondary text)
  - value      (large primary number)
  - delta      (optional small colored text)
  - delta_tone ("positive" | "negative" | "neutral"; controls color)
  - sub        (optional small caption beneath the delta)
"""

from __future__ import annotations

import html

import streamlit as st


def kpi_card(label: str,
             value: str,
             delta: str | None = None,
             delta_tone: str = "neutral",
             sub: str | None = None) -> None:
    """Render a single KPI card. Pass already-formatted strings — the
    component doesn't format numbers; data fetching code owns that."""
    if delta_tone not in {"positive", "negative", "neutral"}:
        delta_tone = "neutral"

    parts = [
        f'<div class="kpi-card__label">{html.escape(label)}</div>',
        f'<div class="kpi-card__value">{html.escape(value)}</div>',
    ]
    if delta:
        parts.append(
            f'<div class="kpi-card__delta kpi-card__delta--{delta_tone}">'
            f'{html.escape(delta)}</div>'
        )
    if sub:
        parts.append(f'<div class="kpi-card__sub">{html.escape(sub)}</div>')

    st.markdown(
        f'<div class="kpi-card">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )
