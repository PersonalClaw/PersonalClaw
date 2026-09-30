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
  since deleted. A conversation has no entry: its transcript's metadata names the
  tool and session it came from, so it answers "ours or theirs" itself — and a
  history of thousands of them stays out of a file rewritten whole on every write.

Destinations
============

===================  ==========================================================
``instructions``     ``workspace/memory/instructions/<source>/<key>.md``, whole:
                     every conversation carries it (``standing_instructions``),
                     a project's own file only a conversation in that folder
``memories``         memories in your memory store (``memory.db``), all of the
                     note's text, which recall searches and the re-index embeds;
                     plus the note as a file, ``workspace/memory/imported/…``
``mcp_servers``      ``mcp.json`` → ``mcpServers`` (the user-owned override file
                     ``agent.py`` already merges at highest priority)
``skills``           ``skills/imported/<source>/<name>/`` via ``install_scanned``
                     — the same supply-chain gate as a Store skill
``agents``           ``config.json`` → ``agents.<name>``: the profile the Agents
                     page lists, as ``POST /api/agents`` would create it
``prompts``          ``prompts/<name>.yaml``: a prompt you run as ``@name``
``conversations``    ``sessions/dashboard_<source>-<id>.jsonl``: a chat in your
                     history, readable and resumable
``denied_commands``  ``config.json`` → ``security.denied_commands``: a pattern the
                     shell denylist refuses (Settings › Security). Only ever a
                     command the other tool refused to run: an import tightens it
===================  ==========================================================

Another tool's own options have no destination: they are not PersonalClaw's, so a scan names
them under "Not brought over" and no writer exists for them.
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


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

_STATE_REL = Path("onboarding") / "import_state.json"
_IMPORTED_DIRNAME = "imported"
#: How much an imported memory is trusted to matter, beside what conversations teach: the value
#: memory's own migration gives the notes it brings over from the older markdown files.
_MEMORY_IMPORTANCE = 0.6


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


def _record(item: ImportItem, destination: str, **provenance: str) -> None:
    """Record an ``imported`` outcome. Only imports are recorded: recording a
    conflict would make the next run report ``existing`` for something we never
    wrote.

    ``provenance`` is what a reader of the destination needs to say where the thing came from —
    value-free words, like the item's own ``title`` and ``origin``. An empty one is left out."""
    state = _load_state()
    state["items"][item.fingerprint] = {
        "source": item.source,
        "category": item.category.value,
        "key": item.key,
        "destination": destination,
        "at": datetime.now(tz=timezone.utc).isoformat(),
        **{name: value for name, value in provenance.items() if value},
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def imported_items(category: ImportCategory) -> list[dict[str, Any]]:
    """What this importer wrote of ``category``, as its ledger records each one.

    The ledger answers "ours or theirs", so this is the list of things the owner's import put
    there — not proof that they are still there, which only the destination can say.
    """
    return [
        dict(entry)
        for entry in _load_state()["items"].values()
        if isinstance(entry, dict) and entry.get("category") == category.value
    ]


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
    *,
    redactions: int | None = None,
) -> WriteResult:
    """One item's outcome, audited. ``redactions`` is what the text that landed had redacted: the
    item's own count unless the writer read the text itself (a conversation), and 0 for anything
    that did not land."""
    _audit(
        f"import.{item.category.value}",
        outcome.value,
        resources=f"{item.source}:{item.category.value}:{item.key}",
    )
    landed = outcome is WriteOutcome.IMPORTED
    return WriteResult(
        fingerprint=item.fingerprint,
        source=item.source,
        category=item.category,
        key=item.key,
        outcome=outcome,
        destination=destination,
        detail=detail,
        redactions=(item.redactions if redactions is None else redactions) if landed else 0,
    )


def _rel_to_home(path: Path) -> str:
    try:
        return str(path.relative_to(config_dir()))
    except ValueError:  # pragma: no cover - a destination is always under the home
        return str(path)


# ── instructions → your standing instructions; memories → your memory store ──


def _doc_name(item: ImportItem) -> str:
    name = _slug(item.key)
    return name if name.lower().endswith(".md") else f"{name}.md"


