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
"""

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


def init_db(db_path: str | None = None):
    conn = _get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.close()


def _validate_record(r: dict) -> list[str]:
    """Return list of required field names that are missing/empty."""
    return [f for f in REQUIRED_FIELDS if not r.get(f)]


@_retry
def insert_metrics(records: list[dict], db_path: str | None = None) -> dict:
    """Insert parsed metric records.

    Records missing required fields are logged to rejected_records and skipped
    rather than aborting the whole batch.

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
                    extraction_notes, date_ingested, last_edited_by, last_edited_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["source"], r.get("source_page"), r["report_date"],
                    r["quarter"], r["metric_period"], r["period_type"],
                    r["market"], r.get("submarket"), r["asset_class"],
                    r["metric_type"], r.get("metric_value"), r["unit"],
                    r.get("confidence"), r.get("raw_text"),
                    r.get("parser_strategy"), r.get("extraction_notes"),
                    now, user, now,
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
    conn = _get_connection(db_path)
    try:
        cur = conn.execute("DELETE FROM metrics WHERE source = ?", (source,))
        # Also clear any prior rejections from this source so re-import is clean.
        conn.execute("DELETE FROM rejected_records WHERE source = ?", (source,))
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
