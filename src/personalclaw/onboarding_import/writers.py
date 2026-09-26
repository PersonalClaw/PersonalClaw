"""Per-category planners and writers — the only code in the import path that touches our home.

Each :class:`~.model.ImportCategory` has a PLANNER and a WRITER, dispatched through the
exhaustive ``_PLANNERS`` / ``_WRITERS`` maps (an unmapped category raises rather than
silently importing nothing). The planner only reads; the writer only writes, and is only
reached when the planner found the destination free. Every category obeys the same rules:

- **The destination is the source of truth.** The planner asks the destination whether
  this thing is already there. Identical → ``existing``. Present and DIFFERENT →
  ``conflict``: the existing thing is left byte-identical and the conflict is reported for
  review. No writer resolves a conflict by overwriting the user's state.
- **The scan and the write read the destination through ONE function.** The onboarding
  step shows each item's :func:`plan_item` before anything is written, and
  :func:`write_item` consults the very same planner, so "this is already here" on the
  screen and ``existing`` in the report cannot come from two different checks. That is
  also why each planner's ``detail`` is worded to be true both before and after an import.
- **The import ledger answers "ours or theirs", not "is it there".**
  ``onboarding/import_state.json`` records the fingerprints WE wrote, which is how
  a skill dir we installed (``existing``) is told apart from a skill of the same
  name the user wrote themselves (``conflict``). Deriving presence from the ledger
  instead of the destination would report ``existing`` for something a user had
  since deleted.

Destinations
============

===================  ==========================================================
``instructions``     ``workspace/memory/imported/<source>/<key>.md`` + a memory
``memories``         record through the filesystem memory provider
``mcp_servers``      ``mcp.json`` → ``mcpServers`` (the user-owned override file
                     ``agent.py`` already merges at highest priority)
``skills``           ``skills/imported/<source>/<name>/`` via ``install_scanned``
                     — the same supply-chain gate as a Store skill
``settings``         ``onboarding/staged/<source>-<key>.json`` — a REVIEW QUEUE.
                     Foreign settings never reach live config, so for this
                     category ``imported`` means "staged for a human", which is
                     that category's destination.
===================  ==========================================================
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.onboarding_import.floors import refuses
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
    ItemState,
    Plan,
    WriteOutcome,
    WriteResult,
    withheld_notes,
)
from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    read_skill_file_entry,
)


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_STATE_REL = Path("onboarding") / "import_state.json"
_STAGED_REL = Path("onboarding") / "staged"
_IMPORTED_DIRNAME = "imported"
#: How much of an imported doc goes into the memory record's text. The full document
#: is written to disk; the record is the searchable one-liner that points at it.
_SUMMARY_CHARS = 220


# ── the import ledger (provenance only) ──────────────────────────────────────


def state_path() -> Path:
    return config_dir() / _STATE_REL


def _load_state() -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return {"version": 1, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("unreadable onboarding import state at %s — treating as empty", path)
        return {"version": 1, "items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"version": 1, "items": {}}
    return data


def _ours(fingerprint: str) -> bool:
    """True when THIS importer wrote the thing at that fingerprint."""
    return fingerprint in _load_state()["items"]


def _record(item: ImportItem, destination: str) -> None:
    """Record an ``imported`` outcome. Only imports are recorded: recording a
    conflict would make the next run report ``existing`` for something we never
    wrote."""
    state = _load_state()
    state["items"][item.fingerprint] = {
        "source": item.source,
        "category": item.category.value,
        "key": item.key,
        "destination": destination,
        "at": datetime.now(tz=timezone.utc).isoformat(),
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


# ── shared helpers ───────────────────────────────────────────────────────────


def _audit(operation: str, outcome: str, *, resources: str = "", error: str = "") -> None:
    """One SEL event per write (best-effort; audit never breaks an import).

    ``resources`` carries the source/category/key — never a value, so an audit log
    can't become the place a skipped secret leaks.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="onboarding.import",
            operation=operation,
            outcome=outcome,
            source="dashboard",
            resources=resources,
            error=error,
        )
    except Exception:  # pragma: no cover - audit is best-effort
        logger.debug("onboarding import SEL audit failed for %s", operation, exc_info=True)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-._")
    return cleaned or "item"


