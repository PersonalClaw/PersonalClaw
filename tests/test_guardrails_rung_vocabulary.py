"""A rung a USER reads is named by its label, never by its code key.

`RUNG_LABELS`' own docstring draws the line: *"runs on its own" is what a user needs to know;
"autonomous" is what the code calls it.* Four sentences a person reads were on the wrong side of
it, in three modules:

    rungs.route_action_type      "action.create_task resolves draft_only"
                                 → the BODY of the inbox row a held action raises
                                   ("The 'create_task' action on trigger t-1 did not run: …"),
                                   and the trigger's recorded outcome error
    rungs.route_action_type      "…, narrowed to auto_with_undo by the cautious profile"
    autonomy.promotion_eligibility  "Already at its ceiling (autonomous)."
                                 → the Guardrails panel's `record` line, on twelve rows
    ladder.explain_refused_grant "…can never go above one_tap — that is its declared ceiling."
                                 → the 400 body a refused promotion click shows

This file is the ratchet for the rule, driven through the real composers rather than asserted
against a hand-built object, plus the four MACHINE-facing sites that deliberately keep the key.

🪤 Assert the exact rung KEYS, never a word from one. Action-type keys contain rung-ish words
(`inbox.reply_draft` has "draft"), so a substring rule on "draft" would fail on a correct sentence.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any

import pytest

from personalclaw.action_providers.base import ActionContext, ActionProvider, ActionResult
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import ladder as ld
from personalclaw.guardrails import policy as pol
from personalclaw.guardrails import rungs as rg


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the rung store, the SEL and the inbox. `PERSONALCLAW_HOME` as well
    as the patched `config_dir`, because several stores bind `config_dir` at import, and the
    SEL singleton is reset so its rows land in this home."""
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
    rg.ensure_core_action_types()
    yield home
    sel_mod.SecurityEventLog._instance = None
    sel_mod.SecurityEventLog._initialized = False


#: A declared action type the ladder holds: its floor drafts only, so every fire is withheld.
_HELD = "action.vocabulary_probe"
_PROVIDER = "vocabulary-probe"


class _Probe(ActionProvider):
    """An action provider that records every execution, so a held fire is provable."""

    def __init__(self) -> None:
        self.calls: list[ActionContext] = []

    @property
    def name(self) -> str:
        return _PROVIDER

    @property
    def display_name(self) -> str:
        return "Vocabulary probe"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        self.calls.append(ctx)
        return ActionResult(success=True, stdout="ran")


@pytest.fixture
def held_action():
    """`_PROVIDER` registered, and `_HELD` declared for it at the bottom rung."""
    from personalclaw.action_providers.registry import _providers, register_action_provider

    probe = _Probe()
    register_action_provider(probe)
    au.register_action_type(
        au.ActionTypeSpec(
            key=_HELD,
            floor=rg.RUNG_DRAFT_ONLY,
            ceiling=rg.RUNG_ONE_TAP,
            providers=(_PROVIDER,),
        )
    )
    yield probe
    au.unregister_action_type(_HELD)
    _providers.pop(_PROVIDER, None)


def _inbox_rows(home) -> list[dict]:
    path = home / "inbox.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", data)
    return list(items.values()) if isinstance(items, dict) else list(items)


def _keys_in(sentence: str) -> list[str]:
    """Which rung CODE keys leak into this sentence."""
    return [r for r in rg.RUNGS if r in sentence]


def test_the_vocabularies_are_actually_distinct() -> None:
    """The floor under every assertion below. If a key ever equalled its label, or a key were a
    substring of some label, the whole file would pass while proving nothing."""
    assert len(rg.RUNGS) >= 4
    for rung in rg.RUNGS:
        label = rg.rung_label(rung)
        assert label != rung, f"{rung} has no user-facing label, so this rule is unenforceable"
        assert rung not in label, f"{rung!r} inside its own label {label!r} defeats _keys_in"


