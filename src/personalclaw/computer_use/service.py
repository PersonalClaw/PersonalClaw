"""In-gateway dispatch for desktop computer use — the chain, in order (`DCU-4`).

This module is the only place a computer-use decision is made, and
:func:`computer_dispatch` is the only dispatchable entry point in the package. Everything
else is a step it calls, in the order DESKTOP-COMPUTER-USE §2 lays out:

===== ============================================ ==================================
step   what                                          owner
===== ============================================ ==================================
1      keystone enable (out-of-band document)        ``enable_state.require_enabled``
2      target-app allowlist                          ``policy.check_app``
3      index freshness (TTL) + fingerprint re-walk    :func:`_require_fresh_element`
4      secure-field / sensitive-text screen           ``policy.check_input_target``
5      SEL audit — records, never decides             ``gate.require_computer_use``
6      platform driver, as a ceilinged subprocess     :func:`_run_driver`
7      re-snapshot + redact the result                :func:`_redact_result`
===== ============================================ ==================================

**Composition is this atom's deliverable, and the ORDER is the substance of it.** `DCU-2`
shipped steps 2/4/5 as three correct, tested, and provably inert functions — its own audit
censused the production callers and found zero, which is why
``tests/test_computer_use_call_sites.py`` exists. Wiring them is therefore not "add three
calls": a screen that runs after the driver has already pressed the button is not a screen, so
``test_the_chain_runs_every_screen_before_the_acting_driver_call`` records the actual sequence
at runtime and asserts it, rather than asserting that each step happened somewhere.

**Why the re-walk (step 3) is allowed to touch the driver before step 4 runs.** It is a READ,
and ``policy.check_input_target``'s own docstring requires it: the screen must see *"the
element that will be typed into, not a stale row describing something the user has since
replaced"*. So the element handed to step 4 comes from the re-walk, never from the stored
snapshot. The ordering rail states this precisely — every screen precedes the **acting** driver
call, and the only driver interaction before them is the re-walk.

**Exactly one SEL row per attempt, and the allowed path writes one too.** `DCU-2` left this
open by design: ``policy`` raises without recording, because ``gate`` is a separate step *a
caller must remember* — and this module is that caller. Every exit from the chain passes
through :func:`_audit` exactly once: a refusal records ``outcome="denied"`` with the refusal's
stable code, and an approved attempt records ``outcome="approved"`` **before** the driver runs.
Before, not after, for the reason the plan puts the audit at step 5: a driver that wedges, is
killed by its ceiling, or crashes the machine must still have left evidence that the attempt
was made and permitted. The consequence is deliberate — the row records the *verdict*, not the
outcome of the action, and a driver failure is reported to the caller rather than written as a
second row. One attempt, one row, so "every attempt is audited" stays countable.

**The driver is a subprocess, not an import.** §3.5 requires the driver to run as a ceilinged
spawn so *"a wedged/looping driver is bounded by the kernel, not just a userspace timeout"*.
`DCU-3` will ship the macOS accessibility FFI; this atom ships the harness it runs inside —
:mod:`personalclaw.computer_use.driver_host`, spawned through
``sandbox.create_subprocess_limited`` (the one seam that carries the resource ceiling) with a
userspace timeout on top. Today that child answers every operation with a typed
"no accessibility driver for this platform" refusal, which is an honest refusal reached through
the real spawn — not a stub the dispatch fakes on the driver's behalf.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, NoReturn

from personalclaw.computer_use import enable_state, gate, policy
from personalclaw.computer_use.tools import TOOLS_BY_NAME, ToolSpec
from personalclaw.errors import AgentError

logger = logging.getLogger(__name__)

ERR_UNKNOWN_TOOL = "ERR_COMPUTER_USE_UNKNOWN_TOOL"
ERR_BAD_ARGUMENT = "ERR_COMPUTER_USE_BAD_ARGUMENT"
ERR_STALE_INDEX = "ERR_COMPUTER_USE_STALE_INDEX"
ERR_DRIVER_UNAVAILABLE = "ERR_COMPUTER_USE_DRIVER_UNAVAILABLE"
ERR_DRIVER_FAILED = "ERR_COMPUTER_USE_DRIVER_FAILED"
ERR_AX_PERMISSION = "ERR_COMPUTER_USE_AX_PERMISSION"
ERR_PLATFORM_UNSUPPORTED = "ERR_COMPUTER_USE_PLATFORM_UNSUPPORTED"

#: The codes the driver child may name for itself. An allowlist, still — the reasoning in
#: :func:`_run_driver` is that a child able to pick any code could dress a failure up as a
#: verdict the policy never reached. Every member is a REFUSAL the child is the only party able
#: to determine: it alone re-walked the tree at the moment of acting (stale index), it alone
#: asked the OS whether input access is granted (accessibility permission), and it alone resolved
#: which platform driver this machine actually has (`DCU-6`'s Windows/Linux refusal — the parent
#: never imports a driver, so it cannot know). A child naming one of these can only cause a
#: refusal, never an approval, and the "approved" audit row is already written before the child
#: runs — so honouring them cannot alter a decision, only explain it.
_CHILD_CODES = (
    ERR_DRIVER_UNAVAILABLE,
    ERR_DRIVER_FAILED,
    ERR_AX_PERMISSION,
    ERR_STALE_INDEX,
    ERR_PLATFORM_UNSUPPORTED,
)

#: How long a snapshot's element indices stay actable, in seconds.
#:
#: A bound, so it is tested from both sides: at exactly this age the call proceeds, one tick
#: past it refuses (``test_an_index_at_the_ttl_boundary_still_acts`` /
#: ``test_an_index_one_tick_past_the_ttl_refuses``). Thirty seconds is short enough that a
#: human who has moved on has almost certainly invalidated the tree, and long enough for a
#: model to read a snapshot and decide. The comparison is ``<=``: the boundary belongs to the
#: side that still works, because a bound that refuses *at* its documented value teaches
#: operators the documented value is wrong.
SNAPSHOT_TTL_SECS = 30.0

#: How many snapshots stay actable at once. A ceiling on memory a model can grow by looping on
#: ``computer_snapshot``; the oldest is evicted, and acting on an evicted id refuses visibly
#: (as a stale index) rather than silently acting on the wrong tree.
MAX_LIVE_SNAPSHOTS = 16

#: Userspace bound on one driver operation. The kernel ceiling on the spawn is the real
#: containment (§3.5); this is what turns a wedged child into a legible refusal instead of a
#: gateway request that never answers.
DRIVER_TIMEOUT_SECS = 20.0

#: The child module the driver runs in. Named here rather than in :func:`_driver_argv` so the
#: spawn-ceiling census and the tests can name the same string this module executes.
DRIVER_CHILD_MODULE = "personalclaw.computer_use.driver_host"

#: Click methods, and which of them touch a pointer. ``auto`` is the default and the only
#: value that resolves to a pure accessibility press — §3 floor 2: *"Coordinate/global paths
#: must be explicitly named by the model"*, and ``auto`` never resolves onto them.
_CLICK_METHODS = ("auto", "located", "global")
_POINTER_METHODS = ("located", "global")


class ComputerUseRefusal(Exception):
    """The dispatch refused. Carries an :class:`AgentError`, like the other two refusals.

    A third exception class beside ``ComputerUseDisabled`` and ``ComputerUsePolicyRefusal``
    rather than a reuse of either: those two mean "the keystone says no" and "the policy says
    no", and this one means "the request is not one this chain can run" (unknown tool, missing
    argument, expired index, absent driver). Collapsing them would make the handler unable to
    tell an operator-fixable refusal from a model-fixable one — and they carry different FIX
    lines for exactly that reason. All three are discriminated by
    :attr:`AgentError.code`, and the dispatch catches all three in one place.
    """

    def __init__(self, error: AgentError) -> None:
        super().__init__(error.render())
        self.error = error


@dataclass(frozen=True)
class Snapshot:
    """One accessibility walk, as the dispatch remembers it between calls.

    ``fingerprint`` is the driver's own summary of the tree it walked. It is stored and
    compared, never recomputed here: the dispatch has no accessibility API and a fingerprint it
    derived from the *stored* elements would compare a value against itself and always agree —
    a check that cannot fail, which is the defect shape this codebase keeps finding.
    """

    snapshot_id: str
    app: str
    fingerprint: str
    taken_at: float
    elements: tuple[dict[str, Any], ...]


#: Live snapshots, newest last. Process-local: an index is a handle on a window the *gateway*
#: walked, so it cannot outlive the process that walked it, and persisting one would let a
#: restart resurrect indices pointing at a desktop that has since changed completely.
_SNAPSHOTS: dict[str, Snapshot] = {}


def reset_snapshots() -> None:
    """Drop every live snapshot. For tests, and for a keystone re-read after a restart."""
    _SNAPSHOTS.clear()


def _refuse(
    code: str, *, what: str, why: str, fix: str, suggestions: tuple[str, ...] = ()
) -> NoReturn:
    """Raise one refusal with the three lines a model can act on.

    Typed ``NoReturn`` so every call site is a real terminator to the type checker as well as
    at runtime — otherwise each one needs a dead ``raise`` after it to convince mypy, and a
    dead raise is indistinguishable from a guard someone forgot to finish.
    """
    raise ComputerUseRefusal(
        AgentError(code=code, what=what, why=why, fix=fix, suggestions=suggestions)
    )


def _audit(*, tool: str, app: str, outcome: str, error: str, source: str, identity: str) -> None:
    """Step 5. Write exactly one SEL row for this attempt. Never decides, never raises.

    Every exit from the chain reaches here once — the refusal paths with ``outcome="denied"``
    and the approved path with ``outcome="approved"``. ``metadata`` is left empty rather than
    filled with the tool's arguments: ``gate`` would reduce any string to a shape summary
    anyway (window titles and typed text are personal data the SEL does not redact), so
    passing them would buy nothing and read as though it did.
    """
    gate.require_computer_use(
        tool=tool,
        app=app,
        outcome=outcome,
        error=error,
        source=source or "background",
        caller_identity=identity,
    )


def _spec(tool: str) -> ToolSpec:
    """The tool's declaration, or a refusal naming the seven that exist."""
    spec = TOOLS_BY_NAME.get(tool)
    if spec is None:
        _refuse(
            ERR_UNKNOWN_TOOL,
            what=f"{tool!r} is not a computer-use tool.",
            why=(
                "The dispatch accepts exactly the seven tools the surface declares; a name it "
                "does not know cannot be given a safe meaning by guessing."
            ),
            fix="Call one of the tools listed below.",
            suggestions=tuple(sorted(TOOLS_BY_NAME)),
        )
    return spec


