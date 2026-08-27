"""`DCU-5` — the approval ladder at the desktop-drive seam (DESKTOP-COMPUTER-USE §3.4).

The clause is *"an unattended run without the grant refuses and notifies; an interactive run
prompts"*, and every part of it is a claim about a CALL SITE rather than about a function:

1. **The refusal happens through ``service.computer_dispatch``**, not by this file calling
   ``policy.check_autonomy`` directly. `DCU-2` shipped three correct screens with zero callers
   and its own audit had to censure them; a suite that only drives the screen would reproduce
   exactly that. Every refusal here goes in at the dispatch, and the census in
   ``test_computer_use_call_sites.py`` is the flipped half (the screen has ONE caller, and
   removing it reds).
2. **The permitted rung goes through the SAME code path and succeeds.** A refusal test alone
   passes against a dispatch that refuses everything, so each guard below has a leg where the
   grant is present, the driver is reached, and the result comes back. Without it the refusal
   proves nothing.
3. **The notification is a durable row**, read back out of a real ``InboxStore`` in an isolated
   home — not an assertion that ``emit_attention_item`` was called.
4. **The rung comes from the ladder.** ``guardrails.autonomy`` owns the four rung names and this
   atom adds none; what it adds is one DECLARATION (``rungs.COMPUTER_USE_DRIVE``) at the
   existing ``one_tap``. ``test_the_declaration_is_what_makes_this_an_ask`` pins that, because
   ``announce_withheld`` files a row for ``ask``/``draft`` and NOTHING for a route that
   executes — so a widened declaration would silently drop the "and notifies" half while every
   other test here stayed green.

🪤 **The ladder registry is process-global and lazy.** ``resolve_rung`` fails closed to
``draft_only`` for a declared key with no registration, and the gateway registers the core
declarations from the action-provider seam — a path a computer-use dispatch never travels. The
refusal would still refuse, so nothing would look broken; it would file a *proposal* ("here is
what it would have done") instead of an *agent request* ("decide"). ``check_autonomy`` calls
``ensure_core_action_types()`` for that reason and ``test_the_hold_row_is_a_request_from_a_cold
_registry`` is the rail: it clears the registry first.

**No test in this file touches the real home.** ``PERSONALCLAW_HOME``, ``config_dir``,
``config_path`` and the keystone path are all redirected per test, and the ladder/inbox stores
resolve under them.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

from personalclaw.computer_use import enable_state, policy, service
from personalclaw.computer_use import tools as ct
from personalclaw.errors import ERROR_CODES
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import rungs as rg
from personalclaw.sel import SecurityEventLog

SRC = str(Path(__file__).resolve().parents[1] / "src")

ARMED_APP = "TextEdit"

#: A cron fire: no human, no prompt, nothing watching. ``guardrails.policy`` resolves it to the
#: HEADLESS profile (``approval="hook_based"``) by construction.
UNATTENDED = "cron:desktop-tidy"

#: A dashboard chat session: a person is looking at it. Resolves to INTERACTIVE (``"ask"``).
INTERACTIVE = "dashboard:sess-7"

ORDINARY_FIELD = {"role": "AXTextField", "label": "Subject", "value": "Lunch on Tuesday"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A throwaway home for the keystone, the ladder store and the inbox store.

    All four redirects are load-bearing. The keystone env var keeps the grant off the real
    governance directory; ``config_dir``/``config_path``/``PERSONALCLAW_HOME`` are what the rung
    store, the governance ceiling and the inbox store resolve through, and several of them bind
    at import — so patching one and not the others is how a suite writes into the developer's
    own ``~/.personalclaw``.
    """
    home = tmp_path / "home"
    (home / "governance").mkdir(parents=True)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(home / "governance" / "enable.json"))
    enable_state.reset_enable_state()
    service.reset_snapshots()
    yield home
    enable_state.reset_enable_state()
    service.reset_snapshots()


def _assert_isolated(home: Path) -> None:
    """The redirect actually took: the keystone this process reads is under *home*."""
    assert str(home) in str(enable_state.enable_file_path())


