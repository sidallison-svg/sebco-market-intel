"""Andover Puget Sound ingestion: parse + upsert."""

from pdf_parser import _parse_andover

from ._common import parse_and_upsert


def ingest_andover(filepath: str,
                   source_file_id: int | None = None,
                   db_path: str | None = None) -> dict:
    return parse_and_upsert(filepath, _parse_andover,
                            source_file_id=source_file_id,
                            db_path=db_path)
