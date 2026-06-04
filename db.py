"""
Sebco Market Intel — shared database module (v2 normalized schema).

Single `metrics` table; every ingest path (Kidder, CBRE, Voit, JLL) writes
the same shape via `upsert_metrics()`. Auxiliary tables (uploaded_files,
rejected_records, saved_views) are unchanged from the v1 layout.

Field names follow the v2 spec: value, period_date, source_series,
ingested_at. The parsers in pdf_parser.py still emit v1 names
(metric_value, metric_period, parser_strategy, date_ingested) — those get
translated on the way in by upsert_metrics via _V1_TO_V2, so the
parser modules don't have to change.

Why submarket / lease_type / source_series default to '' (not NULL):
SQLite treats NULLs as distinct in UNIQUE constraints, so two rows with
NULL submarket would both insert silently — exactly the duplicate bug we
hit before. Empty-string keeps the UNIQUE constraint honest.
"""

import getpass
import json
import sqlite3
import time
from datetime import datetime

from config import get_db_path


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- identity dimensions (all participate in UNIQUE constraint)
    metric_type     TEXT NOT NULL,
    market          TEXT NOT NULL,
    submarket       TEXT NOT NULL DEFAULT '',
    asset_class     TEXT NOT NULL,
    period_date     TEXT NOT NULL,
    lease_type      TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL,
    source_series   TEXT NOT NULL DEFAULT '',

    -- value + unit
    value           REAL,
    unit            TEXT NOT NULL,

    -- temporal context. period_type IS part of the UNIQUE constraint
    -- because a Kidder breakdown row reports the same metric for the same
    -- period as both an absolute value (period_type='current', unit='sf')
    -- and a YoY delta (period_type='yoy_change', unit='percent_change') —
    -- those are semantically distinct rows that must coexist.
    period_type     TEXT NOT NULL DEFAULT 'current',
    frequency       TEXT NOT NULL DEFAULT 'quarterly',
    report_date     TEXT NOT NULL,
    quarter         TEXT NOT NULL,

    -- provenance + audit
    is_estimate     INTEGER NOT NULL DEFAULT 0,
    confidence      REAL,
    raw_text        TEXT,
    source_page     INTEGER,
    source_file_id  INTEGER,
    ingested_at     TEXT NOT NULL,
    last_edited_by  TEXT,
    last_edited_at  TEXT,

    UNIQUE(metric_type, market, submarket, asset_class,
           period_date, lease_type, source, source_series, period_type)
);

CREATE INDEX IF NOT EXISTS idx_metrics_market       ON metrics(market);
CREATE INDEX IF NOT EXISTS idx_metrics_submarket    ON metrics(submarket);
CREATE INDEX IF NOT EXISTS idx_metrics_metric_type  ON metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_metrics_period_date  ON metrics(period_date);
CREATE INDEX IF NOT EXISTS idx_metrics_source       ON metrics(source);
CREATE INDEX IF NOT EXISTS idx_metrics_source_file  ON metrics(source_file_id);

CREATE TABLE IF NOT EXISTS rejected_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT,
    source_page     INTEGER,
    reason          TEXT NOT NULL,
    missing_fields  TEXT,
    raw_text        TEXT,
    record_json     TEXT,
    parser_strategy TEXT,
    date_rejected   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejected_source ON rejected_records(source);