def _instruction_doc_path(item: ImportItem) -> Path:
    from personalclaw.standing_instructions import instructions_dir

    return instructions_dir() / _slug(item.source) / _doc_name(item)


def _memory_doc_path(item: ImportItem) -> Path:
    from personalclaw.memory import memory_dir

    return memory_dir() / _IMPORTED_DIRNAME / _slug(item.source) / _doc_name(item)


def _doc_text(item: ImportItem) -> str:
    return item.text if item.text.endswith("\n") else item.text + "\n"


def _plan_document(item: ImportItem, doc: Path) -> Plan:
    dest = _rel_to_home(doc)
    if doc.is_file():
        try:
            current = doc.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == _doc_text(item):
            return Plan(ItemState.EXISTING, dest, "already imported, unchanged")
        return Plan(
            ItemState.CONFLICT,
            dest,
            "an imported copy with different content is already here, and it is kept",
        )
    return Plan(ItemState.NEW, dest)


def _plan_instruction(item: ImportItem) -> Plan:
    return _plan_document(item, _instruction_doc_path(item))


def _plan_memory(item: ImportItem) -> Plan:
    return _plan_document(item, _memory_doc_path(item))


def _write_instruction(item: ImportItem, dest: str) -> WriteResult:
    """Write the redacted instruction file whole, where every conversation reads it.

    The copy is the file itself, byte for byte as the scan redacted it — never a summary of it:
    ``standing_instructions`` carries it into each conversation whole, within the room the model
    has. What the reader needs to say where it came from is the ledger's: the tool, the file's name
    in it, and the folder a project's own file applies to (``workspace``, the project directory on
    this machine). A file only this importer wrote is followed, so the ledger is also what keeps a
    file dropped into the folder by something else out of the prompt.
    """
    doc = _instruction_doc_path(item)
    doc.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(doc, _doc_text(item))
    _record(
        item,
        dest,
        title=item.title,
        origin=item.origin,
        workspace=str(item.payload.get("workspace") or ""),
    )
    return _result(item, WriteOutcome.IMPORTED, dest)


def _memory_parts(body: str, room: int) -> list[str]:
    """``body`` as pieces of at most ``room`` characters, split where the text breaks.

    Paragraphs are kept together while they fit, then lines; only a single line longer than a
    whole piece is cut, and nothing of the text is dropped.
    """
    room = max(room, 1)
    parts: list[str] = []
    current = ""

    def add(piece: str, joiner: str) -> None:
        nonlocal current
        candidate = f"{current}{joiner}{piece}" if current else piece
        if len(candidate) <= room:
            current = candidate
            return
        if current:
            parts.append(current)
        while len(piece) > room:
            parts.append(piece[:room])
            piece = piece[room:]
        current = piece

    for paragraph in re.split(r"\n\s*\n", body.strip()):
        if len(paragraph) <= room:
            add(paragraph, "\n\n")
            continue
        for index, line in enumerate(paragraph.splitlines()):
            add(line, "\n\n" if index == 0 else "\n")
    if current:
        parts.append(current)
    return [part.strip() for part in parts if part.strip()]


def _memory_store() -> tuple[Any, bool]:
    """The memory store recall reads, and whether this call opened it (and so closes it).

    In the gateway that is the store it serves recall from: a second copy of it would write
    memories whose vectors the served index never learns of. Anywhere else, the home's own.
    """
    from personalclaw.vector_memory import VectorMemoryStore, recall_store

    store = VectorMemoryStore()
    served = recall_store(store.db_path)
    if served is not None:
        return served, False
    store.init()
    return store, True


#: How much of a note's title leads each of its memories. Short enough that a part number after it
#: falls inside the opening the store dedupes memories by (``write_episodic`` refuses a memory whose
#: first 80 characters match one it holds), so the parts of one long note never read as repeats.
_MEMORY_TITLE_CHARS = 60
#: Room kept in each memory for its part number, `` (12 of 12)``.
_PART_MARK_CHARS = 16
#: How much of where a note came from (``Project · ~/src/app``) each of its memories repeats.
_MEMORY_ORIGIN_CHARS = 200


