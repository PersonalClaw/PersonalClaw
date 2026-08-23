"""The ladder as a USER drives it (AUTONOMY-GUARDRAILS §6.1, atom AG-8).

The four things this atom promised, each asserted end to end rather than against a
constructed object:

1. **Promotion never happens without a click.** The proposal scan runs, files its row, and
   changes no rung; the only thing in the tree that calls ``grant_rung`` is the HTTP
   handler, asserted from the SOURCE so a future caller cannot slip in quietly.
2. **An undo click both reverses the action AND demotes the type.** Driven through a REAL
   event-trigger fire that creates a REAL task row, then undone through the REAL endpoint —
   the task file is gone afterwards and the type is back at its floor with a cooldown.
3. **The chip answers "why is this allowed to run by itself?"** — the ``authority`` sentence
   the chip renders, for all three provenances (declared floor, your grant, incident hold).
4. Fail-closed behaviour for every refusal the undo executor can reach, and for a grant
   above the ceiling or during a cooldown.

The withhold → grant → execute → undo round trip runs against ``execute_event_action``, the
same dispatch AG-7 wired, so nothing here proves a code path a user cannot reach.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.apps.manifest import AppManifest, AutonomyConfig, ProviderConfig
from personalclaw.dashboard.handlers import autonomy as api_h
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import ladder as ld
from personalclaw.guardrails import rungs as rg

APP_KEY = "app:acme.acme-file-task"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the rung store, the reversal store, the SEL, tasks and the inbox.

    ``PERSONALCLAW_HOME`` as well as the patched ``config_dir``: several stores bind
    ``config_dir`` at import (``tasks.native``), so the env var is what actually keeps their
    writes out of the real home.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    from personalclaw import sel as sel_mod

    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False
    yield home
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False


@pytest.fixture(autouse=True)
def _clean_registries():
    """Restore the action-provider registry — these tests install a fake app provider."""
    from personalclaw.action_providers.registry import _providers

    before = dict(_providers)
    yield
    _providers.clear()
    _providers.update(before)


# ── a real-effect app action: it files a task and can delete it again ──────────


class _TaskFilingAction(ActionProvider):
    """An app's action provider with a REVERSIBLE real effect.

    Files a native task through the same registry ``create-task`` uses and hands back the
    same handle shape, so the undo executor's dispatch is exercised against a provider that
    genuinely created something — the row is on disk and its absence afterwards is the
    assertion.
    """

    @property
    def name(self) -> str:
        return "acme-file-task"

    @property
    def display_name(self) -> str:
        return "Acme File Task"

    @property
    def reversal_kinds(self) -> tuple[str, ...]:
        return ("task",)

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from personalclaw.tasks.registry import create_task

        task = await create_task("native", title="Filed by the acme automation")
        task_id = str(getattr(task, "id", "") or "")
        return ActionResult(
            success=True, stdout=f"filed {task_id}", reversal=f"task:native:{task_id}"
        )

    async def reverse(self, handle: str) -> ActionResult:
        from personalclaw.tasks.registry import delete_task, get_task

        task_id = handle.rpartition(":")[2]
        if await get_task(task_id, "native") is None:
            return ActionResult(success=False, error=f"task {task_id} is already gone")
        await delete_task(task_id, "native")
        return ActionResult(success=True, stdout=f"deleted {task_id}")


def _install_app_action(*, floor: str, ceiling: str) -> _TaskFilingAction:
    """Register the app provider THROUGH the production app-registration handler."""
    from personalclaw.providers.registry import ActionTypeHandler, RegisteredProvider

    manifest = AppManifest(name="acme", version="1.0.0", displayName="Acme", description="d")
    provider_config = ProviderConfig(
        type="action",
        implementation="acme.provider:create",
        autonomy=AutonomyConfig(floor=floor, ceiling=ceiling),
    )
    ext = RegisteredProvider(name="acme", manifest=manifest, provider_config=provider_config)
    instance = _TaskFilingAction()
    ActionTypeHandler().register(ext, instance)
    return instance


def _fire() -> Any:
    """Drive the REAL data-event fire path for the app's action."""
    from personalclaw.event_triggers import (
        MEMORY_UPDATE,
        SOURCE_MEMORY,
        EventTrigger,
        execute_event_action,
    )

    trigger = EventTrigger(
        id="t-acme",
        pattern=MEMORY_UPDATE,
        source=SOURCE_MEMORY,
        action_provider="acme-file-task",
        action_config={},
    )
    return asyncio.run(
        execute_event_action(
            trigger,
            source=SOURCE_MEMORY,
            event_type="update",
            key="project.acme.status",
            value="green",
        )
    )


