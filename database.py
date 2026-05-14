"""
SQLite database layer with retry logic for OneDrive-shared access.
"""

import getpass
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
"""

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


@_retry
def insert_metrics(records: list[dict], db_path: str | None = None) -> int:
    """Insert parsed metric records. Returns number of rows inserted."""
    conn = _get_connection(db_path)
    now = datetime.now().isoformat()
    user = getpass.getuser()
    count = 0
    try:
        for r in records:
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
            count += 1
        conn.commit()
    finally:
        conn.close()
    return count


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
        conn.commit()
        return cur.rowcount
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
