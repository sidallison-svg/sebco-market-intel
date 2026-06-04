"""Kidder Mathews ingestion: parse + upsert.

Kidder reports use four different layout strategies (structured tables,
dual breakdowns, submarket statistics, narrative). The dispatch sits in
pdf_parser._parse_kidder; this module wraps it with the schema-mapping
detail that lives in the ingest layer.

Schema mapping (this is the kind of provider-specific normalization the
ingest layer exists for):
  * Kidder always reports rents NNN, but the parser doesn't emit
    lease_type. CBRE/Voit/JLL parsers set it explicitly (NNN / industrial_
    gross / NNN). We default Kidder's asking_rent records to lease_type=
    'NNN' here so they UPSERT against existing rows correctly instead of
    inserting duplicates with an empty lease_type.
"""

import os

import pdfplumber

from db import upsert_metrics
from pdf_parser import PARSER_WARNINGS, _parse_kidder


def _apply_lease_type_default(records: list[dict]) -> list[dict]:
    """Set lease_type='NNN' on Kidder asking_rent records that don't have
    one set yet. All Kidder reports are NNN; the parser just doesn't
    bother emitting the field. v1 used to backfill this in init_db."""
    for r in records:
        if (r.get("metric_type") == "asking_rent"
                and not r.get("lease_type")):
            r["lease_type"] = "NNN"
    return records


def ingest_kidder(filepath: str,
                  source_file_id: int | None = None,
                  db_path: str | None = None) -> dict:
    PARSER_WARNINGS.clear()
    source = os.path.basename(filepath)
    with pdfplumber.open(filepath) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
        records = _parse_kidder(pdf, pages, source, filepath)
    records = _apply_lease_type_default(records)
    return upsert_metrics(records, db_path=db_path,
                          source_file_id=source_file_id)
