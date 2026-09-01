"""Per-category writers — the only code in the import path that touches our home.

One writer per :class:`~.model.ImportCategory`, dispatched through an exhaustive
``_WRITERS`` map (an unmapped category raises rather than silently importing
nothing). Every writer obeys the same two rules:

- **The destination is the source of truth.** Before writing, the writer asks the
  destination whether this thing is already there. Identical → ``existing``.
  Present and DIFFERENT → ``conflict``: the existing thing is left byte-identical
  and the conflict is reported for review. No writer resolves a conflict by
  overwriting the user's state.
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
from personalclaw.config.loader import config_dir
from personalclaw.onboarding_import.floors import refuses
from personalclaw.onboarding_import.model import (
    ImportCategory,
    ImportItem,
    ImportReport,
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


def imported_fingerprints() -> set[str]:
    """Everything this importer has written — what the onboarding step shows as
    already-imported on re-entry."""
    return set(_load_state()["items"])


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


def _write_memory(item: ImportItem) -> WriteResult:
    """Write the redacted doc under the memory dir and add one memory record.

    The document keeps full fidelity on disk; the record is what makes it a
    *memory* (searchable through the store's own projection) rather than a loose
    file. Both are idempotent: an identical doc is a no-op, and the provider's
    append dedupes the record line.
    """
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_providers.filesystem import FilesystemMemoryProvider
    from personalclaw.memory_record import MemoryKind, MemoryRecord

    doc = _memory_doc_path(item)
    dest = _rel_to_home(doc)
    text = item.text if item.text.endswith("\n") else item.text + "\n"

    if doc.is_file():
        try:
            current = doc.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == text:
            return _result(item, WriteOutcome.EXISTING, dest, "already imported, unchanged")
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "an imported document of this name already exists with different content; "
            "the existing document was kept",
        )

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


def _write_mcp_server(item: ImportItem) -> WriteResult:
    path = mcp_config_path()
    dest = f"{_rel_to_home(path)}#mcpServers.{item.key}"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            logger.warning("unreadable %s — refusing to overwrite it", path)
            return _result(
                item,
                WriteOutcome.CONFLICT,
                dest,
                "the existing mcp.json could not be parsed; it was left untouched",
            )
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    existing = servers.get(item.key)
    if isinstance(existing, dict):
        if existing == item.payload:
            return _result(item, WriteOutcome.EXISTING, dest, "already configured identically")
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "an MCP server of this name is already configured differently; "
            "the existing entry was kept",
        )

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


def _write_skill(item: ImportItem) -> WriteResult:
    """Install a foreign skill through the shared supply-chain gate.

    Namespaced under ``imported/<source>/`` so a re-import or a removal is scoped
    and reversible, and routed through ``install_scanned`` so a foreign skill gets
    exactly the quarantine → scan → commit treatment a Store skill gets. A
    DANGEROUS verdict is ``rejected``, never force-installed.
    """
    from personalclaw.skills.marketplace import SkillInstallRefused, install_scanned

    target = imported_skills_dir(item.source)
    dest = _rel_to_home(target / item.key)
    src_dir = Path(item.path) if item.path else None
    if src_dir is None or not src_dir.is_dir():
        return _result(item, WriteOutcome.REJECTED, dest, "source skill directory is missing")
    if refuses(src_dir):
        return _result(item, WriteOutcome.REJECTED, dest, "source path is a sensitive location")

    existing = target / item.key
    if existing.exists():
        if _ours(item.fingerprint):
            return _result(item, WriteOutcome.EXISTING, dest, "already imported")
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "a skill of this name already exists here and was not written by an "
            "import; it was kept",
        )

    target.mkdir(parents=True, exist_ok=True)
    marketplace = _ImportedSkillsMarketplace(src_dir)
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


def _write_settings(item: ImportItem) -> WriteResult:
    """Stage foreign settings for human review. Never merge them into config.

    Another tool's settings keys are not ours, so an automatic merge could only
    guess. The destination for this category IS the review queue: ``imported``
    means "staged", and a differing staged file is a ``conflict`` rather than an
    overwrite.
    """
    path = staged_settings_path(item.source, item.key)
    dest = _rel_to_home(path)
    payload = json.dumps(
        {"source": item.source, "key": item.key, "settings": item.payload},
        indent=2,
        sort_keys=True,
    )
    payload += "\n"
    if path.is_file():
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == payload:
            return _result(item, WriteOutcome.EXISTING, dest, "already staged for review")
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "different settings from this source are already staged for review; "
            "the staged copy was kept",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, payload)
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest, "staged for review — not applied to config")


# ── dispatch ─────────────────────────────────────────────────────────────────

#: Exhaustive over ImportCategory on purpose (see model.ImportCategory). A new
#: category without a writer must fail loudly, not import nothing quietly.
_WRITERS: dict[ImportCategory, Callable[[ImportItem], WriteResult]] = {
    ImportCategory.INSTRUCTIONS: _write_memory,
    ImportCategory.MEMORIES: _write_memory,
    ImportCategory.MCP_SERVERS: _write_mcp_server,
    ImportCategory.SKILLS: _write_skill,
    ImportCategory.SETTINGS: _write_settings,
}


def write_item(item: ImportItem) -> WriteResult:
    try:
        writer = _WRITERS[item.category]
    except KeyError:  # pragma: no cover - guarded by test_writers_cover_every_category
        raise KeyError(f"no writer for import category {item.category!r}") from None
    return writer(item)


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