def _task_files(home: Path) -> list[Path]:
    d = home / "tasks"
    return sorted(p for p in d.glob("*.json") if not p.name.startswith("_")) if d.exists() else []


def _inbox_rows(home: Path) -> list[dict]:
    path = home / "inbox.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", data)
    return list(items.values()) if isinstance(items, dict) else list(items)


def _post(path: str, body: dict):
    req = make_mocked_request("POST", path)

    async def _j():
        return body

    req.json = _j  # type: ignore[method-assign]
    return req


def _json_body(resp) -> dict:
    return json.loads(resp.body.decode())


def _ladder() -> dict:
    resp = asyncio.run(api_h.api_autonomy(make_mocked_request("GET", "/api/autonomy")))
    return _json_body(resp)


def _row(view: dict, key: str) -> dict:
    return next(t for t in view["types"] if t["key"] == key)


# ── done_when 2: the whole round trip, as a user drives it ────────────────────


def test_withheld_then_granted_then_undone_through_the_REAL_endpoints(_isolated_home):
    """🔴 THE ATOM, end to end.

    A real fire is held at ``draft_only`` and files a proposal; a grant through the API lets
    the SAME fire execute and file a real task; the undo endpoint deletes that task AND
    demotes the type. Every step goes through production code — the only thing the test
    supplies is the app.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)

    # 1. Held at the bottom rung: nothing executed, and a proposal row says what would have.
    _fire()
    assert _task_files(_isolated_home) == []
    proposals = [r for r in _inbox_rows(_isolated_home) if r.get("item_kind") == "proposal"]
    assert len(proposals) == 1
    assert proposals[0]["refs"]["action_type"] == APP_KEY

    # 2. The click. Through the endpoint, with the rung the panel would send.
    resp = asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": APP_KEY, "rung": au.RUNG_AUTO_WITH_UNDO})
        )
    )
    assert resp.status == 200, _json_body(resp)
    assert _json_body(resp)["rung"] == au.RUNG_AUTO_WITH_UNDO

    # 3. The SAME fire now executes and leaves a real task row + a reversal record.
    _fire()
    tasks = _task_files(_isolated_home)
    assert len(tasks) == 1, "the granted rung must let the action actually run"
    records = ld.reversal_records()
    assert len(records) == 1 and records[0].pending
    assert records[0].action_type == APP_KEY
    assert records[0].handle.startswith("task:native:")

    # 4. The undo click: the effect is gone AND the type lost the rung it had earned.
    resp = asyncio.run(api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": records[0].id})))
    assert resp.status == 200, _json_body(resp)
    body = _json_body(resp)
    assert body["ok"] is True and body["demoted"] is True
    assert _task_files(_isolated_home) == [], "undo must delete the row the action created"
    assert au.granted_rung(APP_KEY) == au.RUNG_DRAFT_ONLY
    state = au.rung_state(APP_KEY)
    assert state is not None and len(state.demotions) == 1
    assert state.demotions[0].cooldown_until  # a cooldown is running

    # 5. And the ladder now routes the same fire back to withheld — the demotion is real at
    #    the dispatch seam, not only in the store.
    _fire()
    assert _task_files(_isolated_home) == []
    assert ld.reversal_records()[0].reversed_at, "the record is marked, so no second undo"


def test_the_HELD_row_names_the_action_ONCE_and_never_by_its_type_key(_isolated_home):
    """The inbox row a withheld action raises, driven through the REAL fire path.

    Its body is composed as "The <provider> action on trigger <id> did not run: <route.reason>."
    — so the sentence has already named the action before the reason begins. While the reason
    carried the action-TYPE key it said it twice, the second time as a code identifier the user has
    never seen (`app:acme.acme-file-task`) and, for a core provider, as a *different* name for the
    thing the sentence just called `'bash'` (`action.execute_code`).

    The key is not lost: `refs["action_type"]` still carries it, which is what AG-8's card and the
    ladder panel query by.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_ONE_TAP)
    outcome = _fire()

    assert getattr(outcome, "ran", True) is False, "a draft_only action must be withheld"
    rows = [r for r in _inbox_rows(_isolated_home) if (r.get("refs") or {}).get("action_type")]
    assert len(rows) == 1, rows
    row = rows[0]
    message = str(row.get("message", ""))

    assert "did not run" in message, message
    assert "acme-file-task" in message, "the row must still say WHICH action was held"
    assert APP_KEY not in message, f"the type key is a code identifier, not user copy: {message!r}"
    assert "drafts only" in message, f"and the rung in user words: {message!r}"
    # The machine-facing half is untouched: the row is still findable by type.
    assert row["refs"]["action_type"] == APP_KEY
    # And the seam's own outcome string is framed too, so "this action" has a referent there.
    assert str(getattr(outcome, "reason", "")).startswith("held for your approval: ")
    assert APP_KEY not in str(getattr(outcome, "reason", ""))