def _arm(home: Path, *, apps=(ARMED_APP,), unattended=()) -> None:
    """Write a REAL enable document and force a re-read.

    Deliberately the real parser rather than a patched ``unattended_tools``: this suite has to
    prove the seam reads the operator's actual document, and a patched accessor would pass
    against a dispatch that consulted nothing.
    """
    document = {"version": 1, "enabled": True, "apps": list(apps)}
    if unattended:
        document[enable_state.UNATTENDED_KEY] = list(unattended)
    enable_state.enable_file_path().write_text(json.dumps(document), encoding="utf-8")
    enable_state.reset_enable_state()
    _assert_isolated(home)


def _fake_driver(monkeypatch, *, apps=("TextEdit",), elements=None):
    """Replace step 6 with an in-process double, recording the ops it was asked to run.

    The recording IS the vacuity assertion: "the drive was permitted" means the driver ran, and
    an empty list means the chain refused somewhere even when no exception reached the test.
    """
    calls: list[str] = []

    async def run(op, payload, *, tool):
        calls.append(op)
        if op == "list_apps":
            return {"apps": list(apps)}
        if op == "snapshot":
            return {"fingerprint": "fp-1", "elements": list(elements or [ORDINARY_FIELD])}
        return {"ok": True, "op": op}

    monkeypatch.setattr(service, "_run_driver", run)
    return calls


def _run(coro):
    return asyncio.run(coro)


def _dispatch(tool, params=None, *, identity):
    """One real dispatch. Returns ``(error | None, result | None)``."""
    try:
        return None, _run(
            service.computer_dispatch(tool, params or {}, source="test", caller_identity=identity)
        )
    except (
        enable_state.ComputerUseDisabled,
        policy.ComputerUsePolicyRefusal,
        service.ComputerUseRefusal,
    ) as exc:
        return exc.error, None


def _args_for(spec, snapshot_id: str = "") -> dict:
    """The minimum a tool needs to REACH step 4b, so the refusal under test is the ladder's and
    not an argument complaint standing in for it. `DCU-6`'s helper in shape: an index-screened
    tool with no ``snapshot_id`` refuses at step 3a, which would make every sweep below green for
    the wrong reason."""
    if spec.name == "computer_list_apps":
        return {}
    if spec.name == "computer_snapshot":
        return {"app": ARMED_APP}
    extra = {
        "computer_type": {"text": "Lunch on Tuesday"},
        "computer_set_value": {"value": "Lunch on Tuesday"},
        "computer_scroll": {"direction": "down"},
        "computer_perform_action": {"action": "AXPress"},
    }
    return {"snapshot_id": snapshot_id, "element_index": 0, **extra.get(spec.name, {})}


def _planted_snapshot():
    """One snapshot in the store, planted directly. An index-screened tool needs it to get past
    step 3a, and an unattended run cannot earn one by calling ``computer_snapshot`` — that call is
    the very thing under test."""
    return service._remember(ARMED_APP, "fp-1", [ORDINARY_FIELD]).snapshot_id


def _inbox_rows(home: Path) -> list[dict]:
    """Every persisted inbox row, read back off disk rather than from a captured call."""
    from personalclaw.inbox import InboxStore

    store = InboxStore()
    store.load()
    return [
        {
            "id": item.id,
            "kind": getattr(item, "item_kind", "") or getattr(item, "kind", ""),
            "message": item.message,
            "source": item.source,
            "refs": dict(getattr(item, "refs", {}) or {}),
        }
        for item in store.items.values()
    ]


# ── 1. the clause, through the dispatch, with its vacuity leg ─────────────────


def test_an_unattended_drive_without_the_grant_is_refused_through_the_dispatch(
    _isolated, monkeypatch
):
    """Clause half one. The machine is ARMED and the app IS allowlisted — the only thing missing
    is the standing grant, so this cannot pass by accident on an unarmed keystone."""
    _arm(_isolated)
    calls = _fake_driver(monkeypatch)
    error, result = _dispatch("computer_list_apps", identity=UNATTENDED)
    assert result is None
    assert error is not None, "an unattended drive was permitted with no grant"
    assert error.code == policy.ERR_UNATTENDED_NOT_GRANTED, error.code
    assert calls == [], "the driver ran for a call the ladder withheld"