def test_the_HELD_ACTION_inbox_body_names_the_rung_in_user_words() -> None:
    """`route_action_type().reason` is the sentence `announce_withheld` puts in an inbox row."""
    seen = 0
    for spec in rg.CORE_ACTION_TYPES:
        reason = rg.route_action_type(spec.key).reason
        assert _keys_in(reason) == [], f"{spec.key}: code name in user copy — {reason!r}"
        assert rg.rung_label(rg.resolve_rung(spec.key)) in reason, reason
        seen += 1
    assert seen >= 4, "the sweep must actually cover the declared types"


def test_the_reason_does_not_RENAME_the_action_the_sentence_already_named() -> None:
    """`route_action_type().reason` is always EMBEDDED in a sentence that has already named the
    action — "The 'bash' action on trigger t-1 did not run: …", "held for your approval: …". Naming
    the action TYPE here said it twice, the second time as a code identifier the user has never
    seen, and often as a *different* name for the thing just called `'bash'`
    (`action.execute_code`).
    """
    seen = 0
    for spec in rg.CORE_ACTION_TYPES:
        reason = rg.route_action_type(spec.key).reason
        assert spec.key not in reason, f"the reason renames the action: {reason!r}"
        assert reason.startswith("this action "), reason
        assert rg.rung_label(rg.resolve_rung(spec.key)) in reason, reason
        seen += 1
    assert seen >= 4


def test_the_key_is_still_carried_where_a_MACHINE_needs_it(_isolated_home, held_action) -> None:
    """Dropping the key from the prose must not drop it from the record. It stays on the route for
    any caller, on the inbox row's `refs["action_type"]`, and in the dedup key."""
    route = rg.route_action_type(_HELD)
    assert route.key == _HELD and not route.executes, route
    rg.announce_withheld(route, title="held")
    rg.announce_withheld(route, title="held again")  # the same standing request

    rows = _inbox_rows(_isolated_home)
    assert len(rows) == 1, "a second hold of one type stacked a second row"
    assert rows[0]["refs"]["action_type"] == _HELD, "the row must still be findable by type"
    assert rows[0]["refs"]["dedup_key"] == f"autonomy_hold:{_HELD}"

    rg.announce_withheld(route, title="from a trigger", dedup_key="autonomy_hold:own")
    assert {r["refs"]["dedup_key"] for r in _inbox_rows(_isolated_home)} == {
        f"autonomy_hold:{_HELD}",
        "autonomy_hold:own",
    }, "a caller's own dedup key is kept"


def _held_hook_run() -> str:
    """A hook on the held action, run through `hooks.run_script_hook`; its result's error."""
    from personalclaw.hooks import ScriptHook, run_script_hook

    hook = ScriptHook(
        id="h-probe",
        name="Probe on stop",
        event="Stop",
        provider=_PROVIDER,
        provider_config={},
        capabilities={"providers": [_PROVIDER]},
    )
    return asyncio.run(run_script_hook(hook)).error


