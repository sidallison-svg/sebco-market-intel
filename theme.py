"""
Visual design foundation for the v2 Sebco Market Intel dashboard.

Restrained / institutional palette — mostly white, one navy accent, no
playful flourishes. Optimized for weekly skim use by non-technical
principals: large readable numbers, generous whitespace, no chart
chrome that doesn't carry meaning.

Public API:
    apply_theme()           — call once per page (at the top, after
                              st.set_page_config) to inject CSS + set
                              the Plotly default template.

    PALETTE, TYPE_SCALE,    — exported constants for component code
    SPACE, RADIUS              that needs them inline.

    plotly_template()       — returns the dict Plotly template; useful
                              when you want to apply it to a single fig
                              without changing pio.templates.default.
"""

from __future__ import annotations

import plotly.io as pio
import streamlit as st


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

PALETTE = {
    "bg":           "#FFFFFF",
    "surface":      "#F9FAFB",   # card bg / subtle fill
    "border":       "#E2E8F0",   # dividers, card borders
    "border_strong": "#CBD5E1",  # focused / emphasized borders

    "text_primary":   "#0F172A",  # near-black
    "text_secondary": "#475569",  # labels, captions
    "text_tertiary":  "#94A3B8",  # tertiary metadata

    "accent":         "#1E3A8A",  # navy — the one accent
    "accent_soft":    "#DBEAFE",  # tinted bg for accent surfaces

    "positive":       "#047857",  # gains (rent up, absorption positive)
    "negative":       "#B91C1C",  # losses (vacancy up, rent down)
    "warning":        "#B45309",  # rejected records, low confidence
    "neutral":        "#64748B",  # no-change / flat
}

TYPE_SCALE = {
    "xs":   "11px",
    "sm":   "13px",
    "base": "14px",
    "lg":   "16px",
    "xl":   "20px",
    "2xl":  "24px",
    "3xl":  "32px",   # KPI numbers
    "4xl":  "40px",   # page titles
}

SPACE = {
    "1":  "4px",
    "2":  "8px",
    "3":  "12px",
    "4":  "16px",
    "5":  "20px",
    "6":  "24px",
    "8":  "32px",
    "10": "40px",
    "12": "48px",
    "16": "64px",
}

RADIUS = {
    "sm": "4px",
    "md": "8px",
    "lg": "12px",
}

SHADOW = {
    "card": "0 1px 2px 0 rgba(15, 23, 42, 0.05)",
    "hover": "0 4px 12px -2px rgba(15, 23, 42, 0.10)",
}


# ---------------------------------------------------------------------------
# CSS injection
# ---------------------------------------------------------------------------

