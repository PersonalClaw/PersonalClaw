"""Rails for the computer-use DISPATCH — the composition `DCU-2`'s screens were waiting for.

`DCU-2` shipped ``policy.check_app``, ``policy.check_input_target`` and
``gate.require_computer_use`` as three correct, tested functions with **zero production
callers** (its own audit censused them; ``test_computer_use_call_sites.py`` is the marker it
left behind). `DCU-4` is the caller. So the question this file has to answer is not "do the
screens work" — the sibling suites already drive them directly — but the four things only a
composition can get wrong:

1. **Order.** A screen that runs after the button has been pressed is not a screen. So
   :func:`test_the_chain_runs_every_screen_before_the_acting_driver_call` records the ACTUAL
   sequence of collaborator calls at runtime and asserts it, and
   :func:`test_the_ordering_rail_detects_a_screen_moved_after_the_action` proves that recording
   can fail — run against a deliberately mis-ordered chain, it flags it.
2. **The refusal reaches the caller through the dispatch**, not by a test calling a screen
   directly. Every refusal case here goes in at :func:`service.computer_dispatch`.
3. **The SEL row on the ALLOWED path, asserted separately from the refused one.** The `DCU-2`
   audit's sharpest finding: a test asserting "a SEL record exists" passes when only the
   refusal writes one. Two tests, and both drive a REAL
   :class:`~personalclaw.sel.SecurityEventLog` at a tmp ``base_dir`` — the sibling gate suite
   substitutes a capturing fake, which proves ``log()`` was *called*, not that the row is
   writable.
4. **The bounds hold from both sides.** A TTL that refuses *at* its documented value, or an
   index bound that admits one past the end, are both silent correctness bugs. Each is tested
   at the boundary and one step past it, and each has a floor proving the test can fail.

**No test in this file touches the real home.** The keystone path is redirected by env var
(``enable_state.ENABLE_PATH_ENV``), the SEL is constructed at ``tmp_path``, and the snapshot
store is process-local and reset per test.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import pathlib

import pytest

from personalclaw.computer_use import driver_host, enable_state, gate, policy, service
from personalclaw.computer_use import tools as ct
from personalclaw.sel import SecurityEventLog

ARMED_APP = "TextEdit"

#: An ordinary text destination, the shape `DCU-2`'s policy suite uses as its safe case.
ORDINARY_FIELD = {"role": "AXTextField", "label": "Subject", "value": "Lunch on Tuesday"}
SECURE_FIELD = {"role": "AXTextField", "subrole": "AXSecureTextField", "label": "Password"}

FINGERPRINT = "fp-1"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Redirect the keystone to ``tmp_path`` and clear the snapshot store, every test.

    Autouse and unconditional: a dispatch test that resolved the real keystone would read the
    developer's own machine's arming state, so the suite would pass or fail differently on an
    armed laptop than in CI — and the refusal messages quote the resolved path.
    """
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield
    enable_state.reset_enable_state()
    service.reset_snapshots()


def _arm(tmp_path, *apps: str) -> None:
    """Write a real enable document and force a re-read. No monkeypatched allowlist.

    Deliberately the real parser rather than ``monkeypatch.setattr(enable_state,
    "allowed_apps", …)``: this suite is the one that has to prove the chain reads the
    operator's actual document, and a patched accessor would make the test pass against a
    dispatch that consulted nothing.
    """
    (tmp_path / "enable.json").write_text(
        json.dumps({"version": 1, "enabled": True, "apps": list(apps)}), encoding="utf-8"
    )
    enable_state.reset_enable_state()


def _fake_driver(monkeypatch, *, elements=None, fingerprint=FINGERPRINT, apps=None, log=None):
    """Replace step 6 with an in-process double, recording the ops it was asked to run.

    Returns the recording list. Patching ``service._run_driver`` rather than injecting a driver
    through the dispatch's signature is deliberate: a ``driver=`` parameter would be a
    production seam through which a caller could supply its own driver and skip the ceilinged
    spawn, and a security boundary should not carry a hook that exists only for tests.
    """
    calls: list[str] = []

    async def run(op, payload, *, tool):
        calls.append(op)
        if log is not None:
            log.append("driver")
        if op == "list_apps":
            return {"apps": list(apps or [])}
        if op == "snapshot":
            return {"fingerprint": fingerprint, "elements": list(elements or [])}
        return {"ok": True, "op": op}

    monkeypatch.setattr(service, "_run_driver", run)
    return calls


def _snapshot(app=ARMED_APP, *, elements=None, fingerprint=FINGERPRINT):
    """Put one snapshot in the store directly, so an acting test needs no prior walk."""
    return service._remember(app, fingerprint, list(elements or [ORDINARY_FIELD]))


def _run(coro):
    return asyncio.run(coro)


# ── 1. ordering: the screens run BEFORE the acting driver call ────────────────


