"""Voit ingestion: parse + upsert."""

from pdf_parser import _parse_voit

from ._common import parse_and_upsert


def ingest_voit(filepath: str,
                source_file_id: int | None = None,
                db_path: str | None = None) -> dict:
    return parse_and_upsert(filepath, _parse_voit,
                            source_file_id=source_file_id,
                            db_path=db_path)
