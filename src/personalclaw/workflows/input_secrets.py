"""The ``{{secret:NAME}}`` references a workflow run is handed in its inputs, filled where used.

Three things hand a run its inputs from a configuration of their own: an automation's Run workflow
action (``action_providers.run_workflow_provider``, from a fire or Run now), a workflow step that
starts a run with that action, and a ``subworkflow`` step (``engine.dispatch_subworkflow``). A
reference in what they hand on used to be filled in there, so everything that records the run held
the secret's value: its inputs, its ledger's opening row, the run list, and the prompt a model step
was given. Now the run is handed the reference, and this module is the rest of the rule.

* **The run's record names the inputs it was handed a reference in** (:data:`EXTRA_KEY` on
  ``WorkflowRun.extra``, written when the run is created): each input's name, with the secrets its
  value refers to. A fork carries it over with the inputs (:func:`carried`).
* **A run fills those, and only those.** A step that reads one of those inputs has each reference
  filled as a reference the step writes itself is (``bindings.resolve_expr``): through the one
  resolver, the run's project first, and kept as the name where the step's text goes to a model.
  Text that reads as a reference in any other input — typed at Run, given by an agent's call or by
  a caller from outside, or produced by a step — stays text, as it always was: who wrote it could
  not have named a secret for a run of the owner's to fill.
* **A step that hands a run its inputs keeps references as references** (:class:`Handed`, the
  resolver ``node_bindings._secrets_for`` gives it) and names the ones it kept. Text from elsewhere
  that refers to a secret the step hands on would be filled by the run as if the step's author had
  written it, so the step is refused instead (:func:`handed_on`).

Records written before this rule hold the value. :func:`redact_home` finds a stored secret's value
in a run's inputs, ledger, state and prompts, and in the automations' history, and writes the
secret's reference in its place.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from personalclaw.workflows.bindings import BindingContext, BindingError, resolve
from personalclaw.workflows.models import REFUSED_TO_START, Failure, FailureClass
from personalclaw.workflows.secrets import SECRET_BINDING_RE

logger = logging.getLogger(__name__)

#: Where a run's record names the inputs it was handed a reference in: input name → the secrets
#: its value refers to.
EXTRA_KEY = "secret_inputs"

#: A stored value shorter than this is not looked for in a record: `1`, `true` or a region name
#: would match ordinary words (the floor ``security.redact_known_values`` keeps too).
_MIN_VALUE_LEN = 8


def reference(name: str) -> str:
    """The reference to the secret *name*, as a run's record keeps it."""
    return "{{secret:" + name + "}}"


def references_in(value: Any) -> Counter[str]:
    """Every secret reference in *value*, counted. Dicts, lists and strings are walked."""
    found: Counter[str] = Counter()

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            found.update(SECRET_BINDING_RE.findall(node))
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return found


# ── the run's record ─────────────────────────────────────────────────────────


def of_run(run: Any) -> dict[str, tuple[str, ...]]:
    """The inputs *run* was handed a reference in, by name, with the secrets each refers to."""
    return _held(getattr(run, "extra", None) or {})


def _held(extra: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    raw = extra.get(EXTRA_KEY)
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): tuple(str(n) for n in names if isinstance(n, str))
        for key, names in raw.items()
        if isinstance(names, list)
    }


def stamp(extra: dict[str, Any], inputs: dict[str, Any], kept: Iterable[str]) -> dict[str, Any]:
    """*extra* naming which of *inputs* refer to one of the *kept* secrets, beside any it named."""
    wanted = set(kept)
    merged = {key: set(names) for key, names in _held(extra).items()}
    for key, value in inputs.items():
        names = set(references_in(value)) & wanted
        if names:
            merged[str(key)] = merged.get(str(key), set()) | names
    if not merged:
        return dict(extra)
    return {**extra, EXTRA_KEY: {key: sorted(names) for key, names in sorted(merged.items())}}


def carried(parent: Any) -> dict[str, Any]:
    """What a fork of *parent* carries over beside its inputs: the inputs it was handed a reference
    in, so the fork fills them as *parent* did."""
    held = of_run(parent)
    return {EXTRA_KEY: {key: list(names) for key, names in held.items()}} if held else {}


# ── a step that hands a run its inputs ───────────────────────────────────────