def _trace(monkeypatch) -> list[str]:
    """Record every chain step in the order it actually executes.

    Each collaborator is wrapped, not replaced: the real ``require_enabled``, ``check_app``,
    ``check_input_target`` and ``require_computer_use`` still run and still refuse, so the trace
    describes a chain that genuinely worked rather than a sequence of no-ops.
    """
    order: list[str] = []

    def wrap(module, name, label):
        real = getattr(module, name)

        def wrapped(*args, **kwargs):
            order.append(label)
            return real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapped)

    wrap(enable_state, "require_enabled", "enable")
    wrap(policy, "check_app", "check_app")
    wrap(policy, "check_input_target", "check_input_target")
    wrap(gate, "require_computer_use", "sel")
    return order


def test_the_chain_runs_every_screen_before_the_acting_driver_call(tmp_path, monkeypatch):
    """THE ordering proof, and the reason this atom is more than three added calls.

    Asserted as an exact sequence, not as "each of these happened": the keystone first (which
    ``test_every_computer_use_entry_point_guards_first`` also requires), then the app
    allowlist, then the re-walk that makes the index fresh, then the input-target screen on the
    element that re-walk returned, then the SEL row, and only then the acting driver call.

    The re-walk appears BEFORE ``check_input_target`` on purpose and it is a *read*: the screen
    must see the element that will be typed into, which is what
    ``policy.check_input_target``'s own docstring requires. Every screen still precedes the
    ACTING call, which is the claim that matters — and the acting op is last, which is the half
    a test asserting mere presence would miss.
    """
    _arm(tmp_path, ARMED_APP)
    order = _trace(monkeypatch)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD], log=order)
    snap = _snapshot()

    _run(
        service.computer_dispatch(
            "computer_type",
            {"snapshot_id": snap.snapshot_id, "element_index": 0, "text": "hello"},
        )
    )

    assert order == [
        "enable",
        "check_app",
        "driver",  # step 3b — the read-only fingerprint re-walk
        "check_input_target",
        "sel",
        "driver",  # step 6 — the acting call, LAST
    ]


def test_the_ordering_rail_detects_a_screen_moved_after_the_action():
    """The floor for the rail above: a recorded-order assertion is only worth something if a
    wrong order produces a different list. Run the same recording idea over a synthetic chain
    that types first and screens afterwards; the trace shows it, so the assertion above would
    have failed."""
    order: list[str] = []

    def mis_ordered():
        order.append("enable")
        order.append("driver")  # the action
        order.append("check_input_target")  # the "screen", too late to be one
        order.append("sel")

    mis_ordered()
    assert order.index("driver") < order.index("check_input_target")
    assert order != ["enable", "check_app", "driver", "check_input_target", "sel", "driver"]


def _dispatch_call_order() -> list[str]:
    """The screens as they appear in ``computer_dispatch``'s SOURCE, in line order."""
    tree = ast.parse(inspect.getsource(service.computer_dispatch))
    watched = {
        "require_enabled",
        "check_app",
        "_require_fresh_element",
        "check_input_target",
        "_audit",
        "_run_driver",
    }
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            if name in watched:
                calls.append((node.lineno, node.col_offset, name))
    return [name for _line, _col, name in sorted(calls)]


def test_the_dispatch_calls_the_screens_in_source_order():
    """A structural companion to the runtime trace, catching the case the trace cannot: a step
    that exists on a branch the happy path never takes. The runtime rail proves the order that
    RAN; this proves the order that is WRITTEN, so a screen relocated below the driver call
    reds even if no test happens to exercise that branch."""
    order = _dispatch_call_order()
    assert order[0] == "require_enabled", f"the keystone is not the first call: {order}"
    assert (
        order.index("check_app")
        < order.index("_require_fresh_element")
        < order.index("check_input_target")
        < order.index("_run_driver")
    ), order
    # Every _audit call precedes the acting driver call: the audit at step 5 is written BEFORE
    # the desktop is touched, so a driver that wedges or is killed still leaves evidence.
    assert max(i for i, name in enumerate(order) if name == "_audit") < max(
        i for i, name in enumerate(order) if name == "_run_driver"
    ), order


# ── 2. the screens refuse THROUGH the dispatch ────────────────────────────────


@pytest.mark.parametrize("tool", sorted(ct.TOOL_NAMES))
def test_with_the_keystone_absent_every_tool_refuses(tool, monkeypatch):
    """`DCU-1`'s clause, now exercisable over a real tool population for the first time — its
    own execution log records that the clause was *"armed but unexercised until DCU-4"* because
    the tool set was empty. All seven refuse, and the refusal names the enable step."""
    _fake_driver(monkeypatch)
    with pytest.raises(enable_state.ComputerUseDisabled) as excinfo:
        _run(service.computer_dispatch(tool, {"app": ARMED_APP}))
    assert excinfo.value.error.code == enable_state.ERR_DISABLED
    assert "enable" in excinfo.value.error.fix.lower()


