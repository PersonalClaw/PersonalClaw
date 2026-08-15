"""Monotonic template version store — WF2LEA-6 (§3.1 "Accept → new template VERSION").

Every save of a writable definition — and so every accepted refiner ``template_diff`` that
applies through ``save_def`` — appends an immutable version snapshot; a run pins the version
it executed (``WorkflowRun.spec_version``); re-pin/rollback only moves the pinned pointer and
NEVER rewrites history.

Append-only by construction: each version is its own file (``v001.json``, ``v002.json``, …),
so a concurrent writer cannot corrupt a prior entry — the exact shape the run-local
``spec_history/`` store (``store.write_spec_history``) uses. Lives under the already-inventoried
``workflows/`` tree (claimed by the ``workflows`` json_entity_dir StateEntry, longest-prefix),
beside ``workflows/runs/`` — no new durability entry and no ``.db``.

The pinned pointer (``pinned.json``) is what a NEW run executes; ``repin`` is rollback. The
version number is the definition's own monotonic ``version`` field, so
``get_version(name, run.spec_version)`` returns the exact spec a past run executed —
reproducibility, which is the whole point.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.workflows import store
from personalclaw.workflows.models import valid_name

logger = logging.getLogger(__name__)

#: Sources that may author a template version (metadata only — never a gate).
SOURCE_USER = "user"
SOURCE_REFINER = "refiner"


def _versions_root():
    return store.workflows_dir() / "versions"


def _template_dir(name: str):
    return _versions_root() / name


def _pin_path(name: str):
    return _template_dir(name) / "pinned.json"


def _version_path(name: str, version: int):
    return _template_dir(name) / f"v{version:03d}.json"


@dataclass
class VersionRecord:
    """One immutable snapshot of a template's full spec, plus why it was written."""

    version: int
    spec: dict[str, Any]
    source: str = SOURCE_USER
    created_at: str = ""
    ops: list[dict[str, Any]] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "spec": self.spec,
            "source": self.source,
            "created_at": self.created_at,
            "ops": self.ops,
            "run_ids": self.run_ids,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VersionRecord":
        return cls(
            version=int(d.get("version", 0) or 0),
            spec=dict(d.get("spec") or {}),
            source=str(d.get("source", SOURCE_USER) or SOURCE_USER),
            created_at=str(d.get("created_at", "") or ""),
            ops=[o for o in (d.get("ops") or []) if isinstance(o, dict)],
            run_ids=[str(r) for r in (d.get("run_ids") or [])],
            note=str(d.get("note", "") or ""),
        )


def _existing_versions(name: str) -> list[int]:
    tdir = _template_dir(name)
    if not tdir.is_dir():
        return []
    out: list[int] = []
    for child in tdir.iterdir():
        stem = child.name
        if stem.startswith("v") and stem.endswith(".json") and stem != "pinned.json":
            try:
                out.append(int(stem[1:-5]))
            except ValueError:
                continue
    return sorted(out)


def latest_version(name: str) -> int:
    """The highest recorded version on disk, or 0 when none has been recorded."""
    existing = _existing_versions(name)
    return existing[-1] if existing else 0


def record_version(
    name: str,
    spec: dict[str, Any],
    *,
    source: str = SOURCE_USER,
    ops: list[dict[str, Any]] | None = None,
    run_ids: list[str] | None = None,
    note: str = "",
) -> int:
    """Append an immutable snapshot and pin it. Returns the version number written.

    The number is the spec's own ``version`` (monotonic because ``save_def`` advances it on
    every save); a non-positive or missing value falls back to ``latest+1``. If a file for
    that version already exists it is NOT overwritten — history is append-only, so a repeat
    record is a no-op that still (re-)pins, never a rewrite.
    """
    if not valid_name(name):
        raise ValueError(f"{name!r} is not a valid definition name")
    n = int(spec.get("version", 0) or 0)
    if n <= 0:
        n = latest_version(name) + 1
    path = _version_path(name, n)
    if not path.exists():
        record = VersionRecord(
            version=n,
            spec=dict(spec),
            source=source,
            created_at=str(spec.get("updated_at") or spec.get("created_at") or ""),
            ops=[o for o in (ops or []) if isinstance(o, dict)],
            run_ids=[str(r) for r in (run_ids or [])],
            note=note,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    _write_pin(name, n)
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
    for n in _existing_versions(name):
        rec = get_version(name, n)
        if rec is not None:
            out.append(rec)
    return out


def _write_pin(name: str, version: int) -> None:
    path = _pin_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"pinned": int(version)}, indent=2, ensure_ascii=False))


def pinned_version(name: str) -> int | None:
    """The version a NEW run executes. Defaults to the latest recorded when unset."""
    path = _pin_path(name)
    if path.is_file():
        try:
            pinned = int(json.loads(path.read_text(encoding="utf-8")).get("pinned", 0) or 0)
        except (OSError, ValueError):
            pinned = 0
        if pinned and _version_path(name, pinned).is_file():
            return pinned
    latest = latest_version(name)
    return latest or None


def repin(name: str, version: int) -> bool:
    """Move the pinned pointer to an EXISTING version (rollback / re-pin).

    Never touches a version file: the whole monotonic guarantee is that history survives a
    rollback and only the pointer moves. Returns False when the target version was never
    recorded — you cannot pin a version that does not exist.
    """
    if get_version(name, version) is None:
        return False
    _write_pin(name, version)
    return True


#: ``rollback`` is exactly ``repin`` — pinning an older version IS the rollback. Named so the
#: FE/CLI reads the way a user thinks about it.
rollback = repin


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


# ── maturity (R11) — the badge the Versions tab shows ────────────────────────────


#: Static spec signals that raise a template's maturity (a check that never rejects is not a
#: check; a template with none of these is a first draft). Read off the pinned spec's node tree
#: plus its runtime hints.
def _static_signals(spec: dict[str, Any]) -> dict[str, bool]:
    _, by_id = _flatten(dict(spec.get("root") or {}))

    def _sub(container: dict[str, Any], key: str) -> dict[str, Any]:
        value = container.get(key)
        return value if isinstance(value, dict) else {}

    has_gate = any(
        node["kind"] in ("gate", "judge") or bool((node["config"] or {}).get("judge_contract"))
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
    """Compute a template's maturity level L0–L3 (R11).

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