class Handed:
    """How a step that hands a run its inputs resolves a ``{{secret:NAME}}``: kept as the
    reference and counted, so the step can name the ones it hands on (:func:`handed_on`).

    A name nothing reads by name (a setting's own key, a project's stored key) is refused as it is
    wherever a step names it."""

    def __init__(self) -> None:
        self.kept: Counter[str] = Counter()

    def __call__(self, key: str) -> str:
        from personalclaw.llm.credentials import name_refusal

        refused = name_refusal(key)
        if refused is not None:
            raise BindingError(refused.cause, remediation=refused.remedy, caller_supplied=True)
        self.kept[key] += 1
        return reference(key)


def handed_on(resolver: Any, value: Any) -> tuple[tuple[str, ...], Failure | None]:
    """The secrets whose references *value* hands a run, or the failure that refuses the step.

    *value* is what the step's bindings resolved to with *resolver*. Every reference the step kept
    is in it once for each time it was kept; one more is text from another step's output or an
    input that refers to the same secret, which the run would fill as the step's own, so the step
    is refused (a security refusal, never retried). A reference to any other secret in it is text
    and is not named, so the run leaves it as text. ``((), None)`` for a step that hands nothing on
    (*resolver* is not :class:`Handed`)."""
    if not isinstance(resolver, Handed):
        return (), None
    present = references_in(value)
    for name in sorted(resolver.kept):
        if present[name] > resolver.kept[name]:
            return (), Failure(
                failure_class=FailureClass.PERMISSION,
                cause_plain=(
                    f"this step hands the run {reference(name)}, and text it takes from another "
                    "step or an input refers to that secret too, which the run would fill in as "
                    "the step's own; so the run was not started"
                ),
                remediation=(
                    "hand that text on from a step that names no secret, or take the reference "
                    "out of what produces it"
                ),
                terminal_reason=REFUSED_TO_START,
            )
    return tuple(sorted(name for name in resolver.kept if present[name])), None


def hand_to_child(
    raw: Any, ctx: BindingContext, project_id: str
) -> tuple[dict[str, Any], dict[str, Any], Failure | None]:
    """A ``subworkflow`` step's inputs as its child run is handed them, and what the child's record
    keeps of them (``extra``); or the failure that keeps the child from being started.

    Each input resolves against the parent's run, so the child receives values rather than bindings
    of a graph it cannot read — except a secret reference, kept as the reference (``ctx`` resolves
    them with :class:`Handed`). Each one the child is handed is checked against the secrets a run
    of *project_id* (the child's, which is its parent's) reads, so a secret that is not stored
    fails the step before anything is started, as it did when the step filled it in."""
    from personalclaw.triggers.secrets import UnresolvedSecret, check
    from personalclaw.workflows.failure_taxonomy import binding_failure

    inputs: dict[str, Any] = {}
    for key, value in (raw if isinstance(raw, dict) else {}).items():
        try:
            inputs[str(key)] = resolve(value, ctx)
        except BindingError as exc:
            return {}, {}, binding_failure(exc, f"subworkflow input {key!r} did not resolve")
    kept, refused = handed_on(ctx.secret_resolver, inputs)
    if refused is not None:
        return {}, {}, refused
    try:
        check(kept, project_id=project_id)
    except UnresolvedSecret as missing:
        where = "this project's or the global secrets" if project_id else "the global secrets"
        return (
            {},
            {},
            Failure(
                failure_class=FailureClass.USER,
                cause_plain=f"secret {missing.key!r} is not set in {where}",
                remediation=f"store {missing.key!r} in Settings → Secrets, then fork this run",
            ),
        )
    return inputs, stamp({}, inputs, kept), None


# ── records written before ───────────────────────────────────────────────────


def _stored_values() -> list[tuple[str, str, str]]:
    """``(value, name, project_id)`` for every secret the vault lists, the gateway's own included
    (``project_id`` "" for those), whose value is long enough to look for."""
    from personalclaw import secrets_vault
    from personalclaw.llm.credentials import resolve_secret

    found: list[tuple[str, str, str]] = []
    for row in secrets_vault.list_presence():
        try:
            value = resolve_secret(row.name, project_id=row.project_id).secret
        except KeyError:
            continue
        if len(value) >= _MIN_VALUE_LEN:
            found.append((value, row.name, row.project_id))
    return found