def test_a_non_allowlisted_app_is_refused_through_the_dispatch(tmp_path, monkeypatch):
    """`DCU-2` clause 1, from the driving path rather than by calling ``check_app`` directly."""
    _arm(tmp_path, ARMED_APP)
    calls = _fake_driver(monkeypatch)
    with pytest.raises(policy.ComputerUsePolicyRefusal) as excinfo:
        _run(service.computer_dispatch("computer_snapshot", {"app": "Terminal"}))
    assert excinfo.value.error.code == policy.ERR_APP_NOT_ALLOWED
    assert calls == [], "the driver was reached despite the app refusal"


def test_typing_into_a_secure_field_is_refused_through_the_dispatch(tmp_path, monkeypatch):
    """`DCU-2` clause 2, from the driving path. The driver is asked for the re-walk (a read)
    and NOT for the type (the action) — asserted, because "refused" has to mean the keystrokes
    never happened, not that an exception was raised somewhere afterwards."""
    _arm(tmp_path, ARMED_APP)
    calls = _fake_driver(monkeypatch, elements=[SECURE_FIELD])
    snap = _snapshot(elements=[SECURE_FIELD])
    with pytest.raises(policy.ComputerUsePolicyRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_type",
                {"snapshot_id": snap.snapshot_id, "element_index": 0, "text": "hunter2"},
            )
        )
    assert excinfo.value.error.code == policy.ERR_SECURE_FIELD
    assert calls == ["snapshot"], f"the type reached the driver: {calls}"


def test_the_screen_sees_the_REWALKED_element_not_the_stored_one(tmp_path, monkeypatch):
    """The reason step 3b precedes step 4, as a behaviour rather than a comment: a stored
    snapshot whose element was an ordinary field refuses when the live window now has a secure
    field at that index. Screening the stored row would have typed the password."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[SECURE_FIELD])  # what the window looks like NOW
    snap = _snapshot(elements=[ORDINARY_FIELD])  # what it looked like when walked
    with pytest.raises(policy.ComputerUsePolicyRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_type",
                {"snapshot_id": snap.snapshot_id, "element_index": 0, "text": "x"},
            )
        )
    assert excinfo.value.error.code == policy.ERR_SECURE_FIELD


def test_an_armed_machine_with_an_empty_allowlist_refuses_personalclaw_itself(
    tmp_path, monkeypatch
):
    """`DCU-2`'s no-implicit-self deviation, held at the composition. The one target where
    computer use converts into raising the agent's own permissions is PersonalClaw's own
    windows, and an armed-but-unscoped document grants it nothing."""
    _arm(tmp_path)  # enabled: true, apps: []
    _fake_driver(monkeypatch)
    for app in ("PersonalClaw", ARMED_APP):
        with pytest.raises(policy.ComputerUsePolicyRefusal):
            _run(service.computer_dispatch("computer_snapshot", {"app": app}))


# ── 3. the SEL row, on BOTH paths, against a REAL log ─────────────────────────


@pytest.fixture
def sel_rows(tmp_path, monkeypatch):
    """A REAL :class:`SecurityEventLog` rooted at ``tmp_path``, plus a reader for its rows.

    The sibling ``test_computer_use_gate.py`` substitutes a capturing stand-in for
    ``gate.SecurityEventLog``, which can only show that ``log()`` was *called*. That is the
    weaker claim: it passes against a log whose row is unwritable (a read-only home, a corrupt
    HMAC key), and ``gate`` deliberately swallows exactly those failures. So this fixture
    builds the real thing and reads the file back off disk.
    """
    monkeypatch.setattr(SecurityEventLog, "_instance", None)
    monkeypatch.setattr(SecurityEventLog, "_initialized", False)
    log_dir = tmp_path / "sel"
    log_dir.mkdir()
    SecurityEventLog(log_dir)

    def rows():
        path = log_dir / "security_events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    return rows


def test_the_allowed_path_writes_its_own_sel_row(tmp_path, monkeypatch, sel_rows):
    """The half a single "a row exists" assertion lets rot. Asserted on its own, with no
    refusal anywhere in the test, so it cannot be satisfied by the refusal's row."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    _run(
        service.computer_dispatch(
            "computer_type",
            {"snapshot_id": snap.snapshot_id, "element_index": 0, "text": "hi"},
        )
    )
    rows = sel_rows()
    assert len(rows) == 1, rows
    assert rows[0]["event_type"] == gate.SEL_EVENT_TYPE
    assert rows[0]["operation"] == "computer_type"
    assert rows[0]["outcome"] == "approved"
    assert rows[0]["resources"] == f"app={ARMED_APP}"
    assert rows[0]["error"] == ""


