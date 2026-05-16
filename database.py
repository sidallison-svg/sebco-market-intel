"""
SQLite database layer with retry logic for OneDrive-shared access.
"""

import getpass
import json
import sqlite3
import time
from datetime import datetime

from config import get_db_path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_page     INTEGER,
    report_date     TEXT NOT NULL,
    quarter         TEXT NOT NULL,
    metric_period   TEXT NOT NULL,
    period_type     TEXT NOT NULL,
    market          TEXT NOT NULL,
    submarket       TEXT,
    asset_class     TEXT NOT NULL,
    metric_type     TEXT NOT NULL,
    metric_value    REAL,
    unit            TEXT NOT NULL,
    confidence      REAL,
    raw_text        TEXT,
    parser_strategy TEXT,
    lease_type      TEXT,
    extraction_notes TEXT,
    date_ingested   TEXT NOT NULL,
    last_edited_by  TEXT,
    last_edited_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_market ON metrics(market);
CREATE INDEX IF NOT EXISTS idx_submarket ON metrics(submarket);
CREATE INDEX IF NOT EXISTS idx_metric_type ON metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_metric_period ON metrics(metric_period);
CREATE INDEX IF NOT EXISTS idx_source ON metrics(source);

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
    file_hash         TEXT NOT NULL UNIQUE,
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

# Statuses for uploaded_files.status
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REJECTED = "rejected"

REQUIRED_FIELDS = (
    "source", "report_date", "quarter", "metric_period",
    "period_type", "market", "asset_class", "metric_type", "unit",
)

MAX_RETRIES = 5
RETRY_DELAY = 0.5  # seconds


