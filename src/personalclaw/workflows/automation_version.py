"""Which version of a workflow an automation runs: the one its owner allowed.

An automation's Allow is the owner's yes to its action as it stood (`triggers.grants`). A
``run-workflow`` action runs a workflow, and the workflow changes after the yes, so the yes records
the version of the workflow it was given for (:class:`Allowed`, kept in the grant): its number, and
a digest of what it runs (`versions.digest`). What a fire runs (:func:`runs`):

* **the version it was allowed**: the definition as it is now while it is still that version, else
  the version from the history (`workflows.versions`);
* **a newer version the owner saved herself**, at her own door (``versions.OWNER``): her save is
  her yes, so the automation follows it;
* **never a newer version anything else saved**: an agent's tool, an accepted refiner proposal, an
  import, an app, another machine's sync. The automation says a newer version exists, and the owner
  moves it there with "Use vN", which asks her first (`grants.use_current_version`).

**What it runs as steps, too.** A workflow can start others: a ``subworkflow`` step, or a
``run-workflow`` action step. Each is looked up by name when the step runs, so a yes to the first
workflow alone would let any later save of one it starts run under it. So the Allow records each
workflow the allowed version starts, at every depth, as it was then (:func:`closure`); a version
the owner saves herself records them as they were at her save (``VersionRecord.calls``); and a run
an allowed automation starts carries those versions on its record (:data:`STEP_VERSIONS`), so each
step starts the version allowed with it, by the same rule (:func:`step_runs`), at every depth. A
step that starts a workflow its automation was not allowed with does not run, and says why.

A version the automation was allowed that can no longer be found is refused, never swapped for
another: the run does not start, and it says why and what to do (:attr:`Runs.problem`).

The automations PersonalClaw's own code makes and grants run the workflow as it is now
(`grants.made_by_personalclaw`): each runs a template PersonalClaw ships, which changes only with
PersonalClaw itself. A run started any other way — the Run button, an agent's ``workflow_start``, a
step of another workflow such a run started — runs the definition as it is now too.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import versions

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: The definition providers whose definitions are not saved through a door on this machine, and who
#: a definition of theirs is said to be saved by. Any other read-only provider is an app's.
_NATIVE = "native"
_BUNDLED = "bundled"

#: Where a run an owner-allowed automation started keeps the versions of the workflows its steps may
#: start (`WorkflowRun.extra`): ``{name: {"version", "digest"}}``, the workflow it runs among them.
#: Absent from every other run, whose steps start each workflow as it is.
STEP_VERSIONS = "step_versions"

#: The action that runs a workflow (`grants.RUNS_A_WORKFLOW`), as a step of another names it.
_RUNS_A_WORKFLOW = "run-workflow"


@dataclass(frozen=True)
class Allowed:
    """The version of a workflow an automation's owner allowed it to run: as kept in its grant.

    ``calls`` are the workflows it runs as steps, at every depth, each at the version allowed with
    it (:func:`closure`); a version a run's record carries for a step has none of its own."""

    workflow: str
    version: int
    digest: str
    calls: tuple["Allowed", ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.workflow,
            "version": self.version,
            "digest": self.digest,
        }
        if self.calls:
            out["calls"] = [call.to_dict() for call in self.calls]
        return out

    @classmethod
    def from_dict(cls, raw: Any) -> "Allowed | None":
        """The version a grant records, or None when it records none it can be held to: a record
        with no name, no version or no digest allows nothing, and a call that does not say all three
        is left out, so the step that would start it does not run."""
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        digest = str(raw.get("digest") or "").strip()
        try:
            version = int(raw.get("version") or 0)
        except (TypeError, ValueError):
            return None
        if not name or version <= 0 or not digest:
            return None
        listed = raw.get("calls")
        calls = tuple(
            call
            for call in (
                cls.from_dict(item) for item in (listed if isinstance(listed, list) else ())
            )
            if call is not None
        )
        return cls(
            name, version, digest, tuple(cls(c.workflow, c.version, c.digest) for c in calls)
        )