def test_the_granted_tool_goes_through_the_same_path_and_reaches_the_driver(_isolated, monkeypatch):
    """🪤 THE VACUITY LEG. Same session key, same document, same dispatch — one list entry
    different. Without this the refusal above is indistinguishable from a dispatch that refuses
    every unattended call, or from one that refuses for some unrelated reason."""
    _arm(_isolated, unattended=("computer_list_apps",))
    calls = _fake_driver(monkeypatch)
    error, result = _dispatch("computer_list_apps", identity=UNATTENDED)
    assert error is None, f"the granted tool was still refused: {error}"
    assert result is not None and result["apps"] == ["TextEdit"], result
    assert calls == ["list_apps"], "the permitted leg never reached the driver"


def test_an_interactive_run_is_not_refused_because_the_prompt_is_the_ask(_isolated, monkeypatch):
    """Clause half two. An interactive run gets the tool layer's approval prompt, so this screen
    has nothing to add — and it must not invent a second refusal on top of it.

    The rung is asserted alongside, because "not refused" on its own is also what an ungoverned
    action looks like: the ladder still says ``one_tap`` here. What differs is that somebody is
    present to answer it.
    """
    _arm(_isolated)
    calls = _fake_driver(monkeypatch)
    error, result = _dispatch("computer_list_apps", identity=INTERACTIVE)
    assert error is None, f"an interactive drive was refused: {error}"
    assert result is not None and result["apps"] == ["TextEdit"], result
    assert calls == ["list_apps"]
    rg.ensure_core_action_types()
    route = rg.route_action_type(rg.COMPUTER_USE_DRIVE, session_key=INTERACTIVE)
    assert route.route == rg.ROUTE_ASK and route.rung == au.RUNG_ONE_TAP


def test_the_grant_is_per_tool_so_permission_to_look_is_not_permission_to_act(
    _isolated, monkeypatch
):
    """Exact, per-tool matching, asserted from BOTH sides in one test: the granted tool runs and
    the ungranted one refuses under the identical document. A rail that only proved the refusal
    could not detect a grant that licenses the whole surface, which is the direction that costs
    something here."""
    _arm(_isolated, unattended=("computer_snapshot",))
    calls = _fake_driver(monkeypatch)

    error, result = _dispatch("computer_snapshot", {"app": ARMED_APP}, identity=UNATTENDED)
    assert error is None, f"the granted snapshot was refused: {error}"
    assert result is not None and result.get("snapshot_id")

    snap_id = result["snapshot_id"]
    error, _ = _dispatch(
        "computer_click", {"snapshot_id": snap_id, "element_index": 0}, identity=UNATTENDED
    )
    assert error is not None, "a snapshot grant licensed a click"
    assert error.code == policy.ERR_UNATTENDED_NOT_GRANTED
    assert calls == ["snapshot", "snapshot"], calls  # the walk + the click's freshness re-walk


@pytest.mark.parametrize(
    "near",
    ["computer_list_app", "COMPUTER_LIST_APPS", "list_apps", "computer_list_appsx"],
)
def test_a_grant_that_merely_resembles_the_tool_grants_nothing(_isolated, monkeypatch, near):
    """The document refuses a name outside the declared surface rather than storing it, so a
    near-miss cannot become a grant by a normalisation nobody wrote. Either way the drive is
    refused — asserted as the OUTCOME, since that is the property that matters."""
    document = {
        "version": 1,
        "enabled": True,
        "apps": [ARMED_APP],
        enable_state.UNATTENDED_KEY: [near],
    }
    enable_state.enable_file_path().write_text(json.dumps(document), encoding="utf-8")
    enable_state.reset_enable_state()
    _fake_driver(monkeypatch)
    error, _ = _dispatch("computer_list_apps", identity=UNATTENDED)
    assert error is not None, f"{near!r} was honoured as a grant for computer_list_apps"


# ── 2. "and notifies" — a durable row, read back off disk ────────────────────


