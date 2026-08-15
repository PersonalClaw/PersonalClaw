"""Onboarding import — adopt another local agent tool's config, once, safely.

A user arriving from another local agent tool has already written the instructions,
MCP servers and skills they care about. Making them redo that work is the real
switching cost, so this package reads those files and offers to bring them across.

Three properties define it, and each has a test that fails when it breaks:

- **Counted-and-skipped secrets.** A credential is never imported and never
  logged — but the user is told a count, so they learn something was withheld
  rather than silently losing it (:mod:`.floors`).
- **Idempotence.** Item identity is ``sha256(source\\0category\\0key)``; a re-scan
  yields the same set and a re-import writes nothing new (:mod:`.model`,
  :mod:`.writers`).
- **Never clobber.** A destination that already holds something different reports
  ``conflict`` and keeps what's there (:mod:`.writers`).

Reading the foreign root is strictly read-only: importing from another tool must
never modify that tool's configuration.

The scan phase is pure (a directory in, a :class:`~.model.ScanResult` out) so it is
fixture-testable with no store, session or home involved; only :mod:`.writers`
touches our home.
"""

from __future__ import annotations

from personalclaw.onboarding_import.engine import (
    already_imported,
    detected,
    run_import,
    scan_all,
    scan_source,
    select_items,
)
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
    ScanResult,
    WriteOutcome,
    WriteResult,
    fingerprint_of,
)
from personalclaw.onboarding_import.registry import ImportSource, get_source, list_sources

__all__ = [
    "ImportCategory",
    "ImportItem",
    "ImportReport",
    "ImportSource",
    "ScanResult",
    "WriteOutcome",
    "WriteResult",
    "already_imported",
    "detected",
    "fingerprint_of",
    "get_source",
    "list_sources",
    "run_import",
    "scan_all",
    "scan_source",
    "select_items",
]
