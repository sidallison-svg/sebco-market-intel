"""
Compat shim — historically the data layer lived here. After the v2 schema
rebuild it moved to db.py; this module just re-exports the same names so
the dashboard's `from database import ...` calls keep working without a
frontend change.

New code should import from db directly.
"""

from db import (  # noqa: F401
    # status constants
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    STATUS_REJECTED,
    # schema / init
    init_db,
    # writes
    insert_metrics,
    upsert_metrics,
    update_metric,
    # uploaded_files
    get_file_by_hash,
    count_metrics_for_file,
    clear_orphan_upload,
    find_active_report,
    record_uploaded_file,
    supersede_file,
    get_upload_history,
    delete_uploaded_file_row,
    # metrics queries
    check_source_exists,
    delete_by_source,
    get_upload_summaries,
    get_metrics_for_source,
    get_all_metrics,
    get_distinct_values,
    # rejected_records
    get_rejected_records,
    delete_rejected_record,
    # saved_views
    save_view,
    get_saved_views,
    delete_saved_view,
)