CREATE TABLE IF NOT EXISTS uploaded_files (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash         TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    display_name      TEXT,
    file_size_bytes   INTEGER,
    uploaded_at       TEXT NOT NULL,
    uploaded_by       TEXT,
    market            TEXT,
    asset_class       TEXT,
    report_date       TEXT,
    quarter           TEXT,
    record_count      INTEGER DEFAULT 0,
    parser_strategy   TEXT,
    status            TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_uploaded_hash ON uploaded_files(file_hash);
CREATE INDEX IF NOT EXISTS idx_uploaded_identity
    ON uploaded_files(market, asset_class, report_date, quarter, status);
-- file_hash is unique only among non-superseded rows: a superseded row
-- keeps its hash for audit while the same file can be re-uploaded.
CREATE UNIQUE INDEX IF NOT EXISTS idx_uploaded_files_hash_active
    ON uploaded_files(file_hash) WHERE status <> 'superseded';

CREATE TABLE IF NOT EXISTS saved_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    page        TEXT NOT NULL,
    filter_json TEXT NOT NULL,
    created_by  TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(page, name)
);
CREATE INDEX IF NOT EXISTS idx_saved_views_page ON saved_views(page);
"""

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REJECTED = "rejected"

# Required fields on upsert input (using v2 names). insert_metrics()
# translates v1 names (metric_value, metric_period, ...) before this check.
REQUIRED_FIELDS = (
    "source", "period_date", "market", "asset_class",
    "metric_type", "unit",
)

# Legacy field names accepted by insert_metrics(); translated to v2 names.
_V1_TO_V2 = {
    "metric_value": "value",
    "metric_period": "period_date",
    "parser_strategy": "source_series",
    "date_ingested": "ingested_at",
}

MAX_RETRIES = 5
RETRY_DELAY = 0.5  # seconds


# ---------------------------------------------------------------------------
# Column list for the metric-row SELECTs. Listed explicitly (vs. SELECT *) so
# the query result schema is stable when columns get added/reordered later.
# ---------------------------------------------------------------------------

_METRICS_COLS = """
    id,
    source,
    source_page,
    report_date,
    quarter,
    period_date,
    period_type,
    market,
    submarket,
    asset_class,
    metric_type,
    value,
    unit,
    confidence,
    raw_text,
    source_series,
    lease_type,
    frequency,
    is_estimate,
    ingested_at,
    last_edited_by,
    last_edited_at,
    source_file_id
