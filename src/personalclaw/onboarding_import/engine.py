"""Scan → plan → pick → import. The orchestration the onboarding step drives.

Two phases on purpose, mirroring the pack importer's inspect/commit split. A scan writes
nothing to our home and reads nothing from the foreign root twice, and each item's plan
reads our home without writing — so the onboarding step can list every item, say what
importing it would do, and let the user pick item by item before a single byte lands.

**The pick is a set of fingerprints and nothing else.** :func:`run_import` is handed a
scan the caller made itself and treats it as the allowlist: a chosen fingerprint that scan
does not contain imports nothing and is reported as missing. That is what lets the
fingerprints travel over HTTP while the items never do — an item carries a filesystem path
and a body, and honouring a client-supplied one would copy any directory into the home.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from personalclaw.onboarding_import.model import (
    ImportItem,
    ImportReport,
    Plan,
    ScanResult,
)
from personalclaw.onboarding_import.registry import get_source, list_sources
from personalclaw.onboarding_import.writers import import_report, plan_item


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


def plans(results: Iterable[ScanResult]) -> dict[str, Plan]:
    """What importing each scanned item would do right now, keyed by fingerprint.

    The same planner :func:`~.writers.write_item` consults, so the state the step shows
    beside an item is the one its import will act on. Reads our home; writes nothing.
    """
    return {item.fingerprint: plan_item(item) for result in results for item in result.items}


def select_items(
    results: Iterable[ScanResult],
    *,
    fingerprints: Iterable[str] | None = None,
) -> list[ImportItem]:
    """The scanned items whose fingerprints were chosen, in scan order.

    ``None`` chooses every item — a programmatic caller importing everything. The HTTP
    route never passes it: a request must name what it wants.
    """
    wanted = None if fingerprints is None else set(fingerprints)
    return [
        item
        for result in results
        for item in result.items
        if wanted is None or item.fingerprint in wanted
    ]


def run_import(
    results: Iterable[ScanResult],
    *,
    fingerprints: Iterable[str] | None = None,
) -> ImportReport:
    """Import the chosen items from an existing scan and report the whole choice.

    Beside each chosen item's outcome, the report carries every scanned item that was
    NOT chosen, with the plan it had BEFORE the writes — importing one tool's MCP server
    can change the plan of another tool's server of the same name, and the state the
    user chose from is the one that explains why they left it out. Chosen fingerprints
    the scan does not contain are reported as ``missing``, never dropped in silence.

    The withheld-credential count follows the choice: each chosen item's own share, plus
    whatever belongs to no item in a source something was imported from (a credential
    file at its root is left behind whatever is picked).
    """
    scanned = list(results)
    wanted = None if fingerprints is None else list(dict.fromkeys(fingerprints))
    items = select_items(scanned, fingerprints=wanted)
    chosen = {item.fingerprint for item in items}
    found = {item.fingerprint for result in scanned for item in result.items}
    unselected = [
        (item, plan_item(item))
        for result in scanned
        for item in result.items
        if item.fingerprint not in chosen
    ]
    sources = {item.source for item in items}
    secrets_skipped = sum(
        result.secrets_outside_items() for result in scanned if result.source in sources
    ) + sum(item.secrets_skipped for item in items)
    report = import_report(items, secrets_skipped=secrets_skipped)
    return replace(
        report,
        unselected=unselected,
        missing=[] if wanted is None else [fp for fp in wanted if fp not in found],
    )
