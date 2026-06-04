"""
Reusable UI primitives for the v2 dashboard.

Each component renders directly to Streamlit (calls st.markdown /
st.plotly_chart internally) so call sites stay one-liners. Pure
presentation — no data fetching, no business logic.
"""

from .freshness_badge import freshness_badge
from .kpi_card import kpi_card
from .sparkline import sparkline

__all__ = ["kpi_card", "sparkline", "freshness_badge"]
