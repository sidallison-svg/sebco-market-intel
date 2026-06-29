"""
LLM helper for the Ask page.

Wraps the Anthropic API: resolves an API key (env / Streamlit secrets /
session-provided), builds a compact text digest of the current market data
so Claude answers from real numbers rather than guessing, and streams a
reply. No key is ever written to disk — a key pasted into the Ask page lives
only in st.session_state for that browser session.
"""

from __future__ import annotations

import os

import pandas as pd

from db import get_all_metrics
from utils import load_sebco_portfolio, resolve_data_source

# Default model. Opus 4.8 is the most capable Opus-tier model and a sensible
# default for grounded Q&A; override via the ANTHROPIC_MODEL env var.
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")

_SESSION_KEY = "anthropic_api_key"


def resolve_api_key() -> str | None:
    """Return an Anthropic API key from (in order) the environment, Streamlit
    secrets, or a key the user pasted into the Ask page this session. Returns
    None if none is available."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        import streamlit as st

        if _SESSION_KEY in st.session_state and st.session_state[_SESSION_KEY]:
            return st.session_state[_SESSION_KEY]
        # st.secrets raises if no secrets file exists; guard it.
        try:
            if "ANTHROPIC_API_KEY" in st.secrets:
                return st.secrets["ANTHROPIC_API_KEY"]
        except Exception:
            pass
    except Exception:
        pass
    return None


def _fmt_value(value, unit: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    if unit == "percent":
        return f"{value:.1f}%"
    if unit == "dollar_per_sf":
        return f"${value:.2f}/SF"
    if unit == "sf":
        return f"{value:,.0f} SF"
    return f"{value:g}"


def build_data_digest(max_rows: int = 1500) -> str:
    """Compact, plain-text snapshot of the current market data plus the Sebco
    portfolio, suitable for grounding an LLM answer. Current-period rows only,
    grouped by market, capped at `max_rows` (with a note if truncated)."""
    df = pd.DataFrame(get_all_metrics())
    portfolio = load_sebco_portfolio()

    lines: list[str] = []
    lines.append("# Sebco Market Intel — current data snapshot\n")

    # ---- Sebco portfolio (with where each market's data is sourced) ----
    lines.append("## Sebco portfolio (in-place positions)")
    for market, cfg in portfolio.items():
        rent = cfg.get("sebco_asking_rent")
        rent_txt = f"${rent:.2f}/SF" if rent is not None else "—"
        ds = resolve_data_source(market, portfolio)
        src = (
            f"; market data sourced from {ds['market']} submarket(s) "
            f"{', '.join(ds['submarket_aliases'])}"
            if ds else "; top-level market"
        )
        lines.append(
            f"- {market}: {cfg.get('building_count', '—')} buildings, "
            f"{cfg.get('total_sf', 0):,} SF, in-place asking rent {rent_txt}, "
            f"lease type {cfg.get('lease_type', 'NNN')}{src}"
        )
    lines.append("")

    if df.empty:
        lines.append("No market metrics have been uploaded yet.")
        return "\n".join(lines)

    cur = df[df["period_type"] == "current"].copy()
    if cur.empty:
        cur = df.copy()

    truncated = len(cur) > max_rows
    cur = cur.head(max_rows)

    lines.append("## Market metrics (current period)")
    lines.append(
        "Columns: market | submarket | asset_class | metric | value | "
        "quarter | source"
    )
    for market, g in cur.groupby("market"):
        lines.append(f"\n### {market}")
        for _, r in g.iterrows():
            sub = r["submarket"] or "(market-wide)"
            lines.append(
                f"- {sub} | {r['asset_class']} | {r['metric_type']} | "
                f"{_fmt_value(r['value'], r['unit'])} | {r['quarter']} | "
                f"{r['source']}"
            )

    if truncated:
        lines.append(
            f"\n(Note: showing first {max_rows} rows; some detail omitted.)"
        )
    return "\n".join(lines)


SYSTEM_PROMPT = """You are the analyst assistant inside Sebco Market Intel, a \
commercial real-estate market dashboard used by Sebco's principals.

Answer questions using ONLY the data snapshot provided below. Rules:
- Ground every number in the snapshot. Quote specific values, markets, \
submarkets, and quarters.
- If the snapshot doesn't contain what's needed, say so plainly and suggest \
which report to upload — never invent figures.
- Note that some Sebco markets are submarkets inside a parent report (e.g. \
Kent Valley → Seattle/Southend, Marysville → Seattle/Northend); use the \
sourcing notes in the portfolio section when the user asks about those.
- Be concise and lead with the answer. These are busy, non-technical readers.

DATA SNAPSHOT
=============
{digest}
"""


def make_client(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


def stream_answer(client, history: list[dict], digest: str,
                  model: str = DEFAULT_MODEL):
    """Yield text chunks of Claude's answer. `history` is the full list of
    {role, content} message dicts (user/assistant turns)."""
    system = SYSTEM_PROMPT.format(digest=digest)
    with client.messages.stream(
        model=model,
        max_tokens=4000,
        system=system,
        messages=history,
    ) as stream:
        for text in stream.text_stream:
            yield text