def _held_trigger_run(home) -> str:
    """A clock trigger on the held action, fired through the gateway's store dispatch; the error
    its run-history row records."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.schedule_history import ScheduleRunStore

    trigger = types.SimpleNamespace(
        id="clock:probe",
        kind="clock",
        workflow={"inline": {"provider": _PROVIDER, "config": {}}},
        capabilities={"providers": [_PROVIDER]},
    )
    asyncio.run(object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, {"kind": "clock"}))
    runs, total = asyncio.run(ScheduleRunStore(home).list_for_job(trigger.id, 0, 20))
    assert total == 1, "a held fire left no run-history row"
    return runs[0]["error"]


def test_every_call_site_still_EMBEDS_the_reason_rather_than_showing_it_alone(
    _isolated_home, held_action
) -> None:
    """The warrant for a subject-less-of-the-key sentence: each of the four consumers supplies the
    context. If a call site ever surfaced `reason` on its own, "this action …" would have no
    referent and the key would need to come back.

    Four: the inbox row and the recorded error, for a hook's action and for a store trigger's
    (every clock, file, webhook, chained and data-event fire takes that dispatch). Each is driven,
    and what the user reads is checked: the row names the action and says it did not run, and the
    error says it is held for your approval, before the reason."""
    reason = rg.route_action_type(_HELD).reason
    assert reason.startswith("this action "), reason

    hook_error = _held_hook_run()
    trigger_error = _held_trigger_run(_isolated_home)
    assert held_action.calls == [], "a held action ran"
    assert hook_error == f"held for your approval: {reason}"
    assert trigger_error == f"held for your approval: {reason}"

    bodies = sorted(r["message"] for r in _inbox_rows(_isolated_home))
    assert len(bodies) == 2, bodies
    assert any(
        f"The {_PROVIDER!r} action on the Stop event did not run: {reason}." in b for b in bodies
    ), bodies
    assert any(
        f"The {_PROVIDER!r} action on trigger clock:probe did not run: {reason}." in b
        for b in bodies
    ), bodies


def test_the_NARROWED_branch_names_both_rungs_in_user_words(monkeypatch) -> None:
    """The second half of that sentence: a SafetyProfile that narrows names two rungs, and both
    were code keys. Exercised through a real narrowing profile, not a string built by hand."""
    monkeypatch.setattr(
        pol,
        "profile_for_session",
        lambda *a, **k: pol.SafetyProfile(name="cautious", approval="reads"),
    )
    # An action that can be undone: the profile's "runs with undo" bound binds only such an
    # action, so this is the type whose route takes the narrowing branch.
    reason = rg.route_action_type("action.create_task", session_key="s").reason

    assert "narrowed" in reason, f"the narrowing branch was not taken: {reason!r}"
    assert _keys_in(reason) == [], f"code name in user copy — {reason!r}"
    # 🪤 Found by mutation: reverting ONLY this branch to name the action type changed no test.
    # The sweep above exercises the unnarrowed branch (the default profile does not narrow), so the
    # narrowed half needs its own assertion for both vocabularies.
    assert "action.create_task" not in reason, f"the reason renames the action: {reason!r}"
    assert reason.startswith("this action "), reason
    # Both halves, in user words: what it would have been, and what it was narrowed to.
    assert rg.rung_label(rg.RUNG_AUTONOMOUS) in reason
    assert rg.rung_label(rg.RUNG_AUTO_WITH_UNDO) in reason


def test_the_PANEL_record_line_names_the_ceiling_in_user_words() -> None:
    """ "Already at its ceiling (autonomous)." rendered on twelve rows of the Guardrails panel."""
    at_ceiling = [s for s in rg.CORE_ACTION_TYPES if s.floor == s.ceiling]
    assert at_ceiling, "no type is at its ceiling, so this branch is unreachable here"
    reason = au.promotion_eligibility(at_ceiling[0].key).reason

    assert "ceiling" in reason, reason
    assert _keys_in(reason) == [], f"code name in user copy — {reason!r}"
    assert rg.rung_label(at_ceiling[0].ceiling) in reason, reason


def test_the_REFUSED_GRANT_error_names_the_ceiling_in_user_words() -> None:
    """The 400 body a refused promotion shows. "above <rung>" needs a noun, so the predicate is
    quoted as the rung's name — the form this family settled on for the inbox proposal title."""
    low = [
        s
        for s in rg.CORE_ACTION_TYPES
        if rg.rung_rank(s.ceiling) < rg.rung_rank(rg.RUNG_AUTONOMOUS)
    ]
    assert low, "no type ceilings below autonomous, so this branch is unreachable here"
    spec = low[0]
    msg = ld.explain_refused_grant(spec.key, rg.RUNG_AUTONOMOUS)

    assert "declared ceiling" in msg, msg
    assert _keys_in(msg) == [], f"code name in user copy — {msg!r}"
    assert f"“{rg.rung_label(spec.ceiling)}”" in msg, msg


# ── the machine-facing sites that deliberately KEEP the key ───────────────────