def _base_css() -> str:
    p = PALETTE
    return f"""
    /* Inter — clean institutional sans, falls back to system if blocked */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"], .stApp {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont,
                     'Segoe UI', sans-serif;
        color: {p['text_primary']};
        background: {p['bg']};
    }}

    /* Hide Streamlit's default chrome we're replacing */
    #MainMenu       {{ visibility: hidden; }}
    footer          {{ visibility: hidden; }}
    header          {{ visibility: hidden; }}

    /* Hide the auto-rendered nav (we render our own top tab bar in app.py) */
    [data-testid="stSidebarNav"] {{ display: none !important; }}

    /* Tighten the default block container — Streamlit's default padding
       is generous; we want the content closer to the top edge. */
    .block-container {{
        padding-top: 2rem;
        padding-bottom: 4rem;
        max-width: 1280px;
    }}

    /* Page title — large, restrained */
    h1 {{
        font-size: {TYPE_SCALE['4xl']} !important;
        font-weight: 600 !important;
        letter-spacing: -0.02em;
        color: {p['text_primary']};
        margin-bottom: {SPACE['2']} !important;
    }}
    h2 {{
        font-size: {TYPE_SCALE['2xl']} !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em;
        margin-top: {SPACE['8']} !important;
        margin-bottom: {SPACE['3']} !important;
    }}
    h3 {{
        font-size: {TYPE_SCALE['lg']} !important;
        font-weight: 600 !important;
        margin-top: {SPACE['6']} !important;
    }}

    /* Section subtitle / page lede */
    .page-lede {{
        color: {p['text_secondary']};
        font-size: {TYPE_SCALE['base']};
        margin-bottom: {SPACE['8']};
    }}

    /* Component: KPI card */
    .kpi-card {{
        background: {p['bg']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS['md']};
        padding: {SPACE['5']} {SPACE['5']};
        box-shadow: {SHADOW['card']};
    }}
    .kpi-card__label {{
        font-size: {TYPE_SCALE['sm']};
        font-weight: 500;
        color: {p['text_secondary']};
        text-transform: none;
        margin-bottom: {SPACE['2']};
    }}
    .kpi-card__value {{
        font-size: {TYPE_SCALE['3xl']};
        font-weight: 600;
        line-height: 1.1;
        color: {p['text_primary']};
        letter-spacing: -0.02em;
        margin-bottom: {SPACE['1']};
    }}
    .kpi-card__delta {{
        font-size: {TYPE_SCALE['sm']};
        font-weight: 500;
        line-height: 1.4;
    }}
    .kpi-card__delta--positive {{ color: {p['positive']}; }}
    .kpi-card__delta--negative {{ color: {p['negative']}; }}
    .kpi-card__delta--neutral  {{ color: {p['neutral']}; }}
    .kpi-card__sub {{
        font-size: {TYPE_SCALE['xs']};
        color: {p['text_tertiary']};
        margin-top: {SPACE['1']};
    }}

    /* Component: Freshness badge (a small pill summarizing a data source) */
    .badge {{
        display: inline-flex;
        align-items: center;
        gap: {SPACE['2']};
        font-size: {TYPE_SCALE['xs']};
        font-weight: 500;
        color: {p['text_secondary']};
        background: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: 999px;
        padding: 2px 10px;
        line-height: 1.6;
    }}
    .badge--dot::before {{
        content: '';
        display: inline-block;
        width: 6px; height: 6px;
        border-radius: 50%;
        background: {p['neutral']};
    }}
    .badge--fresh::before  {{ background: {p['positive']}; }}
    .badge--stale::before  {{ background: {p['warning']}; }}
    .badge--missing::before {{ background: {p['negative']}; }}

    /* Top tab bar: brand text + st.page_link components + horizontal rule.
       Streamlit renders each st.* call inside its own element-container,
       so we can't wrap them in a CSS-meaningful parent div from Python —
       instead each piece is styled by class/data-testid.
    */
    .topnav-brand {{
        font-weight: 600;
        font-size: {TYPE_SCALE['lg']};
        color: {p['text_primary']};
        letter-spacing: -0.01em;
        margin-bottom: {SPACE['1']};
    }}
    .topnav-rule {{
        border: none;
        border-bottom: 1px solid {p['border']};
        margin: {SPACE['2']} 0 {SPACE['6']} 0;
    }}

    /* st.page_link rendered links — restyled to look like tabs.
       Streamlit uses data-testid='stPageLink-NavLink' in 1.36+. */
    [data-testid="stPageLink-NavLink"],
    a[data-testid="stPageLink-NavLink"] {{
        display: inline-flex;
        align-items: center;
        padding: {SPACE['2']} {SPACE['4']};
        border-radius: {RADIUS['sm']};
        font-size: {TYPE_SCALE['sm']};
        font-weight: 500;
        color: {p['text_secondary']} !important;
        text-decoration: none !important;
        background: transparent;
        border: 1px solid transparent;
        transition: background 80ms ease, color 80ms ease;
        width: fit-content;
    }}
    [data-testid="stPageLink-NavLink"]:hover {{
        background: {p['surface']};
        color: {p['text_primary']} !important;
    }}
    /* Streamlit marks the active page link with aria-current="page". */
    [data-testid="stPageLink-NavLink"][aria-current="page"] {{
        color: {p['accent']} !important;
        background: {p['accent_soft']};
    }}
    /* Hide the small icon the page_link wants to draw next to the label. */
    [data-testid="stPageLink-NavLink"] svg {{
        display: none;
    }}

    /* Tone down Streamlit's built-in metric so we don't accidentally
       render two different visual styles for the same idea. We use our
       own .kpi-card; the only place st.metric should appear is small
       inline contexts where a card is overkill. */
    [data-testid="stMetricValue"] {{
        font-family: 'Inter', sans-serif;
        font-weight: 600;
        color: {p['text_primary']};
    }}
    [data-testid="stMetricLabel"] {{
        color: {p['text_secondary']};
    }}
    """


def apply_theme() -> None:
    """Inject CSS + register the Plotly template. Call once per page,
    immediately after `st.set_page_config(...)`.

    Idempotent within a session — Streamlit re-renders st.markdown
    blocks on every script run, but the browser dedup-s identical
    <style> contents."""
    st.markdown(f"<style>{_base_css()}</style>", unsafe_allow_html=True)
    pio.templates.default = "sebco"


# ---------------------------------------------------------------------------
# Plotly template
# ---------------------------------------------------------------------------

def plotly_template() -> dict:
    p = PALETTE
    return {
        "layout": {
            "font": {
                "family": "Inter, -apple-system, sans-serif",
                "size": 12,
                "color": p["text_primary"],
            },
            "paper_bgcolor": p["bg"],
            "plot_bgcolor":  p["bg"],
            "colorway":      [p["accent"], p["neutral"], p["positive"],
                              p["warning"], p["negative"]],
            "xaxis": {
                "showgrid": False,
                "showline": True,
                "linecolor": p["border"],
                "ticks": "outside",
                "tickcolor": p["border"],
                "tickfont": {"color": p["text_secondary"], "size": 11},
                "title": {"font": {"color": p["text_secondary"],
                                   "size": 12}},
            },
            "yaxis": {
                "showgrid": True,
                "gridcolor": p["border"],
                "gridwidth": 1,
                "showline": False,
                "tickfont": {"color": p["text_secondary"], "size": 11},
                "title": {"font": {"color": p["text_secondary"],
                                   "size": 12}},
                "zeroline": False,
            },
            "margin": {"l": 48, "r": 16, "t": 32, "b": 40},
            "hovermode": "x unified",
            "hoverlabel": {
                "bgcolor": p["bg"],
                "bordercolor": p["border"],
                "font": {"family": "Inter, sans-serif",
                         "color": p["text_primary"]},
            },
            "legend": {
                "bgcolor": p["bg"],
                "bordercolor": p["border"],
                "borderwidth": 0,
                "font": {"size": 11, "color": p["text_secondary"]},
            },
        }
    }


pio.templates["sebco"] = plotly_template()
