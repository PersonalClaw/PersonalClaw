"""The version history of a workflow definition: one immutable snapshot per save, and who saved it.

Every save of a writable definition (``service._write_definition``, the one writer) appends a
snapshot keyed by the definition's own monotonic ``version`` (``v001.json``, ``v002.json``, …), and
never rewrites one: each version is its own file, so a concurrent writer cannot corrupt a prior
entry. A run records the version it executed (``WorkflowRun.spec_version``), so
``get_version(name, run.spec_version)`` is the exact spec a past run read.

**What a run executes.** A run started by hand (the Run button, an agent's ``workflow_start``)
executes the definition as it is now. A run an automation starts executes the version its owner
allowed (``workflows.automation_version``), which can be an older one, read from here. A restore is
an edit of a recorded version, saved as the newest one: nothing moves what runs without a save.

**Who saved it** (``saved_by``) is set by the door the save came through, never by what that door
was handed (:data:`SAVERS`): an automation follows a newer version only when the owner saved it
herself, so a label a caller could write would be a yes anyone could give. A version the owner
saved also keeps the versions of the workflows it runs as steps, as they were at her save
(``calls``): her save is her yes to those too, and an automation that follows it runs them.

**Each machine's own.** The history lives under ``workflows/versions/``, beside the definitions,
and a sync never carries it (``durability.inventory``'s ``workflows`` entry): a version is what an
automation here may run, with the steps its owner allowed here, and another machine's history says
who saved each version THERE. A definition from another machine arrives as the current one, saved
by nobody here.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.safety_flags import yes_or_no
from personalclaw.workflows import store
from personalclaw.workflows.models import WorkflowDef, valid_name

logger = logging.getLogger(__name__)

# ── who saved a version: set by the door it came through ─────────────────────────

#: The owner, in the workflow's editor on this machine, where she is shown every step she saves.
OWNER = "owner"
#: The owner's switch that publishes a workflow to A2A. It changes no step and shows none: what it
#: writes is the steps of the version before it, so it is no yes to them.
PUBLISH = "publish"
#: An agent's tool: ``workflow_author`` (saved at once, or on the owner's Allow of what its steps
#: would then do), or a chat's batch.
AGENT = "agent"
#: An accepted refiner proposal: a model's diff, accepted in the Inbox.
REFINER = "refiner"
#: An import: a prompt card the owner accepted.
IMPORT = "import"
#: An app: a template an installed app provides, or a save made with an app's token.
APP = "app"
#: A template PersonalClaw ships, as the installed release has it.
SHIPPED = "shipped"
#: A definition no door on this machine saved — another machine's sync, a restore, a pack —
#: recorded when an automation here is allowed to run it.
BROUGHT_IN = "brought_in"

#: Every door's name. A save that names none is refused: who saved it is what an automation's
#: owner reads before she lets it run a version.
SAVERS: frozenset[str] = frozenset(
    {OWNER, PUBLISH, AGENT, REFINER, IMPORT, APP, SHIPPED, BROUGHT_IN}
)


def _versions_root():
    return store.workflows_dir() / "versions"


def _template_dir(name: str):
    return _versions_root() / name


def _version_path(name: str, version: int):
    return _template_dir(name) / f"v{version:03d}.json"


@dataclass
class VersionRecord:
    """One immutable snapshot of a definition's full spec, who saved it, and why it was written.

    ``saved_by`` is ``""`` for a version recorded before the history said who saved each one.
    ``calls`` is, for a version the owner saved, each workflow it runs as a step and the version of
    it as it was at her save (``{name: {"version", "digest"}}``); ``{}`` for any other."""

    version: int
    spec: dict[str, Any]
    saved_by: str = ""
    created_at: str = ""
    ops: list[dict[str, Any]] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    note: str = ""
    calls: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "spec": self.spec,
            "saved_by": self.saved_by,
            "created_at": self.created_at,
            "ops": self.ops,
            "run_ids": self.run_ids,
            "note": self.note,
            "calls": self.calls,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VersionRecord":
        calls = d.get("calls")
        return cls(
            version=int(d.get("version", 0) or 0),
            spec=dict(d.get("spec") or {}),
            saved_by=str(d.get("saved_by", "") or ""),
            created_at=str(d.get("created_at", "") or ""),
            ops=[o for o in (d.get("ops") or []) if isinstance(o, dict)],
            run_ids=[str(r) for r in (d.get("run_ids") or [])],
            note=str(d.get("note", "") or ""),
            calls={
                str(k): dict(v)
                for k, v in (calls.items() if isinstance(calls, dict) else ())
                if isinstance(v, dict)
            },
        )


def recorded_numbers(name: str) -> list[int]:
    """The version numbers recorded for *name*, ascending."""
    tdir = _template_dir(name)
    if not tdir.is_dir():
        return []
    out: list[int] = []
    for child in tdir.iterdir():
        stem = child.name
        if stem.startswith("v") and stem.endswith(".json"):
            try:
                out.append(int(stem[1:-5]))
            except ValueError:
                continue
    return sorted(out)


def latest_version(name: str) -> int:
    """The highest recorded version on disk, or 0 when none has been recorded."""
    existing = recorded_numbers(name)
    return existing[-1] if existing else 0


def record_version(
    name: str,
    spec: dict[str, Any],
    *,
    saved_by: str,
    ops: list[dict[str, Any]] | None = None,
    run_ids: list[str] | None = None,
    note: str = "",
    calls: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Append an immutable snapshot of *spec*, saved by *saved_by* (one of :data:`SAVERS`), with the
    versions of the workflows it runs as steps when the owner saved it (*calls*). Returns the
    version number it is recorded under.

    The number is the spec's own ``version`` (monotonic because ``save_def`` advances it past every
    recorded one); a non-positive or missing value falls back to ``latest+1``. A file already there
    for that number is NOT overwritten: history is append-only, so a repeat record is a no-op.
    """
    if not valid_name(name):
        raise ValueError(f"{name!r} is not a valid definition name")
    if saved_by not in SAVERS:
        raise ValueError(f"a version names who saved it, one of {sorted(SAVERS)}: not {saved_by!r}")
    n = int(spec.get("version", 0) or 0)
    if n <= 0:
        n = latest_version(name) + 1
    path = _version_path(name, n)
    if not path.exists():
        record = VersionRecord(
            version=n,
            spec=dict(spec),
            saved_by=saved_by,
            created_at=str(spec.get("updated_at") or spec.get("created_at") or ""),
            ops=[o for o in (ops or []) if isinstance(o, dict)],
            run_ids=[str(r) for r in (run_ids or [])],
            note=note,
            calls={str(k): dict(v) for k, v in (calls or {}).items() if isinstance(v, dict)},
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    return n


def get_version(name: str, version: int) -> VersionRecord | None:
    path = _version_path(name, version)
    if not path.is_file():
        return None
    try:
        return VersionRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        logger.warning("versions: unreadable %s v%s", name, version, exc_info=True)
        return None


def list_versions(name: str) -> list[VersionRecord]:
    """Every recorded version, ascending. Empty when nothing has been recorded yet."""
    out: list[VersionRecord] = []
    for n in recorded_numbers(name):
        rec = get_version(name, n)
        if rec is not None:
            out.append(rec)
    return out


# ── what a version runs ──────────────────────────────────────────────────────────

#: What a digest leaves out: when and under which number a version was saved, which store holds
#: it, and who saved it. None of them changes what a run of it does.
_NOT_WHAT_RUNS: frozenset[str] = frozenset(
    {"version", "created_at", "updated_at", "source", "provenance"}
)
#: And the one key of its ``metadata`` that only says whether an outside agent may start it (the
#: publish switch's), so a version that only flips it runs as the one before it.
_NOT_WHAT_RUNS_IN_METADATA: frozenset[str] = frozenset({"a2a_published"})


def runnable(spec: Any) -> dict[str, Any]:
    """*spec* — a definition as a provider returns it, or a recorded version's spec — as a run reads
    it: through the definition model and back (``WorkflowDef``), which every run start does, so a
    definition read now and the same version read from here are the same document."""
    raw = spec if isinstance(spec, dict) else getattr(spec, "to_dict", lambda: {})()
    return WorkflowDef.from_dict(dict(raw or {})).to_dict()


def digest(spec: dict[str, Any]) -> str:
    """What a run of *spec* (:func:`runnable`) does, as one string: a sha-256 over every field but
    :data:`_NOT_WHAT_RUNS`, as canonical JSON. Two versions with one digest run alike, which is what
    binds an automation's Allow to the version it was given for."""
    body = {k: v for k, v in runnable(spec).items() if k not in _NOT_WHAT_RUNS}
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        body["metadata"] = {
            k: v for k, v in metadata.items() if k not in _NOT_WHAT_RUNS_IN_METADATA
        }
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def holding(name: str, wanted: str) -> VersionRecord | None:
    """The newest recorded version of *name* whose digest is *wanted*, or None."""
    for record in reversed(list_versions(name)):
        try:
            if digest(record.spec) == wanted:
                return record
        except (ValueError, TypeError):
            continue
    return None


# ── typed-op diff (the Versions tab renders this) ────────────────────────────────


def _flatten(root: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Pre-order node ids and an id → ``{kind, config, macro}`` map for a spec ``root``."""
    order: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        nid = str(node.get("id", "") or "")
        if nid:
            order.append(nid)
            by_id[nid] = {
                "kind": str(node.get("kind", node.get("macro", "")) or ""),
                "config": node.get("config") if isinstance(node.get("config"), dict) else {},
            }
        for key in ("children", "body"):
            child = node.get(key)
            if isinstance(child, list):
                for c in child:
                    walk(c)
            elif isinstance(child, dict):
                walk(child)

    walk(root if isinstance(root, dict) else {})
    return order, by_id


def diff(name: str, a: int, b: int) -> list[dict[str, Any]]:
    """A typed-op diff from version ``a`` to version ``b`` in the engine's own op vocabulary.

    Emits ``insert`` / ``delete`` / ``update_node`` (with the changed field names) / ``move``
    (a reordered node whose id is present in both) / ``set_input`` (changed inputs). Returns an
    empty list when a version is missing rather than raising — a diff view must degrade, not 500.
    """
    ra, rb = get_version(name, a), get_version(name, b)
    if ra is None or rb is None:
        return []
    oa, mapa = _flatten(dict(ra.spec.get("root") or {}))
    ob, mapb = _flatten(dict(rb.spec.get("root") or {}))
    ops: list[dict[str, Any]] = []

    for nid in ob:
        if nid not in mapa:
            ops.append({"op": "insert", "node_id": nid, "kind": mapb[nid]["kind"]})
        else:
            changed = [
                key for key in ("kind", "config") if mapa[nid].get(key) != mapb[nid].get(key)
            ]
            if changed:
                ops.append({"op": "update_node", "node_id": nid, "fields": changed})
    for nid in oa:
        if nid not in mapb:
            ops.append({"op": "delete", "node_id": nid})

    common_a = [n for n in oa if n in mapb]
    common_b = [n for n in ob if n in mapa]
    if common_a != common_b:
        moved = [n for n, m in zip(common_a, common_b) if n != m]
        for nid in moved:
            ops.append({"op": "move", "node_id": nid})

    if (ra.spec.get("inputs") or {}) != (rb.spec.get("inputs") or {}):
        ops.append({"op": "set_input"})
    return ops


# ── maturity — the badge the Versions tab shows ──────────────────────────────────


#: Static spec signals that raise a template's maturity (a check that never rejects is not a
#: check; a template with none of these is a first draft). Read off the current definition's node
#: tree plus its runtime hints.
def _static_signals(spec: dict[str, Any]) -> dict[str, bool]:
    _, by_id = _flatten(dict(spec.get("root") or {}))

    def _sub(container: dict[str, Any], key: str) -> dict[str, Any]:
        value = container.get(key)
        return value if isinstance(value, dict) else {}

    has_gate = any(
        node["kind"] in ("gate", "judge")
        or yes_or_no((node["config"] or {}).get("judge_contract")) is True
        for node in by_id.values()
    )
    hints = _sub(spec, "runtime_hints")
    execution = _sub(hints, "execution")
    judge = _sub(hints, "judge")
    return {
        "has_gate_or_judge": has_gate,
        "has_escalation": bool(execution.get("escalation")),
        "has_breaker": bool(execution.get("breaker")),
        "has_stop_condition": bool(judge.get("stop_condition")),
    }


def template_maturity(
    spec: dict[str, Any],
    *,
    clean_runs: int = 0,
    evaluator_rejected: bool = False,
) -> dict[str, Any]:
    """Compute a template's maturity level L0–L3.

    Level combines STATIC spec signals (does it verify, escalate, stop) with DEMONSTRATED
    ledger activity (clean runs, and — the load-bearing one — "the evaluator has rejected at
    least one real bad run", because a gate that has never rejected is not yet proven). The
    caller supplies the ledger figures; this stays a pure function over (spec, stats).
    """
    signals = _static_signals(spec)
    static_count = sum(1 for v in signals.values() if v)
    if static_count == 0:
        level = 0
    elif clean_runs < 3:
        level = 1
    elif not (evaluator_rejected and signals["has_gate_or_judge"]):
        level = 2
    else:
        level = 3
    labels = {0: "draft", 1: "shaping", 2: "proven", 3: "mature"}
    return {
        "level": level,
        "label": labels[level],
        "signals": signals,
        "clean_runs": int(clean_runs),
        "evaluator_rejected": bool(evaluator_rejected),
    }
