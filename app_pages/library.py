"""
Library — every report ingested, its freshness, the records it produced.

Merges the v1 Upload + Uploads + Raw Data + Rejected pages into one
page so the principals don't have to learn three places to find the
same data.

Layout:
    1. Upload area (expander, closed by default)
    2. Either the source list OR a drilled-in detail view for one source
       (toggled by st.session_state['library_drill'])
    3. Cross-source rejected records (expander, only if any exist)

The Upload flow keeps the v1 two-layer dup detection:
    Layer 1 — exact-bytes (file hash) match against active uploaded_files
    Layer 2 — same report identity (market+asset+quarter+source) match
"""

from __future__ import annotations

import getpass
import hashlib
import os
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st

from components import freshness_badge
from db import (
    STATUS_ACTIVE, STATUS_REJECTED,
    clear_orphan_upload, count_metrics_for_file, delete_by_source,
    delete_rejected_record, delete_uploaded_file_row, find_active_report,
    get_file_by_hash, get_metrics_for_source, get_rejected_records,
    get_upload_history, record_uploaded_file, supersede_file,
    update_metric, upsert_metrics,
)
from pdf_parser import get_warnings, parse_pdf
from utils import format_display_name


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("Library")
st.markdown(
    '<p class="page-lede">Every report ingested, its freshness, and the '
    'records it produced.</p>',
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y · %H:%M")
    except ValueError:
        return iso[:19]


def _report_identity(records: list[dict]) -> dict:
    """First parsed record's identity — deterministic per file (parsers
    run their strategies in a fixed order)."""
    r0 = records[0]
    return {
        "market":      r0.get("market"),
        "asset_class": r0.get("asset_class"),
        "report_date": r0.get("report_date"),
        "quarter":     r0.get("quarter"),
        "source":      r0.get("source"),
    }


# ---------------------------------------------------------------------------
# Upload section
# ---------------------------------------------------------------------------

def _render_upload_section() -> None:
    """File uploader + the two-layer dup-detection save flow.

    Parse is cached in st.session_state keyed by file hash so the Save
    button (which re-runs the script) doesn't re-parse the PDF.
    """
    with st.expander("Upload a report", expanded=False):
        st.markdown(
            "PDF reports from Kidder Mathews, CBRE, Voit, or JLL. "
            "Identical files are blocked; a different file covering the "
            "same market + quarter prompts a Replace / Cancel."
        )
        uploaded = st.file_uploader(
            "Drop one or more PDFs",
            type=["pdf"],
            accept_multiple_files=True,
            key="lib_uploader",
        )
        if not uploaded:
            return
        for uf in uploaded:
            _process_single_upload(uf)


def _process_single_upload(uf) -> None:
    file_bytes = bytes(uf.getbuffer())
    file_hash = _sha256(file_bytes)
    st.markdown(f"### {uf.name}")

    # Layer 1: exact-bytes dup
    existing = get_file_by_hash(file_hash)
    if existing:
        if count_metrics_for_file(existing["id"]) > 0:
            st.error(
                f"Already uploaded {_fmt_dt(existing.get('uploaded_at'))} "
                f"as `{existing.get('original_filename')}`. No changes."
            )
            return
        # Orphaned (rejected upload, or active row whose metrics were wiped):
        # auto-clear so we don't trap the user.
        clear_orphan_upload(existing["id"])
        st.session_state.pop(f"_lib_parse_{file_hash}", None)
        st.info("Re-processing a previously uploaded file with no current data.")

    # Parse (cached so Save click doesn't re-parse)
    cache_key = f"_lib_parse_{file_hash}"
    if cache_key not in st.session_state:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            with st.spinner("Parsing..."):
                records = parse_pdf(tmp_path)
                warnings = get_warnings()
                for r in records:
                    r["source"] = uf.name
        finally:
            os.unlink(tmp_path)
        st.session_state[cache_key] = {"records": records, "warnings": warnings}

    cached = st.session_state[cache_key]
    records = cached["records"]
    for w in cached["warnings"]:
        st.warning(w)

    meta_base = {
        "file_hash": file_hash,
        "original_filename": uf.name,
        "display_name": format_display_name(uf.name),
        "file_size_bytes": len(file_bytes),
    }

    # Zero records → record as rejected so the user doesn't keep retrying
    if not records:
        record_uploaded_file({
            **meta_base, "market": None, "asset_class": None,
            "report_date": None, "quarter": None, "record_count": 0,
            "parser_strategy": None, "status": STATUS_REJECTED,
        })
        st.session_state.pop(cache_key, None)
        st.error(
            "Could not extract any data — this layout isn't supported "
            "yet. Marked as rejected so it won't show up as pending."
        )
        return

    identity = _report_identity(records)
    strategies = ",".join(sorted({
        r.get("parser_strategy") for r in records if r.get("parser_strategy")
    }))

    # Preview
    st.markdown(
        f"**{identity['market']}** · "
        f"**{(identity['asset_class'] or '').title()}** · "
        f"**{identity['quarter']}** — {len(records)} records "
        f"({sum(1 for r in records if r.get('period_type') == 'current')} "
        f"current-period)"
    )

    # Layer 2: same identity, different file
    conflict = find_active_report(
        identity["market"], identity["asset_class"],
        identity["report_date"], identity["quarter"], identity["source"],
    )

    if conflict:
        st.warning(
            f"A `{format_display_name(identity['source'])}` report for "
            f"{identity['market']} {(identity['asset_class'] or '').title()} "
            f"{identity['quarter']} is already loaded "
            f"({conflict.get('record_count')} records, uploaded "
            f"{_fmt_dt(conflict.get('uploaded_at'))})."
        )
        c1, c2 = st.columns([1, 4])
        with c1:
            if st.button("Replace", type="primary",
                         key=f"lib_replace_{file_hash}"):
                deleted = supersede_file(conflict["id"])
                new_id = record_uploaded_file({
                    **meta_base, **identity,
                    "record_count": len(records),
                    "parser_strategy": strategies,
                    "status": STATUS_ACTIVE,
                })
                res = upsert_metrics(records, source_file_id=new_id)
                st.session_state.pop(cache_key, None)
                st.success(
                    f"Replaced. Removed {deleted} old rows, "
                    f"inserted {res['inserted']}, updated {res['updated']}."
                )
                st.rerun()
        with c2:
            if st.button("Cancel", key=f"lib_cancel_{file_hash}"):
                st.session_state.pop(cache_key, None)
                st.rerun()
        return

    # Normal path: no conflict
    if st.button("Save to database", type="primary",
                 key=f"lib_save_{file_hash}"):
        new_id = record_uploaded_file({
            **meta_base, **identity,
            "record_count": len(records),
            "parser_strategy": strategies,
            "status": STATUS_ACTIVE,
        })
        res = upsert_metrics(records, source_file_id=new_id)
        st.session_state.pop(cache_key, None)
        if res["rejected"]:
            st.warning(
                f"Saved {res['inserted']} new + {res['updated']} updated "
                f"from {uf.name}; {res['rejected']} rejected (see below)."
            )
        else:
            st.success(
                f"Saved {res['inserted']} new + {res['updated']} updated "
                f"from {uf.name}."
            )
        st.rerun()


# ---------------------------------------------------------------------------
# Source list
# ---------------------------------------------------------------------------

def _render_source_list() -> None:
    """Custom-rendered table: one row per uploaded_files entry, with a
    freshness badge and per-row Drill / Delete actions. Uses st.columns
    instead of st.dataframe so the badge + buttons render inline."""
    history = get_upload_history()
    if not history:
        st.info("No reports uploaded yet. Use the upload area above.")
        return

    # Optional filters across the list
    fcol1, fcol2, _ = st.columns([2, 2, 6])
    with fcol1:
        markets = sorted({h.get("market") for h in history if h.get("market")})
        mkt_filter = st.selectbox("Market", ["All"] + markets,
                                  key="lib_filter_market",
                                  label_visibility="collapsed")
    with fcol2:
        statuses = sorted({h.get("status") for h in history if h.get("status")})
        status_filter = st.selectbox("Status", ["All"] + statuses,
                                     key="lib_filter_status",
                                     label_visibility="collapsed")

    filtered = [
        h for h in history
        if (mkt_filter == "All" or h.get("market") == mkt_filter)
        and (status_filter == "All" or h.get("status") == status_filter)
    ]
    st.markdown(f"**{len(filtered)} of {len(history)} sources**")
    st.markdown("")  # tiny gap

    # Table header
    h1, h2, h3, h4, h5 = st.columns([4, 3, 1, 1, 1])
    for col, label in zip([h1, h2, h3, h4, h5],
                          ["Report", "Freshness", "Records", "", ""]):
        col.markdown(f"<span style='font-size:11px;font-weight:600;"
                     f"color:#475569;text-transform:uppercase;letter-spacing:"
                     f"0.04em;'>{label}</span>", unsafe_allow_html=True)

    for h in filtered:
        c1, c2, c3, c4, c5 = st.columns([4, 3, 1, 1, 1])
        with c1:
            display = (h.get("display_name")
                       or format_display_name(h.get("original_filename") or ""))
            st.markdown(
                f"**{display}**<br>"
                f"<span style='color:#94A3B8;font-size:12px;'>"
                f"{h.get('original_filename') or ''}</span>",
                unsafe_allow_html=True,
            )
        with c2:
            # Avg confidence comes from the metrics summary, not uploaded_files;
            # for the list view we just show freshness + record count.
            freshness_badge(
                quarter=h.get("quarter"),
                uploaded_at=h.get("uploaded_at"),
                records=h.get("record_count"),
            )
            if h.get("status") and h["status"] != STATUS_ACTIVE:
                st.caption(f"status: {h['status']}")
        with c3:
            st.markdown(f"{h.get('record_count') or 0:,}")
        with c4:
            if st.button("View", key=f"lib_view_{h['id']}",
                         width="stretch"):
                st.session_state["library_drill"] = h["original_filename"]
                st.rerun()
        with c5:
            if st.button("Delete", key=f"lib_del_{h['id']}",
                         width="stretch"):
                st.session_state[f"_confirm_del_{h['id']}"] = True
        # Inline confirm — keeps the user on the page instead of a modal
        if st.session_state.get(f"_confirm_del_{h['id']}"):
            cc1, cc2, _ = st.columns([1, 1, 6])
            with cc1:
                if st.button("Confirm delete", type="primary",
                             key=f"lib_del_yes_{h['id']}"):
                    delete_uploaded_file_row(h["id"])
                    if h.get("original_filename"):
                        delete_by_source(h["original_filename"])
                    st.session_state.pop(f"_confirm_del_{h['id']}", None)
                    st.rerun()
            with cc2:
                if st.button("Cancel", key=f"lib_del_no_{h['id']}"):
                    st.session_state.pop(f"_confirm_del_{h['id']}", None)
                    st.rerun()


# ---------------------------------------------------------------------------
# Drilled-in detail (records for one source)
# ---------------------------------------------------------------------------

def _render_source_detail(source: str) -> None:
    rows = get_metrics_for_source(source)
    summary = next(
        (h for h in get_upload_history()
         if h.get("original_filename") == source),
        None,
    )

    head_c1, head_c2 = st.columns([5, 1])
    with head_c1:
        if st.button("← Back to all sources", key="lib_back"):
            st.session_state.pop("library_drill", None)
            st.rerun()
        st.markdown(f"## {format_display_name(source)}")
        if summary:
            freshness_badge(
                quarter=summary.get("quarter"),
                uploaded_at=summary.get("uploaded_at"),
                records=summary.get("record_count"),
            )
    with head_c2:
        if st.button("Delete source", key="lib_drill_delete"):
            st.session_state["_confirm_drill_delete"] = True
    if st.session_state.get("_confirm_drill_delete"):
        st.warning(f"Delete all {len(rows)} records from {source}?")
        c1, c2, _ = st.columns([1, 1, 6])
        with c1:
            if st.button("Yes, delete", type="primary",
                         key="lib_drill_delete_yes"):
                delete_by_source(source)
                st.session_state.pop("_confirm_drill_delete", None)
                st.session_state.pop("library_drill", None)
                st.rerun()
        with c2:
            if st.button("Cancel", key="lib_drill_delete_no"):
                st.session_state.pop("_confirm_drill_delete", None)
                st.rerun()

    if not rows:
        st.info("No records linked to this source.")
        return

    df = pd.DataFrame(rows)

    # Filters
    f1, f2, _ = st.columns([2, 2, 6])
    with f1:
        metric_opts = ["All"] + sorted(df["metric_type"].unique().tolist())
        sel_metric = st.selectbox("Metric", metric_opts,
                                  key="lib_detail_metric",
                                  label_visibility="collapsed")
    with f2:
        sub_opts = ["All"] + sorted(s for s in df["submarket"].unique()
                                    if s)
        sel_sub = st.selectbox("Submarket", sub_opts,
                               key="lib_detail_sub",
                               label_visibility="collapsed")

    view = df.copy()
    if sel_metric != "All":
        view = view[view["metric_type"] == sel_metric]
    if sel_sub != "All":
        view = view[view["submarket"] == sel_sub]

    st.markdown(f"**{len(view)} records**")
    cols_to_show = [
        "id", "submarket", "metric_type", "value", "unit", "lease_type",
        "period_date", "period_type", "confidence", "source_series",
    ]
    cols_present = [c for c in cols_to_show if c in view.columns]
    st.dataframe(
        view[cols_present],
        width="stretch",
        hide_index=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "value": st.column_config.NumberColumn(format="%.4f"),
            "confidence": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    # Inline edit
    with st.expander("Edit a record", expanded=False):
        e1, e2, e3 = st.columns([1, 1, 1])
        with e1:
            edit_id = st.number_input("Record ID", min_value=1, step=1,
                                      key="lib_edit_id")
        with e2:
            new_val = st.number_input("New value", format="%.4f",
                                      key="lib_edit_val")
        with e3:
            st.markdown(" ")  # vertical alignment with the inputs
            if st.button("Update", key="lib_edit_apply"):
                try:
                    update_metric(int(edit_id), float(new_val))
                    st.success(
                        f"Record {edit_id} -> {new_val} "
                        f"({getpass.getuser()})"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Update failed: {e}")

    # Rejected records for this source
    rejected = get_rejected_records(source=source)
    if rejected:
        with st.expander(f"Rejected records for this source ({len(rejected)})",
                         expanded=False):
            st.dataframe(pd.DataFrame(rejected),
                         width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# Cross-source rejected records (browse mode only)
# ---------------------------------------------------------------------------

def _render_rejected_section() -> None:
    rejected = get_rejected_records()
    if not rejected:
        return
    with st.expander(f"Rejected records across all sources "
                     f"({len(rejected)})", expanded=False):
        st.caption(
            "Records skipped because required fields were missing "
            "(market, asset_class, metric_type, etc.). Fix the source PDF "
            "and re-upload, or delete the rejection by ID."
        )
        rej_df = pd.DataFrame(rejected)
        if "source" in rej_df.columns:
            rej_df["report"] = rej_df["source"].apply(format_display_name)
        cols = [c for c in ["id", "report", "source", "source_page",
                            "reason", "missing_fields", "parser_strategy",
                            "date_rejected"]
                if c in rej_df.columns]
        st.dataframe(rej_df[cols], width="stretch", hide_index=True)

        d1, d2, _ = st.columns([1, 1, 6])
        with d1:
            rid = st.number_input("Rejected ID to delete", min_value=1,
                                  step=1, key="lib_rej_id")
        with d2:
            if st.button("Delete", key="lib_rej_del"):
                if delete_rejected_record(int(rid)):
                    st.success(f"Deleted rejected record {int(rid)}")
                    st.rerun()
                else:
                    st.error(f"No rejected record with id {int(rid)}")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_render_upload_section()

st.markdown("---")

if st.session_state.get("library_drill"):
    _render_source_detail(st.session_state["library_drill"])
else:
    _render_source_list()
    _render_rejected_section()