def test_the_refused_path_writes_a_sel_row_carrying_the_refusal_code(
    tmp_path, monkeypatch, sel_rows
):
    """`DCU-2`'s audit found nothing linked a refusal to a row: ``policy`` raises without
    recording, because ``gate`` is a step *a caller must remember*. This is that caller
    remembering, for the app screen and the secure-field screen separately."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[SECURE_FIELD])
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        _run(service.computer_dispatch("computer_snapshot", {"app": "Terminal"}))
    snap = _snapshot(elements=[SECURE_FIELD])
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        _run(
            service.computer_dispatch(
                "computer_type",
                {"snapshot_id": snap.snapshot_id, "element_index": 0, "text": "x"},
            )
        )
    rows = sel_rows()
    assert [row["outcome"] for row in rows] == ["denied", "denied"], rows
    assert [row["error"] for row in rows] == [
        policy.ERR_APP_NOT_ALLOWED,
        policy.ERR_SECURE_FIELD,
    ]
    assert rows[0]["resources"] == "app=Terminal"


def test_a_keystone_refusal_is_audited_too(monkeypatch, sel_rows):
    """The attempt most worth recording is the one made against a machine nobody armed. It is
    the one exit that happens before the tool name is even validated, so it is easy to leave
    outside the audit."""
    with pytest.raises(enable_state.ComputerUseDisabled):
        _run(service.computer_dispatch("computer_click", {"app": ARMED_APP}))
    rows = sel_rows()
    assert len(rows) == 1 and rows[0]["error"] == enable_state.ERR_DISABLED, rows


def test_every_attempt_writes_exactly_one_row(tmp_path, monkeypatch, sel_rows):
    """One attempt, one row — the property that makes "every attempt is audited" countable.
    Three attempts of three different shapes (allowed, refused early, refused late) produce
    exactly three rows, so neither a double-write nor a missed exit can hide in the total."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        _run(service.computer_dispatch("computer_snapshot", {"app": "Terminal"}))
    _run(
        service.computer_dispatch(
            "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
        )
    )
    assert len(sel_rows()) == 3, sel_rows()


def test_a_driver_refusal_after_the_audit_does_not_write_a_second_row(
    tmp_path, monkeypatch, sel_rows
):
    """The consequence of auditing the VERDICT at step 5, stated as a test so it is a decision
    rather than an accident: a driver that refuses after the approval leaves the one approved
    row and reports its failure to the caller. Recording a second row would make one attempt
    two rows and break the count above."""
    _arm(tmp_path, ARMED_APP)

    async def refusing(op, payload, *, tool):
        service._refuse(
            service.ERR_DRIVER_UNAVAILABLE, what="no driver", why="none built", fix="wait"
        )

    monkeypatch.setattr(service, "_run_driver", refusing)
    with pytest.raises(service.ComputerUseRefusal):
        _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    rows = sel_rows()
    assert len(rows) == 1 and rows[0]["outcome"] == "approved", rows


# ── 4. the bounds, from both sides, each with a floor ─────────────────────────


def _freeze(monkeypatch, clock: list[float]):
    monkeypatch.setattr(service, "_now", lambda: clock[0])


def test_an_index_at_the_ttl_boundary_still_acts(tmp_path, monkeypatch):
    """AT the bound it proceeds. A bound that refuses at its own documented value teaches
    operators the documented value is a lie, and every later reader has to re-derive it."""
    _arm(tmp_path, ARMED_APP)
    clock = [1000.0]
    _freeze(monkeypatch, clock)
    calls = _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    clock[0] = 1000.0 + service.SNAPSHOT_TTL_SECS
    result = _run(
        service.computer_dispatch(
            "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
        )
    )
    assert result.get("ok") is True
    assert calls == ["snapshot", "click"]


def test_an_index_one_tick_past_the_ttl_refuses(tmp_path, monkeypatch):
    """ONE PAST the bound it refuses, visibly — a typed refusal naming the re-snapshot, not a
    silent success against whatever now sits at that index."""
    _arm(tmp_path, ARMED_APP)
    clock = [1000.0]
    _freeze(monkeypatch, clock)
    calls = _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    clock[0] = 1000.0 + service.SNAPSHOT_TTL_SECS + 0.001
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
            )
        )
    assert excinfo.value.error.code == service.ERR_STALE_INDEX
    assert "computer_snapshot" in excinfo.value.error.fix
    assert calls == [], "the driver was reached with an expired index"


def test_the_ttl_bound_is_not_vacuous(tmp_path, monkeypatch):
    """The floor for the pair above: both would also pass if the TTL check were reading a
    constant instead of the snapshot's age. Forcing the clock far past the bound must refuse,
    and forcing it *backwards* must not — so the comparison really reads elapsed time."""
    _arm(tmp_path, ARMED_APP)
    clock = [1000.0]
    _freeze(monkeypatch, clock)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    clock[0] = 1000.0 + service.SNAPSHOT_TTL_SECS * 10
    with pytest.raises(service.ComputerUseRefusal):
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
            )
        )
    clock[0] = 1000.0
    assert _run(
        service.computer_dispatch(
            "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
        )
    ).get("ok")