def _result(
    item: ImportItem,
    outcome: WriteOutcome,
    destination: str = "",
    detail: str = "",
) -> WriteResult:
    _audit(
        f"import.{item.category.value}",
        outcome.value,
        resources=f"{item.source}:{item.category.value}:{item.key}",
    )
    return WriteResult(
        fingerprint=item.fingerprint,
        source=item.source,
        category=item.category,
        key=item.key,
        outcome=outcome,
        destination=destination,
        detail=detail,
    )


def _rel_to_home(path: Path) -> str:
    try:
        return str(path.relative_to(config_dir()))
    except ValueError:  # pragma: no cover - a destination is always under the home
        return str(path)


# ── instructions + memories → the memory store ───────────────────────────────


def _memory_doc_path(item: ImportItem) -> Path:
    from personalclaw.memory import memory_dir

    name = _slug(item.key)
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    return memory_dir() / _IMPORTED_DIRNAME / _slug(item.source) / name


def _memory_doc_text(item: ImportItem) -> str:
    return item.text if item.text.endswith("\n") else item.text + "\n"


def _plan_memory(item: ImportItem) -> Plan:
    doc = _memory_doc_path(item)
    dest = _rel_to_home(doc)
    if doc.is_file():
        try:
            current = doc.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == _memory_doc_text(item):
            return Plan(ItemState.EXISTING, dest, "already imported, unchanged")
        return Plan(
            ItemState.CONFLICT,
            dest,
            "an imported copy with different content is already here, and it is kept",
        )
    return Plan(ItemState.NEW, dest)


def _write_memory(item: ImportItem, dest: str) -> WriteResult:
    """Write the redacted doc under the memory dir and add one memory record.

    The document keeps full fidelity on disk; the record is what makes it a
    *memory* (searchable through the store's own projection) rather than a loose
    file. Both are idempotent: an identical doc is a no-op (the planner answers
    ``existing`` before this runs), and the provider's append dedupes the record line.
    """
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_providers.filesystem import FilesystemMemoryProvider
    from personalclaw.memory_record import MemoryKind, MemoryRecord

    doc = _memory_doc_path(item)
    text = _memory_doc_text(item)

    store = MemoryStore()
    store.init()
    doc.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(doc, text)

    summary = re.sub(r"\s+", " ", item.text).strip()[:_SUMMARY_CHARS]
    FilesystemMemoryProvider(store).put(
        [
            MemoryRecord(
                id=f"import:{item.source}:{item.fingerprint}",
                kind=MemoryKind.NOTE,
                text=f"Imported from {item.source} ({item.key}): {summary}",
                source=f"onboarding_import:{item.source}",
                category=item.category.value,
            )
        ]
    )
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── mcp_servers → ~/.personalclaw/mcp.json ───────────────────────────────────


def mcp_config_path() -> Path:
    return config_dir() / "mcp.json"


