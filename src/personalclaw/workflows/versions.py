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

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.ledger.hashing import canonical_digest
from personalclaw.safety_flags import strict_bool, yes_or_no
from personalclaw.workflows import store
from personalclaw.workflows.models import InputParam, Node, NodeKind, WorkflowDef, valid_name

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
    #: Whether the owner's own yes covered what this save asked her (`service._write_definition`):
    #: a save by another door that wrote a command, made on her Allow.
    allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "spec": self.spec,
            "saved_by": self.saved_by,
            "created_at": self.created_at,
            "ops": self.ops,
            "allowed": self.allowed,
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
            allowed=strict_bool(d.get("allowed"), field="workflow version allowed"),
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
    allowed: bool = False,
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
            allowed=allowed,
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


def hers(name: str, spec: Any) -> bool:
    """Whether *spec*, a definition of *name* as a run holds it, is the owner's own: it runs as
    (:func:`digest`) a version of *name* she saved, or one whose save asked her and her yes covered
    (``allowed``), or as the template this release ships under that name.

    Judged by what *spec* runs, never by the number a run names. A file another door wrote at a
    number she saved (a sync, a restore, a pack) runs as no version of hers, and nor does a run's
    own spec an edit changed after its start, whatever its number moved on to. A version the
    publish switch saved runs as the one before it, so that one decides. A spec with no version
    behind it, or one another door saved with nothing asked of her, is not hers: what it writes is
    not a value she chose (`command_inputs.handed`)."""
    if not name or not isinstance(spec, dict) or not spec:
        return False
    try:
        wanted = digest(spec)
    except (ValueError, TypeError):
        return False
    if _ships_as(name, wanted):
        return True
    return any(
        (record.saved_by in (OWNER, SHIPPED) or record.allowed) and matches(wanted, record.spec)
        for record in reversed(list_versions(name))
    )


def _ships_as(name: str, wanted: str) -> bool:
    """Whether the template this release ships as *name* runs as *wanted*, a digest."""
    from personalclaw.workflows.bundled_defs import read_template

    try:
        template = read_template(name)
    except (OSError, ValueError):
        return False
    return template is not None and matches(wanted, template.to_dict())


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


#: The revision of the definition model a digest is taken under. Bumped by hand, and only when a
#: release changes the VALUE of a default in a way that changes what a workflow that leaves it unset
#: does (a timeout, a model tier): its digest changes, so every Allow it covers is asked again, and
#: the automation says a PersonalClaw default changed, not the workflow (the maintainer's settled
#: rule). A field added or removed with its default needs no bump: the digest is of what the
#: definition declares (:func:`declared`), so it is the same before and after. The defaults rail
#: (`tests/test_a_workflow_allow_survives_a_model_change.py`) fails a changed default value that
#: leaves this as it was.
DEFINITION_REVISION: int = 1


def model_defaults() -> dict[str, Any]:
    """What the definition model gives each field a definition leaves unset: a definition of no
    steps, read through the model, and one input of no settings. The defaults rail snapshots it."""
    blank = WorkflowDef(name="", root=Node(kind=NodeKind.SEQUENCE)).to_dict()
    blank.pop("name", None)
    blank.pop("root", None)
    return {"definition": blank, "input": InputParam.from_dict({}).to_dict()}


def _sparse(value: dict[str, Any], blank: dict[str, Any]) -> dict[str, Any]:
    """*value* without each field equal to *blank*'s, at every depth of the documents both hold."""
    out: dict[str, Any] = {}
    for key, held in value.items():
        default = blank.get(key, _UNSET)
        if held == default:
            continue
        if isinstance(held, dict) and isinstance(default, dict):
            held = _sparse(held, default)
            if not held:
                continue
        out[key] = held
    return out


_UNSET = object()


def declared(spec: Any) -> dict[str, Any]:
    """*spec* as it declares itself: what a run reads (:func:`runnable`), less every field that
    holds the value the definition model gives it when it is unset, at every depth (its run
    defaults and their budget, its metadata, each of its inputs). Its name and its steps are kept
    whole; a step is written sparse by the model already (``Node.to_dict`` writes a field only when
    it is set), which the defaults rail holds it to: a bare step writes its kind alone.

    What a digest is of, so a release that adds a field with a default, or removes one, leaves the
    digest of a definition that did not set it as it was, and leaves every Allow standing. A field
    set to its default and the same field left unset are one declaration."""
    return _declared(runnable(spec))


def _declared(full: dict[str, Any]) -> dict[str, Any]:
    """:func:`declared`, of a definition already read through the model (:func:`runnable`)."""
    defaults = model_defaults()
    keep: dict[str, Any] = {key: full[key] for key in ("name", "root") if key in full}
    rest = {key: value for key, value in full.items() if key not in keep}
    out: dict[str, Any] = {**keep, **_sparse(rest, defaults["definition"])}
    inputs = full.get("inputs")
    if isinstance(inputs, dict) and inputs:
        out["inputs"] = {
            name: _sparse(param, defaults["input"]) if isinstance(param, dict) else param
            for name, param in inputs.items()
        }
    return out


def _what_runs(document: dict[str, Any]) -> dict[str, Any]:
    """*document* less what changes nothing a run of it does (:data:`_NOT_WHAT_RUNS`)."""
    body = {k: v for k, v in document.items() if k not in _NOT_WHAT_RUNS}
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        body["metadata"] = {
            k: v for k, v in metadata.items() if k not in _NOT_WHAT_RUNS_IN_METADATA
        }
    return body


