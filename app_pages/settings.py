"""
Settings — edit sebco_portfolio.json.

A single editable table backed by st.data_editor; on Save we write the
whole dict back via utils.save_sebco_portfolio. Adding new rows and
deleting existing ones are both allowed — the JSON file is treated as
authoritative, not utils.SEBCO_PORTFOLIO_ORDER.

Persistence caveat: on Streamlit Cloud the container's filesystem is
ephemeral, so edits made there don't survive a restart. For real
edits, run the app locally and commit the JSON change.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from utils import load_sebco_portfolio, save_sebco_portfolio


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("Settings")
st.markdown(
    '<p class="page-lede">Sebco portfolio configuration — building counts, '
    'in-place rents, lease type per market.</p>',
    unsafe_allow_html=True,
)

st.caption(
    "Edits are written to `sebco_portfolio.json`. On Streamlit Cloud the "
    "filesystem is ephemeral, so cloud edits don't persist past a restart "
    "— do real edits locally and commit the file."
)


# ---------------------------------------------------------------------------
# Load + render
# ---------------------------------------------------------------------------

raw = load_sebco_portfolio()

# Build a DataFrame the data_editor can chew on.
rows = []
for market, cfg in raw.items():
    rows.append({
        "market":            market,
        "building_count":    cfg.get("building_count"),
        "total_sf":          cfg.get("total_sf"),
        "sebco_asking_rent": cfg.get("sebco_asking_rent"),
        "lease_type":        cfg.get("lease_type") or "NNN",
    })

# Stable order, even when JSON ordering differs from canonical
df = pd.DataFrame(rows)
if df.empty:
    df = pd.DataFrame(columns=["market", "building_count", "total_sf",
                               "sebco_asking_rent", "lease_type"])


edited = st.data_editor(
    df,
    num_rows="dynamic",
    width="stretch",
    hide_index=True,
    key="settings_editor",
    column_config={
        "market": st.column_config.TextColumn(
            "Market", required=True,
            help="Market name as it appears in the parsed PDFs "
                 "(case-sensitive).",
        ),
        "building_count": st.column_config.NumberColumn(
            "Buildings", min_value=0, step=1, format="%d",
        ),
        "total_sf": st.column_config.NumberColumn(
            "Total SF", min_value=0, step=1000, format="%d",
        ),
        "sebco_asking_rent": st.column_config.NumberColumn(
            "Sebco Rent",
            min_value=0.0, step=0.01, format="$%.2f",
            help="Sebco's in-place asking rent. Overlaid on Trends and "
                 "shown alongside market rent on Pulse.",
        ),
        "lease_type": st.column_config.SelectboxColumn(
            "Lease",
            options=["NNN", "industrial_gross", "modified_gross"],
            required=True,
        ),
    },
)


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

st.markdown("")

if st.button("Save changes", type="primary", key="settings_save"):
    # Validate: market name required + unique
    cleaned = edited.dropna(subset=["market"]).copy()
    cleaned["market"] = cleaned["market"].astype(str).str.strip()
    cleaned = cleaned[cleaned["market"] != ""]
    if cleaned["market"].duplicated().any():
        dups = cleaned[cleaned["market"].duplicated()]["market"].tolist()
        st.error(f"Duplicate market name(s): {', '.join(dups)}. "
                 "Each market must appear once.")
    else:
        out: dict[str, dict] = {}
        for r in cleaned.to_dict(orient="records"):
            out[r["market"]] = {
                "building_count":    int(r["building_count"]) if pd.notna(r["building_count"]) else None,
                "total_sf":          int(r["total_sf"]) if pd.notna(r["total_sf"]) else None,
                "sebco_asking_rent": float(r["sebco_asking_rent"]) if pd.notna(r["sebco_asking_rent"]) else None,
                "lease_type":        r["lease_type"] or "NNN",
            }
            # Drop keys whose value is None so the JSON stays tidy.
            out[r["market"]] = {k: v for k, v in out[r["market"]].items()
                                if v is not None}

        save_sebco_portfolio(out)
        st.success(f"Saved {len(out)} markets to sebco_portfolio.json.")
        st.rerun()
