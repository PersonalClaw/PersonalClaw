"""Upload subsystem: one filetype-keyed size policy + the resumable transfer store.

Every upload surface (chat attach, Files browser, Knowledge ingest, workspace)
routes its size gate through :mod:`personalclaw.uploads.policy` so per-filetype
limits are consistent and centrally tunable, and its bytes through the resumable
protocol in :mod:`personalclaw.uploads.store` for anything above the single-POST
threshold.
"""

from personalclaw.uploads.policy import (
    UPLOAD_CATEGORIES,
    UploadCheck,
    category_for,
    check_upload,
    limit_for_category,
    limits_table,
    single_post_threshold,
)

__all__ = [
    "UPLOAD_CATEGORIES",
    "UploadCheck",
    "category_for",
    "check_upload",
    "limit_for_category",
    "limits_table",
    "single_post_threshold",
]