@dataclass(frozen=True)
class Current:
    """A workflow's definition as a run started now reads it (`versions.runnable`): its version,
    a digest of what it runs, and who saved it — its recorded version's saver, or, for one no door
    here saved, where it came from (``versions.BROUGHT_IN``, ``SHIPPED`` or ``APP``). ``spec`` is
    ``{}`` for a definition with no usable root, which nothing can run."""

    name: str
    spec: dict[str, Any]
    version: int
    digest: str
    saved_by: str


async def find(name: str) -> tuple[Any, str] | None:
    """The definition named *name* and the provider that holds it, from the first provider that
    has it — the one lookup every run start makes, a step's included. Never raises: a provider that
    fails on the name is passed over."""
    for provider_name in defs_mod.list_providers():
        provider = defs_mod.get_provider(provider_name)
        if provider is None:
            continue
        try:
            found = await provider.get_def(name)
        except Exception:  # noqa: BLE001 - a bad name is a user error, never a traceback
            logger.debug("workflow def provider %s failed on %s", provider_name, name)
            continue
        if found is not None:
            return found, provider_name
    return None


def current_of(name: str, definition: Any, provider_name: str) -> Current:
    """The workflow *name* as a run reads it, from the *definition* :func:`find` found and the
    provider that holds it."""
    try:
        spec = versions.runnable(definition)
    except (ValueError, TypeError):
        return Current(name, {}, 0, "", "")
    version = int(spec.get("version", 1) or 1)
    digest = versions.digest(spec)
    recorded = versions.get_version(name, version)
    if recorded is not None and _digest_of(recorded) == digest:
        saved_by = recorded.saved_by
    elif provider_name == _NATIVE:
        saved_by = versions.BROUGHT_IN
    elif provider_name == _BUNDLED:
        saved_by = versions.SHIPPED
    else:
        saved_by = versions.APP
    return Current(name, spec, version, digest, saved_by)


async def current(name: str) -> Current | None:
    """The workflow *name* as a run started now reads it, or None when no provider has it."""
    found = await find(name)
    if found is None:
        return None
    return current_of(name, *found)


def _blocking(make: Callable[[], Coroutine[Any, Any, _T]]) -> _T:
    """Run the coroutine *make* returns to completion from synchronous code: directly with no loop
    running here, else on a loop of its own in a worker thread, as the workflow tools read a
    definition from their synchronous boundary (``mcp_workflows._run``)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make())

    from personalclaw import memory_writes

    with memory_writes.ScopeCarryingExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(make())).result()


def current_now(name: str) -> Current | None:
    """:func:`current`, from synchronous code: an automation's Allow, and its row."""
    return _blocking(lambda: current(name))


def keep(now: Current) -> None:
    """Make sure the version history holds *now*, so an automation allowed it can still run it once
    the workflow has moved on. A version this machine saved is there already; one that reached it
    some other way — another machine's sync, a restore, a pack, a template an app or PersonalClaw
    ships — is recorded now, said to be saved by where it came from (``now.saved_by``)."""
    if not now.spec or _recorded(now.name, now.version, now.digest) is not None:
        return
    versions.record_version(now.name, now.spec, saved_by=now.saved_by or versions.BROUGHT_IN)


# ── the workflows a workflow runs as steps ───────────────────────────────────────


def called(spec: dict[str, Any] | None) -> list[str]:
    """The workflows *spec* runs as steps, by the names its steps give as written, in order and
    without repeats: a ``subworkflow`` step's ``ref`` (less any ``@version``, which a lookup
    ignores), and a ``run-workflow`` action step's ``workflow``. A name a step takes from a binding
    is known only when it runs, so it is not one of them."""
    from personalclaw.workflows.models import Node, NodeKind, walk

    root = (spec or {}).get("root")
    if not isinstance(root, dict):
        return []
    try:
        nodes = walk(Node.from_dict(root))
    except (ValueError, TypeError, KeyError):
        return []
    out: list[str] = []
    for _path, node in nodes:
        config = node.config or {}
        if node.kind == NodeKind.SUBWORKFLOW:
            name = str(config.get("ref") or "").split("@", 1)[0].strip()
        elif node.kind == NodeKind.ACTION and str(config.get("provider") or "") == _RUNS_A_WORKFLOW:
            picked = config.get("with") or config.get("config")
            name = str((picked if isinstance(picked, dict) else {}).get("workflow") or "").strip()
        else:
            continue
        if name and "{{" not in name and name not in out:
            out.append(name)
    return out