def _remember(item: ImportItem) -> None:
    """Keep an imported memory as memories of your own: all of its text, where recall looks.

    Each memory names what it is and where it came from, so a recalled one says so. A note longer
    than one memory holds is kept as several, split where its text breaks and numbered. Written
    with the model bound for embedding when there is one; without one it is read by keyword until
    the re-index embeds it, like any memory written then.
    """
    from personalclaw.onboarding_import.registry import get_source
    from personalclaw.skills.loader import SkillsLoader
    from personalclaw.vector_memory import EPISODIC_TEXT_MAX

    title = (item.title or item.key)[:_MEMORY_TITLE_CHARS]
    where = f" ({item.origin[:_MEMORY_ORIGIN_CHARS]})" if item.origin else ""
    tail = f" — from {get_source(item.source).display_name}'s memory{where}"
    body = SkillsLoader.strip_frontmatter(item.text).strip()
    room = EPISODIC_TEXT_MAX - len(title) - _PART_MARK_CHARS - len(tail) - 1
    parts = _memory_parts(body, room)
    store, opened = _memory_store()
    try:
        for number, part in enumerate(parts, start=1):
            mark = f" ({number} of {len(parts)})" if len(parts) > 1 else ""
            store.write_episodic(
                f"{title}{mark}{tail}\n{part}",
                tags=["imported", item.source],
                importance=_MEMORY_IMPORTANCE,
                source=f"onboarding_import:{item.source}",
            )
    finally:
        if opened:
            store.close()


def _write_memory(item: ImportItem, dest: str) -> WriteResult:
    """Add the note to your memories, whole, and keep the redacted file it came from.

    The memories are what recall searches and the re-index embeds. The file is the note as the
    other tool held it, and the planner's answer to "is it already here". The memories are
    written first: a store that refuses them rejects the item with nothing written, so importing
    it again tries again, rather than leaving a file that reads as imported while recall finds
    nothing. A memory the store already holds in other words is not written twice.
    """
    from personalclaw.sqlite_compat import sqlite3

    try:
        _remember(item)
    except (sqlite3.Error, OSError) as exc:
        return _result(
            item, WriteOutcome.REJECTED, dest, f"your memory store could not take it: {exc}"
        )
    doc = _memory_doc_path(item)
    doc.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(doc, _doc_text(item))
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
    name = item.target
    dest = f"{_rel_to_home(mcp_config_path())}#mcpServers.{name}"
    if data is None:
        return Plan(
            ItemState.CONFLICT,
            dest,
            "the existing mcp.json could not be parsed, so it is left untouched",
        )
    servers = data.get("mcpServers")
    existing = servers.get(name) if isinstance(servers, dict) else None
    if isinstance(existing, dict):
        # Compared in LOGICAL form: the file holds the server's values as credential-store
        # references, so the stored spec never equals the scanned one byte for byte. A spec
        # naming a credential its owner does not hold cannot be read to compare, so it is
        # "configured differently" — and kept, like any other.
        #
        # And only the keys that say how the server starts (`MCP_DEFINITION_KEYS`) are compared.
        # The rest are what the owner did with it here — signed in to it, turned it or a tool
        # off, approved its calls — which the other tool's copy never holds, so comparing them
        # read every server she had used as a different one.
        from personalclaw.config.secret_refs import (
            MCP_DEFINITION_KEYS,
            ForeignSecretReference,
            resolve_mcp_spec,
        )

        def definition(spec: dict[str, Any]) -> dict[str, Any]:
            return {
                k: v for k, v in resolve_mcp_spec(name, spec).items() if k in MCP_DEFINITION_KEYS
            }

        try:
            same = definition(existing) == definition(item.payload)
        except ForeignSecretReference:
            same = False
        if same:
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
    servers[item.target] = dict(item.payload)
    data["mcpServers"] = servers
    # The MCP document writer, Tools › Import's too: every `env` and `headers` value reaches the
    # file as a credential-store reference (`config.secret_refs`), whatever its name — a
    # `DATABASE_URL` with a password in it is as secret as an `API_KEY`.
    from personalclaw.config.secret_refs import write_mcp_document

    write_mcp_document(path, data)
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── skills → skills/imported/<source>/<name>/ ────────────────────────────────