def _load_mcp_config(path: Path) -> dict[str, Any] | None:
    """``mcp.json`` as a dict: ``{}`` when absent, ``None`` when present but unparseable —
    a file this importer never overwrites."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else {}


def _mcp_plan(data: dict[str, Any] | None, item: ImportItem) -> Plan:
    dest = f"{_rel_to_home(mcp_config_path())}#mcpServers.{item.key}"
    if data is None:
        return Plan(
            ItemState.CONFLICT,
            dest,
            "the existing mcp.json could not be parsed, so it is left untouched",
        )
    servers = data.get("mcpServers")
    existing = servers.get(item.key) if isinstance(servers, dict) else None
    if isinstance(existing, dict):
        if existing == item.payload:
            return Plan(ItemState.EXISTING, dest, "already configured identically")
        return Plan(
            ItemState.CONFLICT,
            dest,
            "an MCP server of this name is already configured differently, and it is kept",
        )
    return Plan(ItemState.NEW, dest)


def _plan_mcp_server(item: ImportItem) -> Plan:
    return _mcp_plan(_load_mcp_config(mcp_config_path()), item)


def _write_mcp_server(item: ImportItem, dest: str) -> WriteResult:
    path = mcp_config_path()
    data = _load_mcp_config(path)
    # A read-modify-write of a file other writers share, so the plan is re-asked of THIS
    # read: a server that appeared since the scan is kept, never overwritten.
    plan = _mcp_plan(data, item)
    if data is None or plan.state is not ItemState.NEW:
        return _result(item, WriteOutcome(plan.state.value), plan.destination, plan.detail)
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[item.key] = dict(item.payload)
    data["mcpServers"] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── skills → skills/imported/<source>/<name>/ ────────────────────────────────


def imported_skills_dir(source: str) -> Path:
    from personalclaw.skills.loader import skills_dir

    return skills_dir() / _IMPORTED_DIRNAME / _slug(source)


def _plan_skill(item: ImportItem) -> Plan:
    """Everything about a skill the destination can answer without installing it.

    The supply-chain scan is not part of the plan — it needs the quarantine copy an
    install makes — so a skill it would refuse still plans ``new``; the report then
    carries the scanner's own reason as ``rejected``.
    """
    target = imported_skills_dir(item.source) / item.key
    dest = _rel_to_home(target)
    src_dir = Path(item.path) if item.path else None
    if src_dir is None or not src_dir.is_dir():
        return Plan(ItemState.REJECTED, dest, "the source skill directory is missing")
    if refuses(src_dir):
        return Plan(ItemState.REJECTED, dest, "the source path is a sensitive location")
    if target.exists():
        if _ours(item.fingerprint):
            return Plan(ItemState.EXISTING, dest, "already imported")
        return Plan(
            ItemState.CONFLICT,
            dest,
            "a skill of this name that no import wrote is already here, and it is kept",
        )
    return Plan(ItemState.NEW, dest)


def _write_skill(item: ImportItem, dest: str) -> WriteResult:
    """Install a foreign skill through the shared supply-chain gate.

    Namespaced under ``imported/<source>/`` so a re-import or a removal is scoped
    and reversible, and routed through ``install_scanned`` so a foreign skill gets
    exactly the quarantine → scan → commit treatment a Store skill gets. A
    DANGEROUS verdict is ``rejected``, never force-installed.
    """
    from personalclaw.skills.marketplace import SkillInstallRefused, install_scanned

    target = imported_skills_dir(item.source)
    target.mkdir(parents=True, exist_ok=True)
    marketplace = _ImportedSkillsMarketplace(Path(item.path))
    try:
        install_scanned(marketplace, f"import:{item.source}", item.key, target, force=False)
    except SkillInstallRefused as exc:
        return _result(
            item,
            WriteOutcome.REJECTED,
            dest,
            f"the skill supply-chain scan refused this skill: {exc}",
        )
    except (ValueError, OSError) as exc:
        return _result(item, WriteOutcome.REJECTED, dest, f"could not install: {exc}")
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


def _imported_skills_marketplace_files(skill_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        # The same floor the scanner counted with: a credential file inside a
        # foreign skill is never staged, scanned or installed.
        if refuses(path):
            continue
        try:
            files.append(read_skill_file_entry(path, path.relative_to(skill_dir).as_posix()))
        except OSError:
            logger.warning("skipping unreadable file in imported skill %s", skill_dir.name)
    return files


class _ImportedSkillsMarketplace(SkillsMarketplace):
    """A transient, single-directory skills source rooted at a foreign skill dir.

    Not registered in the shared registry — another tool's skills dir is not a
    marketplace. It exists only so an imported skill flows through the exact same
    :func:`install_scanned` gate (quarantine → scan → commit → lock) as any other
    install, at the ``community`` trust tier (foreign, unsigned content).
    """

    def __init__(self, skill_dir: Path) -> None:
        self._skill_dir = Path(skill_dir)

    @property
    def marketplace_type(self) -> str:
        return "onboarding_import"

    @property
    def trust_tier(self) -> str:
        return "community"

    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:  # pragma: no cover
        return []

    def fetch(self, skill_id: str) -> SkillDetail:
        return SkillDetail(
            id=skill_id,
            name=self._skill_dir.name,
            files=_imported_skills_marketplace_files(self._skill_dir),
            audit_status="pass",
        )


# ── settings → the review queue (never live config) ──────────────────────────


def staged_settings_path(source: str, key: str) -> Path:
    return config_dir() / _STAGED_REL / f"{_slug(source)}-{_slug(key)}.json"


def _staged_settings_text(item: ImportItem) -> str:
    return (
        json.dumps(
            {"source": item.source, "key": item.key, "settings": item.payload},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _plan_settings(item: ImportItem) -> Plan:
    path = staged_settings_path(item.source, item.key)
    dest = _rel_to_home(path)
    if path.is_file():
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == _staged_settings_text(item):
            return Plan(ItemState.EXISTING, dest, "already staged for review")
        return Plan(
            ItemState.CONFLICT,
            dest,
            "different settings from this source are already staged for review, "
            "and they are kept",
        )
    return Plan(ItemState.NEW, dest)


def _write_settings(item: ImportItem, dest: str) -> WriteResult:
    """Stage foreign settings for human review. Never merge them into config.

    Another tool's settings keys are not ours, so an automatic merge could only
    guess. The destination for this category IS the review queue: ``imported``
    means "staged", and a differing staged file is a ``conflict`` rather than an
    overwrite.
    """
    path = staged_settings_path(item.source, item.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, _staged_settings_text(item))
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest, "staged for review — not applied to config")


# ── dispatch ─────────────────────────────────────────────────────────────────

#: Both maps are exhaustive over ImportCategory on purpose (see model.ImportCategory).
#: A new category without a planner or a writer must fail loudly, not import nothing
#: quietly. A writer is only ever called with the destination its planner found free.
_PLANNERS: dict[ImportCategory, Callable[[ImportItem], Plan]] = {
    ImportCategory.INSTRUCTIONS: _plan_memory,
    ImportCategory.MEMORIES: _plan_memory,
    ImportCategory.MCP_SERVERS: _plan_mcp_server,
    ImportCategory.SKILLS: _plan_skill,
    ImportCategory.SETTINGS: _plan_settings,
}
_WRITERS: dict[ImportCategory, Callable[[ImportItem, str], WriteResult]] = {
    ImportCategory.INSTRUCTIONS: _write_memory,
    ImportCategory.MEMORIES: _write_memory,
    ImportCategory.MCP_SERVERS: _write_mcp_server,
    ImportCategory.SKILLS: _write_skill,
    ImportCategory.SETTINGS: _write_settings,
}


def plan_item(item: ImportItem) -> Plan:
    """What importing ``item`` would do right now. Reads our home and writes nothing —
    not a file, not an audit line — so a scan can ask it of every item it shows."""
    try:
        planner = _PLANNERS[item.category]
    except KeyError:  # pragma: no cover - guarded by test_a_writer_exists_for_every_category
        raise KeyError(f"no planner for import category {item.category!r}") from None
    return planner(item)


def write_item(item: ImportItem) -> WriteResult:
    """Import one item: ask its plan, and write only when the destination is free."""
    plan = plan_item(item)
    if plan.state is not ItemState.NEW:
        return _result(item, WriteOutcome(plan.state.value), plan.destination, plan.detail)
    return _WRITERS[item.category](item, plan.destination)


def write_items(items: list[ImportItem]) -> list[WriteResult]:
    return [write_item(item) for item in items]


def import_report(items: list[ImportItem], *, secrets_skipped: int = 0) -> ImportReport:
    """Write every item and report outcomes plus what was withheld."""
    results = write_items(items)
    redactions = sum(item.redactions for item in items)
    report = ImportReport(results=results, secrets_skipped=secrets_skipped, redactions=redactions)
    # 🔑 The SECOND producer of these two sentences used to live here, word for word. `ImportReport`
    # and `ScanResult` carry the same three fields, so both now read one composer — see
    # `model.withheld_notes` for why the plurals and the verb both have to agree.
    report.notes.extend(withheld_notes(secrets_skipped=secrets_skipped, redactions=redactions))
    return report
