"""Installed-pack ledger (AGENT-PACKS §9, AP-3).

``<home>/packs/installed.json`` records what each imported pack put on this machine: its
components, the resolution of each connector requirement (§3.3), and its post-install
setup skill (§3.4). It is the READER surface behind two done_when contracts:

* a **skipped connector** degrades with a machine-readable ``connector_missing:<name>``
  marker recorded here, so a connector-dependent feature (and the pack detail page) can
  read "this is unavailable" without re-deriving it;
* a **setup skill** is re-runnable — the ledger keeps ``setup_pending`` true while a pack
  carries one, so the "Finish setup" chip always has something to re-invoke.

It is NOT the import journal (:class:`packs.import_._Journal` — the crash-safe rollback
ledger, deleted on success). This ledger is the durable post-install record; it is written
only after a commit fully succeeds, so it never describes a rolled-back pack.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

LEDGER_FILE = "installed.json"


@dataclass
class InstalledPack:
    """One installed pack's durable record.

    ``connector_markers`` holds every ``connector_missing:<name>`` for a skipped connector
    (the degraded-completion surface). ``setup_skill`` is the committed skill id of the
    pack's ``setup/SKILL.md`` (empty when the pack ships none); ``setup_pending`` stays true
    while a setup skill exists — the re-runnable "Finish setup" affordance never expires on
    its own (a user re-runs the interview whenever they want).
    """

    name: str
    version: str
    components: list[str] = field(default_factory=list)  # ["skill:cfo-report", ...]
    connectors: list[dict[str, Any]] = field(default_factory=list)  # ConnectorResolution dicts
    connector_markers: list[str] = field(default_factory=list)  # ["connector_missing:x", ...]
    setup_skill: str = ""
    setup_pending: bool = False
    installed_at: str = ""
    #: The setup interview's declared questions (§4.1) — ``[{key, kind, label, required}]``.
    #: Declared by the pack at ``setup/bindings.json``; recorded here so the chip can name
    #: what is still missing rather than only that setup exists.
    bindings: list[dict[str, Any]] = field(default_factory=list)
    #: The answers the interview has bound so far, ``{key: value}``. Written only through
    #: :func:`bind_answer`, which validates against the declared kind.
    bound: dict[str, str] = field(default_factory=dict)
    #: The staged roster's rows (§4.2), so the pack detail surface can show the whole team
    #: (and which tier each member is in) without re-reading the staging area.
    roster: list[dict[str, Any]] = field(default_factory=list)
    #: The manifest's ``pack_owned`` path patterns as installed (§1). Recorded here rather than
    #: re-read from an archive, because an UPDATE must decide overwrite-vs-skip against what
    #: the INSTALLED pack claimed to own — a new archive could widen its own ownership and
    #: quietly acquire the right to clobber a file the user has been editing.
    pack_owned: list[str] = field(default_factory=list)
    #: The per-component drift lock (§1, the LEARNING-FLYWHEEL ``{source, computedHash}``
    #: convention): ``{"skill:cfo-report": {"source", "computedHash", "path"}}``, where
    #: ``path`` is home-relative and ``computedHash`` is
    #: :func:`packs.update.component_digest` over the bytes that landed. An update compares
    #: the digest again: equal = still the pack's copy (safe to overwrite); different = the
    #: user edited it (skip with a drift note); absent = unverifiable, so never clobbered.
    component_locks: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def unbound(self) -> list[str]:
        """Required binding keys with no answer yet — what "Finish setup" still owes."""
        return [
            str(b.get("key"))
            for b in self.bindings
            if b.get("required", True) and not str(self.bound.get(str(b.get("key")), "")).strip()
        ]

    def to_dict(self) -> dict[str, Any]:
        """The PERSISTED shape — pure fields only, so no derived value is ever stored."""
        return asdict(self)

    def to_view(self) -> dict[str, Any]:
        """The API shape: the record plus ``unbound``, derived once here so two readers
        cannot disagree about whether a pack's setup is actually finished."""
        out = asdict(self)
        out["unbound"] = self.unbound
        return out


