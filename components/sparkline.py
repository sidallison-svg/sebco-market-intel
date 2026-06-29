"""
Thin Plotly sparkline — no axes, no chrome, just a line.

    sparkline([1.42, 1.45, 1.46, 1.46])
    sparkline(values, color="#047857", height=44, key="rent_sd")

Designed to sit inside a KPI card (height ~40-60px). Pass `key` when
calling from a loop; Streamlit needs unique keys for plotly charts on
the same page.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from theme import PALETTE


def sparkline(values: list[float | None],
              color: str | None = None,
              height: int = 44,
              key: str | None = None) -> None:
    """Render a tiny line. None values create gaps; an all-None series
    renders nothing (caller decides whether to show a placeholder).
    """
    if not values or all(v is None for v in values):
        # Render a neutral skeleton line so the card height stays stable.
        st.markdown(
            f'<div style="height:{height}px;background:'
            f'{PALETTE["surface"]};border-radius:4px;"></div>',
            unsafe_allow_html=True,
        )
        return

    fig = go.Figure(
        data=go.Scatter(
            x=list(range(len(values))),
            y=values,
            mode="lines",
            line={"color": color or PALETTE["accent"], "width": 2},
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        height=height,
        xaxis={"visible": False},
        yaxis={"visible": False},
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    # staticPlot disables all interactivity (drag/pan/zoom/hover) so the
    # sparkline renders as a plain, non-movable line instead of a draggable
    # chart.
    st.plotly_chart(fig, width="stretch",
                    config={"displayModeBar": False, "staticPlot": True},
                    key=key)