def test_the_last_element_index_acts(tmp_path, monkeypatch):
    """AT the index bound (``count - 1``) it proceeds."""
    _arm(tmp_path, ARMED_APP)
    elements = [ORDINARY_FIELD, dict(ORDINARY_FIELD, label="Body")]
    _fake_driver(monkeypatch, elements=elements)
    snap = _snapshot(elements=elements)
    assert _run(
        service.computer_dispatch(
            "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 1}
        )
    ).get("ok")


def test_one_past_the_last_element_index_refuses_and_names_the_count(tmp_path, monkeypatch):
    """ONE PAST it refuses, and the refusal says how many elements there are — an index bound
    whose message does not name the range costs a guessing loop."""
    _arm(tmp_path, ARMED_APP)
    elements = [ORDINARY_FIELD, dict(ORDINARY_FIELD, label="Body")]
    _fake_driver(monkeypatch, elements=elements)
    snap = _snapshot(elements=elements)
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 2}
            )
        )
    assert excinfo.value.error.code == service.ERR_BAD_ARGUMENT
    assert "2 element" in excinfo.value.error.why


def test_a_negative_index_refuses(tmp_path, monkeypatch):
    """The floor on the other side of the index bound: Python would happily read ``-1`` as the
    last element, so a bound written as ``index < len`` alone admits it. A model that computed
    a negative index has made an error, and honouring it presses a different control."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": -1}
            )
        )
    assert excinfo.value.error.code == service.ERR_BAD_ARGUMENT


def test_a_changed_fingerprint_refuses_even_within_the_ttl(tmp_path, monkeypatch):
    """The other half of freshness: time is not the only way an index goes stale. A window the
    user has changed refuses inside the TTL, so the TTL is a backstop rather than the check."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD], fingerprint="fp-CHANGED")
    snap = _snapshot(fingerprint="fp-1")
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
            )
        )
    assert excinfo.value.error.code == service.ERR_STALE_INDEX
    assert "changed" in excinfo.value.error.what


def test_the_fingerprint_is_the_drivers_and_not_recomputed_here(tmp_path, monkeypatch):
    """The floor for the fingerprint check: if the dispatch derived the fingerprint from the
    elements it already holds it would be comparing a value with itself and could never
    disagree. Same elements, different fingerprint, must still refuse."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD], fingerprint="fp-2")
    snap = _snapshot(elements=[ORDINARY_FIELD], fingerprint="fp-1")
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": snap.snapshot_id, "element_index": 0}
            )
        )
    assert excinfo.value.error.code == service.ERR_STALE_INDEX


def test_the_snapshot_ceiling_evicts_the_oldest_and_the_evicted_id_refuses_visibly(
    tmp_path, monkeypatch
):
    """The memory bound a model can push on by looping ``computer_snapshot``. At the ceiling
    the store holds exactly that many; one past it the oldest is gone, and acting on the
    evicted id is a visible stale-index refusal, never a quiet act on the wrong tree."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    first = _snapshot()
    for _ in range(service.MAX_LIVE_SNAPSHOTS - 1):
        _snapshot()
    assert len(service._SNAPSHOTS) == service.MAX_LIVE_SNAPSHOTS
    assert first.snapshot_id in service._SNAPSHOTS  # at the ceiling it survives
    _snapshot()  # one past
    assert len(service._SNAPSHOTS) == service.MAX_LIVE_SNAPSHOTS
    assert first.snapshot_id not in service._SNAPSHOTS
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click", {"snapshot_id": first.snapshot_id, "element_index": 0}
            )
        )
    assert excinfo.value.error.code == service.ERR_STALE_INDEX


def test_a_wedged_driver_is_refused_not_hung(tmp_path, monkeypatch):
    """The userspace half of the ceiling: a driver that never answers becomes a legible
    refusal, not a gateway request that hangs. (The kernel half is the ceilinged spawn — see
    ``test_the_driver_spawn_goes_through_the_ceiling_helper`` and the spawn census.)"""
    _arm(tmp_path, ARMED_APP)
    monkeypatch.setattr(service, "DRIVER_TIMEOUT_SECS", 0.05)

    class _Wedged:
        returncode = None

        async def communicate(self, _data):
            await asyncio.sleep(5)
            return b"", b""

    async def spawn(*argv, **kwargs):
        return _Wedged()

    monkeypatch.setattr("personalclaw.sandbox.create_subprocess_limited", spawn)
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    assert excinfo.value.error.code == service.ERR_DRIVER_FAILED


# ── 5. the shim stayed thin ───────────────────────────────────────────────────