def digest(spec: dict[str, Any], *, revision: int | None = None) -> str:
    """What a run of *spec* does, as one string: a sha-256, as canonical JSON, over what it declares
    (:func:`declared`) of what a run does (:data:`_NOT_WHAT_RUNS` left out first), under the
    definition model's *revision* (:data:`DEFINITION_REVISION` unless said). Two versions with one
    digest run alike, which is what binds an automation's Allow to the version it was given for.

    Left out before the defaults are, so metadata that held only the publish switch is no
    declaration: a version that only flips it has the digest of the one before it."""
    rev = DEFINITION_REVISION if revision is None else revision
    return canonical_digest({"definition": _declared(_what_runs(runnable(spec))), "revision": rev})


def round_trip_digest(spec: dict[str, Any]) -> str:
    """The digest an Allow recorded before digests were of the declared definition: over the
    definition read through the model with every default filled in (:func:`runnable`). A release
    that added or removed a defaulted field changed it, and so voided every Allow; read only to
    carry those Allows over (:func:`remember_digests`)."""
    return canonical_digest(_what_runs(runnable(spec)))


# ── the digests recorded before digests were of the declared definition ───────────────────────

#: Beside the history: each round-trip digest a recorded version had (:func:`round_trip_digest`),
#: and its digest now. Written once, at the first start of the build that changed what a digest
#: is of that finds a version to note, while the model is the one those digests were taken under;
#: never again, since under a later model the same specs would give other round-trip digests,
#: which nothing recorded. A home with no recorded version has nothing to note and holds no table.
_RENAMED = "digests-renamed.json"

_renamed_cache: dict[str, Any] = {"at": None, "map": {}}


def _renamed_path():
    return _versions_root() / _RENAMED


def remember_digests() -> int:
    """At start: note, once, the round-trip digest of every recorded version of every workflow and
    its digest now, so an Allow, a version the owner saved with its steps' versions
    (``VersionRecord.calls``) and a run's pinned steps (``automation_version.STEP_VERSIONS``) that
    recorded the round-trip one still match the version they were given for (:func:`current_form`).
    The history itself is never rewritten. Returns how many it noted; 0, writing nothing, once
    noted, or with no recorded version to note: a start that finds none writes no table, so a home
    with no workflow version holds nothing of this (a snapshot counts no row of it), and a later
    start notes the versions it finds then. Each entry maps a spec's round-trip digest to that same
    spec's digest, so one written later still binds no Allow to another definition.

    The table is read as written, and lives in the home beside the history, as the automations'
    grants do. A writer of the home could map an Allow onto another version through it, as it
    could edit the grant itself, so it widens nothing."""
    path = _renamed_path()
    if path.exists():
        return 0
    root = _versions_root()
    renamed: dict[str, str] = {}
    if root.is_dir():
        for tdir in sorted(p for p in root.iterdir() if p.is_dir()):
            for record in list_versions(tdir.name):
                try:
                    renamed[round_trip_digest(record.spec)] = digest(record.spec)
                except (ValueError, TypeError):
                    continue
    if not renamed:
        return 0
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(
        path,
        json.dumps({"revision": DEFINITION_REVISION, "renamed": renamed}, indent=2, sort_keys=True),
    )
    _renamed_cache["at"] = None
    return len(renamed)


def remember_digests_at_start() -> None:
    """:func:`remember_digests`, as the gateway's start runs it: best-effort, said in the log, never
    raised. Without it an Allow given before digests were of the declared definition is asked for
    again; it never runs another version."""
    try:
        noted = remember_digests()
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("could not note the workflow versions' earlier digests", exc_info=True)
        return
    if noted:
        logger.info("workflow versions: noted %d earlier digest(s)", noted)


def _renamed() -> dict[str, str]:
    path = _renamed_path()
    try:
        at = path.stat().st_mtime_ns
    except OSError:
        return {}
    if _renamed_cache["at"] != (str(path), at):
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("renamed") or {}
        except (OSError, ValueError, AttributeError):
            raw = {}
        _renamed_cache["map"] = (
            {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        )
        _renamed_cache["at"] = (str(path), at)
    return dict(_renamed_cache["map"])


def current_form(stored: str) -> str:
    """The digest an Allow, a saved version's steps or a run's pin that recorded *stored* stands for
    now: itself, or, for one recorded before digests were of the declared definition, the digest
    its version has now (:func:`remember_digests`)."""
    return _renamed().get(stored, stored)


def matches(stored: str, spec: dict[str, Any]) -> bool:
    """Whether *stored*, a digest an Allow or a pin recorded, is the digest of *spec* now."""
    try:
        return bool(stored) and digest(spec) == current_form(stored)
    except (ValueError, TypeError):
        return False


def revised_since(stored: str, spec: dict[str, Any]) -> bool:
    """Whether *stored* is *spec*'s digest under an earlier revision of the definition model: what
    it declares is the same, and a PersonalClaw default it runs with changed since
    (:data:`DEFINITION_REVISION`)."""
    wanted = current_form(stored)
    try:
        return any(digest(spec, revision=rev) == wanted for rev in range(1, DEFINITION_REVISION))
    except (ValueError, TypeError):
        return False


def holding(name: str, wanted: str) -> VersionRecord | None:
    """The newest recorded version of *name* whose digest is *wanted* (:func:`matches`), or None."""
    for record in reversed(list_versions(name)):
        if matches(wanted, record.spec):
            return record
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
    return {
        "has_gate_or_judge": has_gate,
        "has_escalation": bool(execution.get("escalation")),
        "has_breaker": bool(execution.get("breaker")),
    }


def template_maturity(
    spec: dict[str, Any],
    *,
    clean_runs: int = 0,
    evaluator_rejected: bool = False,
) -> dict[str, Any]:
    """Compute a template's maturity level L0–L3.

    Level combines STATIC spec signals (does it verify, escalate, break a stall) with DEMONSTRATED
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