def imported_skills_dir(source: str) -> Path:
    from personalclaw.skills.loader import skills_dir

    return skills_dir() / _IMPORTED_DIRNAME / _slug(source)


def _refusing_rules(item: ImportItem) -> str:
    """The rules that make a skill's scan dangerous, for the sentence that says so."""
    findings = item.scan.findings if item.scan is not None else ()
    rules = sorted({f["rule"] for f in findings if f["severity"] == "dangerous"})
    return ", ".join(rules) or "no specific rule"


def _plan_skill(item: ImportItem) -> Plan:
    """Everything about a skill the destination and its scan can answer without installing it.

    The scan is the one the import's install will make, made when the tool was scanned: a
    dangerous verdict is refused here, before anything is chosen, with the rules that refuse
    it. A skill whose scan has warnings plans ``new``; it comes over only with them accepted,
    and without that its row is ``rejected`` with the scanner's reason.
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
    if item.scan is not None and item.scan.verdict == "dangerous":
        return Plan(
            ItemState.REJECTED,
            dest,
            f"the skill supply-chain scan refuses it as dangerous: {_refusing_rules(item)}",
        )
    return Plan(ItemState.NEW, dest)


def _write_skill(item: ImportItem, dest: str) -> WriteResult:
    """Install a foreign skill through the shared supply-chain gate.

    Namespaced under ``imported/<source>/`` so a re-import or a removal is scoped and
    reversible, and routed through ``install_scanned`` so a foreign skill gets exactly the
    quarantine → scan → commit treatment a Store skill gets. A DANGEROUS verdict is
    ``rejected``, never installed. A WARNING verdict installs only over the warnings the person
    accepted (``accepted_warnings``): the gate compares their digest with the scan it makes of
    the bytes it installs, and records the acceptance in the SEL.
    """
    from personalclaw.onboarding_import.sources.common import ImportedSkillMarketplace
    from personalclaw.skills.marketplace import SkillInstallRefused, install_scanned

    target = imported_skills_dir(item.source)
    target.mkdir(parents=True, exist_ok=True)
    marketplace = ImportedSkillMarketplace(Path(item.path))
    try:
        install_scanned(
            marketplace,
            f"import:{item.source}",
            item.key,
            target,
            accepted_warnings=item.accepted_warnings or None,
        )
    except SkillInstallRefused as exc:
        band = "dangerous" if exc.dangerous else "warning"
        rules = ", ".join(sorted({f.rule for f in exc.report.findings if f.severity.value == band}))
        if exc.dangerous:
            detail = f"the skill supply-chain scan refuses it as dangerous: {rules}"
        elif item.accepted_warnings:
            detail = (
                "its security scan finds warnings other than the ones you accepted, so it was "
                f"not installed: {rules}. Scan again to read them"
            )
        else:
            detail = (
                f"its security scan found warnings, and it comes over only if you accept them: "
                f"{rules}"
            )
        return _result(item, WriteOutcome.REJECTED, dest, detail)
    except (ValueError, OSError) as exc:
        return _result(item, WriteOutcome.REJECTED, dest, f"could not install: {exc}")
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── agents → config.json agents (the profiles the Agents page lists) ─────────

#: The agent-name rule every path that introduces a profile applies (``POST /api/agents``, the
#: file-store sync, ``AgentDefinition.validate``): lowercase letters, digits and dashes.
_AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _agent_named(name: str, agents: dict[str, Any]) -> str | None:
    """The profile ``name`` resolves to, case-insensitively — how every agent route matches one."""
    lowered = name.lower()
    return next((existing for existing in agents if existing.lower() == lowered), None)


def _plan_agent(item: ImportItem) -> Plan:
    from personalclaw.agents.defaults import RETIRED_AGENT_NAMES, is_reserved_agent
    from personalclaw.config.loader import AppConfig

    name = item.target
    dest = f"config.json#agents.{name}"
    if not _AGENT_NAME_RE.fullmatch(name):
        return Plan(
            ItemState.REJECTED,
            dest,
            "its name has no letters or digits PersonalClaw can name an agent with",
        )
    if name in {retired.lower() for retired in RETIRED_AGENT_NAMES}:
        return Plan(ItemState.REJECTED, dest, "its name is a retired PersonalClaw agent name")
    if is_reserved_agent(name):
        return Plan(
            ItemState.CONFLICT,
            dest,
            "a built-in PersonalClaw agent has this name, and it is kept",
        )
    if _agent_named(name, AppConfig.load().agents) is not None:
        if _ours(item.fingerprint):
            return Plan(ItemState.EXISTING, dest, "already imported")
        return Plan(
            ItemState.CONFLICT, dest, "an agent of this name is already here, and it is kept"
        )
    return Plan(ItemState.NEW, dest)


def _write_agent(item: ImportItem, dest: str) -> WriteResult:
    """Add the agent as a profile, exactly as ``POST /api/agents`` would with these two fields.

    Only its description and instructions come over (the scan's note says what does not), so
    every other field — the approval mode above all — is the profile default, as for any agent
    created on the Agents page. ``source`` is stamped ``local``, as the file-store sync stamps it:
    the profile is yours now.
    """
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig.load()
    # A read-modify-write of the whole config: the plan is re-asked of THIS read, so an agent
    # that appeared since the scan is kept, never overwritten.
    if _agent_named(item.target, cfg.agents) is not None:
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "an agent of this name is already here, and it is kept",
        )
    cfg.agents[item.target] = AgentProfile(
        description=str(item.payload.get("description") or ""),
        system_prompt=item.text,
        source="local",
    )
    cfg.save()
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── prompts → prompts/<name>.yaml (run in chat as @name) ─────────────────────


def _plan_prompt(item: ImportItem) -> Plan:
    from personalclaw.prompt_providers.native_provider import prompt_file

    try:
        path = prompt_file(item.target)
    except ValueError:
        return Plan(
            ItemState.REJECTED,
            f"prompts/{item.target}.yaml",
            "its name has no letters or digits PersonalClaw can name a prompt with",
        )
    dest = _rel_to_home(path)
    if path.exists():
        if _ours(item.fingerprint):
            return Plan(ItemState.EXISTING, dest, "already imported")
        return Plan(
            ItemState.CONFLICT, dest, "a prompt of this name is already here, and it is kept"
        )
    return Plan(ItemState.NEW, dest)


def _write_prompt(item: ImportItem, dest: str) -> WriteResult:
    from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable
    from personalclaw.prompt_providers.native_provider import NativePromptProvider

    variables = [
        PromptVariable.from_dict(v)
        for v in item.payload.get("variables") or []
        if isinstance(v, dict) and v.get("name")
    ]
    template = PromptTemplate(
        name=item.target,
        kind="user",
        description=str(item.payload.get("description") or ""),
        content=item.text,
        variables=variables,
        tags=["imported"],
    )
    try:
        NativePromptProvider().create_prompt(template)
    except ValueError:
        # Created since the plan read the directory: the one already there is kept.
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "a prompt of this name is already here, and it is kept",
        )
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── conversations → sessions/ (Chat history) ─────────────────────────────────


def conversation_key(item: ImportItem) -> str:
    """The session key an imported conversation lives under: in the ``dashboard_`` namespace, so
    Chat lists it with your own, and named for the tool it came from so it cannot collide with a
    conversation started here."""
    return f"dashboard_{_slug(item.source).replace('_', '-')}-{_slug(item.target)}"


def _imported_from(path: Path) -> dict[str, Any] | None:
    """Where the transcript at ``path`` says it was brought over from — its metadata line's
    ``imported_from`` — or ``None`` when there is no such file. ``{}`` for a transcript that
    names no other tool. Reads the first line only: a transcript can run to megabytes."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    try:
        meta = json.loads(first)
    except ValueError:
        return {}
    held = meta.get("imported_from") if isinstance(meta, dict) else None
    return held if isinstance(held, dict) else {}


def _plan_conversation(item: ImportItem) -> Plan:
    """Whose conversation is at the destination is read off the TRANSCRIPT, not the ledger.

    Every imported transcript is written whole, in one atomic write, with its metadata saying which
    tool and session it came from — so it answers "ours or theirs" itself, at every instant: a
    stop, a crash or a restart part-way through an import cannot leave one of ours reading as
    someone else's, which a ledger written beside it could. It also keeps a months-long history
    out of the ledger, which is rewritten whole on every write (measured: 8 s of 30 for 3,000
    conversations, growing with each one).
    """
    from personalclaw.history import session_path

    path = session_path(conversation_key(item))
    # `sessions/<file>`: a transcript sits directly in the home's sessions folder, and resolving
    # the home a second time for each of a history's thousands of items is what this saves.
    dest = f"{path.parent.name}/{path.name}"
    held = _imported_from(path)
    if held is None:
        return Plan(ItemState.NEW, dest)
    if held.get("source") == item.source and held.get("session") == item.target:
        return Plan(ItemState.EXISTING, dest, "already imported")
    return Plan(
        ItemState.CONFLICT, dest, "a conversation with this id is already here, and it is kept"
    )


def _epoch(stamp: str) -> float | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


#: The transcript class each role renders with — what a chat written here carries.
_CONVERSATION_CLS = {"user": "msg msg-u", "assistant": "msg msg-a", "tool": "msg msg-tool"}


def _write_conversation(item: ImportItem, dest: str) -> WriteResult:
    """Read the conversation in full, and write it as a chat transcript dated as it was held.

    The transcript is read HERE, when it is imported, and not by the scan: a months-long history
    is gigabytes of transcripts, which a scan that held them would hold all at once. So this is
    also where a file that turns out unreadable is refused, with the reason.

    The file's modified time is the conversation's last message, so the history lists it where
    it happened rather than at the top as "just now". It is marked consolidated through its last
    message: bringing old conversations over is not new material for the memory consolidator,
    which reads a chat only from where it last stopped — so a chat continued here is read from
    the first new turn on. No ledger entry is written: the transcript's own ``imported_from``
    says it is ours (:func:`_plan_conversation`).
    """
    from personalclaw.history import import_conversation
    from personalclaw.onboarding_import.registry import get_source
    from personalclaw.onboarding_import.sources.common import SessionUnreadable

    try:
        read = get_source(item.source).read_for_import(item)
    except SessionUnreadable as exc:
        return _result(item, WriteOutcome.REJECTED, dest, f"it {exc.reason}")
    if read is None:
        return _result(
            item,
            WriteOutcome.REJECTED,
            dest,
            "it holds no prompt, so there is no conversation in it to bring over",
        )
    conversation, redactions = read
    messages = [
        {
            "role": m["role"],
            "content": m["content"],
            "ts": m.get("ts") or "",
            "cls": _CONVERSATION_CLS.get(m["role"], "msg msg-a"),
        }
        for m in conversation["messages"]
        if isinstance(m, dict) and m.get("role") in _CONVERSATION_CLS
    ]
    key = conversation_key(item)
    try:
        import_conversation(
            key,
            metadata={
                "created_at": conversation.get("created_at") or "",
                "title": conversation["title"],
                "last_consolidated": len(messages),
                "imported_from": {
                    "source": item.source,
                    "session": item.target,
                    "cwd": conversation.get("cwd") or "",
                },
            },
            messages=messages,
            modified=_epoch(str(conversation.get("updated_at") or "")),
        )
    except FileExistsError:
        return _result(
            item,
            WriteOutcome.CONFLICT,
            dest,
            "a conversation with this id is already here, and it is kept",
        )
    try:
        from personalclaw import session_search

        session_search.reindex_session(key)
    except Exception:  # noqa: BLE001 — the index is derived; its indexer catches up
        logger.debug("could not index imported conversation %s", key, exc_info=True)
    return _result(item, WriteOutcome.IMPORTED, dest, redactions=redactions)


# ── denied_commands → config.json security.denied_commands (the shell denylist) ──

_DENYLIST_DEST = "config.json#security.denied_commands"


def _denied_pattern_of(item: ImportItem) -> str | None:
    """The item's pattern when it is one the shell denylist can check, else ``None``."""
    pattern = item.payload.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    try:
        re.compile(pattern)
    except re.error:
        return None
    return pattern


def _plan_denied_command(item: ImportItem) -> Plan:
    from personalclaw.config.loader import AppConfig

    pattern = _denied_pattern_of(item)
    if pattern is None:
        return Plan(
            ItemState.REJECTED,
            _DENYLIST_DEST,
            "its command could not be written as a pattern the shell denylist can check",
        )
    if pattern in AppConfig.load().security.denied_commands:
        return Plan(ItemState.EXISTING, _DENYLIST_DEST, "already in your shell denylist")
    return Plan(ItemState.NEW, _DENYLIST_DEST)


def _write_denied_command(item: ImportItem, dest: str) -> WriteResult:
    """Add the pattern to your own shell denylist — the list Settings › Security shows and edits.

    It only ever adds: a denylist entry refuses commands, so bringing one over tightens what the
    agent may run and loosens nothing. Removing it later is the Settings panel's to do.
    """
    from personalclaw.config.loader import AppConfig

    pattern = _denied_pattern_of(item) or ""
    cfg = AppConfig.load()
    # A read-modify-write of the whole config: the plan is re-asked of THIS read.
    if pattern in cfg.security.denied_commands:
        return _result(item, WriteOutcome.EXISTING, dest, "already in your shell denylist")
    cfg.security.denied_commands.append(pattern)
    cfg.save()
    _record(item, dest)
    return _result(item, WriteOutcome.IMPORTED, dest)


# ── dispatch ─────────────────────────────────────────────────────────────────

#: Both maps are exhaustive over ImportCategory on purpose (see model.ImportCategory).
#: A new category without a planner or a writer must fail loudly, not import nothing
#: quietly. A writer is only ever called with the destination its planner found free.
_PLANNERS: dict[ImportCategory, Callable[[ImportItem], Plan]] = {
    ImportCategory.INSTRUCTIONS: _plan_instruction,
    ImportCategory.MEMORIES: _plan_memory,
    ImportCategory.MCP_SERVERS: _plan_mcp_server,
    ImportCategory.SKILLS: _plan_skill,
    ImportCategory.AGENTS: _plan_agent,
    ImportCategory.PROMPTS: _plan_prompt,
    ImportCategory.CONVERSATIONS: _plan_conversation,
    ImportCategory.DENIED_COMMANDS: _plan_denied_command,
}
_WRITERS: dict[ImportCategory, Callable[[ImportItem, str], WriteResult]] = {
    ImportCategory.INSTRUCTIONS: _write_instruction,
    ImportCategory.MEMORIES: _write_memory,
    ImportCategory.MCP_SERVERS: _write_mcp_server,
    ImportCategory.SKILLS: _write_skill,
    ImportCategory.AGENTS: _write_agent,
    ImportCategory.PROMPTS: _write_prompt,
    ImportCategory.CONVERSATIONS: _write_conversation,
    ImportCategory.DENIED_COMMANDS: _write_denied_command,
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


def import_report(
    items: list[ImportItem],
    *,
    secrets_skipped: int = 0,
    on_result: Callable[[ImportItem, WriteResult], None] | None = None,
    stop_before: Callable[[ImportItem], bool] | None = None,
) -> ImportReport:
    """Write the items one at a time and report outcomes plus what was withheld.

    ``stop_before(item)`` is asked with each item before it is written; once it answers true
    nothing more is written and the rest are ``not_reached``. ``on_result`` hears each outcome as
    it lands. The redactions reported are those in text that landed.
    """
    results: list[WriteResult] = []
    not_reached: list[str] = []
    for index, item in enumerate(items):
        if stop_before is not None and stop_before(item):
            not_reached = [rest.fingerprint for rest in items[index:]]
            break
        result = write_item(item)
        results.append(result)
        if on_result is not None:
            on_result(item, result)
    redactions = sum(result.redactions for result in results)
    report = ImportReport(
        results=results,
        secrets_skipped=secrets_skipped,
        redactions=redactions,
        not_reached=not_reached,
    )
    # 🔑 The SECOND producer of these two sentences used to live here, word for word. `ImportReport`
    # and `ScanResult` carry the same three fields, so both now read one composer — see
    # `model.withheld_notes` for why the plurals and the verb both have to agree.
    report.notes.extend(withheld_notes(secrets_skipped=secrets_skipped, redactions=redactions))
    return report