def _depth() -> int:
    """How many levels of steps starting workflows are followed: as deep as a subworkflow nests."""
    from personalclaw.workflows.engine import MAX_SUBWORKFLOW_DEPTH

    return MAX_SUBWORKFLOW_DEPTH


async def closure(
    spec: dict[str, Any] | None, *, root: str = "", keeping: bool = True
) -> tuple[Allowed, ...]:
    """Each workflow *spec* runs as a step, at every depth a step can start another, as it is now:
    the versions a yes to *spec* (the workflow *root*) is a yes to. *keeping*, each is kept in the
    history (:func:`keep`), as the workflow allowed is, so it can still run once it changes; a
    question that only says what a yes would allow keeps nothing. A workflow no provider has, or
    one with no usable spec, is left out: a step that starts it does not run."""
    found: dict[str, Allowed] = {}
    level = [spec or {}]
    for _ in range(_depth()):
        below: list[dict[str, Any]] = []
        for each in level:
            for name in called(each):
                if name == root or name in found:
                    continue
                now = await current(name)
                if now is None or not now.spec:
                    continue
                if keeping:
                    keep(now)
                found[name] = Allowed(name, now.version, now.digest)
                below.append(now.spec)
        level = below
    return tuple(found.values())


def closure_now(
    spec: dict[str, Any] | None, *, root: str = "", keeping: bool = True
) -> tuple[Allowed, ...]:
    """:func:`closure`, from synchronous code: an automation's Allow, and the question before it."""
    return _blocking(lambda: closure(spec, root=root, keeping=keeping))


def bound_of(allowed: Allowed) -> dict[str, Allowed]:
    """The versions, by name, a run of the automation allowed *allowed* may run: its workflow's, and
    each workflow it runs as a step."""
    out = {call.workflow: call for call in allowed.calls}
    out[allowed.workflow] = Allowed(allowed.workflow, allowed.version, allowed.digest)
    return out


def step_versions(bound: dict[str, Allowed], name: str, fire: "Runs") -> dict[str, Allowed]:
    """The versions the steps of a run of *name* may start, in a run an owner-allowed automation
    started: those the run may run (*bound*), and — when the version of *name* it runs (*fire*) is
    a newer one the owner saved herself — the ones that version starts as steps that *bound* does
    not name, as they were at her save. One *bound* names keeps its version there: her save of one
    workflow is no yes to a newer version of another."""
    out: dict[str, Allowed] = {}
    if fire.followed:
        record = versions.get_version(name, fire.version)
        if record is not None:
            out.update(_pins(record.calls))
    out.update(bound)
    return out


def stamp(extra: dict[str, Any], bound: dict[str, Allowed]) -> dict[str, Any]:
    """*extra* for a run whose steps may start the versions *bound* names
    (:data:`STEP_VERSIONS`)."""
    return {
        **extra,
        STEP_VERSIONS: {
            name: {"version": pin.version, "digest": pin.digest} for name, pin in bound.items()
        },
    }


def bound_in(run: Any) -> dict[str, Allowed] | None:
    """The versions the steps of *run* may start (:data:`STEP_VERSIONS`), or None when it is no run
    an owner-allowed automation started, and its steps start each workflow as it is."""
    extra = getattr(run, "extra", None)
    raw = extra.get(STEP_VERSIONS) if isinstance(extra, dict) else None
    return _pins(raw) if isinstance(raw, dict) else None