def _string_arg(params: dict[str, Any], key: str, *, tool: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        _refuse(
            ERR_BAD_ARGUMENT,
            what=f"{tool} needs a non-empty string {key!r}.",
            why=f"Got {type(value).__name__}, which names no target.",
            fix=f"Pass {key!r} as a string.",
        )
    return str(value).strip()


def _int_arg(params: dict[str, Any], key: str, *, tool: str) -> int:
    value = params.get(key)
    # bool is an int in Python and True would read as index 1 — a caller that passed a flag
    # where an index goes has made a mistake worth reporting, not one worth silently honouring.
    if isinstance(value, bool) or not isinstance(value, int):
        _refuse(
            ERR_BAD_ARGUMENT,
            what=f"{tool} needs an integer {key!r}.",
            why=f"Got {type(value).__name__}.",
            fix=f"Pass {key!r} as a zero-based integer index.",
        )
    return int(value)


def _now() -> float:
    """The clock the TTL reads. A named seam so a test can move time without sleeping."""
    return time.monotonic()


def _remember(app: str, fingerprint: str, elements: list[dict[str, Any]]) -> Snapshot:
    """Store one walk and return its handle, evicting the oldest past the ceiling."""
    snap = Snapshot(
        snapshot_id=uuid.uuid4().hex[:16],
        app=app,
        fingerprint=str(fingerprint),
        taken_at=_now(),
        elements=tuple(elements),
    )
    _SNAPSHOTS[snap.snapshot_id] = snap
    while len(_SNAPSHOTS) > MAX_LIVE_SNAPSHOTS:
        _SNAPSHOTS.pop(next(iter(_SNAPSHOTS)))
    return snap


def _stale(snapshot_id: str, *, tool: str, detail: str) -> NoReturn:
    _refuse(
        ERR_STALE_INDEX,
        what=f"Snapshot {snapshot_id!r} can no longer be acted on: {detail}.",
        why=(
            "An element index only means anything against the tree it came from. Acting on a "
            "stale index would press whatever now sits at that position, which is how an "
            "automation clicks the wrong button."
        ),
        fix=f"Call computer_snapshot again and use the new index for {tool}.",
    )


def _resolve_snapshot(tool: str, params: dict[str, Any]) -> Snapshot:
    """Step 3a — the half of index freshness that needs no driver: which tree, and how old.

    Split out and run BEFORE ``policy.check_app`` on purpose. The app an acting call targets is
    a property of the snapshot, not of the arguments — a caller able to re-assert it could pass
    an allowlisted name beside an index taken from a window that was never allowlisted. So the
    id resolves first, and the *stored* app is what step 2 screens. Nothing here touches the
    desktop, so an off-limits app is still learned before any window is walked, which is the
    ordering ``policy.check_app``'s docstring asks for.
    """
    snapshot_id = _string_arg(params, "snapshot_id", tool=tool)
    snap = _SNAPSHOTS.get(snapshot_id)
    if snap is None:
        _stale(snapshot_id, tool=tool, detail="no such snapshot is live in this gateway")
    age = _now() - snap.taken_at
    if age > SNAPSHOT_TTL_SECS:
        _stale(
            snapshot_id,
            tool=tool,
            detail=f"it is {age:.1f}s old and indices expire after {SNAPSHOT_TTL_SECS:.0f}s",
        )
    return snap


async def _require_fresh_element(
    tool: str, snap: Snapshot, params: dict[str, Any]
) -> dict[str, Any]:
    """Step 3b. Re-walk *snap*'s app, confirm the fingerprint, and return the CURRENT element.

    Two refusals, both fail-closed: a fingerprint the re-walk no longer matches, and an index
    outside the tree that came back. Returns the element from the **re-walked** tree, never the
    stored one, so step 4 screens exactly what step 6 will touch — which is the ordering
    ``policy.check_input_target``'s docstring requires (*"the element screened is the element
    that will be typed into, not a stale row"*).
    """
    index = _int_arg(params, "element_index", tool=tool)
    fresh = await _run_driver("snapshot", {"app": snap.app}, tool=tool)
    if str(fresh.get("fingerprint") or "") != snap.fingerprint:
        _stale(snap.snapshot_id, tool=tool, detail="the window has changed since it was walked")
    raw = fresh.get("elements")
    elements = list(raw) if isinstance(raw, list) else []
    if not 0 <= index < len(elements):
        _refuse(
            ERR_BAD_ARGUMENT,
            what=f"element_index {index} is outside snapshot {snap.snapshot_id!r}.",
            why=f"That window exposes {len(elements)} element(s), indexed 0..{len(elements) - 1}.",
            fix="Use an index from the snapshot's own element list.",
        )
    element = elements[index]
    if not isinstance(element, dict):
        _refuse(
            ERR_BAD_ARGUMENT,
            what=f"element {index} of snapshot {snap.snapshot_id!r} is not an element object.",
            why=f"The driver described it as {type(element).__name__}.",
            fix="Re-snapshot; if it persists the driver is producing malformed elements.",
        )
    return element


def _driver_argv() -> list[str]:
    """The child's argv. The ceiling is prepended by ``create_subprocess_limited``, not here.

    ``sys.executable -m <module>`` rather than a console script: the gateway may be running
    from a venv whose ``bin`` is not on ``PATH``, and a driver that silently resolved to a
    *different* interpreter's PersonalClaw would read the wrong home — the wrong keystone.
    """
    return [sys.executable, "-m", DRIVER_CHILD_MODULE]


async def _run_driver(op: str, payload: dict[str, Any], *, tool: str) -> dict[str, Any]:
    """Step 6 (and step 3's read). Run one operation in a ceilinged child process.

    ``sandbox.create_subprocess_limited`` is the repo's single seam for an agent-influenced
    spawn — it prepends the post-exec ceiling shim and never uses ``preexec_fn`` (PHF-1), so
    the gateway's event loop is not forked. ``tests/test_spawn_ceiling_audit.py`` classifies
    this call site as ceiling-wrapped, which is what keeps the ceiling from being quietly
    dropped later.

    Every failure mode becomes a typed refusal, never a silent empty result: a timeout, a
    non-zero exit, unparseable output, and the child's own "no driver for this platform" all
    reach the model as WHAT/WHY/FIX. §3 floor 6 is explicit that an unsupported platform
    reports a typed refusal and *"never a silent no-op or a simulated success"*.
    """
    from personalclaw.sandbox import PROFILE_TOOL, create_subprocess_limited

    request = json.dumps({"op": op, **payload}).encode()
    try:
        proc = await create_subprocess_limited(
            *_driver_argv(),
            profile=PROFILE_TOOL,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(request), timeout=DRIVER_TIMEOUT_SECS
        )
    except asyncio.TimeoutError:
        _refuse(
            ERR_DRIVER_FAILED,
            what=f"The desktop driver did not answer {tool} within " f"{DRIVER_TIMEOUT_SECS:.0f}s.",
            why=(
                "The child was killed rather than left running. A driver that stops answering "
                "must not turn into a request that never returns."
            ),
            fix="Retry once; if it repeats, the target application is not responding to the "
            "accessibility API.",
        )
    except Exception as exc:  # noqa: BLE001 - a failed spawn is a reported refusal
        logger.debug("computer-use driver spawn failed for %s", tool, exc_info=True)
        _refuse(
            ERR_DRIVER_FAILED,
            what=f"The desktop driver could not be started for {tool}.",
            why=f"{type(exc).__name__}: {exc}",
            fix="Check the gateway log; the driver runs as a separate ceilinged process.",
        )

    try:
        answer = json.loads(stdout.decode() or "{}")
    except Exception:
        answer = None
    if not isinstance(answer, dict):
        _refuse(
            ERR_DRIVER_FAILED,
            what=f"The desktop driver returned no usable answer for {tool}.",
            why=f"exit={proc.returncode} stderr={stderr.decode()[:200]!r}",
            fix="Check the gateway log; this is a driver defect, not a policy refusal.",
        )
    err = answer.get("error")
    if err:
        # The child's code is honoured only when it is one of the refusals in _CHILD_CODES.
        # Anything else is reported as a driver failure rather than forwarded: a child able to
        # name an arbitrary code could dress a failure up as, say, a policy refusal, and the
        # audit row would then record a verdict the policy never reached.
        detail = str(err.get("message") or err) if isinstance(err, dict) else str(err)
        raw_code = str(err.get("code") or "") if isinstance(err, dict) else ""
        _refuse(
            raw_code if raw_code in _CHILD_CODES else ERR_DRIVER_FAILED,
            what=f"{tool} could not run: {detail}",
            why=str(err.get("why") or "") if isinstance(err, dict) else "",
            fix=str(err.get("fix") or "See the gateway log.") if isinstance(err, dict) else "",
        )
    return answer


def _redact_result(spec: ToolSpec, result: dict[str, Any]) -> dict[str, Any]:
    """Step 7. Narrow and redact what goes back to the model.

    Two jobs, both fail-closed:

    * ``computer_list_apps`` is narrowed to the operator's allowlist, and reports how many
      applications were withheld. An armed machine granting "drive TextEdit" should not
      thereby disclose the full list of what the operator has open; the count keeps the
      narrowing honest instead of pretending nothing was hidden.
    * every string a driver produced is passed through
      :func:`personalclaw.security.redact_credentials`, this codebase's one definition of
      credential-shaped text. A field value the system redacts on the way out of a log is one
      it must not hand to a model out of a window.
    """
    from personalclaw.security import redact_credentials

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            cleaned, _warnings = redact_credentials(value)
            return cleaned
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    out = dict(result)
    if spec.name == "computer_list_apps":
        allowed = {name.strip() for name in enable_state.allowed_apps()}
        apps = out.get("apps")
        apps = apps if isinstance(apps, list) else []
        kept = [app for app in apps if isinstance(app, str) and app.strip() in allowed]
        out["apps"] = kept
        out["withheld"] = len(apps) - len(kept)
    return dict(scrub(out))


def _click_method(params: dict[str, Any]) -> str:
    """Resolve the click method. ``auto`` NEVER becomes a pointer method.

    §3 floor 2 in one function: an absent or empty method is ``auto`` (an accessibility press,
    no pointer involved), and the two methods that post or warp a real pointer are reachable
    only by a model naming them. Resolution never *widens* — there is no fallback from ``auto``
    to a coordinate click when an element press is unavailable, because that fallback is how a
    cursor moves by accident.
    """
    raw = params.get("click_method")
    method = raw.strip() if isinstance(raw, str) and raw.strip() else "auto"
    if method not in _CLICK_METHODS:
        _refuse(
            ERR_BAD_ARGUMENT,
            what=f"{method!r} is not a click method.",
            why="Only the declared methods exist, and an unknown one cannot be resolved safely.",
            fix="Use 'auto' unless you specifically need a coordinate click.",
            suggestions=_CLICK_METHODS,
        )
    return method


def _operation(tool: str, params: dict[str, Any]) -> str:
    """The SEL ``operation`` for this attempt — per-tool, and per pointer method.

    §2 wants the pointer paths distinguishable in the audit ("each emits a distinct SEL
    ``tool_kind``"); ``SecurityEvent`` documents ``tool_kind`` as a *category*, so the
    distinctness lives in ``operation``, which is the field documented to hold the tool name.
    A real-cursor warp is therefore one filter away from every other click.
    """
    if tool == "computer_click":
        method = params.get("click_method")
        method = method.strip() if isinstance(method, str) and method.strip() else "auto"
        if method in _POINTER_METHODS:
            return f"{tool}:{method}"
    return tool


async def computer_dispatch(
    tool: str,
    params: dict[str, Any] | None = None,
    *,
    source: str = "",
    caller_identity: str = "",
) -> dict[str, Any]:
    """Run one computer-use call through the whole chain. The ONLY entry point.

    Raises :class:`~personalclaw.computer_use.enable_state.ComputerUseDisabled`,
    :class:`~personalclaw.computer_use.policy.ComputerUsePolicyRefusal` or
    :class:`ComputerUseRefusal` — all three carrying an :class:`AgentError` — so the HTTP
    handler renders one envelope and the model reads one voice. Returns the driver's result,
    narrowed and redacted, on success.

    The keystone is the first statement executed, which is what
    ``test_every_computer_use_entry_point_guards_first`` requires of every ``computer_*``
    function in this package. It is wrapped only so a refusal is *audited* before it
    propagates: `DCU-2`'s clause is "every attempt, allowed or refused, produces a SEL record",
    and an unaudited keystone refusal would leave the most interesting attempt of all — one
    made against a machine the operator never armed — with no trace.
    """
    try:
        enable_state.require_enabled(tool)
    except enable_state.ComputerUseDisabled as exc:
        _audit(
            tool=tool,
            app="",
            outcome="denied",
            error=exc.error.code,
            source=source,
            identity=caller_identity,
        )
        raise

    args = dict(params or {})
    app = ""
    snap: Snapshot | None = None
    try:
        spec = _spec(tool)
        # A coordinate click carries no element index — §2 reserves the coordinate path for
        # canvas/custom-drawn UI that exposes no addressable element, so there is nothing to
        # index. It is NOT a bypass of the chain: the method must be named by the model, the
        # app must be named and allowlisted, and the attempt is audited under its own
        # ``operation`` so a real-cursor warp is one filter away from every ordinary click.
        by_index = spec.screen_index
        if spec.name == "computer_click" and _click_method(args) in _POINTER_METHODS:
            by_index = False
        if by_index:
            snap = _resolve_snapshot(tool, args)  # step 3a — which tree, and how old
            app = snap.app
        elif spec.screen_app:
            app = _string_arg(args, "app", tool=tool)
        if spec.screen_app:
            policy.check_app(app, tool=tool)  # step 2 — before any window is walked
        element: dict[str, Any] = {}
        if by_index and snap is not None:
            element = await _require_fresh_element(tool, snap, args)  # step 3b — re-walk
        if spec.screen_input_target:
            policy.check_input_target(element, tool=tool)  # step 4
    except (
        enable_state.ComputerUseDisabled,
        policy.ComputerUsePolicyRefusal,
        ComputerUseRefusal,
    ) as exc:
        _audit(
            tool=tool,
            app=app,
            outcome="denied",
            error=exc.error.code,
            source=source,
            identity=caller_identity,
        )
        raise

    _audit(  # step 5 — the ALLOWED row, written before the driver runs
        tool=_operation(tool, args),
        app=app,
        outcome="approved",
        error="",
        source=source,
        identity=caller_identity,
    )

    driver_payload = dict(args)
    if snap is not None:
        driver_payload["app"] = snap.app
        driver_payload["fingerprint"] = snap.fingerprint
    result = await _run_driver(_driver_op(spec), driver_payload, tool=tool)  # step 6
    if spec.name == "computer_snapshot":
        elements = result.get("elements")
        stored = _remember(
            app,
            str(result.get("fingerprint") or ""),
            list(elements) if isinstance(elements, list) else [],
        )
        result = {**result, "snapshot_id": stored.snapshot_id, "app": stored.app}
    return _redact_result(spec, result)  # step 7


def _driver_op(spec: ToolSpec) -> str:
    """The driver-side operation name: the tool name without its ``computer_`` prefix.

    Derived rather than tabulated. A second table mapping seven tools to seven ops is a second
    place to add a tool and forget, and the failure would be silent — a missing entry would
    read as "no operation" rather than as an error.
    """
    return spec.name[len("computer_") :]