def test_the_undo_record_is_what_the_notification_carries(_isolated_home):
    """The affordance is rendered from a persisted record id, not from a raw handle.

    `record_reversal` stamps `reversal_id` into the notification meta; the frontend sends
    that id back. A page that held the provider's handle instead could ask to reverse
    something the system never recorded doing.
    """
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    notes: list[dict] = []

    class _State:
        def notify(self, kind, title, body, *, meta=None):
            notes.append({"kind": kind, "title": title, "body": body, "meta": dict(meta or {})})

    class _Services:
        state = _State()

    import personalclaw.action_providers.services as svc

    original = svc.get_action_services
    svc.get_action_services = lambda: _Services()  # type: ignore[assignment]
    try:
        _fire()
    finally:
        svc.get_action_services = original  # type: ignore[assignment]

    assert len(notes) == 1
    meta = notes[0]["meta"]
    record = ld.reversal_records()[0]
    assert meta["reversal_id"] == record.id
    assert meta["reversal"] == record.handle
    assert meta["action_type"] == APP_KEY
    assert "undo" in notes[0]["body"].lower()


# ── done_when 1: promotion is a click, and only a click ───────────────────────


def _seed_clean_record(key: str, *, approvals: int = 12, days: int = 9) -> None:
    """Write the SEL approval trail a promotion proposal is derived from.

    Real rows through the real logger — `promotion_eligibility` reads the SEL tail, so the
    only honest way to give a type a track record is to leave one.
    """
    from personalclaw.sel import sel

    now = datetime.now(timezone.utc)
    log = sel()
    for i in range(approvals):
        log.log_tool_invocation(
            tool_name="acme",
            outcome="approved",
            session_key="s",
            metadata={au.SEL_ACTION_TYPE_KEY: key},
        )
    # Backdate the stamps so the approvals SPAN the required window (they are written in one
    # instant here, and "ten approvals over seven days" means what it says).
    path = Path(log._path)
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    stamped = 0
    for line in lines:
        row = json.loads(line)
        if (row.get("metadata") or {}).get(au.SEL_ACTION_TYPE_KEY) == key:
            row["timestamp"] = (
                now - timedelta(days=days) + timedelta(days=days * stamped / max(approvals - 1, 1))
            ).isoformat()
            stamped += 1
        out.append(json.dumps(row))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_the_proposal_scan_offers_a_rung_and_grants_NOTHING(_isolated_home):
    """`promotion_eligibility` has a production caller — and it cannot promote.

    The scan is what makes an earned rung visible without opening Settings. It files one
    proposal row and leaves the rung exactly where it was: the ladder's upward path is a
    click, so a scan that promoted would be the whole safety property gone.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _seed_clean_record(APP_KEY)
    assert au.promotion_eligibility(APP_KEY).eligible is True

    proposed = ld.propose_promotions()
    assert APP_KEY in proposed
    assert au.granted_rung(APP_KEY) == au.RUNG_DRAFT_ONLY, "a scan must never promote"

    rows = [
        r
        for r in _inbox_rows(_isolated_home)
        if (r.get("refs") or {}).get("action_type") == APP_KEY
    ]
    assert len(rows) == 1
    assert rows[0]["refs"]["rung"] == au.RUNG_ONE_TAP

    # Idempotent: a scan every few hours must leave ONE standing row, not a pile.
    ld.propose_promotions()
    rows = [
        r
        for r in _inbox_rows(_isolated_home)
        if (r.get("refs") or {}).get("action_type") == APP_KEY
    ]
    assert len(rows) == 1


def test_nothing_but_the_api_handler_grants_a_rung():
    """The upward path has exactly ONE call site, asserted from the source.

    A grant is the only thing in this subsystem that increases what runs unattended. Pinning
    the call site is what keeps "promotion is always a click" true as the tree grows — a
    background job that called `grant_rung` would satisfy every other test in this file.
    """
    import ast

    root = Path(au.__file__).resolve().parent.parent
    callers: set[str] = set()
    for path in root.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            if name == "grant_rung":
                callers.add(str(path.relative_to(root)))
    assert callers == {"dashboard/handlers/autonomy.py"}, callers


# ── done_when 4 (the refusals): a grant is validated, never trusted ──────────


def test_the_api_refuses_a_grant_ABOVE_the_declared_ceiling(_isolated_home):
    """A client-supplied rung is an ask. The declaration is the answer."""
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_ONE_TAP)
    resp = asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": APP_KEY, "rung": au.RUNG_AUTONOMOUS})
        )
    )
    assert resp.status == 400
    body = _json_body(resp)
    assert body["ok"] is False
    # Re-pointed, not relaxed: this pinned the raw rung KEY (`one_tap`) being in a message the
    # USER reads. It now asserts the stronger property — the label is there and the key is not.
    assert "ceiling" in body["error"]
    assert rg.rung_label(au.RUNG_ONE_TAP) in body["error"], body["error"]
    assert au.RUNG_ONE_TAP not in body["error"], body["error"]
    assert au.granted_rung(APP_KEY) == au.RUNG_DRAFT_ONLY


def test_the_api_refuses_a_grant_DURING_a_demotion_cooldown(_isolated_home):
    """The cooldown is the point of a demotion: it must survive a re-grant attempt."""
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    au.demote(APP_KEY, "rejected once")
    resp = asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": APP_KEY, "rung": au.RUNG_ONE_TAP})
        )
    )
    assert resp.status == 400
    assert "cannot be promoted again" in _json_body(resp)["error"]
    assert au.granted_rung(APP_KEY) == au.RUNG_DRAFT_ONLY


def test_the_record_a_DEMOTED_row_shows_says_when_the_cooldown_LIFTS(_isolated_home):
    """The panel renders ``record`` verbatim, so whatever it omits, the user cannot learn.

    Every other branch of that sentence quantifies what is missing ("4 of 10 clean approvals
    so far", "Approvals span 2.5 of the 14 days required"). The cooldown branch held the
    concrete date on the very same object and said only "A recent demotion is still in
    cooldown" — so a demoted user saw the demotion, its cause, no promote button, and had no
    way to tell whether they were blocked by the cooldown or by their track record, nor when
    it lifts. Driven through the REAL endpoint, because ``record`` is what the row carries.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    au.demote(APP_KEY, "rejected once")

    row = _row(_ladder(), APP_KEY)

    assert row["cooldown_until"], "the wire must still carry the full instant"
    when = row["cooldown_until"][:10]
    assert "cooldown" in row["record"], "it must still say WHAT is blocking promotion"
    assert when in row["record"], f"the record must name the end date: {row['record']!r}"
    # And a date, not a machine instant: the panel prints this string as-is.
    assert "T" not in row["record"] and "+00:00" not in row["record"], row["record"]


def test_the_two_cooldown_EXPLANATIONS_agree_on_the_date_and_its_form(_isolated_home):
    """One fact, two server-composed sentences — they must not disagree.

    ``record`` answers "why is this row inert"; the grant refusal answers "why was your
    click refused". Both are legitimate and stay separate (different questions, and the
    refusal names the key because it travels alone as an API error). What they may not do is
    state the same cooldown in two different forms — the refusal used to emit the raw ISO
    instant while the record named no date at all.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    au.demote(APP_KEY, "rejected once")

    record = _row(_ladder(), APP_KEY)["record"]
    resp = asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": APP_KEY, "rung": au.RUNG_ONE_TAP})
        )
    )
    refusal = _json_body(resp)["error"]

    when = au.cooldown_date(_row(_ladder(), APP_KEY)["cooldown_until"])
    assert when and "T" not in when
    assert when in record and when in refusal, f"{record!r} vs {refusal!r}"
    for sentence in (record, refusal):
        assert "+00:00" not in sentence, f"a raw instant leaked into user copy: {sentence!r}"


def test_an_UNPARSEABLE_cooldown_still_reports_the_cooldown_without_inventing_a_date(
    _isolated_home,
):
    """``_in_cooldown`` deliberately treats a corrupt timestamp as STILL RUNNING, so this
    branch is reachable by design. The cooldown is real; only its end is unknown. The
    sentence must drop the date and keep the cooldown, never the other way round."""
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    au.demote(APP_KEY, "rejected once")
    path = au._store_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data[APP_KEY]["demotions"][0]["cooldown_until"] = "not-a-timestamp"
    path.write_text(json.dumps(data), encoding="utf-8")

    row = _row(_ladder(), APP_KEY)

    assert row["eligible"] is False, "a corrupt cooldown must not read as permission"
    assert "cooldown" in row["record"]
    # 🪤 NOT `"not-a-timestamp" not in record` — a formatter that sliced the garbage would emit
    # "not-a-time" and pass that. Assert the SEMANTIC property: the sentence must not claim an
    # end date at all, however it were derived.
    assert (
        "until" not in row["record"]
    ), f"it must not claim a date it does not have: {row['record']!r}"
    assert "not-a-time" not in row["record"], "and it must not print the garbage either"


def test_a_grant_for_an_unknown_type_is_refused_by_name(_isolated_home):
    resp = asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": "app:nope.nothing", "rung": au.RUNG_ONE_TAP})
        )
    )
    assert resp.status == 400
    assert "not a registered action type" in _json_body(resp)["error"]


def test_the_grants_evidence_string_is_the_SERVERS_not_the_bodys(_isolated_home):
    """An audit row is worth its provenance. The body cannot write the evidence line."""
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _seed_clean_record(APP_KEY)
    asyncio.run(
        api_h.api_autonomy_grant(
            _post(
                "/api/autonomy/grant",
                {
                    "key": APP_KEY,
                    "rung": au.RUNG_ONE_TAP,
                    "evidence_window": "I promise it has been good",
                },
            )
        )
    )
    state = au.rung_state(APP_KEY)
    assert state is not None
    assert "clean approvals" in state.evidence_window
    assert "promise" not in state.evidence_window


def test_handing_autonomy_back_is_always_allowed(_isolated_home):
    """The safe direction needs no confirmation — and it starts the same cooldown."""
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    au.grant_rung(APP_KEY, au.RUNG_AUTO_WITH_UNDO, evidence_window="manual")
    resp = asyncio.run(api_h.api_autonomy_demote(_post("/api/autonomy/demote", {"key": APP_KEY})))
    assert resp.status == 200
    assert _json_body(resp)["cooldown_until"]
    assert au.granted_rung(APP_KEY) == au.RUNG_DRAFT_ONLY


# ── the undo executor fails CLOSED, and never demotes on a refusal ────────────


def test_an_unknown_record_id_refuses_and_does_NOT_demote(_isolated_home):
    """A bogus id must not be a way to degrade a type's autonomy.

    The refusal is the easy half. The demotion is the dangerous half: if a malformed request
    demoted, anyone able to POST could walk every type back to its floor.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    au.grant_rung(APP_KEY, au.RUNG_AUTO_WITH_UNDO, evidence_window="manual")
    resp = asyncio.run(
        api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": "rev_00000000000000ff"}))
    )
    assert resp.status == 404
    body = _json_body(resp)
    assert body["ok"] is False and body["code"] == "unknown_record"
    assert au.granted_rung(APP_KEY) == au.RUNG_AUTO_WITH_UNDO
    assert au.rung_state(APP_KEY).demotions == ()


@pytest.mark.parametrize(
    "bogus",
    [
        "not-an-id",
        "rev_zzzz",
        "../../etc/passwd",
        "rev_0000000000000000; DROP TABLE tasks",
        "",
    ],
)
def test_an_unshaped_id_never_reaches_the_store(_isolated_home, bogus):
    """Every id is shape-checked before anything reads state.

    None of these can name a record, and the point of refusing on SHAPE is that a string
    like a path or a fragment of SQL never travels further into the system than the regex.
    """
    if bogus:
        assert ld.reversal_record(bogus) is None
    resp = asyncio.run(api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": bogus})))
    assert resp.status in (400, 404)


@pytest.mark.parametrize(
    "handle",
    [
        "",
        "task",
        "task:",
        "task:  ",
        "TASK:native:1",
        "9task:native:1",
        "task:../../etc/passwd",
        "task:native:/etc/passwd",
        "task:native:a\nb",
        "task:native:" + "x" * 300,
        "task:native:a\x00b",
    ],
)
def test_a_handle_that_does_not_parse_is_refused_at_BOTH_ends(_isolated_home, handle):
    """Bounded, printable, kind-prefixed, escape-free — or it is not a handle.

    Both ends: the writer refuses to record it (so no undo button is ever drawn for it) and
    the reader refuses to act on one that reached the store some other way.
    """
    assert ld.parse_handle(handle) is None
    assert (
        ld.record_reversal_handle(
            action_type=APP_KEY, rung=au.RUNG_AUTO_WITH_UNDO, handle=handle, label="x"
        )
        == ""
    )
    assert ld.reversal_records() == ()


def test_a_hand_edited_store_row_with_a_bad_handle_is_dropped(_isolated_home):
    """The store is a file. A row that cannot be trusted is not an undo offer."""
    path = _isolated_home / "autonomy_reversals.json"
    path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "rev_00000000000000aa",
                        "action_type": APP_KEY,
                        "rung": au.RUNG_AUTO_WITH_UNDO,
                        "handle": "task:../../secrets",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert ld.reversal_records() == ()
    resp = asyncio.run(
        api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": "rev_00000000000000aa"}))
    )
    assert resp.status == 404


def test_a_provider_that_refuses_leaves_the_rung_ALONE(_isolated_home):
    """A failed reversal is a refusal, not a demotion.

    The task is deleted out from under the record here, which is the realistic case (the
    user tidied it up themselves). The undo cannot succeed, so nothing about the type's
    autonomy changes — a demotion here would spend the user's earned rung on nothing.
    """
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _fire()
    record = ld.reversal_records()[0]
    for path in _task_files(_isolated_home):
        path.unlink()

    resp = asyncio.run(api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": record.id})))
    assert resp.status == 400
    body = _json_body(resp)
    assert body["code"] == "provider_refused"
    assert "already gone" in body["error"]
    assert body["demoted"] is False
    assert au.rung_state(APP_KEY) is None, "a refusal writes nothing to the rung store"
    assert ld.reversal_records()[0].pending, "and the record stays pending"


def test_undoing_twice_refuses_the_second_time(_isolated_home):
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _fire()
    record = ld.reversal_records()[0]
    first = asyncio.run(api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": record.id})))
    assert first.status == 200
    second = asyncio.run(api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": record.id})))
    assert second.status == 400
    assert _json_body(second)["code"] == "already_reversed"


def test_a_handle_kind_no_provider_claims_is_refused(_isolated_home):
    """Resolution is bounded by the DECLARATION, so a handle cannot pick its own reverser."""
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    rid = ld.record_reversal_handle(
        action_type=APP_KEY,
        rung=au.RUNG_AUTO_WITH_UNDO,
        handle="mailbox:sent:42",
        label="Send",
    )
    resp = asyncio.run(api_h.api_autonomy_undo(_post("/api/autonomy/undo", {"id": rid})))
    assert resp.status == 400
    assert _json_body(resp)["code"] == "no_reverser"


def test_every_refusal_is_SEL_audited(_isolated_home):
    """A silent refusal is the failure mode this tree keeps finding. Both outcomes land."""
    from personalclaw.sel import sel

    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _fire()
    record = ld.reversal_records()[0]
    asyncio.run(ld.reverse_action("rev_00000000000000ff"))
    asyncio.run(ld.reverse_action(record.id))
    ops = [e.get("operation", "") for e in sel().recent(400)]
    assert "guardrails.autonomy_reverse_refused" in ops
    assert "guardrails.autonomy_reversed" in ops


def test_the_reversal_store_is_bounded(_isolated_home):
    """An undo handle is a small standing liability, so the ring is capped."""
    for i in range(ld._MAX_RECORDS + 12):
        ld.record_reversal_handle(
            action_type=APP_KEY, rung=au.RUNG_AUTO_WITH_UNDO, handle=f"task:native:{i}", label="t"
        )
    records = ld.reversal_records()
    assert len(records) == ld._MAX_RECORDS
    # Newest kept, oldest dropped.
    assert records[0].handle == f"task:native:{ld._MAX_RECORDS + 11}"


# ── done_when 3: the chip's sentence ──────────────────────────────────────────


def test_the_authority_sentence_names_the_DECLARED_floor(_isolated_home):
    """ "Why is this allowed to run by itself?" — because it was declared that way."""
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    row = _row(_ladder(), APP_KEY)
    assert row["resolved_rung"] == au.RUNG_AUTO_WITH_UNDO
    assert "declared" in row["authority"]
    assert rg.RUNG_LABELS[au.RUNG_AUTO_WITH_UNDO] in row["authority"]


def test_the_authority_sentence_names_YOUR_grant_and_its_evidence(_isolated_home):
    """A promoted type says who promoted it and on what record."""
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _seed_clean_record(APP_KEY)
    asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": APP_KEY, "rung": au.RUNG_ONE_TAP})
        )
    )
    row = _row(_ladder(), APP_KEY)
    # Re-pointed, not relaxed: this used to pin the literal "You promoted this to <label> on
    # <date>", which is the shape that read "You promoted this to runs on its own on 2026-08-17".
    # The properties that matter are the same three, asserted individually.
    assert "You promoted it" in row["authority"], row["authority"]
    assert "clean approvals" in row["authority"]
    assert rg.RUNG_LABELS[au.RUNG_ONE_TAP] in row["authority"]


def test_the_authority_sentence_says_when_an_incident_HOLDS_a_rung(_isolated_home):
    """The one case where the rung a user was granted is not the rung that applies."""
    from personalclaw.guardrails.incident import activate, resume

    _install_app_action(floor=au.RUNG_AUTONOMOUS, ceiling=au.RUNG_AUTONOMOUS)
    activate("testing")
    try:
        row = _row(_ladder(), APP_KEY)
        assert row["held_by_incident"] is True
        assert row["resolved_rung"] == au.RUNG_ONE_TAP
        assert "incident" in row["authority"]
        assert _ladder()["incident_active"] is True
    finally:
        resume()


def test_EVERY_authority_sentence_gives_the_rung_label_a_subject(_isolated_home):
    """A rung label is a PREDICATE, and every slot that interpolates one must read as one.

    ``RUNG_LABELS`` is declared "in terms of BEHAVIOUR rather than of the ladder" — "drafts
    only", "asks first", "runs with undo", "runs on its own". That is right for a chip, and right
    after a subject. Dropped into a noun slot it mangles: every row of the Guardrails panel used
    to read *"Runs at runs on its own because that is the rung it was declared with"*.

    This sweeps all three provenance branches — declared, granted, incident-held — because the
    defect was in one and the shape was in all three. The rule asserted is the one a reader can
    check without knowing the wording: **the label never directly follows a preposition.**
    """
    from personalclaw.guardrails.incident import activate, resume

    bad_prepositions = ("at ", "to ", "earned ", "granted ")

    def offenders(sentence: str) -> list[str]:
        out = []
        for label in rg.RUNG_LABELS.values():
            i = sentence.find(label)
            while i > 0:
                before = sentence[:i]
                if any(before.endswith(p) for p in bad_prepositions):
                    out.append(f"{before[-14:]!r} + {label!r}")
                i = sentence.find(label, i + 1)
        return out

    seen: list[str] = []

    # 1. declared floor
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    seen.append(_row(_ladder(), APP_KEY)["authority"])

    # 2. your grant
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _seed_clean_record(APP_KEY)
    asyncio.run(
        api_h.api_autonomy_grant(
            _post("/api/autonomy/grant", {"key": APP_KEY, "rung": au.RUNG_ONE_TAP})
        )
    )
    seen.append(_row(_ladder(), APP_KEY)["authority"])

    # 3. held by an incident
    _install_app_action(floor=au.RUNG_AUTONOMOUS, ceiling=au.RUNG_AUTONOMOUS)
    activate("testing")
    try:
        seen.append(_row(_ladder(), APP_KEY)["authority"])
    finally:
        resume()

    # Vacuity floor: a sweep over sentences that contain no label proves nothing.
    assert len(seen) == 3
    for sentence in seen:
        assert any(v in sentence for v in rg.RUNG_LABELS.values()), f"no label in {sentence!r}"
        assert offenders(sentence) == [], f"{sentence!r} → {offenders(sentence)}"


def test_the_PROPOSAL_title_quotes_the_label_instead_of_mangling_it(_isolated_home):
    """The one slot with no room for a subject: an inbox row title.

    It read "action.digest has earned runs on its own". A title cannot carry a clause, so the
    predicate is quoted as the name of the rung instead.
    """
    _install_app_action(floor=au.RUNG_DRAFT_ONLY, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _seed_clean_record(APP_KEY)
    assert ld.propose_promotions() == [APP_KEY]
    # 🪤 The persisted field is `message` — the title and body joined with a blank line
    # (`inbox.py`: "Title FIRST, then the body"). There is no `title` key on the row, so reading
    # one returns "" and the sweep passes while seeing nothing.
    rows = _inbox_rows(_isolated_home)
    titles = [str(r.get("message", "")).split("\n")[0] for r in rows]
    earned = [t for t in titles if "has earned" in t]
    assert earned, titles
    label = rg.RUNG_LABELS[au.RUNG_ONE_TAP]
    assert f"\u201c{label}\u201d" in earned[0], earned[0]
    assert f"earned {label}" not in earned[0], "the bare predicate must not follow 'earned'"


def test_the_ladder_view_carries_the_whole_governed_inventory(_isolated_home):
    """The panel enumerates every DECLARED type, plus the rung vocabulary it renders with."""
    view = _ladder()
    keys = {t["key"] for t in view["types"]}
    for spec in rg.CORE_ACTION_TYPES:
        assert spec.key in keys
    assert [m["key"] for m in view["rung_meta"]] == list(au.RUNGS)
    assert all(m["label"] and m["hint"] for m in view["rung_meta"])
    row = _row(view, "action.create_task")
    # Exact, not `in`: this row is the sample that proves a row carries its providers, and an
    # exact list is also the ratchet that makes an unnoticed addition to a governed class red.
    # `selfqa-file-finding` (SV-9) shares this class deliberately — what it ultimately does is
    # file a task through the provider beside it — so it is named here rather than the assertion
    # being loosened to accommodate it.
    assert row["providers"] == ["create-task", "selfqa-file-finding"]
    assert row["record"], "every row explains its track record, eligible or not"


def test_the_ladder_view_lists_a_pending_undo(_isolated_home):
    _install_app_action(floor=au.RUNG_AUTO_WITH_UNDO, ceiling=au.RUNG_AUTO_WITH_UNDO)
    _fire()
    view = _ladder()
    assert len(view["reversals"]) == 1
    assert view["reversals"][0]["reversed_at"] == ""
    assert view["reversals"][0]["action_type"] == APP_KEY


# ── the shipped create-task provider can reverse its own handle ───────────────


def test_create_task_deletes_the_row_it_filed(_isolated_home):
    """The one core provider that writes a reversal handle can also take it back.

    Driven through the provider, against the real native task store: the handle it returns
    from `execute` is fed straight back into `reverse`, so the two halves cannot disagree
    about the handle format.
    """
    from personalclaw.action_providers.create_task_provider import CreateTaskActionProvider

    provider = CreateTaskActionProvider()
    assert provider.reversal_kinds == ("task",)
    created = asyncio.run(
        provider.execute({"title_template": "Follow up"}, ActionContext(event="Stop"))
    )
    assert created.success and created.reversal.startswith("task:native:")
    assert len(_task_files(_isolated_home)) == 1

    undone = asyncio.run(provider.reverse(created.reversal))
    assert undone.success is True
    assert _task_files(_isolated_home) == []

    # A second reversal finds nothing and says so, rather than reporting success.
    again = asyncio.run(provider.reverse(created.reversal))
    assert again.success is False and "already gone" in again.error


def test_a_provider_with_no_undo_refuses_by_default(_isolated_home):
    """The base-class default is the right answer for every provider that never sets
    `reversal` — a refusal, not a silent success."""
    from personalclaw.action_providers.notify_provider import NotifyActionProvider

    provider = NotifyActionProvider()
    assert provider.reversal_kinds == ()
    result = asyncio.run(provider.reverse("task:native:1"))
    assert result.success is False
    assert "cannot undo" in result.error


def test_the_gateway_scan_is_the_proposal_paths_production_caller():
    """The proposal scan has a call site on a real loop, not only in this file."""
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    scan = inspect.getsource(GatewayOrchestrator._scan_autonomy_promotions)
    assert "propose_promotions" in scan
    loop = inspect.getsource(GatewayOrchestrator._file_watch_poll_loop)
    assert "_scan_autonomy_promotions" in loop
