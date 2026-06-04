"""
Ingestion layer for Sebco Market Intel.

Each provider has its own ingestion module (cbre, voit, jll, kidder) that
parses a report PDF and writes the resulting metrics through
`db.upsert_metrics`. The parsing logic still lives in pdf_parser.py
(unchanged — validated against real reports); these modules are just the
thin glue that maps parser output to the v2 schema shape and routes the
write.

Public API:
    ingest_pdf(filepath, source_file_id=None) -> dict
        Detects the provider, runs that provider's ingest function,
        returns the upsert result (plus 'provider' and 'parser_warnings').

    ingest_cbre(...) / ingest_voit(...) / ingest_jll(...) / ingest_kidder(...)
        Per-provider entry points. Same return shape as ingest_pdf().
"""

from typing import Any

import pdfplumber

from pdf_parser import _detect_provider, get_warnings

from .andover import ingest_andover
from .cbre import ingest_cbre
from .jll import ingest_jll
from .kidder import ingest_kidder
from .voit import ingest_voit


_INGESTORS = {
    "cbre": ingest_cbre,
    "voit": ingest_voit,
    "jll": ingest_jll,
    "kidder": ingest_kidder,
    "andover": ingest_andover,
}


def ingest_pdf(filepath: str,
               source_file_id: int | None = None,
               db_path: str | None = None) -> dict[str, Any]:
    """Detect provider and run that provider's ingest. Returns the
    upsert result with 'provider' and 'parser_warnings' added.

    db_path overrides config.get_db_path() — primarily for tests that
    don't want to touch the live DB."""
    with pdfplumber.open(filepath) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    provider = _detect_provider(pages)
    result = _INGESTORS[provider](filepath,
                                  source_file_id=source_file_id,
                                  db_path=db_path)
    result["provider"] = provider
    result["parser_warnings"] = get_warnings()
    return result


__all__ = [
    "ingest_pdf",
    "ingest_cbre",
    "ingest_voit",
    "ingest_jll",
    "ingest_kidder",
    "ingest_andover",
]