"""


# ---------------------------------------------------------------------------
# Connection + retry helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _retry(func):
    """Retry on 'database is locked' errors (multi-user OneDrive case)."""
    def wrapper(*args, **kwargs):
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    last_err = e
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    raise
        raise last_err
    return wrapper


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _display_name(source: str | None) -> str:
    if not source:
        return ""
    try:
        from utils import format_display_name
        return format_display_name(source)
    except Exception:
        return source or ""


# ---------------------------------------------------------------------------
# Quarter derivation
# ---------------------------------------------------------------------------

def quarter_label_from_date(d: str | None) -> str:
    """'2026-03-31' -> '1Q 2026'. Empty/unknown -> ''."""
    if not d or len(d) < 7:
        return ""
    yr, mo = d[:4], d[5:7]
    return {
        "03": f"1Q {yr}",
        "06": f"2Q {yr}",
        "09": f"3Q {yr}",
        "12": f"4Q {yr}",
    }.get(mo, "")


# ---------------------------------------------------------------------------
# Schema init + migration from v1
# ---------------------------------------------------------------------------

def init_db(db_path: str | None = None) -> None:
    """Create v2 schema (idempotent). If a v1 `metrics` table exists,
    migrate it in place by renaming the old table to `metrics_legacy`,
    creating the v2 table, and copying rows with field mapping.

    The legacy table is kept (not dropped) so the migration is reversible
    and so `scripts/migrate_to_v2.py` can verify counts after the fact.
    """
    conn = _get_connection(db_path)
    try:
        needs_migration = (
            _table_exists(conn, "metrics")
            and _column_exists(conn, "metrics", "metric_value")
            and not _column_exists(conn, "metrics", "value")
        )

        if needs_migration:
            _migrate_v1_metrics(conn)

        conn.executescript(SCHEMA_SQL)
        _backfill_legacy_uploaded_files(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_v1_metrics(conn: sqlite3.Connection) -> None:
    """Move v1 `metrics` -> `metrics_legacy`, create v2 `metrics`, copy rows.

    Field mapping:
      metric_value     -> value
      metric_period    -> period_date          (per-row period this value covers)
      parser_strategy  -> source_series
      date_ingested    -> ingested_at
      report_date      -> report_date          (kept; report's quarter-end)
      quarter          -> quarter              (kept; report's display label)
      extraction_notes -> dropped (dead)
      submarket NULL   -> ''                   (UNIQUE-safety)
      lease_type NULL  -> ''                   (UNIQUE-safety)

    v1 had no UNIQUE constraint, so it may contain duplicate rows that
    violate v2's identity key. For each duplicate group we keep the row
    with the LOWEST id (chronologically first; in practice the correct
    row — the higher-id duplicate has only ever shown up from one Kidder
    parser misclassification). Dropped rows are stashed in
    `metrics_v1_dups` (same shape as `metrics_legacy`) for audit.
    """
    if _table_exists(conn, "metrics_legacy"):
        # Prior migration left a stale legacy table; drop it before re-running.
        conn.execute("DROP TABLE metrics_legacy")
    if _table_exists(conn, "metrics_v1_dups"):
        conn.execute("DROP TABLE metrics_v1_dups")

    conn.execute("ALTER TABLE metrics RENAME TO metrics_legacy")

    # The new schema includes `metrics`; create just that table now so we
    # can COPY into it before the rest of SCHEMA_SQL runs.
    conn.executescript(SCHEMA_SQL)

    # Stash the v1 rows we'll skip (anything with id NOT MIN within its
    # v2-identity group) for audit. Keying matches the v2 UNIQUE constraint.
    conn.execute(
        """
        CREATE TABLE metrics_v1_dups AS
        SELECT * FROM metrics_legacy
        WHERE id NOT IN (
            SELECT MIN(id) FROM metrics_legacy
            GROUP BY metric_type, market, COALESCE(submarket, ''),
                     asset_class, metric_period, COALESCE(lease_type, ''),
                     source, COALESCE(parser_strategy, ''), period_type
        )
        """
    )

    now = datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO metrics (
            id,
            metric_type, market, submarket, asset_class,
            period_date, lease_type, source, source_series,
            value, unit,
            period_type, frequency, report_date, quarter,
            is_estimate, confidence, raw_text, source_page,
            source_file_id, ingested_at, last_edited_by, last_edited_at
        )
        SELECT
            id,
            metric_type,
            market,
            COALESCE(submarket, ''),
            asset_class,
            metric_period,
            COALESCE(lease_type, ''),
            source,
            COALESCE(parser_strategy, ''),
            metric_value,
            unit,
            COALESCE(period_type, 'current'),
            'quarterly',
            report_date,
            quarter,
            0,
            confidence,
            raw_text,
            source_page,
            source_file_id,
            COALESCE(date_ingested, ?),
            last_edited_by,
            last_edited_at
        FROM metrics_legacy
        WHERE id IN (
            SELECT MIN(id) FROM metrics_legacy
            GROUP BY metric_type, market, COALESCE(submarket, ''),
                     asset_class, metric_period, COALESCE(lease_type, ''),
                     source, COALESCE(parser_strategy, ''), period_type
        )
        """,
        (now,),
    )


def _backfill_legacy_uploaded_files(conn: sqlite3.Connection) -> None:
    """For any metrics.source without an uploaded_files row, create a
    'legacy:<source>' placeholder so the Uploads page lists every source.

    Carried over from v1 init_db. No-op on already-backfilled databases.
    """
    orphan_sources = conn.execute(
        """
        SELECT m.source                AS source,
               MAX(m.market)           AS market,
               MAX(m.asset_class)      AS asset_class,
               MAX(m.report_date)      AS report_date,
               MAX(m.quarter)          AS quarter,
               MIN(m.ingested_at)      AS first_ingested,
               COUNT(*)                AS record_count,
               MAX(m.last_edited_by)   AS uploaded_by
        FROM metrics m
        WHERE m.source_file_id IS NULL
        GROUP BY m.source
        """
    ).fetchall()

    for row in orphan_sources:
        legacy_hash = f"legacy:{row['source']}"
        existing = conn.execute(
            "SELECT id FROM uploaded_files WHERE file_hash = ?",
            (legacy_hash,),
        ).fetchone()
        if existing:
            file_id = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO uploaded_files
                   (file_hash, original_filename, display_name,
                    file_size_bytes, uploaded_at, uploaded_by, market,
                    asset_class, report_date, quarter, record_count,
                    parser_strategy, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    legacy_hash,
                    row["source"],
                    _display_name(row["source"]),
                    None,
                    row["first_ingested"] or datetime.now().isoformat(),
                    row["uploaded_by"],
                    row["market"],
                    row["asset_class"],
                    row["report_date"],
                    row["quarter"],
                    row["record_count"],
                    "legacy-backfill",
                    STATUS_ACTIVE,
                ),
            )
            file_id = cur.lastrowid
        conn.execute(
            "UPDATE metrics SET source_file_id = ? "
            "WHERE source = ? AND source_file_id IS NULL",
            (file_id, row["source"]),
        )


# ---------------------------------------------------------------------------
# Upsert (the new write path)
# ---------------------------------------------------------------------------

def _translate_v1(record: dict) -> dict:
    """Rename v1 field names to v2 in place (returns a new dict)."""
    out = dict(record)
    for old, new in _V1_TO_V2.items():
        if old in out and new not in out:
            out[new] = out.pop(old)
        elif old in out:
            # Both present: prefer the v2 value (caller already used new name).
            out.pop(old)
    return out


