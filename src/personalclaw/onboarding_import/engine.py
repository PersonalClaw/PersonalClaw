"""Scan → plan → pick → import. The orchestration the onboarding step drives.

Two phases on purpose, mirroring the pack importer's inspect/commit split. A scan writes
nothing to our home and reads nothing from the foreign root twice, and each item's plan
reads our home without writing — so the onboarding step can list every item, say what
importing it would do, and let the user pick item by item before a single byte lands.

**A scan can LOOK instead of read.** A months-long history is gigabytes of transcripts in
thousands of files, and reading every one before the step could show anything kept it on a
spinner for minutes (measured: 41 s for a third of one power user's history). A looking scan
reads each conversation it has not read before only as far as its first prompt — enough to list
it — and says so on the item (``provisional``) and on the result (``unread``);
:func:`read_unread` then reads those files in full, one at a time, and the next scan answers
from what it found. What was read is kept per file until the file changes
(:data:`~.sources.common.READINGS`), and only what the listing shows is kept: a conversation's
messages are read when it is imported, never held by a scan.

**The pick is a set of fingerprints and nothing else.** :func:`run_import` is handed a
scan the caller made itself and treats it as the allowlist: a chosen fingerprint that scan
does not contain imports nothing and is reported as missing. That is what lets the
fingerprints travel over HTTP while the items never do — an item carries a filesystem path
and a body, and honouring a client-supplied one would copy any directory into the home.
Beside the pick, a skill whose scan has warnings can carry the ``consent`` its scan
showed: the one thing the person accepted, which its install checks against the bytes it
installs.

**A tool is read only when it may be.** Another tool's setup is a place outside the home, so a
scan of this machine's tools reads one only when the owner pressed Look in it for this request
(``asked``, the places the press names) or turned it on in Settings
(:func:`personalclaw.outside_home.readable`). Any other comes back ``looked`` false, with
nothing of it opened, listed or checked for.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from personalclaw import outside_home
from personalclaw.onboarding_import.floors import one_walk
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
    Plan,
    ScanResult,
    WriteResult,
)
from personalclaw.onboarding_import.registry import ImportSource, get_source, list_sources
from personalclaw.onboarding_import.writers import import_report, plan_item


def _not_looked(source: ImportSource) -> ScanResult:
    """A tool this request may not read: named, with where it would be, and nothing read."""
    return ScanResult(
        source=source.name,
        display_name=source.display_name,
        root=str(source.resolve_root()),
        present=False,
        looked=False,
    )


def _scan(
    source: ImportSource, root: Path | str | None, *, look: bool, asked: Collection[str]
) -> ScanResult:
    """``source`` scanned at ``root`` when it is named, else this machine's copy of it, which
    only a press for it (``asked``) or the owner's yes in Settings lets anything read."""
    if root is None and not outside_home.readable(source.place, asked=asked):
        return _not_looked(source)
    return source.scan(root, look=look)


def scan_source(
    name: str,
    root: Path | str | None = None,
    *,
    look: bool = False,
    asked: Collection[str] = (),
) -> ScanResult:
    """Scan one registered source: the folder ``root`` names, or this machine's copy of the tool
    when ``asked`` names its setup or the owner turned it on. Never writes — to our home or
    theirs."""
    return _scan(get_source(name), root, look=look, asked=asked)


def scan_all(*, look: bool = False, asked: Collection[str] = ()) -> list[ScanResult]:
    """Scan every registered source on this machine that may be read for this request, each at
    its root resolved env-var-then-default.

    ``asked`` is the setups the owner pressed Look in for this request; the others are read only
    when turned on in Settings, and come back ``looked`` false otherwise. A tool that is not on
    this machine yields ``present=False``, not an error. To ``look`` is to read each
    conversation not read before only as far as its first prompt.
    """
    return [_scan(src, None, look=look, asked=asked) for src in list_sources()]


def unread(results: Iterable[ScanResult]) -> int:
    """How many conversation files the scan left unread: 0 means every word of it is final."""
    return sum(len(result.unread) for result in results)


def read_unread(
    results: Iterable[ScanResult],
    *,
    stop: Callable[[], bool] = lambda: False,
    on_read: Callable[[], None] | None = None,
) -> int:
    """Read in full each conversation file a looking scan left unread, one file at a time, until
    every one is read or ``stop()`` says to stop. Returns how many were read.

    What it finds is kept (:data:`~.sources.common.READINGS`), so the next scan's items and
    counts are final. It writes nothing anywhere, and holds one file's conversation at a time.
    """
    done = 0
    with one_walk():
        for result in results:
            source = get_source(result.source)
            for path in result.unread:
                if stop():
                    return done
                source.read_in_full(path)
                done += 1
                if on_read is not None:
                    on_read()
    return done


def detected(results: Iterable[ScanResult]) -> list[ScanResult]:
    """Only the sources actually present on this machine, with something to offer or a file of
    theirs that could not be read. A tool whose config is unreadable has something to say, and
    leaving it out would say nothing was found."""
    return [r for r in results if r.present and (r.items or r.unreadable_files)]


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
    accepted: Mapping[str, str] | None = None,
    on_result: Callable[[ImportItem, WriteResult], None] | None = None,
    stop_before: Callable[[ImportItem], bool] | None = None,
) -> ImportReport:
    """Import the chosen items from an existing scan and report the whole choice.

    ``accepted`` maps a skill's fingerprint to the ``consent`` of the scan warnings the person
    accepted. A skill whose scan has warnings installs only over those, and only when they are
    still what its install scans; picked without them, its row is ``rejected`` with the
    scanner's reason. An acceptance for anything else changes nothing.

    Beside each chosen item's outcome, the report carries every scanned item that was
    NOT chosen, with the plan it had BEFORE the writes — importing one tool's MCP server
    can change the plan of another tool's server of the same name, and the state the
    user chose from is the one that explains why they left it out. Chosen fingerprints
    the scan does not contain are reported as ``missing``, never dropped in silence.

    The withheld-credential count follows the choice: each chosen item's own share, plus
    whatever belongs to no item in a source something was imported from (a credential
    file at its root is left behind whatever is picked).

    Items are written one at a time, every kind before conversations: a person's setup is a few
    dozen items and their history can be thousands, so an import stopped part-way —
    ``stop_before(item)`` answering true, asked with each item before it is written — has brought
    the setup over. ``on_result`` hears each outcome as it lands. Each write is whole or absent,
    so what a stop leaves is every written item and nothing half-written; the report names the
    rest as ``not_reached``.
    """
    scanned = list(results)
    wanted = None if fingerprints is None else list(dict.fromkeys(fingerprints))
    consents = dict(accepted or {})
    items = [
        (
            replace(item, accepted_warnings=consents[item.fingerprint])
            if item.scan is not None and item.scan.needs_acceptance and item.fingerprint in consents
            else item
        )
        for item in select_items(scanned, fingerprints=wanted)
    ]
    items.sort(key=lambda item: item.category is ImportCategory.CONVERSATIONS)
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
    with one_walk():
        report = import_report(
            items, secrets_skipped=secrets_skipped, on_result=on_result, stop_before=stop_before
        )
    return replace(
        report,
        unselected=unselected,
        missing=[] if wanted is None else [fp for fp in wanted if fp not in found],
    )
