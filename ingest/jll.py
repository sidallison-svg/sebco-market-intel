"""JLL ingestion: parse + upsert."""

from pdf_parser import _parse_jll

from ._common import parse_and_upsert


def ingest_jll(filepath: str,
               source_file_id: int | None = None,
               db_path: str | None = None) -> dict:
    return parse_and_upsert(filepath, _parse_jll,
                            source_file_id=source_file_id,
                            db_path=db_path)
