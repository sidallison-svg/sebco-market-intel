"""
Ask — natural-language Q&A over the market data, powered by Claude.

The user types a question; we hand Claude a compact snapshot of the current
market data (see llm.build_data_digest) and stream back a grounded answer.
Requires an Anthropic API key via the ANTHROPIC_API_KEY env var, Streamlit
secrets, or a key pasted below (session-only, never written to disk).
"""

from __future__ import annotations

import streamlit as st

import anthropic

from llm import (
    DEFAULT_MODEL,
    build_data_digest,
    make_client,
    resolve_api_key,
    stream_answer,
)

st.title("Ask")
st.markdown(
    '<p class="page-lede">Ask a question about your market data in plain '
    'English.</p>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# API key
# ---------------------------------------------------------------------------

api_key = resolve_api_key()

if not api_key:
    st.info(
        "This page uses Claude to answer questions about your uploaded data. "
        "Add an Anthropic API key to enable it.\n\n"
        "**Best:** set the `ANTHROPIC_API_KEY` environment variable (or add it "
        "to `.streamlit/secrets.toml`) so you never have to paste it again. "
        "Or paste a key below to use it just for this browser session."
    )
    entered = st.text_input(
        "Anthropic API key", type="password",
        placeholder="sk-ant-...",
        help="Stored only in this session's memory — never written to disk.",
    )
    if entered:
        st.session_state["anthropic_api_key"] = entered.strip()
        st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------

if "ask_history" not in st.session_state:
    st.session_state["ask_history"] = []

col_a, col_b = st.columns([4, 1])
with col_b:
    if st.button("Clear chat", width="stretch"):
        st.session_state["ask_history"] = []
        st.rerun()

# Replay prior turns.
for msg in st.session_state["ask_history"]:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("e.g. Which Sebco market has the highest vacancy?")

if prompt:
    st.session_state["ask_history"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            client = make_client(api_key)
            digest = build_data_digest()
            answer = st.write_stream(
                stream_answer(
                    client, st.session_state["ask_history"], digest,
                    model=DEFAULT_MODEL,
                )
            )
            st.session_state["ask_history"].append(
                {"role": "assistant", "content": answer}
            )
        except anthropic.AuthenticationError:
            st.session_state.pop("anthropic_api_key", None)
            st.error(
                "That API key was rejected. Clear it and try another — "
                "reload the page to re-enter a key."
            )
            # Drop the unanswered user turn so a retry starts clean.
            st.session_state["ask_history"].pop()
        except anthropic.RateLimitError:
            st.error("Rate limited by the API. Wait a moment and try again.")
            st.session_state["ask_history"].pop()
        except anthropic.APIError as e:
            st.error(f"Claude API error: {getattr(e, 'message', str(e))}")
            st.session_state["ask_history"].pop()