def _pins(raw: Any) -> dict[str, Allowed]:
    """``{name: {"version", "digest"}}`` as :class:`Allowed` by name, an entry that does not say
    both left out."""
    out: dict[str, Allowed] = {}
    for name, pin in (raw.items() if isinstance(raw, dict) else ()):
        allowed = Allowed.from_dict({**pin, "name": name}) if isinstance(pin, dict) else None
        if allowed is not None:
            out[allowed.workflow] = allowed
    return out


async def owners_calls(spec: dict[str, Any], *, root: str) -> dict[str, dict[str, Any]]:
    """The workflows *spec*, a version of *root* the owner is saving, runs as steps, as they are at
    her save (``VersionRecord.calls``): her save is her yes to them, so an automation that follows
    it runs them so."""
    return {
        call.workflow: {"version": call.version, "digest": call.digest}
        for call in await closure(spec, root=root)
    }


# ── what a fire, and each of its steps, runs ─────────────────────────────────────


@dataclass(frozen=True)
class Runs:
    """What a fire of an automation runs now: the spec (``None`` when nothing can run, and
    :attr:`problem` says why), the version and who saved it, whether that is a newer version the
    owner saved (``followed``), and the workflow as it is now when that is not what runs
    (``newer``)."""

    spec: dict[str, Any] | None
    version: int
    saved_by: str
    followed: bool = False
    newer: Current | None = None
    problem: str = ""


def runs(allowed: Allowed | None, now: Current, *, step: bool = False) -> Runs:
    """What a fire of an automation allowed *allowed* runs, the workflow being *now*; with *step*,
    what a step of its run that starts that workflow runs.

    ``allowed`` None is an automation that runs the workflow as it is (one PersonalClaw's own code
    made), and a run started any other way."""
    if allowed is None:
        return Runs(now.spec or None, now.version, now.saved_by)
    # The owner's own save since her Allow is her yes to it: the newest one is what runs.
    record = _owners_newest_since(now.name, allowed.version)
    followed = record is not None
    wanted = _digest_of(record) if record is not None else allowed.digest
    if now.digest == wanted:
        # The definition as it is now runs as the version it may run (a switch that changes no
        # step leaves it so), so it is what runs.
        return Runs(now.spec, now.version, now.saved_by, followed=followed)
    held = record or _recorded(now.name, allowed.version, allowed.digest)
    if held is None:
        return Runs(None, allowed.version, "", newer=now, problem=_gone(allowed, now, step=step))
    try:
        spec = versions.runnable(held.spec)
    except (ValueError, TypeError):
        return Runs(None, allowed.version, "", newer=now, problem=_gone(allowed, now, step=step))
    return Runs(spec, held.version, held.saved_by, followed=followed, newer=now)


def step_runs(bound: dict[str, Allowed], now: Current) -> Runs:
    """What a step of a run an owner-allowed automation started runs when it starts *now*'s
    workflow, the run being one that may run *bound*: the version allowed with it, or a newer one
    the owner saved herself, by :func:`runs`; nothing, and why, for a workflow it was not allowed
    with."""
    allowed = bound.get(now.name)
    if allowed is None:
        return Runs(None, 0, "", newer=now, problem=_not_allowed(now.name))
    return runs(allowed, now, step=True)


#: What to do about a step that may run nothing, as a step's failure says it.
ASK_THE_AUTOMATION = "open the automation on the Triggers page: it says which versions it may run"


