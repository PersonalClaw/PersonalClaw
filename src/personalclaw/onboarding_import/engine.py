"""Scan → select → import. The orchestration a UI or CLI drives.

Two phases on purpose, mirroring the pack importer's inspect/commit split: a scan
writes nothing to our home and reads nothing from the foreign root twice, so the
onboarding step can show counts and let the user pick categories before a single
byte lands.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
    ScanResult,
)
from personalclaw.onboarding_import.registry import get_source, list_sources
from personalclaw.onboarding_import.writers import import_report, imported_fingerprints


def scan_source(name: str, root: Path | str | None = None) -> ScanResult:
    """Scan one registered source. Never writes — to our home or theirs."""
    return get_source(name).scan(root)


def scan_all(*, roots: dict[str, Path | str] | None = None) -> list[ScanResult]:
    """Scan every registered source, resolving each root env-var-then-default.

    ``roots`` overrides a source's root by name (what a test fixture or a seeded
    dev home uses); an absent source yields ``present=False``, not an error.
    """
    overrides = roots or {}
    return [src.scan(overrides.get(src.name)) for src in list_sources()]


def detected(results: Iterable[ScanResult]) -> list[ScanResult]:
    """Only the sources actually present on this machine, with something to offer."""
    return [r for r in results if r.present and r.items]


def select_items(
    results: Iterable[ScanResult],
    *,
    categories: Iterable[ImportCategory | str] | None = None,
    sources: Iterable[str] | None = None,
) -> list[ImportItem]:
    """Flatten scan results into the items to import, honouring the user's picks.

    ``None`` means "everything" for that axis — the onboarding step passes the
    checked categories, the CLI defaults to all.
    """
    wanted_cats = (
        None
        if categories is None
        else {c if isinstance(c, ImportCategory) else ImportCategory(c) for c in categories}
    )
    wanted_sources = None if sources is None else set(sources)
    items: list[ImportItem] = []
    for result in results:
        if wanted_sources is not None and result.source not in wanted_sources:
            continue
        for item in result.items:
            if wanted_cats is not None and item.category not in wanted_cats:
                continue
            items.append(item)
    return items


def run_import(
    results: Iterable[ScanResult],
    *,
    categories: Iterable[ImportCategory | str] | None = None,
    sources: Iterable[str] | None = None,
) -> ImportReport:
    """Import the selected items from an existing scan and report every outcome."""
    scanned = list(results)
    items = select_items(scanned, categories=categories, sources=sources)
    chosen_sources = {item.source for item in items}
    secrets_skipped = sum(r.secrets_skipped for r in scanned if r.source in chosen_sources)
    return import_report(items, secrets_skipped=secrets_skipped)


def already_imported(results: Iterable[ScanResult]) -> set[str]:
    """Fingerprints in the scan that this importer has already written — what the
    step marks as ``existing`` on re-entry, without writing anything."""
    known = imported_fingerprints()
    return {item.fingerprint for r in results for item in r.items} & known