def test_the_refusal_raises_a_durable_agent_request_row(_isolated, monkeypatch):
    """ "refuses AND notifies". The row is the notification's durable half, so it is read back
    out of a real store rather than asserted on a captured call — a captured call passes against
    a store that cannot persist."""
    _arm(_isolated)
    _fake_driver(monkeypatch)
    assert _inbox_rows(_isolated) == [], "the fixture home started with rows in it"

    snap = _planted_snapshot()
    error, _ = _dispatch(
        "computer_click", {"snapshot_id": snap, "element_index": 0}, identity=UNATTENDED
    )
    assert error is not None and error.code == policy.ERR_UNATTENDED_NOT_GRANTED, error

    rows = _inbox_rows(_isolated)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["kind"] == "agent_request", row
    assert row["refs"].get("action_type") == rg.COMPUTER_USE_DRIVE, row["refs"]
    assert row["refs"].get("rung") == au.RUNG_ONE_TAP, row["refs"]
    assert row["refs"].get("tool") == "computer_click", row["refs"]
    assert "click" in row["message"]


def test_the_hold_row_is_deduped_per_tool_not_per_attempt(_isolated, monkeypatch):
    """A trigger that fires every thirty seconds must not stack a hundred identical rows — and a
    DIFFERENT tool is a different question a person answers differently, so it gets its own."""
    _arm(_isolated)
    _fake_driver(monkeypatch)
    snap = _planted_snapshot()
    click = {"snapshot_id": snap, "element_index": 0}
    for _ in range(3):
        _dispatch("computer_click", click, identity=UNATTENDED)
    assert len(_inbox_rows(_isolated)) == 1, "three attempts stacked three rows"

    _dispatch("computer_type", {**click, "text": "hi"}, identity=UNATTENDED)
    kinds = sorted(r["refs"].get("tool", "") for r in _inbox_rows(_isolated))
    assert kinds == ["computer_click", "computer_type"], kinds


def test_the_hold_row_is_a_request_from_a_cold_registry(_isolated, monkeypatch):
    """🪤 THE REGISTRATION RAIL. ``resolve_rung`` fails closed to ``draft_only`` for a declared
    key with no registration, and a computer-use dispatch does not travel the provider-
    registration seam. Un-registered, this files a *proposal* instead of an *agent request* —
    still a refusal, still a row, so nothing looks broken, and the wrong thing is in the user's
    inbox. Clears the registry so the seam's own ``ensure_core_action_types()`` is the only
    thing that can put the declaration back."""
    au._REGISTRY.clear()
    assert (
        au.resolve_rung(rg.COMPUTER_USE_DRIVE) == au.RUNG_DRAFT_ONLY
    ), "the cold-registry premise is wrong, so this rail measures nothing"
    _arm(_isolated)
    _fake_driver(monkeypatch)
    error, _ = _dispatch("computer_list_apps", identity=UNATTENDED)
    assert error is not None
    rows = _inbox_rows(_isolated)
    assert [r["kind"] for r in rows] == ["agent_request"], rows
    assert rows[0]["refs"].get("rung") == au.RUNG_ONE_TAP, rows[0]["refs"]


# ── 3. the SEL row for the refusal ───────────────────────────────────────────


@pytest.fixture
def sel_rows(tmp_path, monkeypatch):
    """A REAL :class:`SecurityEventLog` at a tmp dir, plus a reader for its rows off disk."""
    monkeypatch.setattr(SecurityEventLog, "_instance", None)
    monkeypatch.setattr(SecurityEventLog, "_initialized", False)
    log_dir = tmp_path / "sel"
    log_dir.mkdir()
    SecurityEventLog(log_dir)

    def rows():
        path = log_dir / "security_events.jsonl"
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    return rows


def test_the_ladder_refusal_writes_exactly_one_denied_sel_row(_isolated, monkeypatch, sel_rows):
    """`DCU-2`'s clause is "every attempt, allowed or refused, produces a SEL record", and step
    4b is a new exit. Placed inside the audited ``try`` rather than before it, so the row names
    the app the call was aimed at."""
    _arm(_isolated)
    _fake_driver(monkeypatch)
    error, _ = _dispatch("computer_snapshot", {"app": ARMED_APP}, identity=UNATTENDED)
    assert error is not None

    rows = sel_rows()
    assert len(rows) == 1, rows
    blob = json.dumps(rows[0])
    assert policy.ERR_UNATTENDED_NOT_GRANTED in blob, blob
    assert "denied" in blob, blob
    assert ARMED_APP in blob, "the refusal row does not name what the call was aimed at"


