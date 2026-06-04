"""Pulse — landing page. Stub; fleshed out in a later commit."""

import streamlit as st

st.title("Pulse")
st.markdown(
    '<p class="page-lede">At-a-glance state of all six Sebco markets.</p>',
    unsafe_allow_html=True,
)
st.info("This page lands in commit 6. The nav and layout are live "
        "so you can preview the v2 frame end-to-end.")
