"""Upload subsystem: one filetype-keyed size policy + the resumable transfer store.

Every upload surface (chat attach, Files browser, Knowledge ingest, workspace)
routes its size gate through :mod:`personalclaw.uploads.policy` so per-filetype
limits are consistent and centrally tunable, and its bytes through the resumable
protocol in :mod:`personalclaw.uploads.store` for anything above the single-POST
threshold.

This ``__init__`` imports nothing: the content scan's child (:mod:`personalclaw.uploads.scan_child`)
sits in this folder, and every scan waits for what its start imports.
"""
