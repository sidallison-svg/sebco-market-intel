"""
Shared open-pdf + parse + upsert helper for ingest/<provider>.py.

Parsing lives in pdf_parser.py (unchanged — the parsers are validated
against real reports); this is purely the glue that opens the PDF,
hands it to a per-provider parser, and routes records through
db.upsert_metrics. upsert_metrics accepts both v1 (parser-emitted) and
v2 field names, so no manual translation is needed.
"""

import os
from typing import Any, Callable

import pdfplumber

from db import upsert_metrics
from pdf_parser import PARSER_WARNINGS


def parse_and_upsert(filepath: str,
                     parse_fn: Callable,
                     source_file_id: int | None = None,
                     db_path: str | None = None) -> dict[str, Any]:
    """Open the PDF, hand it to `parse_fn(pdf, pages, source, filepath)`,
    and upsert the returned records.

    Returns the v2 upsert result:
        {"inserted": int, "updated": int, "rejected": int,
         "rejected_ids": [int, ...]}

    Re-running this on the same source PDF UPDATES rows in place — that's
    the whole point of upsert vs insert, and what the Upload-page dup-
    check bug was silently bypassing in v1.
    """
    PARSER_WARNINGS.clear()
    source = os.path.basename(filepath)
    with pdfplumber.open(filepath) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        records = parse_fn(pdf, pages, source, filepath)
    return upsert_metrics(records, db_path=db_path,
                          source_file_id=source_file_id)