def _normalize(record: dict) -> dict:
    """Fill defaults and derived fields; coerce NULLs that must be ''.

    The schema enforces NOT NULL on submarket / lease_type / source_series
    / quarter / report_date / period_type / frequency, so callers can omit
    any of those and this function fills the right blank/derived value.
    """
    r = dict(record)
    r["submarket"] = r.get("submarket") or ""
    r["lease_type"] = r.get("lease_type") or ""
    r["source_series"] = r.get("source_series") or ""
    r["period_type"] = r.get("period_type") or "current"
    r["frequency"] = r.get("frequency") or "quarterly"
    r["is_estimate"] = int(bool(r.get("is_estimate", 0)))

    # report_date defaults to period_date (current-period rows).
    if not r.get("report_date") and r.get("period_date"):
        r["report_date"] = r["period_date"]

    # quarter defaults to derive-from-report_date.
    if not r.get("quarter"):
        r["quarter"] = quarter_label_from_date(r.get("report_date"))

    return r


def _validate(r: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if not r.get(f)]


@_retry
def upsert_metrics(records: list[dict],
                   db_path: str | None = None,
                   source_file_id: int | None = None) -> dict:
    """Insert or update metric rows, keyed by the v2 UNIQUE constraint.

    Accepts dicts using v2 field names (value, period_date, source_series,
    ingested_at) OR the legacy v1 names from pdf_parser (metric_value,
    metric_period, parser_strategy, date_ingested) — both are translated
    in-place so parser output flows through unchanged.

    Re-ingesting the same source PDF updates rows in place rather than
    duplicating — this is the fix for the old upload-page bug where a
    save fired before the duplicate check.

    Manual edits made via the Raw Data page (update_metric) ARE overwritten
    on re-ingest. Re-parsing is treated as authoritative.

    Records missing required fields are logged to rejected_records and
    skipped (the rest of the batch still applies).

    Returns:
        {"inserted": int, "updated": int, "rejected": int,
         "rejected_ids": [int, ...]}
    """
    conn = _get_connection(db_path)
    now = datetime.now().isoformat()
    user = getpass.getuser()
    inserted = updated = 0
    rejected_ids: list[int] = []
    try:
        for raw in records:
            r = _normalize(_translate_v1(raw))
            missing = _validate(r)
            if missing:
                cur = conn.execute(
                    """INSERT INTO rejected_records
                       (source, source_page, reason, missing_fields,
                        raw_text, record_json, parser_strategy, date_rejected)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        r.get("source"),
                        r.get("source_page"),
                        f"Missing required field(s): {', '.join(missing)}",
                        ",".join(missing),
                        (r.get("raw_text") or "")[:500],
                        json.dumps(
                            {k: v for k, v in r.items() if k != "raw_text"},
                            default=str,
                        )[:2000],
                        r.get("source_series"),
                        now,
                    ),
                )
                rejected_ids.append(cur.lastrowid)
                continue

            key = (
                r["metric_type"], r["market"], r["submarket"],
                r["asset_class"], r["period_date"], r["lease_type"],
                r["source"], r["source_series"], r["period_type"],
            )
            existing = conn.execute(
                """SELECT id FROM metrics
                   WHERE metric_type=? AND market=? AND submarket=?
                     AND asset_class=? AND period_date=? AND lease_type=?
                     AND source=? AND source_series=? AND period_type=?""",
                key,
            ).fetchone()

            ingested_at = r.get("ingested_at") or now

            if existing:
                conn.execute(
                    """UPDATE metrics
                       SET value=?, unit=?, period_type=?, frequency=?,
                           report_date=?, quarter=?, is_estimate=?,
                           confidence=?, raw_text=?, source_page=?,
                           source_file_id=?, ingested_at=?,
                           last_edited_by=NULL, last_edited_at=NULL
                       WHERE id=?""",
                    (
                        r.get("value"), r["unit"], r["period_type"],
                        r["frequency"], r["report_date"], r["quarter"],
                        r["is_estimate"], r.get("confidence"),
                        r.get("raw_text"), r.get("source_page"),
                        source_file_id if source_file_id is not None
                        else r.get("source_file_id"),
                        ingested_at, existing["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO metrics
                       (metric_type, market, submarket, asset_class,
                        period_date, lease_type, source, source_series,
                        value, unit, period_type, frequency,
                        report_date, quarter, is_estimate, confidence,
                        raw_text, source_page, source_file_id,
                        ingested_at, last_edited_by, last_edited_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        r["metric_type"], r["market"], r["submarket"],
                        r["asset_class"], r["period_date"], r["lease_type"],
                        r["source"], r["source_series"],
                        r.get("value"), r["unit"], r["period_type"],
                        r["frequency"], r["report_date"], r["quarter"],
                        r["is_estimate"], r.get("confidence"),
                        r.get("raw_text"), r.get("source_page"),
                        source_file_id if source_file_id is not None
                        else r.get("source_file_id"),
                        ingested_at, user, ingested_at,
                    ),
                )
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return {
        "inserted": inserted,
        "updated": updated,
        "rejected": len(rejected_ids),
        "rejected_ids": rejected_ids,
    }


def insert_metrics(records: list[dict],
                   db_path: str | None = None,
                   source_file_id: int | None = None) -> dict:
    """v1-compatible facade used by the Upload page.

    Delegates to upsert_metrics (which handles the v1-name translation).
    Returns the v1 result shape ({inserted, rejected, rejected_ids}) —
    `updated` is folded into `inserted` so legacy callers don't have to
    learn the new field.
    """
    res = upsert_metrics(records, db_path=db_path,
                         source_file_id=source_file_id)
    return {
        "inserted": res["inserted"] + res["updated"],
        "rejected": res["rejected"],
        "rejected_ids": res["rejected_ids"],
    }


# ---------------------------------------------------------------------------
# uploaded_files helpers (ported from v1)
# ---------------------------------------------------------------------------

@_retry
def get_file_by_hash(file_hash: str,
                     db_path: str | None = None) -> dict | None:
    """Layer 1 dup check: exact bytes, ignoring superseded rows."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM uploaded_files WHERE file_hash = ? "
            "AND status != ? ORDER BY uploaded_at DESC LIMIT 1",
            (file_hash, STATUS_SUPERSEDED),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@_retry
def count_metrics_for_file(file_id: int,
                           db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM metrics WHERE source_file_id = ?",
            (file_id,),
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


@_retry
def clear_orphan_upload(file_id: int,
                        db_path: str | None = None) -> None:
    """Drop an orphaned uploaded_files row (no live metrics) and its
    rejected_records so the same file can be re-processed cleanly."""
    conn = _get_connection(db_path)
    try:
        fn_row = conn.execute(
            "SELECT original_filename FROM uploaded_files WHERE id = ?",
            (file_id,),
        ).fetchone()
        conn.execute(
            "DELETE FROM metrics WHERE source_file_id = ?", (file_id,)
        )
        if fn_row and fn_row["original_filename"]:
            conn.execute(
                "DELETE FROM rejected_records WHERE source = ?",
                (fn_row["original_filename"],),
            )
        conn.execute(
            "DELETE FROM uploaded_files WHERE id = ?", (file_id,)
        )
        conn.commit()
    finally:
        conn.close()


@_retry
def find_active_report(market: str, asset_class: str, report_date: str,
                       quarter: str, source: str,
                       db_path: str | None = None) -> dict | None:
    """Layer 2 dup check: same report identity, possibly a different file."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM uploaded_files
               WHERE market = ? AND asset_class = ? AND report_date = ?
                 AND quarter = ? AND original_filename = ? AND status = ?
               ORDER BY uploaded_at DESC LIMIT 1""",
            (market, asset_class, report_date, quarter, source,
             STATUS_ACTIVE),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@_retry
def record_uploaded_file(meta: dict, db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    now = datetime.now().isoformat()
    user = getpass.getuser()
    try:
        cur = conn.execute(
            """INSERT INTO uploaded_files
               (file_hash, original_filename, display_name, file_size_bytes,
                uploaded_at, uploaded_by, market, asset_class, report_date,
                quarter, record_count, parser_strategy, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                meta["file_hash"],
                meta["original_filename"],
                meta.get("display_name"),
                meta.get("file_size_bytes"),
                now,
                user,
                meta.get("market"),
                meta.get("asset_class"),
                meta.get("report_date"),
                meta.get("quarter"),
                meta.get("record_count", 0),
                meta.get("parser_strategy"),
                meta.get("status", STATUS_ACTIVE),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@_retry
def supersede_file(file_id: int, db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM metrics WHERE source_file_id = ?", (file_id,)
        )
        deleted = cur.rowcount
        conn.execute(
            "UPDATE uploaded_files SET status = ? WHERE id = ?",
            (STATUS_SUPERSEDED, file_id),
        )
        conn.commit()
        return deleted
    finally:
        conn.close()


@_retry
def get_upload_history(db_path: str | None = None) -> list[dict]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM uploaded_files ORDER BY uploaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def delete_uploaded_file_row(file_id: int,
                             db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    try:
        conn.execute(
            "DELETE FROM metrics WHERE source_file_id = ?", (file_id,)
        )
        cur = conn.execute(
            "DELETE FROM uploaded_files WHERE id = ?", (file_id,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# metrics-table convenience helpers (used by dashboard / pdf_export)
# ---------------------------------------------------------------------------

@_retry
def check_source_exists(source: str,
                        db_path: str | None = None) -> bool:
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM metrics WHERE source = ?",
            (source,),
        ).fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


@_retry
def delete_by_source(source: str, db_path: str | None = None) -> int:
    """Hard-delete every metric for a source; also clears rejected_records
    and uploaded_files entries so the file can be re-uploaded cleanly."""
    conn = _get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM metrics WHERE source = ?", (source,)
        )
        conn.execute(
            "DELETE FROM rejected_records WHERE source = ?", (source,)
        )
        conn.execute(
            "DELETE FROM uploaded_files WHERE original_filename = ?",
            (source,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@_retry
def get_upload_summaries(db_path: str | None = None) -> list[dict]:
    """One row per source PDF with summary stats (Uploads page)."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT m.source                  AS source,
                   MAX(m.market)             AS market,
                   MAX(m.asset_class)        AS asset_class,
                   MAX(m.quarter)            AS quarter,
                   MAX(m.ingested_at)        AS latest_upload,
                   COUNT(*)                  AS record_count,
                   AVG(m.confidence)         AS avg_confidence,
                   MAX(m.source_series)      AS parser_strategy,
                   (SELECT COUNT(*) FROM rejected_records r
                    WHERE r.source = m.source) AS rejected_count
            FROM metrics m
            GROUP BY m.source
            ORDER BY MAX(m.ingested_at) DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def get_metrics_for_source(source: str,
                           db_path: str | None = None) -> list[dict]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_METRICS_COLS} FROM metrics WHERE source = ? "
            "ORDER BY submarket, metric_type, period_date",
            (source,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def get_all_metrics(db_path: str | None = None) -> list[dict]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT {_METRICS_COLS} FROM metrics "
            "ORDER BY period_date DESC, market, submarket"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def update_metric(metric_id: int, metric_value: float,
                  db_path: str | None = None) -> None:
    """Manual edit from the Raw Data page. Parameter is named
    metric_value for v1 call-site compatibility; writes to `value`."""
    conn = _get_connection(db_path)
    user = getpass.getuser()
    now = datetime.now().isoformat()
    try:
        conn.execute(
            "UPDATE metrics SET value=?, last_edited_by=?, "
            "last_edited_at=? WHERE id=?",
            (metric_value, user, now, metric_id),
        )
        conn.commit()
    finally:
        conn.close()


@_retry
def get_distinct_values(column: str,
                        db_path: str | None = None) -> list[str]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM metrics "
            f"WHERE {column} IS NOT NULL AND {column} <> '' "
            f"ORDER BY {column}"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# rejected_records helpers
# ---------------------------------------------------------------------------

@_retry
def get_rejected_records(source: str | None = None,
                         db_path: str | None = None) -> list[dict]:
    conn = _get_connection(db_path)
    try:
        if source:
            rows = conn.execute(
                "SELECT * FROM rejected_records WHERE source = ? "
                "ORDER BY date_rejected DESC",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM rejected_records ORDER BY date_rejected DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def delete_rejected_record(rejected_id: int,
                           db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM rejected_records WHERE id = ?", (rejected_id,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# saved_views helpers
# ---------------------------------------------------------------------------

@_retry
def save_view(name: str, page: str, filter_json: str,
              db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    now = datetime.now().isoformat()
    user = getpass.getuser()
    try:
        cur = conn.execute(
            """INSERT INTO saved_views (name, page, filter_json,
                   created_by, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(page, name) DO UPDATE SET
                   filter_json = excluded.filter_json,
                   created_by  = excluded.created_by,
                   created_at  = excluded.created_at""",
            (name, page, filter_json, user, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@_retry
def get_saved_views(page: str,
                    db_path: str | None = None) -> list[dict]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM saved_views WHERE page = ? "
            "ORDER BY created_at DESC",
            (page,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def delete_saved_view(view_id: int,
                      db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM saved_views WHERE id = ?", (view_id,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