def _ledger_path(home: Path | None = None) -> Path:
    return (home or config_dir()) / "packs" / LEDGER_FILE


def load_installed(home: Path | None = None) -> list[InstalledPack]:
    """Every recorded installed pack (empty when nothing has been imported)."""
    path = _ledger_path(home)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("installed-pack ledger unreadable at %s", path)
        return []
    if not isinstance(raw, dict):
        return []
    out: list[InstalledPack] = []
    for name, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        out.append(
            InstalledPack(
                name=str(name),
                version=str(rec.get("version", "")),
                components=[str(c) for c in rec.get("components", [])],
                connectors=[c for c in rec.get("connectors", []) if isinstance(c, dict)],
                connector_markers=[str(m) for m in rec.get("connector_markers", [])],
                setup_skill=str(rec.get("setup_skill", "")),
                setup_pending=bool(rec.get("setup_pending", False)),
                installed_at=str(rec.get("installed_at", "")),
                bindings=[b for b in rec.get("bindings", []) if isinstance(b, dict)],
                bound={
                    str(k): str(v)
                    for k, v in (rec.get("bound") or {}).items()
                    if isinstance(rec.get("bound"), dict)
                },
                roster=[r for r in rec.get("roster", []) if isinstance(r, dict)],
                pack_owned=[str(p) for p in rec.get("pack_owned", [])],
                component_locks={
                    str(ref): {str(k): str(v) for k, v in lock.items()}
                    for ref, lock in (rec.get("component_locks") or {}).items()
                    if isinstance(lock, dict)
                },
            )
        )
    return out


def record_install(pack: InstalledPack, home: Path | None = None) -> None:
    """Upsert one pack's record into the ledger (keyed by pack name), written atomically.

    Re-importing a pack overwrites its record (the same name = the same pack); other packs'
    records are preserved. The ledger is a dict keyed by name so a read is an O(1) lookup and
    a re-import never duplicates a row.
    """
    from personalclaw.atomic_write import atomic_write

    path = _ledger_path(home)
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}
    rec = pack.to_dict()
    rec.pop("name", None)  # the key IS the name; don't duplicate it in the value
    existing[pack.name] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(existing, indent=2, ensure_ascii=False) + "\n")


class BindingError(Exception):
    """A setup answer that the pack's own declaration does not accept."""


def bind_answer(pack_name: str, key: str, value: str, home: Path | None = None) -> "InstalledPack":
    """Record one setup-interview answer (§3.4/§4.1). Returns the updated record.

    This is what makes "the interview binds a folder" a mechanism rather than a prompt: the
    answer is validated against the binding the PACK declared and persisted in the ledger, so
    the ``unbound`` list shrinks and the "Finish setup" chip can report real progress.

    Fail closed on every disagreement — an unknown pack, an undeclared key, an empty value,
    or (for ``kind: folder``) a path that is not an existing directory. A folder answer is
    resolved and stored absolute so a later reader is not re-resolving it against whatever
    cwd it happens to have.
    """
    packs = {p.name: p for p in load_installed(home)}
    pack = packs.get(pack_name)
    if pack is None:
        raise BindingError(f"pack not installed: {pack_name}")
    declared = next((b for b in pack.bindings if str(b.get("key")) == key), None)
    if declared is None:
        raise BindingError(f"pack {pack_name!r} declares no setup binding {key!r}")
    answer = str(value).strip()
    if not answer:
        raise BindingError(f"binding {key!r} needs a value")
    if str(declared.get("kind")) == "folder":
        resolved = Path(answer).expanduser()
        if not resolved.is_dir():
            raise BindingError(f"binding {key!r} needs an existing directory (got {answer!r})")
        answer = str(resolved.resolve())
    pack.bound = {**pack.bound, key: answer}
    record_install(pack, home)
    return pack
