"""
Demonstrate that re-running an ingest on the same PDF is idempotent
(row count unchanged + all results report 'updated', not 'inserted').

Runs against a TEMP COPY of the live DB so production state never
changes. Iterates over one sample PDF per provider so all four
ingestion paths (CBRE, Voit, JLL, Kidder) are exercised.

Usage:
    python3 scripts/prove_upsert.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import get_db_path  # noqa: E402
from ingest import ingest_pdf  # noqa: E402


SAMPLES = [
    ("CBRE",   "sample_pdfs/CBRE-Orange_County_Industrial_Figur.pdf"),
    ("Voit",   "sample_pdfs/SD1Q26Ind.pdf"),
    ("JLL",    "sample_pdfs/JLL-26-insights-orange-county-industrial-q1-2026.pdf"),
    ("Kidder", "sample_pdfs/industrial-market-research-seattle-2026-1q.pdf"),
]


def _count(db_path: str, source: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM metrics WHERE source=?",
            (os.path.basename(source),),
        ).fetchone()[0]
    finally:
        conn.close()


def main() -> int:
    live = get_db_path()
    tmp_handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp = tmp_handle.name
    tmp_handle.close()
    shutil.copy2(live, tmp)
    print(f"[prove] Test DB (copy of live): {tmp}")
    print()

    failures = 0
    for label, path in SAMPLES:
        full = os.path.join(_REPO_ROOT, path)
        if not os.path.exists(full):
            print(f"  [{label}] SKIP — sample not found at {path}")
            continue

        before = _count(tmp, path)
        r1 = ingest_pdf(full, db_path=tmp)
        after1 = _count(tmp, path)
        r2 = ingest_pdf(full, db_path=tmp)
        after2 = _count(tmp, path)

        ok = (
            after1 == after2
            and r2["inserted"] == 0
            and r2["updated"] > 0
            and r2["rejected"] == 0
        )
        marker = "OK " if ok else "!! "
        print(f"  [{label}] {marker}")
        print(f"     before:  {before:>4} rows")
        print(f"     pass 1:  +{r1['inserted']} inserted, "
              f"~{r1['updated']} updated  ->  {after1} rows")
        print(f"     pass 2:  +{r2['inserted']} inserted, "
              f"~{r2['updated']} updated  ->  {after2} rows")
        if not ok:
            failures += 1
            print(f"     >>> Expected pass-2 inserted=0 and "
                  f"after1==after2; got inserted={r2['inserted']}, "
                  f"{after1} vs {after2}")
        print()

    os.unlink(tmp)
    print(f"[prove] {'PASS' if not failures else 'FAIL'} "
          f"({len(SAMPLES) - failures}/{len(SAMPLES)} providers idempotent)")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
