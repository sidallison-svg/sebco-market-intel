"""
Sebco Market Intel — entry point.

Run with:
    streamlit run app.py
    # or
    python main.py

How the navigation works:
  - `st.navigation` is called with position='hidden', which registers
    each app_pages/*.py as a routable page but skips the default
    sidebar nav widget.
  - We render our own top tab bar using `st.page_link` for each page;
    CSS in theme.py restyles those links into the tab-bar look.
  - The selected page's script body runs in place after pg.run() —
    so anything app.py does before that (set_page_config, theme,
    topnav) renders on every page.
"""

import streamlit as st

import theme

# set_page_config must be the first Streamlit call on every page; calling
# it here covers all pages since they run inside this same script.
st.set_page_config(
    page_title="Sebco Market Intel",
    page_icon="◐",
    layout="wide",
    initial_sidebar_state="collapsed",
)

theme.apply_theme()


_PAGES = [
    st.Page("app_pages/pulse.py",    title="Pulse",    default=True),
    st.Page("app_pages/compare.py",  title="Compare"),
    st.Page("app_pages/trends.py",   title="Trends"),
    st.Page("app_pages/ask.py",      title="Ask"),
    st.Page("app_pages/library.py",  title="Library"),
    st.Page("app_pages/settings.py", title="Settings"),
]
pg = st.navigation(_PAGES, position="hidden")


# ---------------------------------------------------------------------------
# Top tab bar
# ---------------------------------------------------------------------------

def _render_topnav() -> None:
    """Render the custom top tab bar.

    Each tab is an st.page_link, which Streamlit renders as an anchor with
    data-testid='stPageLink-NavLink' (or 'stPageLink' on older versions);
    theme.py's CSS targets those to give the tab appearance. The brand
    label sits flush-left, tabs to its right, fixed pixel widths so
    Streamlit's column auto-sizing doesn't space them out awkwardly.
    """
    st.markdown('<div class="topnav-brand">Sebco Market Intel</div>',
                unsafe_allow_html=True)
    nav_cols = st.columns([1, 1, 1, 1, 1, 1, 5])
    for col, page in zip(nav_cols[:6], _PAGES):
        with col:
            st.page_link(page, label=page.title)
    st.markdown('<hr class="topnav-rule" />', unsafe_allow_html=True)


_render_topnav()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

pg.run()
