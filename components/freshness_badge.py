"""
Pill-shaped badge summarizing how fresh / trustworthy a data source is.

    freshness_badge(quarter="1Q 2026", days_ago=3,
                    records=47, confidence=0.92)

The dot-color tone is auto-derived from days_ago + confidence so call
sites don't have to think about thresholds:
  * <= 100 days old AND confidence >= 0.85 -> fresh   (green)
  * <= 200 days old OR  confidence >= 0.70 -> stale   (amber)
  * else                                    -> missing (red)
"""

from __future__ import annotations

import html
from datetime import date, datetime

import streamlit as st


def _days_since(iso_or_date: str | date | None) -> int | None:
    if iso_or_date is None:
        return None
    if isinstance(iso_or_date, date):
        d = iso_or_date
    else:
        try:
            d = datetime.fromisoformat(str(iso_or_date)[:10]).date()
        except ValueError:
            return None
    return (date.today() - d).days


def _tone(days_ago: int | None, confidence: float | None) -> str:
    if days_ago is None and confidence is None:
        return "missing"
    if (days_ago is not None and days_ago <= 100
            and (confidence is None or confidence >= 0.85)):
        return "fresh"
    if (days_ago is not None and days_ago <= 200) or \
       (confidence is not None and confidence >= 0.70):
        return "stale"
    return "missing"


def freshness_badge(quarter: str | None = None,
                    uploaded_at: str | None = None,
                    days_ago: int | None = None,
                    records: int | None = None,
                    confidence: float | None = None) -> None:
    """Render a freshness pill. Either uploaded_at (ISO string) or
    days_ago can supply the age; uploaded_at wins if both are given."""
    age = _days_since(uploaded_at) if uploaded_at else days_ago
    tone = _tone(age, confidence)

    parts: list[str] = []
    if quarter:
        parts.append(html.escape(quarter))
    if age is not None:
        if age == 0:
            parts.append("today")
        elif age == 1:
            parts.append("1 day ago")
        elif age < 30:
            parts.append(f"{age} days ago")
        elif age < 365:
            parts.append(f"{age // 30} mo ago")
        else:
            parts.append(f"{age // 365}y ago")
    if records is not None:
        parts.append(f"{records:,} records")
    if confidence is not None:
        parts.append(f"{int(round(confidence * 100))}% conf")

    text = " · ".join(parts) if parts else "no data"
    st.markdown(
        f'<span class="badge badge--dot badge--{tone}">{text}</span>',
        unsafe_allow_html=True,
    )
