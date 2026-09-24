"""APE-3 contract half: the background-worker contract an app writes against.

Covers the four properties the atom's done-when clauses rest on:

* the SDK facade and the core contract are the SAME objects (an installed app must not end
  up holding a different class than the host checks against);
* a worker is accepted or rejected at the contract boundary, with a legible error;
* stop is cooperative but does NOT depend on cooperation for correctness;
* pause and stop are DIFFERENT states — the one assertion that stops a future runtime
  overloading a single boolean for "come back later" and "never come back".

Everything here is in-process. Supervision (spawn / watchdog / PPID reaping) is the
parent-side half and is tested against real subprocesses elsewhere.
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw.apps import background as core_background
from personalclaw.apps.manifest import Permissions
from personalclaw.sdk import background as sdk_background
from personalclaw.sdk.background import (
    BACKGROUND_TASKS_PERMISSION,
    DEFAULT_POLL_INTERVAL,
    WORKER_APP_ENV,
    WORKER_DATA_DIR_ENV,
    WORKER_GRANT_ENV,
    WORKER_ID_ENV,
    BackgroundWorker,
    PauseReason,
    StopReason,
    WorkerContext,
    WorkerContractError,
    WorkerControl,
    WorkerState,
    run_worker,
)

CORE_SRC = Path(core_background.__file__)
FACADE_SRC = Path(sdk_background.__file__)


def _valid_env(**over: str) -> dict[str, str]:
    env = {
        WORKER_GRANT_ENV: BACKGROUND_TASKS_PERMISSION,
        WORKER_APP_ENV: "fixture-app",
        WORKER_ID_ENV: "poller",
    }
    env.update(over)
    return env


# ---------------------------------------------------------------------------
# 1. the facade is the SAME objects, not same-named copies
# ---------------------------------------------------------------------------


def test_facade_reexports_exactly_the_contracts_public_names():
    assert set(sdk_background.__all__) == set(core_background.__all__)
    # And the core module has no public name the facade silently drops.
    core_public = {n for n in dir(core_background) if not n.startswith("_")}
    # Module-level imports the contract uses internally are not part of its surface.
    core_public -= {"ABC", "Enum", "Path", "abstractmethod", "dataclass", "field", "logger"}
    core_public -= {"annotations", "logging", "os", "signal", "threading"}
    assert core_public == set(core_background.__all__), (
        "personalclaw.apps.background exposes public names outside its __all__; either add "
        "them to both __all__s or make them private:\n"
        f"  {sorted(core_public - set(core_background.__all__))}"
    )


@pytest.mark.parametrize("name", sorted(sdk_background.__all__))
def test_facade_object_is_identical_to_the_core_object(name):
    """Identity, not name equality. A facade that re-exports a COPY is how an installed app
    ends up holding a different class than the runtime isinstance-checks against."""
    assert getattr(sdk_background, name) is getattr(core_background, name), (
        f"personalclaw.sdk.background.{name} is not the same object as "
        f"personalclaw.apps.background.{name} — the facade re-exported a copy"
    )


# ---------------------------------------------------------------------------
# 2. the facade is ONLY a facade (sdk/action.py's shape)
# ---------------------------------------------------------------------------


def _facade_violations(source: str) -> list[str]:
    """Module-scope statements that are not {docstring, ImportFrom, ``__all__`` assign}."""
    tree = ast.parse(source)
    bad: list[str] = []
    for i, node in enumerate(tree.body):
        if i == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue  # module docstring
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign) and all(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            continue
        bad.append(f"{type(node).__name__} at line {node.lineno}")
    return bad


def test_facade_defines_no_logic_of_its_own():
    assert _facade_violations(FACADE_SRC.read_text()) == [], (
        "personalclaw/sdk/background.py must be a re-export facade like sdk/action.py: "
        "docstring + import + __all__, nothing else. Logic belongs in apps/background.py."
    )


def test_facade_shape_check_is_not_vacuous():
    """The check above would notice logic if it were added — including a lambda alias,
    which is the sneaky way a 'facade' starts wrapping the thing it re-exports."""
    added_function = FACADE_SRC.read_text() + "\n\ndef helper():\n    return 1\n"
    assert _facade_violations(added_function), "the facade check misses a FunctionDef"
    added_class = FACADE_SRC.read_text() + "\n\nclass Shim(BackgroundWorker):\n    pass\n"
    assert _facade_violations(added_class), "the facade check misses a ClassDef"
    added_alias = FACADE_SRC.read_text() + "\n\nrun = lambda w: run_worker(w)\n"
    assert _facade_violations(added_alias), "the facade check misses a lambda alias"


# ---------------------------------------------------------------------------
# 3. the worker shape: accepted, or rejected legibly
# ---------------------------------------------------------------------------


class _Conforming(BackgroundWorker):
    """The minimal worker: one method, one unit of work."""

    poll_interval = 0.0

    def __init__(self, control: WorkerControl) -> None:
        self.control = control
        self.cycles = 0

    def run_once(self, ctx: WorkerContext) -> None:
        self.cycles += 1
        self.control.request_stop(StopReason.DISABLED)


def test_a_minimal_conforming_worker_is_accepted_and_driven():
    control = WorkerControl()
    ctx = WorkerContext.from_env(_valid_env(), control=control)
    worker = _Conforming(control)
    assert run_worker(worker, ctx, install_signals=False) is WorkerState.STOPPED
    assert worker.cycles == 1


def test_the_abcs_default_cadence_is_the_documented_constant():
    """A worker that declares no cadence inherits DEFAULT_POLL_INTERVAL, not a hot loop."""
    assert BackgroundWorker.poll_interval == DEFAULT_POLL_INTERVAL
    assert DEFAULT_POLL_INTERVAL > 0, "an omitted cadence must not mean 'spin'"


def test_a_non_conforming_worker_is_rejected_before_it_ever_runs():
    class NotAWorker:
        def run_once(self, ctx):  # right method name, wrong lineage
            raise AssertionError("must never be driven")

    with pytest.raises(WorkerContractError) as exc:
        run_worker(NotAWorker(), install_signals=False)
    msg = str(exc.value)
    assert "BackgroundWorker" in msg and "run_once" in msg
    assert "NotAWorker" in msg


def test_a_worker_missing_run_once_fails_at_construction_not_at_runtime():
    class Incomplete(BackgroundWorker):
        pass

    with pytest.raises(TypeError) as exc:
        Incomplete()  # type: ignore[abstract]
    assert "run_once" in str(exc.value)


def test_the_validation_runs_before_the_env_handshake():
    """A bad worker reports the WORKER problem even with no host env present, so an app
    author is not misdirected to the environment by their own type error."""
    with pytest.raises(WorkerContractError) as exc:
        run_worker(object(), install_signals=False)  # type: ignore[arg-type]
    assert WORKER_GRANT_ENV not in str(exc.value)


# ---------------------------------------------------------------------------
# 4. cooperative stop — and the fact that correctness does not depend on it
# ---------------------------------------------------------------------------


def test_stop_flag_is_observable_and_interrupts_a_wait():
    control = WorkerControl()
    assert control.should_stop() is False
    assert control.wait(0.0) is False
    control.request_stop(StopReason.DISABLED)
    assert control.should_stop() is True
    assert control.stop_reason is StopReason.DISABLED
    # wait() returns immediately once stopped rather than sleeping out the interval.
    assert control.wait(30.0) is True


class _Polling(BackgroundWorker):
    """Cooperative: polls mid-unit and abandons the rest of the unit when asked."""

    poll_interval = 0.0

    def __init__(self, control: WorkerControl) -> None:
        self.control = control
        self.cycles = 0
        self.observed_stop = False
        self.work_after_stop = 0
        self.torn_down = False

    def run_once(self, ctx: WorkerContext) -> None:
        self.cycles += 1
        if self.cycles == 1:
            # Stand-in for the host: the app is disabled WHILE the unit is in flight.
            self.control.request_stop(StopReason.DISABLED)
        if ctx.should_stop():
            self.observed_stop = True
            return
        self.work_after_stop += 1

    def teardown(self, ctx: WorkerContext) -> None:
        self.torn_down = True


def test_a_polling_worker_observes_the_stop_and_abandons_the_rest_of_its_unit():
    control = WorkerControl()
    ctx = WorkerContext.from_env(_valid_env(), control=control)
    worker = _Polling(control)
    assert run_worker(worker, ctx, install_signals=False) is WorkerState.STOPPED
    assert worker.observed_stop is True
    assert worker.work_after_stop == 0
    assert worker.cycles == 1
    assert worker.torn_down is True, "the graceful path must reach teardown"


class _Deaf(BackgroundWorker):
    """Uncooperative: never polls. Used to prove the DRIVER owns the exit condition."""

    poll_interval = 0.0

    def __init__(self, control: WorkerControl) -> None:
        self.control = control
        self.cycles = 0
        self.work_after_stop = 0

    def run_once(self, ctx: WorkerContext) -> None:
        self.cycles += 1
        if self.cycles == 1:
            self.control.request_stop(StopReason.DISABLED)
        self.work_after_stop += 1  # keeps working; never asks


def test_a_worker_that_ignores_the_stop_still_returns_control():
    """The contract does not DEPEND on cooperation: the loop condition belongs to
    run_worker, so an uncooperative worker exits after its current unit anyway. It only
    loses the in-flight unit's early-out (and, if run_once never returns at all, the
    parent-side supervisor escalates SIGTERM -> SIGKILL as it does for app backends)."""
    control = WorkerControl()
    ctx = WorkerContext.from_env(_valid_env(), control=control)
    worker = _Deaf(control)
    assert run_worker(worker, ctx, install_signals=False) is WorkerState.STOPPED
    assert worker.cycles == 1, "the driver must not start a second unit after a stop"
    assert worker.work_after_stop == 1, "it finished the unit it was in — that is the point"


def test_a_crashing_worker_propagates_so_the_watchdog_can_count_the_restart():
    class Crashing(BackgroundWorker):
        poll_interval = 0.0

        def run_once(self, ctx: WorkerContext) -> None:
            raise RuntimeError("boom")

    control = WorkerControl()
    ctx = WorkerContext.from_env(_valid_env(), control=control)
    with pytest.raises(RuntimeError, match="boom"):
        run_worker(Crashing(), ctx, install_signals=False)
    # Even on the crash path the control reports a terminal state, so the supervisor
    # never sees a worker that is neither running nor stopped.
    assert control.state is WorkerState.STOPPED


# ---------------------------------------------------------------------------
# 5. pause and stop are DIFFERENT states
# ---------------------------------------------------------------------------


def test_the_contract_exposes_both_pause_and_stop_and_they_are_distinct():
    assert WorkerState.PAUSED is not WorkerState.STOPPED
    assert WorkerState.PAUSED is not WorkerState.STOPPING
    assert WorkerState.PAUSED.value != WorkerState.STOPPED.value
    # Two separate reason vocabularies, so "why is it not working" is answerable.
    assert PauseReason.BUDGET.value == "budget"
    assert StopReason.DISABLED.value == "disabled"
    assert set(PauseReason) & set(StopReason) == set()
    for verb in ("pause", "resume", "request_stop", "is_paused", "should_stop"):
        assert callable(getattr(WorkerControl, verb)), f"WorkerControl.{verb} missing"


def test_a_paused_worker_is_not_a_stopped_worker():
    control = WorkerControl()
    assert control.state is WorkerState.RUNNING
    control.pause(PauseReason.BUDGET)
    assert control.state is WorkerState.PAUSED
    assert control.state is not WorkerState.STOPPED
    assert control.state is not WorkerState.STOPPING
    assert control.is_paused() is True
    assert control.should_stop() is False, "a budget pause must not read as a stop"
    assert control.pause_reason is PauseReason.BUDGET
    assert control.stop_reason is None
    control.resume()
    assert control.state is WorkerState.RUNNING
    assert control.pause_reason is None


def test_stop_is_terminal_and_pause_cannot_revive_it():
    control = WorkerControl()
    control.request_stop(StopReason.DISABLED)
    assert control.state is WorkerState.STOPPING
    control.pause(PauseReason.BUDGET)
    assert control.state is WorkerState.STOPPING, "pause must not downgrade a stop"
    assert control.pause_reason is None
    control.resume()
    assert control.state is WorkerState.STOPPING, "resume must not resurrect a stopped worker"
    assert control.should_stop() is True
    control.mark_exited()
    assert control.state is WorkerState.STOPPED


def test_stopping_a_paused_worker_does_not_wedge_it():
    """Pause-then-disable is the ordering the done-when creates (budget breach pauses,
    then the user disables the app). The stop must release the pause wait."""
    control = WorkerControl()
    control.pause(PauseReason.BUDGET)
    control.request_stop(StopReason.DISABLED)
    assert control.wait_while_paused(poll=0.01) is True
    assert control.state is WorkerState.STOPPING


class _Pausing(BackgroundWorker):
    poll_interval = 0.0

    def __init__(self, control: WorkerControl) -> None:
        self.control = control
        self.cycles = 0
        self.pause_states: list[WorkerState] = []
        self.pause_reasons: list[PauseReason | None] = []
        self.resumes = 0

    def run_once(self, ctx: WorkerContext) -> None:
        self.cycles += 1
        if self.cycles == 1:
            self.control.pause(PauseReason.BUDGET)  # stand-in for a budget breach
        else:
            self.control.request_stop(StopReason.DISABLED)

    def on_pause(self, ctx: WorkerContext, reason: PauseReason | None) -> None:
        self.pause_states.append(ctx.control.state)
        self.pause_reasons.append(reason)
        self.control.resume()  # stand-in for the host lifting the budget pause

    def on_resume(self, ctx: WorkerContext) -> None:
        self.resumes += 1


def test_a_paused_worker_resumes_where_a_stopped_one_would_have_exited():
    control = WorkerControl()
    ctx = WorkerContext.from_env(_valid_env(), control=control)
    worker = _Pausing(control)
    assert run_worker(worker, ctx, install_signals=False) is WorkerState.STOPPED
    assert worker.pause_states == [WorkerState.PAUSED]
    assert worker.pause_reasons == [PauseReason.BUDGET]
    assert worker.resumes == 1
    assert worker.cycles == 2, "the pause was resumable; a stop would have ended it at 1"


# ---------------------------------------------------------------------------
# 6. backgroundTasks is the gate, and it cannot be bypassed by writing a worker
# ---------------------------------------------------------------------------


def test_the_gating_permission_is_the_real_manifest_field():
    fields = {f.name for f in dataclasses.fields(Permissions)}
    assert BACKGROUND_TASKS_PERMISSION in fields, (
        "the contract's gate constant must name an actual Permissions field, or a manifest "
        "rename silently orphans the gate"
    )
    assert BACKGROUND_TASKS_PERMISSION == "backgroundTasks"


def test_a_worker_without_the_hosts_verified_grant_fails_closed():
    for env in ({}, _valid_env(**{WORKER_GRANT_ENV: ""}), _valid_env(**{WORKER_GRANT_ENV: "cron"})):
        with pytest.raises(WorkerContractError) as exc:
            WorkerContext.from_env(env)
        assert BACKGROUND_TASKS_PERMISSION in str(exc.value)
        assert WORKER_GRANT_ENV in str(exc.value)


def test_the_context_carries_app_identity_not_a_capability_object():
    ctx = WorkerContext.from_env(_valid_env())
    assert ctx.app_name == "fixture-app"
    assert ctx.worker_id == "poller"
    assert ctx.granted_permission == BACKGROUND_TASKS_PERMISSION
    # Identity only — nothing on the context lets a worker widen its own permissions.
    public = {n for n in dir(ctx) if not n.startswith("_")}
    assert not {n for n in public if "grant" in n.lower() and n != "granted_permission"}
    assert "app_name" in public


def test_an_app_without_the_grant_env_cannot_be_named_into_existence():
    with pytest.raises(WorkerContractError) as exc:
        WorkerContext.from_env({WORKER_GRANT_ENV: BACKGROUND_TASKS_PERMISSION})
    assert WORKER_APP_ENV in str(exc.value)


def test_the_data_dir_follows_the_backends_storage_gate():
    """Absent DATA_DIR means the app did not declare ``storage`` — the worker gets None,
    never a path guessed relative to __file__. Same gate backend_runtime applies."""
    assert WorkerContext.from_env(_valid_env()).data_dir is None
    ctx = WorkerContext.from_env(_valid_env(**{WORKER_DATA_DIR_ENV: "/tmp/app-data"}))
    assert ctx.data_dir == Path("/tmp/app-data")


def test_the_env_handshake_reuses_the_backends_variable_names():
    """One app, one name, one data dir: a worker and a backend of the same app must not
    see two different identities."""
    assert WORKER_APP_ENV == "PERSONALCLAW_APP_NAME"
    assert WORKER_DATA_DIR_ENV == "PERSONALCLAW_APP_DATA_DIR"


# ---------------------------------------------------------------------------
# 7. the contract must not drag the host in
# ---------------------------------------------------------------------------

_FORBIDDEN = ("personalclaw.dashboard", "personalclaw.gateway", "personalclaw.apps.backend_runtime")
_EXPECTED_STDLIB = {"abc", "dataclasses", "enum", "logging", "os", "pathlib", "signal", "threading"}


def _module_scope_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:  # module scope only
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_contract_imports_no_host_module_at_module_scope():
    closure = _module_scope_imports(CORE_SRC.read_text())
    # Vacuity floor: we actually parsed imports, and they are the ones we expect.
    assert closure, "no module-scope imports parsed — the check would pass on anything"
    assert _EXPECTED_STDLIB <= closure, f"expected stdlib imports missing: {closure}"
    leaked = {n for n in closure if n.startswith("personalclaw")}
    assert leaked == set(), (
        "apps/background.py is a contract an APP imports; it must stay stdlib-only so it "
        f"cannot drag core (let alone the host) in. Found: {sorted(leaked)}"
    )


def test_importing_the_contract_does_not_transitively_load_the_host():
    """Transitive, not just module-scope: an innocuous-looking core import can pull the
    whole gateway in behind it."""
    src_root = str(Path(core_background.__file__).parents[2])
    probe = (
        "import sys\n"
        "import personalclaw.sdk.background  # noqa: F401\n"
        f"bad=[m for m in sys.modules if m.startswith({_FORBIDDEN!r})]\n"
        "print('LOADED:'+','.join(sorted(bad)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=120,
        env={"PYTHONPATH": src_root, "PATH": "/usr/bin:/bin", "HOME": "/tmp"},
    )
    assert proc.returncode == 0, f"probe failed to import the facade: {proc.stderr}"
    assert "LOADED:" in proc.stdout, f"probe produced no verdict: {proc.stdout!r}"
    loaded = proc.stdout.split("LOADED:", 1)[1].strip()
    assert loaded == "", f"importing the SDK facade pulled in host modules: {loaded}"


def test_the_entry_point_an_app_is_told_to_use_is_the_one_the_supervisor_resolves():
    """A drift rail across the app/host boundary, and the SDK constants' real consumer.

    `sdk/background.py` exports `WORKER_ENTRY_POINT` and `WORKER_DEFAULT_NAME` because an app
    author needs to know what to name the file and what the worker will be called. The
    supervisor resolves the entry independently, through `declared_workers`. Two spellings of
    one filename is the same defect class that shipped here once already in this atom — the
    host gated registration on `PERSONALCLAW_SCRIPTED_LLM` while the fixture required
    `PERSONALCLAW_SCRIPTED_MODEL_SCRIPT` — and nothing at lint time can see it, so it is
    asserted.
    """
    from personalclaw.apps import worker_runtime as wr
    from personalclaw.apps.manifest import AppManifest, Permissions
    from personalclaw.sdk.background import WORKER_DEFAULT_NAME, WORKER_ENTRY_POINT

    granted = AppManifest(
        name="drift-probe",
        version="1.0.0",
        displayName="Drift probe",
        description="d",
        permissions=Permissions(backgroundTasks=True),
    )
    (spec,) = wr.declared_workers(granted)
    assert spec.entry_point == WORKER_ENTRY_POINT, (
        "the app-facing entry-point constant and the one the supervisor resolves have drifted; "
        "an app author would name the file the SDK documents and the supervisor would not find it"
    )
    assert spec.name == WORKER_DEFAULT_NAME

    # Vacuity floor: the ungranted case yields nothing, so the assertions above are about a
    # real declaration rather than a permissive default.
    plain = AppManifest(
        name="drift-probe-plain",
        version="1.0.0",
        displayName="Drift probe",
        description="d",
        permissions=Permissions(),
    )
    assert wr.declared_workers(plain) == []