def child_spec(
    parent: Any, name: str, definition: Any, provider_name: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """What a ``subworkflow`` step of the run *parent* starts when it starts *name*, which
    :func:`find` found as *definition* in *provider_name*: ``(spec, extra, "")`` — the spec, and
    what the child's record carries for its own steps (:func:`stamp`) — or ``({}, {}, why)`` when it
    may start nothing. A run an owner-allowed automation started starts the version allowed with it
    (:func:`step_runs`); any other run's step, the definition as it is."""
    bound = bound_in(parent)
    if bound is None:
        return (definition if isinstance(definition, dict) else definition.to_dict()), {}, ""
    fire = step_runs(bound, current_of(name, definition, provider_name))
    if fire.spec is None:
        return {}, {}, fire.problem
    return fire.spec, stamp({}, step_versions(bound, name, fire)), ""


@dataclass(frozen=True)
class Step:
    """A workflow a fire runs as a step (``via`` is the workflow whose step starts it), and what
    that step runs (:func:`step_runs`)."""

    workflow: str
    via: str
    runs: Runs


async def steps(name: str, fire: Runs, bound: dict[str, Allowed] | None) -> list[Step]:
    """Each workflow a fire that runs *fire* (a version of *name*) starts as a step, at every depth,
    and what each step runs, its run being one that may run *bound* (None for a run whose steps
    start each workflow as it is): what the automation's row says about them."""
    out: list[Step] = []
    seen = {name}
    level: list[tuple[str, Runs, dict[str, Allowed] | None]] = [(name, fire, bound)]
    for _ in range(_depth()):
        below: list[tuple[str, Runs, dict[str, Allowed] | None]] = []
        for via, ran, held in level:
            if ran.spec is None:
                continue
            under = step_versions(held, via, ran) if held is not None else None
            for child in called(ran.spec):
                if child in seen:
                    continue
                seen.add(child)
                now = await current(child)
                if now is None or not now.spec:
                    missing = f"there is no workflow named “{child}” that can run"
                    out.append(Step(child, via, Runs(None, 0, "", problem=missing)))
                    continue
                child_runs = step_runs(under, now) if under is not None else runs(None, now)
                out.append(Step(child, via, child_runs))
                below.append((child, child_runs, under))
        level = below
    return out


def steps_now(name: str, fire: Runs, bound: dict[str, Allowed] | None) -> list[Step]:
    """:func:`steps`, from synchronous code: the automation's row."""
    return _blocking(lambda: steps(name, fire, bound))


def saved_since(name: str, version: int, now: Current) -> list[tuple[int, str]]:
    """``(version, who saved it)`` for each version of *name* after *version*, up to the definition
    as it is now: what has changed since the version an automation runs, and by whose save."""
    out = [
        (number, record.saved_by)
        for number in versions.recorded_numbers(name)
        if version < number <= now.version
        for record in [versions.get_version(name, number)]
        if record is not None
    ]
    if now.version > version and all(number != now.version for number, _ in out):
        out.append((now.version, now.saved_by))
    return out


def _gone(allowed: Allowed, now: Current, *, step: bool = False) -> str:
    """Why a fire, or one of its steps, did not run: the version it was allowed is not in the
    history any more."""
    said = (
        f"“{now.name}” has changed since this automation was allowed to run it, and the version it "
        f"was allowed (v{allowed.version}) is no longer kept here, so "
    )
    if step:
        return (
            f"{said}this step did not start it. To run version {now.version}, open the automation "
            "on the Triggers page and use its newer versions, which asks you first."
        )
    return (
        f"{said}it did not run. To run version {now.version}, open the automation on the Triggers "
        f"page and choose Use v{now.version}, which asks you first."
    )


def _not_allowed(name: str) -> str:
    """Why a step of an allowed automation's run did not start the workflow *name*."""
    return (
        f"this automation was not allowed to run “{name}”, so this step did not start it: a step "
        "starts only a workflow named in the version of its workflow the automation was allowed, "
        "or in one you saved yourself. A step that names it from a value it is handed never is."
    )


def _digest_of(record: versions.VersionRecord) -> str:
    try:
        return versions.digest(record.spec)
    except (ValueError, TypeError):
        return ""


def _recorded(name: str, version: int, digest: str) -> versions.VersionRecord | None:
    """The recorded version of *name* that runs as *digest* does: the one numbered *version* when it
    does, else the newest that does (`versions.holding`), else None."""
    record = versions.get_version(name, version)
    if record is not None and _digest_of(record) == digest:
        return record
    return versions.holding(name, digest)


def _owners_newest_since(name: str, version: int) -> versions.VersionRecord | None:
    """The newest version of *name* after *version* that the owner saved herself, or None."""
    for number in reversed([n for n in versions.recorded_numbers(name) if n > version]):
        record = versions.get_version(name, number)
        if record is not None and record.saved_by == versions.OWNER:
            return record
    return None