def test_an_unknown_rung_from_a_CALLER_is_echoed_verbatim() -> None:
    """Not a leak — the honest answer to "why was `whatever` refused?" is the value sent. A label
    lookup here would either invent a rung or print an empty string."""
    msg = ld.explain_refused_grant(rg.CORE_ACTION_TYPES[0].key, "not_a_rung")
    assert "not_a_rung" in msg and "is not a rung on the ladder" in msg


def test_a_BAD_DECLARATION_raises_with_the_key_and_the_whole_ladder() -> None:
    """A developer registering an unknown ceiling needs the key and the valid set, not prose."""
    from dataclasses import replace

    with pytest.raises(ValueError) as e:
        au.register_action_type(replace(rg.CORE_ACTION_TYPES[0], ceiling="nonsense"))
    assert "nonsense" in str(e.value) and "ceiling" in str(e.value)


def test_the_MACHINE_strings_still_carry_the_key(_isolated_home, held_action) -> None:
    """Two strings that must stay machine-readable, pinned so a later pass does not "finish the
    job": the audit detail (an SEL row is worth its exact value) and the proposal dedup key. A
    real grant writes the audit row, and a real proposal files the inbox row."""
    from personalclaw.sel import sel

    assert au.grant_rung(_HELD, rg.RUNG_ONE_TAP, evidence_window="w") == rg.RUNG_ONE_TAP
    rows = [e for e in sel().recent(50) if e.get("operation") == "guardrails.autonomy_granted"]
    assert rows, "the grant wrote no audit row"
    assert rows[0]["resources"].startswith(f"{_HELD} rung={rg.RUNG_ONE_TAP} "), rows[0]
    assert rg.rung_label(rg.RUNG_ONE_TAP) not in rows[0]["resources"], "the audit row took prose"

    assert ld._file_proposal(_HELD, rg.RUNG_ONE_TAP, "Ran cleanly.") is True
    proposals = [r for r in _inbox_rows(_isolated_home) if r["item_kind"] == "proposal"]
    assert [r["refs"]["dedup_key"] for r in proposals] == [
        f"autonomy_promotion:{_HELD}:{rg.RUNG_ONE_TAP}"
    ], "a dedup key is machine"


def test_there_is_ONE_label_accessor_now(monkeypatch) -> None:
    """Three copies of `RUNG_LABELS.get(x, x)` had grown up in as many modules. Prose composers
    call `rung_label`, so a label changed there reaches every sentence a person reads: the ladder
    panel's record line, a refused grant's message and the panel's authority sentence. The only
    other reader is the handler that ships the whole vocabulary to the frontend, which needs the
    dict itself."""
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import autonomy as handler

    real = rg.rung_label
    monkeypatch.setattr(rg, "rung_label", lambda rung: f"<{real(rung)}>")

    at_ceiling = next(s for s in rg.CORE_ACTION_TYPES if s.floor == s.ceiling)
    record = au.promotion_eligibility(at_ceiling.key).reason
    assert f"<{real(at_ceiling.ceiling)}>" in record, record
    low = next(
        s
        for s in rg.CORE_ACTION_TYPES
        if rg.rung_rank(s.ceiling) < rg.rung_rank(rg.RUNG_AUTONOMOUS)
    )
    refused = ld.explain_refused_grant(low.key, rg.RUNG_AUTONOMOUS)
    assert f"<{real(low.ceiling)}>" in refused, refused
    row = next(t for t in ld.ladder_view()["types"] if t["key"] == at_ceiling.key)
    assert f"<{real(row['resolved_rung'])}>" in row["authority"], row["authority"]

    resp = asyncio.run(handler.api_autonomy(make_mocked_request("GET", "/api/autonomy")))
    meta = json.loads(resp.body.decode())["rung_meta"]
    assert {m["key"]: m["label"] for m in meta} == {
        r: rg.RUNG_LABELS[r] for r in rg.RUNGS
    }, "the wire vocabulary ships the labels themselves"