def _replaced(value: Any, known: list[tuple[str, str]], names: set[str]) -> Any:
    """*value* with each of the *known* ``(value, name)`` pairs written as the secret's reference,
    in their order (:func:`_for_project`), the names it wrote added to *names*."""
    if isinstance(value, str):
        for secret, name in known:
            if secret in value:
                value = value.replace(secret, reference(name))
                names.add(name)
        return value
    if isinstance(value, dict):
        return {key: _replaced(item, known, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_replaced(item, known, names) for item in value]
    return value


def _for_project(stored: list[tuple[str, str, str]], project_id: str) -> list[tuple[str, str]]:
    """``(value, name)`` for each value a run of *project_id* could have been handed: its
    project's secrets and the global ones, longest value first so one that contains another is
    replaced whole, and for a value two of them hold, the name the run reads first (its
    project's)."""
    ranked = sorted(
        ((v, n, 0 if p else 1) for v, n, p in stored if not p or (project_id and p == project_id)),
        key=lambda vnr: (-len(vnr[0]), vnr[2], vnr[1]),
    )
    seen: set[str] = set()
    known: list[tuple[str, str]] = []
    for value, name, _rank in ranked:
        if value not in seen:
            seen.add(value)
            known.append((value, name))
    return known


def _rewrite_file(path: Path, known: list[tuple[str, str]]) -> bool:
    """Rewrite one JSON or JSON-lines record of a run with the *known* values replaced. Never
    raises: a record that cannot be read is left as it is."""
    from personalclaw.atomic_write import atomic_write

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not any(secret in text or json.dumps(secret)[1:-1] in text for secret, _ in known):
        return False
    try:
        if path.suffix == ".jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
            out = "".join(
                json.dumps(_replaced(row, known, set()), ensure_ascii=False, default=str) + "\n"
                for row in rows
            )
        else:
            replaced = _replaced(json.loads(text), known, set())
            out = json.dumps(replaced, indent=2, ensure_ascii=False)
    except ValueError:
        logger.warning("could not take a secret's value out of %s: unreadable", path)
        return False
    atomic_write(path, out, mode=0o600)
    return True


def _run_files(run_id: str) -> list[Path]:
    """The records of a run a handed value reached: its ledger, its state and its prompts."""
    from personalclaw.workflows import store

    folder = store.run_dir(run_id)
    files = [folder / name for name in ("journal.jsonl", "events.jsonl", "state.json")]
    for path in sorted((folder / "outputs").glob("*.json")):
        try:
            if str(json.loads(path.read_text(encoding="utf-8")).get("node_path", "")).endswith(
                "::prompt"
            ):
                files.append(path)
        except (OSError, ValueError, AttributeError):
            continue
    return [path for path in files if path.is_file()]


def redact_home() -> list[str]:
    """Take every stored secret's value out of the run records of this home, writing the secret's
    reference in its place; return what it rewrote (a run's id, or ``history``).

    A run's inputs that held a value are recorded as handed the reference (:func:`stamp`), so a
    fork fills it where a step uses it, as the run itself did. Its ledger, state and prompts are
    rewritten first and its row last, so a pass that stops part way is finished by the next: a run
    is found by its row. The automations' history is rewritten under its own lock. Run at the
    gateway's start, before any run is driven, and idempotent: a second pass finds nothing. Never
    raises — a record it cannot rewrite is left as it was and logged."""
    try:
        stored = _stored_values()
    except Exception:  # noqa: BLE001 - an unreadable store leaves the records as they are
        logger.warning("could not read the stored secrets to look for them in run records")
        return []
    if not stored:
        return []
    changed: list[str] = []
    try:
        changed += _redact_runs(stored)
    except Exception:  # noqa: BLE001 - the gateway starts whatever this finds
        logger.warning("could not take secrets' values out of the run records", exc_info=True)
    try:
        if _redact_history(_for_project(stored, "")):
            changed.append("history")
    except Exception:  # noqa: BLE001 - the gateway starts whatever this finds
        logger.warning("could not take secrets' values out of the run history", exc_info=True)
    return changed


def _redact_runs(stored: list[tuple[str, str, str]]) -> list[str]:
    from personalclaw.workflows import store

    rewritten = []
    for run in store.all_runs():
        known = _for_project(stored, run.project_id)
        names: set[str] = set()
        inputs = _replaced(dict(run.inputs), known, names)
        if not names:
            continue
        for path in _run_files(run.id):
            _rewrite_file(path, known)
        run.inputs = inputs
        run.extra = stamp(run.extra, inputs, names)
        rewritten.append((run, sorted(names)))
    store.overwrite([run for run, _names in rewritten])
    for run, names_held in rewritten:
        logger.warning(
            "run %s kept the value of %s in its record; it keeps the reference now",
            run.id,
            ", ".join(names_held),
        )
    return [run.id for run, _names in rewritten]


def _redact_history(known: list[tuple[str, str]]) -> bool:
    from personalclaw.config.loader import config_dir
    from personalclaw.schedule_history import ScheduleRunStore

    if not known:
        return False
    return ScheduleRunStore(config_dir()).rewrite_sync(lambda row: _replaced(row, known, set()))