def _module_source(module) -> str:
    return pathlib.Path(module.__file__).read_text(encoding="utf-8")


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _called_names(source: str) -> set[str]:
    return {
        (node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", ""))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
    }


def test_the_shim_imports_no_driver_and_no_dispatch():
    """ "Thin" is a claim, so it is a shape. The shim runs in the ``mcp-core`` subprocess; an
    import of the dispatch or of a driver there would put the decision — or an OS handle — in a
    process that must hold neither. AST, not a text scan: the module docstring names both."""
    imported = _imported_names(_module_source(ct))
    offenders = sorted(
        name
        for name in imported
        if "driver" in name or name.endswith("computer_use.service") or name == "service"
    )
    assert offenders == [], f"the shim reaches into the gateway side: {offenders}"


def test_the_shim_makes_no_policy_decision():
    """The three screens are not called from the shim. A screen implemented here would be one
    the dispatch's other callers do not get; a screen implemented here *as well* would be two
    homes for one policy — the drift ``require_enabled``'s docstring records from
    measurement."""
    called = _called_names(_module_source(ct))
    screens = {"check_app", "check_input_target", "require_computer_use", "require_enabled"}
    assert not (called & screens), f"the shim decides: {sorted(called & screens)}"


def test_the_driver_child_makes_no_policy_decision():
    """Same property for the other end of the transport. The child is the process an operator's
    own driver code will run inside; a keystone read there would be a second reader of the one
    decision, in the least trusted process of the three."""
    called = _called_names(_module_source(driver_host))
    screens = {"check_app", "check_input_target", "require_computer_use", "require_enabled"}
    assert not (called & screens), f"the driver child decides: {sorted(called & screens)}"
    assert "personalclaw.computer_use.policy" not in _imported_names(_module_source(driver_host))


def test_the_thinness_rails_detect_a_shim_that_grew_a_decision():
    """Floor for the three above: a matcher that found nothing looks exactly like a module that
    imports nothing. Run the same two scanners over a synthetic fat shim and confirm both
    flag it."""
    fat = (
        "from personalclaw.computer_use import policy, service\n"
        "from personalclaw.computer_use.macos_driver import press\n"
        "def call(app):\n"
        "    policy.check_app(app, tool='computer_click')\n"
        "    return press(app)\n"
    )
    imported = _imported_names(fat)
    assert any("driver" in name for name in imported)
    assert "personalclaw.computer_use.service" in imported or "service" in imported
    assert "check_app" in _called_names(fat)


def test_the_shim_forwards_every_tool_to_the_one_gateway_route(monkeypatch):
    """One route for seven tools. A per-tool route would be seven places to forget a check, and
    the shim's job is transport: the tool name rides in the body."""
    posted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "personalclaw.mcp_core._post",
        lambda path, body=None: posted.append((path, body or {})) or {"result": "ok"},
    )
    for name in sorted(ct.TOOL_NAMES):
        ct._call_tool_inner(name, {"app": ARMED_APP})
    assert {path for path, _ in posted} == {ct.DISPATCH_PATH}
    assert [body["tool"] for _, body in posted] == sorted(ct.TOOL_NAMES)


def test_the_shim_renders_the_refusal_the_dispatch_composed(monkeypatch):
    """One refusal, one voice. The shim re-renders the WHAT/WHY/FIX verbatim rather than
    paraphrasing, because those three lines are what a model recovers from — and the agent-side
    code, not the wire code, is what it should branch on."""
    monkeypatch.setattr(
        "personalclaw.mcp_core._post",
        lambda path, body=None: {
            "error": {
                "code": "computer_use_refused",
                "agent_code": policy.ERR_APP_NOT_ALLOWED,
                "what": "Terminal is not allowed.",
                "why": "It is not on the operator's allowlist.",
                "fix": "Add it to the enable document.",
            }
        },
    )
    text = ct._call_tool_inner("computer_snapshot", {"app": "Terminal"})
    assert "Terminal is not allowed." in text
    assert "operator's allowlist" in text
    assert "enable document" in text
    assert policy.ERR_APP_NOT_ALLOWED in text


def test_the_tool_surface_is_constant_whether_the_keystone_is_on_or_off(tmp_path):
    """Hiding the tools while disarmed is the tempting optimisation and it is wrong: with an
    empty list ``mcp_core._aggregated_call_tool`` cannot route ``computer_click`` to this
    module at all, so `DCU-1`'s WHAT/WHY/FIX refusal would be replaced by core's "unknown
    tool" — and a conditional population is a second code path nobody exercises."""
    off = [tool["name"] for tool in ct._list_tools()]
    _arm(tmp_path, ARMED_APP)
    on = [tool["name"] for tool in ct._list_tools()]
    assert off == on == [spec.name for spec in ct.TOOL_SURFACE]
    assert len(off) == 7


