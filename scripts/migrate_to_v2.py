"""
Migrate market_data.db from the v1 wide-metrics schema to the v2 normalized
schema (see db.py for the schema definition and field mapping).

What it does, in order:
  1. Back up the current DB to market_data_pre_v2_<timestamp>.db.
  2. Snapshot per-source row counts before migration.
  3. Run db.init_db() — which auto-detects the v1 schema and rebuilds
     `metrics` into v2, keeping the old rows in `metrics_legacy`.
  4. Snapshot per-source row counts after migration.
  5. Compare. Abort (with a loud error) if any source lost rows.

Run from the repo root:
    python -m scripts.migrate_to_v2
or:
    python scripts/migrate_to_v2.py

The legacy table is kept by default so the migration is reversible. Pass
--drop-legacy after a clean run to remove it.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

# Allow running as a script from anywhere in the repo.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import get_db_path  # noqa: E402
from db import init_db  # noqa: E402


def _counts_by_source(db_path: str, table: str) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT source, COUNT(*) FROM {table} GROUP BY source"
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


def _total(db_path: str, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def _table_exists(db_path: str, name: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _has_v1_schema(db_path: str) -> bool:
    """v1 = `metrics` exists with column `metric_value` and no `value`."""
    conn = sqlite3.connect(db_path)
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(metrics)"
        ).fetchall()}
        return "metric_value" in cols and "value" not in cols
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--drop-legacy", action="store_true",
        help="After a clean migration, drop the metrics_legacy table.",
    )
    ap.add_argument(
        "--db-path", default=None,
        help="Override the DB path (default: config.get_db_path()).",
    )
    args = ap.parse_args()

    db_path = args.db_path or get_db_path()
    print(f"[migrate] DB: {db_path}")

    if not os.path.exists(db_path):
        print(f"[migrate] No DB at {db_path} — running init_db() to create "
              f"a fresh v2 schema.")
        init_db(db_path)
        print("[migrate] Fresh v2 schema created. Nothing to migrate.")
        return 0

    is_v1 = _has_v1_schema(db_path)
    if not is_v1:
        if _table_exists(db_path, "metrics"):
            print("[migrate] DB already on v2 schema. Running init_db() "
                  "to make sure indexes / auxiliary tables are in sync.")
            init_db(db_path)
            print(f"[migrate] Total metrics rows: "
                  f"{_total(db_path, 'metrics')}")
        else:
            print("[migrate] No `metrics` table found. Initializing fresh.")
            init_db(db_path)
        return 0

    # --- v1 detected, real migration ahead ---

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = (
        os.path.splitext(db_path)[0] + f"_pre_v2_{ts}.db"
    )
    print(f"[migrate] Backing up to: {backup}")
    shutil.copy2(db_path, backup)

    before = _counts_by_source(db_path, "metrics")
    before_total = sum(before.values())
    print(f"[migrate] BEFORE: {before_total} rows across "
          f"{len(before)} sources.")
    for src, n in sorted(before.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {src}")

    print("[migrate] Running db.init_db() to perform v1 -> v2 migration...")
    init_db(db_path)

    if not _table_exists(db_path, "metrics_legacy"):
        print("[migrate][ERROR] Expected metrics_legacy after migration "
              "but it's not present. Aborting before any cleanup.")
        return 2

    after = _counts_by_source(db_path, "metrics")
    after_total = sum(after.values())
    legacy_total = _total(db_path, "metrics_legacy")
    dups_total = (_total(db_path, "metrics_v1_dups")
                  if _table_exists(db_path, "metrics_v1_dups") else 0)

    print()
    print(f"[migrate] AFTER:  {after_total} rows in `metrics`  "
          f"(legacy backup: {legacy_total}, "
          f"dropped-as-duplicate: {dups_total}).")
    print("[migrate] Per-source comparison (before -> after, "
          "delta = duplicates collapsed):")
    sources = sorted(set(before) | set(after))
    bad = []
    for src in sources:
        b = before.get(src, 0)
        a = after.get(src, 0)
        if a == b:
            marker = "OK "
        elif a < b and (b - a) <= dups_total:
            marker = "DUP"
        else:
            marker = "!! "
            bad.append((src, b, a))
        print(f"  {marker} {b:>5} -> {a:>5}  {src}")

    if bad:
        print()
        print(f"[migrate][ERROR] Unexpected loss beyond duplicate "
              f"collapse — DO NOT drop the legacy table.")
        for src, b, a in bad:
            print(f"  source {src!r}: was {b}, now {a}")
        return 3

    if after_total + dups_total != before_total:
        print()
        print(f"[migrate][ERROR] before({before_total}) != "
              f"after({after_total}) + dups({dups_total}). "
              f"Investigate before dropping anything.")
        return 3

    print()
    print(f"[migrate] OK — every legacy row is accounted for "
          f"({after_total} kept + {dups_total} in metrics_v1_dups = "
          f"{before_total}). Backup at {backup}; legacy at "
          f"metrics_legacy; dropped duplicates at metrics_v1_dups.")

    if dups_total:
        print()
        print("[migrate] Duplicate rows dropped (kept the lower-id row "
              "from each group):")
        conn = sqlite3.connect(db_path)
        try:
            for row in conn.execute(
                "SELECT id, source, metric_type, metric_period, "
                "period_type, metric_value, unit, raw_text "
                "FROM metrics_v1_dups ORDER BY id"
            ).fetchall():
                rid, src, mt, mp, pt, mv, u, rt = row
                print(f"  id={rid}  {src}  {mt}/{pt}  "
                      f"{mp}  value={mv} {u}")
                if rt:
                    print(f"     raw: {rt[:90]}")
        finally:
            conn.close()

    if args.drop_legacy:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DROP TABLE metrics_legacy")
            conn.commit()
        finally:
            conn.close()
        print("[migrate] Dropped metrics_legacy as requested.")
    else:
        print("[migrate] Tip: once you've verified the dashboard works, "
              "re-run with --drop-legacy to remove the legacy table.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