def test_the_granted_leg_writes_an_approved_row_instead(_isolated, monkeypatch, sel_rows):
    """The vacuity half of the row above. Asserted separately because a single "a row exists"
    test passes when only the refusal writes one — `DCU-4`'s sharpest recorded finding."""
    _arm(_isolated, unattended=("computer_snapshot",))
    _fake_driver(monkeypatch)
    error, _ = _dispatch("computer_snapshot", {"app": ARMED_APP}, identity=UNATTENDED)
    assert error is None, error

    rows = sel_rows()
    assert len(rows) == 1, rows
    blob = json.dumps(rows[0])
    assert "approved" in blob and policy.ERR_UNATTENDED_NOT_GRANTED not in blob, blob


# ── 4. the declaration is what makes this an ASK ──────────────────────────────


def test_no_new_rung_name_was_minted():
    """`guardrails.autonomy` owns the rung vocabulary. This atom adds a DECLARATION at an
    existing rung; a fifth rung name would be a second ladder, which is the hazard this program
    has already paid for once.

    Asserted two ways: the ladder is still exactly four names, and the computer-use package
    defines no rung constant of its own.
    """
    assert au.RUNGS == (
        au.RUNG_DRAFT_ONLY,
        au.RUNG_ONE_TAP,
        au.RUNG_AUTO_WITH_UNDO,
        au.RUNG_AUTONOMOUS,
    )
    pkg = Path(policy.__file__).parent
    for path in sorted(pkg.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        minted = [
            t.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name) and t.id.startswith("RUNG")
        ]
        assert minted == [], f"{path.name} mints a rung vocabulary: {minted}"


def test_the_declaration_is_what_makes_this_an_ask():
    """🪤 The seam's premise, pinned. ``one_tap`` at both ends is what turns an unattended drive
    into "needs a standing grant", and ``ROUTE_ASK`` is what ``announce_withheld`` needs to file
    an agent request at all — it files NOTHING for a route that executes. Widen this spec and
    the "and notifies" half disappears silently, so it reds here instead.

    The route is asserted for the unattended AND the interactive key, because the interesting
    fact is that they are the SAME: ``rung_ceiling_for_profile`` narrows an unattended run to
    ``auto_with_undo``, which is above ``one_tap``, so the ladder cannot tell the two apart. The
    seam's second read is what does.
    """
    spec = next(s for s in rg.CORE_ACTION_TYPES if s.key == rg.COMPUTER_USE_DRIVE)
    assert spec.floor == au.RUNG_ONE_TAP and spec.ceiling == au.RUNG_ONE_TAP
    assert spec.leaves_machine is True, "a click can send the mail"
    assert spec.providers == (), "nothing dispatches this through the action-provider registry"

    rg.ensure_core_action_types()
    for key in (UNATTENDED, INTERACTIVE):
        route = rg.route_action_type(rg.COMPUTER_USE_DRIVE, session_key=key)
        assert route.route == rg.ROUTE_ASK, (key, route.route)
        assert route.governed is True
        assert rg._WITHHOLD_SURFACE[route.route][2] == "agent_request"


def test_the_governed_inventory_lists_the_drive_so_a_person_can_see_it():
    """A rung nobody can see is a control with no surface. The ladder panel enumerates
    ``CORE_ACTION_TYPES``, so the declaration has to be in it."""
    assert rg.COMPUTER_USE_DRIVE in {s.key for s in rg.CORE_ACTION_TYPES}
    assert rg.rung_label(au.RUNG_ONE_TAP) == "asks first"


# ── 5. the code is the PARENT's, and a child may not claim it ────────────────


def test_the_code_is_registered_and_distinct():
    assert policy.ERR_UNATTENDED_NOT_GRANTED in ERROR_CODES
    meaning = ERROR_CODES[policy.ERR_UNATTENDED_NOT_GRANTED]
    assert "unattended" in meaning.lower()
    assert meaning != ERROR_CODES[enable_state.ERR_DISABLED], "two codes, one meaning"