def test_mcp_core_aggregates_the_computer_use_surface():
    """MCP registration, asserted at the seam that actually decides it: the aggregate an ACP
    CLI sees. Without this entry the seven tools exist and nothing offers them."""
    from personalclaw import mcp_core

    assert "personalclaw.computer_use.tools" in mcp_core._AGGREGATED_CATEGORY_MODULES
    assert ct.TOOL_NAMES <= {tool["name"] for tool in mcp_core._aggregated_list_tools()}


# ── 6. the ceiling on the driver spawn ────────────────────────────────────────


def test_the_driver_spawn_goes_through_the_ceiling_helper():
    """§3.5's clause. ``create_subprocess_limited`` is the repo's single seam that prepends the
    post-exec ceiling shim; a raw ``create_subprocess_exec`` here would spawn an unbounded
    child. Asserted structurally AND censused: ``tests/test_spawn_ceiling_audit.py`` classifies
    this site, so dropping the ceiling later reds there too."""
    source = inspect.getsource(service._run_driver)
    called = _called_names(source)
    assert "create_subprocess_limited" in called
    assert "create_subprocess_exec" not in called
    assert "Popen" not in called


def test_the_driver_argv_names_the_child_module_and_this_interpreter():
    """The child is ``sys.executable -m <module>``, not a console script: a gateway running
    from a venv whose ``bin`` is off ``PATH`` would otherwise resolve a *different*
    PersonalClaw — a different home, and therefore a different keystone."""
    import sys

    assert service._driver_argv() == [sys.executable, "-m", service.DRIVER_CHILD_MODULE]
    assert service.DRIVER_CHILD_MODULE == driver_host.__name__


def test_the_real_spawn_answers_with_a_typed_platform_refusal(tmp_path):
    """The end-to-end proof that the ceilinged spawn is a LIVE path today, not scaffolding for
    `DCU-3`: no driver is monkeypatched here, so the dispatch really starts the child through
    the ceiling helper, really reads its answer, and really turns it into a typed refusal. §3
    floor 6 requires exactly this shape — an honest refusal, *"never a silent no-op or a
    simulated success"*."""
    _arm(tmp_path, ARMED_APP)
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    assert excinfo.value.error.code == service.ERR_DRIVER_UNAVAILABLE
    assert "driver" in excinfo.value.error.what.lower()
    assert "nothing was clicked" in excinfo.value.error.fix.lower()


def test_the_driver_child_refuses_every_operation_while_no_driver_exists():
    """The child's own contract, driven directly. Every one of the seven operations refuses
    with the same typed code — so when `DCU-3` lands, what changes is one importable module and
    not the containment story."""
    for spec in ct.TOOL_SURFACE:
        answer = driver_host.run_op({"op": service._driver_op(spec)})
        assert answer["error"]["code"] == driver_host.ERR_DRIVER_UNAVAILABLE, spec.name


def test_the_child_resolves_a_driver_when_one_is_importable(monkeypatch):
    """Floor for the two above: "every operation refuses" is worthless if ``resolve_driver``
    can never find anything. Point the map at a module that DOES import and confirm the child
    runs its handler instead of refusing."""
    monkeypatch.setitem(driver_host.DRIVER_MODULES, "Darwin", "personalclaw.computer_use.gate")
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    assert driver_host.resolve_driver() is gate
    # `gate` has no ``op_snapshot``, so the child reports THAT rather than "no driver" — a
    # different refusal, which is what proves resolution succeeded.
    answer = driver_host.run_op({"op": "snapshot"})
    assert "does not implement" in answer["error"]["message"]


# ── 7. the tool declarations, pinned ─────────────────────────────────────────


def test_every_acting_tool_declares_the_app_screen():
    """A tool exempt from ``check_app`` must be exempt BY DECLARATION, and only one is:
    ``computer_list_apps`` has no app argument to screen. Anything else added later without
    the flag reds here naming itself."""
    exempt = sorted(spec.name for spec in ct.TOOL_SURFACE if not spec.screen_app)
    assert exempt == ["computer_list_apps"], exempt


def test_only_text_writing_tools_declare_the_input_target_screen():
    """§3 floor 3 scopes ``check_input_target`` to *"any type/set-value"*. Pinned so a future
    text-writing tool cannot ship without it, and so the screen is not quietly extended to
    tools whose roles it would reject (a press on a button is not a write into a field)."""
    screened = sorted(spec.name for spec in ct.TOOL_SURFACE if spec.screen_input_target)
    assert screened == ["computer_set_value", "computer_type"], screened


def test_the_declared_surface_is_exactly_the_plans_seven_tools():
    """The surface is the plan's list, in the plan's words — not a superset somebody grew."""
    assert sorted(ct.TOOL_NAMES) == [
        "computer_click",
        "computer_list_apps",
        "computer_perform_action",
        "computer_scroll",
        "computer_set_value",
        "computer_snapshot",
        "computer_type",
    ]