def _get_connection(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _retry(func):
    """Decorator to retry on 'database is locked' errors."""
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


def init_db(db_path: str | None = None):
    """Create tables and run idempotent migrations.

    Migration steps (each guarded so re-running is a no-op):
      1. Create all tables / indexes.
      2. Add metrics.source_file_id if missing.
      3. Backfill: for every distinct source already in metrics that has no
         uploaded_files row, create a 'legacy:<source>' placeholder row and
         point that source's metrics at it. This keeps the Uploads page and
         the Layer-2 report-identity check consistent for pre-existing data
         (we can't recover original bytes, hence the legacy: hash prefix —
         it can never collide with a real 64-hex SHA-256).
    """
    conn = _get_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)

        if not _column_exists(conn, "metrics", "source_file_id"):
            conn.execute("ALTER TABLE metrics ADD COLUMN source_file_id INTEGER")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metrics_source_file_id "
                "ON metrics(source_file_id)"
            )

        # lease_type: NNN vs industrial_gross for rent metrics. New CBRE/Voit
        # rows carry it from the parser; backfill pre-existing asking_rent
        # rows (all Kidder, NNN). Idempotent: only touches NULLs.
        if not _column_exists(conn, "metrics", "lease_type"):
            conn.execute("ALTER TABLE metrics ADD COLUMN lease_type TEXT")
        conn.execute(
            "UPDATE metrics SET lease_type='industrial_gross' "
            "WHERE metric_type='asking_rent' AND lease_type IS NULL "
            "AND parser_strategy='voit_submarket_table'"
        )
        conn.execute(
            "UPDATE metrics SET lease_type='NNN' "
            "WHERE metric_type='asking_rent' AND lease_type IS NULL"
        )

        # Backfill legacy placeholder rows for any source not yet tracked.
        orphan_sources = conn.execute(
            """
            SELECT m.source                AS source,
                   MAX(m.market)           AS market,
                   MAX(m.asset_class)      AS asset_class,
                   MAX(m.report_date)      AS report_date,
                   MAX(m.quarter)          AS quarter,
                   MIN(m.date_ingested)    AS first_ingested,
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

        conn.commit()
    finally:
        conn.close()


def _display_name(source: str | None) -> str:
    """Local import to avoid a circular dependency at module load."""
    if not source:
        return ""
    try:
        from utils import format_display_name
        return format_display_name(source)
    except Exception:
        return source or ""


def _validate_record(r: dict) -> list[str]:
    """Return list of required field names that are missing/empty."""
    return [f for f in REQUIRED_FIELDS if not r.get(f)]


@_retry
def insert_metrics(records: list[dict], db_path: str | None = None,
                   source_file_id: int | None = None) -> dict:
    """Insert parsed metric records.

    Records missing required fields are logged to rejected_records and skipped
    rather than aborting the whole batch. When source_file_id is given, every
    inserted metric row is linked back to its uploaded_files entry so a later
    supersede can delete exactly this upload's rows.

    Returns:
        {"inserted": int, "rejected": int, "rejected_ids": [int, ...]}
    """
    conn = _get_connection(db_path)
    now = datetime.now().isoformat()
    user = getpass.getuser()
    inserted = 0
    rejected_ids: list[int] = []
    try:
        for r in records:
            missing = _validate_record(r)
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
                        json.dumps({k: v for k, v in r.items() if k != "raw_text"},
                                   default=str)[:2000],
                        r.get("parser_strategy"),
                        now,
                    ),
                )
                rejected_ids.append(cur.lastrowid)
                continue

            conn.execute(
                """INSERT INTO metrics
                   (source, source_page, report_date, quarter, metric_period,
                    period_type, market, submarket, asset_class, metric_type,
                    metric_value, unit, confidence, raw_text, parser_strategy,
                    lease_type, extraction_notes, date_ingested,
                    last_edited_by, last_edited_at, source_file_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["source"], r.get("source_page"), r["report_date"],
                    r["quarter"], r["metric_period"], r["period_type"],
                    r["market"], r.get("submarket"), r["asset_class"],
                    r["metric_type"], r.get("metric_value"), r["unit"],
                    r.get("confidence"), r.get("raw_text"),
                    r.get("parser_strategy"), r.get("lease_type"),
                    r.get("extraction_notes"),
                    now, user, now, source_file_id,
                ),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return {
        "inserted": inserted,
        "rejected": len(rejected_ids),
        "rejected_ids": rejected_ids,
    }


@_retry
def get_file_by_hash(file_hash: str, db_path: str | None = None) -> dict | None:
    """Layer 1 — exact-bytes lookup. Returns the uploaded_files row or None."""
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM uploaded_files WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@_retry
def find_active_report(market: str, asset_class: str, report_date: str,
                       quarter: str, db_path: str | None = None) -> dict | None:
    """Layer 2 — same report identity, possibly a different file.

    Returns the existing ACTIVE uploaded_files row for this
    (market, asset_class, report_date, quarter) tuple, or None.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM uploaded_files
               WHERE market = ? AND asset_class = ? AND report_date = ?
                 AND quarter = ? AND status = ?
               ORDER BY uploaded_at DESC LIMIT 1""",
            (market, asset_class, report_date, quarter, STATUS_ACTIVE),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@_retry
def record_uploaded_file(meta: dict, db_path: str | None = None) -> int:
    """Insert an uploaded_files row and return its new id.

    `meta` keys: file_hash, original_filename, display_name,
    file_size_bytes, market, asset_class, report_date, quarter,
    record_count, parser_strategy, status.
    """
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
    """Mark an uploaded_files row superseded and delete its metric rows.

    Returns the number of metric rows deleted. The uploaded_files row is
    kept (status='superseded') as the audit trail.
    """
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
    """Every uploaded_files row (active/superseded/rejected), newest first."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM uploaded_files ORDER BY uploaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def delete_uploaded_file_row(file_id: int, db_path: str | None = None) -> int:
    """Delete a single uploaded_files row by id and its linked metrics.

    Used to clear a rejected entry (no metrics linked) so an improved
    parser can retry the same file. Also unlinks any metrics that pointed
    at it, for safety.
    """
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


@_retry
def check_source_exists(source: str, db_path: str | None = None) -> bool:
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM metrics WHERE source = ?", (source,)
        ).fetchone()
        return row["cnt"] > 0
    finally:
        conn.close()


@_retry
def delete_by_source(source: str, db_path: str | None = None) -> int:
    """Hard-delete every metric row for a source.

    Also clears that source's rejected_records and removes its uploaded_files
    entries so the file can be re-uploaded cleanly (no orphan 'active' row
    left behind to trip the Layer-2 report-identity check).
    """
    conn = _get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM metrics WHERE source = ?", (source,))
        conn.execute("DELETE FROM rejected_records WHERE source = ?", (source,))
        conn.execute(
            "DELETE FROM uploaded_files WHERE original_filename = ?", (source,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@_retry
def get_upload_summaries(db_path: str | None = None) -> list[dict]:
    """One row per uploaded source PDF, with summary stats.

    Fields per row:
      source, market, asset_class, quarter, latest_upload, record_count,
      avg_confidence, parser_strategy, rejected_count.

    Ordered most-recent upload first.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT m.source                  AS source,
                   MAX(m.market)             AS market,
                   MAX(m.asset_class)        AS asset_class,
                   MAX(m.quarter)            AS quarter,
                   MAX(m.date_ingested)      AS latest_upload,
                   COUNT(*)                  AS record_count,
                   AVG(m.confidence)         AS avg_confidence,
                   MAX(m.parser_strategy)    AS parser_strategy,
                   (SELECT COUNT(*) FROM rejected_records r
                    WHERE r.source = m.source) AS rejected_count
            FROM metrics m
            GROUP BY m.source
            ORDER BY MAX(m.date_ingested) DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def get_metrics_for_source(source: str, db_path: str | None = None) -> list[dict]:
    """All metric rows for a single source PDF, ordered for display."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM metrics WHERE source = ? "
            "ORDER BY submarket, metric_type, metric_period",
            (source,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def get_all_metrics(db_path: str | None = None) -> list[dict]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM metrics ORDER BY metric_period DESC, market, submarket").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@_retry
def update_metric(metric_id: int, metric_value: float, db_path: str | None = None):
    conn = _get_connection(db_path)
    user = getpass.getuser()
    now = datetime.now().isoformat()
    try:
        conn.execute(
            "UPDATE metrics SET metric_value=?, last_edited_by=?, last_edited_at=? WHERE id=?",
            (metric_value, user, now, metric_id),
        )
        conn.commit()
    finally:
        conn.close()


@_retry
def get_distinct_values(column: str, db_path: str | None = None) -> list[str]:
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM metrics WHERE {column} IS NOT NULL ORDER BY {column}"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@_retry
def get_rejected_records(source: str | None = None,
                         db_path: str | None = None) -> list[dict]:
    """Return rejected records, optionally filtered by source PDF."""
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
def delete_rejected_record(rejected_id: int, db_path: str | None = None) -> int:
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
# Saved views (named filter combinations for Trends / Comparison)
# ---------------------------------------------------------------------------

@_retry
def save_view(name: str, page: str, filter_json: str,
              db_path: str | None = None) -> int:
    """Insert or replace a saved view. (page, name) is unique, so saving
    again under the same name on the same page overwrites it."""
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
def get_saved_views(page: str, db_path: str | None = None) -> list[dict]:
    """All saved views for a page, newest first."""
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
def delete_saved_view(view_id: int, db_path: str | None = None) -> int:
    conn = _get_connection(db_path)
    try:
        cur = conn.execute(
            "DELETE FROM saved_views WHERE id = ?", (view_id,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