def test_the_parent_side_verdict_is_not_a_code_a_child_may_name():
    """The inverse of `DCU-3`/`DCU-6`'s finding. Their codes had to be ADDED to ``_CHILD_CODES``
    because only the child can determine them; this one must be kept OUT, because it is decided
    at step 4b before any child exists. A child able to name it could dress a driver crash up as
    a policy verdict the parent never reached."""
    assert policy.ERR_UNATTENDED_NOT_GRANTED not in service._CHILD_CODES
    for code in service._CHILD_CODES:
        assert code in ERROR_CODES, code


def test_a_real_child_claiming_the_parents_verdict_is_flattened(_isolated, monkeypatch):
    """END-TO-END, across a real process boundary: the real ceilinged spawn, a real child, the
    real JSON protocol and the real ``_run_driver`` translation. A child that names the parent's
    policy code comes back as a generic driver failure, which is the allowlist working."""
    _arm(_isolated, unattended=("computer_list_apps",))
    # 🪤 The envelope shape is load-bearing and it is NOT flat: `_run_driver` reads
    # ``{"error": {"code": …}}`` and a top-level ``code`` is invisible to it. A child that sent the
    # flat shape was flattened to ERR_..._DRIVER_FAILED no matter what ``_CHILD_CODES`` held, so
    # this test passed while measuring nothing — caught by admitting the code to the allowlist and
    # watching it stay green.
    envelope = json.dumps(
        {"error": {"code": policy.ERR_UNATTENDED_NOT_GRANTED, "message": "I am not the parent"}}
    )
    argv = [
        sys.executable,
        "-c",
        f"import sys; sys.path.insert(0, {SRC!r});"
        "sys.stdin.readline();"
        f"print({envelope!r}, flush=True)",
    ]
    monkeypatch.setattr(service, "_driver_argv", lambda: argv)
    error, _ = _dispatch("computer_list_apps", identity=UNATTENDED)
    assert error is not None
    assert (
        error.code == service.ERR_DRIVER_FAILED
    ), f"a child named the parent's policy verdict and it survived as {error.code}"


# ── 6. the operator document: the third grant ─────────────────────────────────


def test_an_absent_unattended_key_grants_nothing(_isolated):
    """Fail closed by construction. Absent and ``[]`` land identically, and neither means all."""
    _arm(_isolated)
    assert enable_state.unattended_tools() == ()
    _arm(_isolated, unattended=())
    assert enable_state.unattended_tools() == ()


def test_the_grant_survives_a_round_trip_and_is_order_independent(_isolated):
    _arm(_isolated, unattended=("computer_type", "computer_click"))
    assert enable_state.unattended_tools() == ("computer_click", "computer_type")


@pytest.mark.parametrize(
    ("document", "needle"),
    [
        ('{"version": 1, "enabled": true, "unattended": "computer_click"}', "not a list of"),
        ('{"version": 1, "enabled": true, "unattended": [7]}', "not a string"),
        ('{"version": 1, "enabled": true, "unattended": [""]}', "empty name"),
        ('{"version": 1, "enabled": true, "unattended": [" computer_click"]}', "padded"),
        (
            '{"version": 1, "enabled": true, "unattended": ["computer_click", "computer_click"]}',
            "twice",
        ),
        ('{"version": 1, "enabled": true, "unattended": ["nope"]}', "not one of this build"),
    ],
)
def test_a_malformed_grant_takes_the_WHOLE_document_off(_isolated, document, needle):
    """The same fail-closed direction ``apps`` has, through the same validator: a malformed grant
    is REFUSED rather than normalised, and it takes the keystone with it. A parser that dropped
    the bad list and kept ``enabled`` would arm a machine whose operator wrote a scope this
    build then ignored — the exact widening the unknown-key rule exists to prevent."""
    enable_state.enable_file_path().write_text(document, encoding="utf-8")
    enable_state.reset_enable_state()
    state = enable_state.active_enable_state()
    assert state.enabled is False, "a malformed grant left the keystone armed"
    assert needle in state.detail, state.detail
    assert enable_state.unattended_tools() == ()