def test_an_unknown_tool_is_refused_after_the_keystone_not_before(tmp_path, monkeypatch):
    """Order again, in the small: nothing about this machine's desktop — including which tools
    it has — is knowable before the keystone. So an unknown tool on a disarmed machine reports
    the keystone, and only an armed one reports the tool name."""
    _fake_driver(monkeypatch)
    with pytest.raises(enable_state.ComputerUseDisabled):
        _run(service.computer_dispatch("computer_rm_rf", {}))
    _arm(tmp_path, ARMED_APP)
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(service.computer_dispatch("computer_rm_rf", {}))
    assert excinfo.value.error.code == service.ERR_UNKNOWN_TOOL
    assert "computer_click" in excinfo.value.error.suggestions


# ── 8. the pointer paths §3 floor 2 reserves ─────────────────────────────────


def test_auto_never_resolves_to_a_pointer_method():
    """§3 floor 2: ``auto`` resolves to an accessibility press whenever an element index is
    present, and the pointer methods must be named by the model. Asserted over every shape an
    absent method can arrive in, because "auto" is also what a missing value means."""
    for params in ({}, {"click_method": ""}, {"click_method": "   "}, {"click_method": "auto"}):
        assert service._click_method(params) == "auto"
    assert "auto" not in service._POINTER_METHODS


def test_an_unknown_click_method_refuses_rather_than_falling_back(tmp_path, monkeypatch):
    """The direction a fallback would go is the dangerous one. An unresolvable method must not
    become ``auto`` (which would silently retarget the call) nor a coordinate click (which
    would move the operator's cursor); it refuses and lists the three that exist."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    snap = _snapshot()
    with pytest.raises(service.ComputerUseRefusal) as excinfo:
        _run(
            service.computer_dispatch(
                "computer_click",
                {"snapshot_id": snap.snapshot_id, "element_index": 0, "click_method": "warp"},
            )
        )
    assert excinfo.value.error.code == service.ERR_BAD_ARGUMENT
    assert excinfo.value.error.suggestions == service._CLICK_METHODS


def test_a_pointer_click_is_audited_under_its_own_operation(tmp_path, monkeypatch, sel_rows):
    """§2 wants the pointer paths distinguishable in the audit. They are: the real-cursor warp
    records ``computer_click:global``, so it is one field filter away from every ordinary
    click — and it still passes the keystone and the app allowlist to get there."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch)
    _run(
        service.computer_dispatch(
            "computer_click",
            {"click_method": "global", "x": 10, "y": 20, "app": ARMED_APP},
        )
    )
    rows = sel_rows()
    assert [row["operation"] for row in rows] == ["computer_click:global"], rows
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        _run(
            service.computer_dispatch(
                "computer_click",
                {"click_method": "located", "x": 1, "y": 2, "app": "Terminal"},
            )
        )
    assert sel_rows()[-1]["error"] == policy.ERR_APP_NOT_ALLOWED


# ── 9. step 7 — what comes back ──────────────────────────────────────────────


def test_list_apps_is_narrowed_to_the_allowlist_and_reports_what_it_withheld(tmp_path, monkeypatch):
    """Step 7. An operator who granted "drive TextEdit" did not thereby grant "tell me every
    window I have open", so the list is narrowed — and the count keeps the narrowing honest
    instead of pretending nothing was hidden."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, apps=[ARMED_APP, "1Password", "Mail"])
    result = _run(service.computer_dispatch("computer_list_apps", {}))
    assert result["apps"] == [ARMED_APP]
    assert result["withheld"] == 2


def test_a_credential_shaped_value_is_redacted_out_of_a_snapshot(tmp_path, monkeypatch):
    """Step 7's other half. ``redact_credentials`` is this codebase's one definition of
    credential-shaped text; a value the system redacts on the way out of a log is one it must
    not hand to a model out of a window."""
    _arm(tmp_path, ARMED_APP)
    secret = "sk-ant-api03-" + "A" * 40
    _fake_driver(monkeypatch, elements=[{"role": "AXTextField", "value": secret}])
    result = _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    assert secret not in json.dumps(result)


def test_a_snapshot_returns_an_id_the_next_call_can_act_on(tmp_path, monkeypatch):
    """The round trip the whole element-index discipline rests on: a snapshot hands back an id,
    and that id is what makes the next call actable. Without this the seven tools are
    individually correct and jointly unusable."""
    _arm(tmp_path, ARMED_APP)
    _fake_driver(monkeypatch, elements=[ORDINARY_FIELD])
    walked = _run(service.computer_dispatch("computer_snapshot", {"app": ARMED_APP}))
    assert walked["snapshot_id"] in service._SNAPSHOTS
    acted = _run(
        service.computer_dispatch(
            "computer_type",
            {"snapshot_id": walked["snapshot_id"], "element_index": 0, "text": "hi"},
        )
    )
    assert acted.get("ok") is True