def test_the_unattended_field_has_exactly_one_reader(_isolated):
    """``EnableState.unattended`` may be read only inside ``unattended_tools()``, for the reason
    ``apps`` has the same rail: a second place that reaches into it is a second place that can
    default or widen it differently, and the divergence is invisible until it matters.

    Walks the AST rather than grepping — this module's prose names the field several times, and a
    text scanner reads comments as code.
    """
    tree = ast.parse(Path(enable_state.__file__).read_text(encoding="utf-8"))
    readers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr == "unattended":
                readers.append(node.name)
    assert sorted(set(readers)) == ["unattended_tools"], readers


# ── 7. the message an operator actually reads ────────────────────────────────


def test_the_refusal_names_the_file_the_key_and_the_way_out(_isolated, monkeypatch):
    """A FIX that does not name the exact edit is how an operator concludes the feature is
    broken. It must name the out-of-band file (never "open Settings"), the key, the tool, and
    the alternative that works right now."""
    _arm(_isolated)
    _fake_driver(monkeypatch)
    snap = _planted_snapshot()
    error, _ = _dispatch(
        "computer_click", {"snapshot_id": snap, "element_index": 0}, identity=UNATTENDED
    )
    assert error is not None and error.code == policy.ERR_UNATTENDED_NOT_GRANTED, error
    assert str(enable_state.enable_file_path()) in error.fix
    assert enable_state.UNATTENDED_KEY in error.fix
    assert "computer_click" in error.fix
    assert "interactive" in error.fix.lower(), "the message never says what DOES work"
    assert "Settings" not in error.fix, "the FIX points at a setting that does not exist"
    assert "headless" in error.what, "the WHAT does not say which posture refused"


def test_every_declared_tool_is_refusable_and_grantable(_isolated, monkeypatch):
    """The sweep. Seven tools, and the screen is unconditional — including
    ``computer_list_apps``, whose enumeration of somebody's open windows is the reconnaissance
    half of driving them and the first call an unattended run would make."""
    refused, granted = [], []
    for spec in ct.TOOL_SURFACE:
        _arm(_isolated)
        _fake_driver(monkeypatch)
        args = _args_for(spec, _planted_snapshot())
        error, _ = _dispatch(spec.name, args, identity=UNATTENDED)
        if error is not None and error.code == policy.ERR_UNATTENDED_NOT_GRANTED:
            refused.append(spec.name)
        _arm(_isolated, unattended=(spec.name,))
        args = _args_for(spec, _planted_snapshot())
        error, _ = _dispatch(spec.name, args, identity=UNATTENDED)
        if error is None or error.code != policy.ERR_UNATTENDED_NOT_GRANTED:
            granted.append(spec.name)
    assert refused == [s.name for s in ct.TOOL_SURFACE], refused
    assert granted == [s.name for s in ct.TOOL_SURFACE], granted


# ── 8. the HTTP seam: a headerless caller is not a human ─────────────────────


def test_a_request_with_no_session_header_resolves_to_an_unattended_identity():
    """🔴 Measured fail-open, closed here. ``caller_identity=""`` resolved to the INTERACTIVE
    profile, so any authenticated client that simply did not send ``X-Session-Key`` — a script,
    an ACP CLI — read as "a human is watching". Minted into a sessionless unattended identity by
    the same helper the trigger and hook seams use, at the one seam that knows the header was
    absent."""
    from personalclaw.dashboard.handlers.computer_use import _caller_identity
    from personalclaw.guardrails.policy import UNATTENDED_DISPATCH_PREFIX, profile_for_session

    class _Req:
        def __init__(self, headers):
            self.headers = headers

    minted = _caller_identity(_Req({}))
    assert minted.startswith(UNATTENDED_DISPATCH_PREFIX), minted
    assert profile_for_session(minted).approval == "hook_based"
    # And a real session key is passed through untouched — the vacuity half.
    assert _caller_identity(_Req({"X-Session-Key": INTERACTIVE})) == INTERACTIVE
    assert profile_for_session(INTERACTIVE).approval == "ask"
