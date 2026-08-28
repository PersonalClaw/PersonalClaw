"""PA-1 — approval memory: kind mapping, non-fact exclusion, matcher, parser.

The two directions the exclusion has to hold in are both asserted here: an
approval row must NOT appear in the ambient fact block, and an ordinary fact row
next to it must still appear. A one-directional assertion would pass with the
clause deleted (facts render) or with the clause over-broad (nothing renders).
"""

from __future__ import annotations

import json

import pytest

from personalclaw.learning import decay
from personalclaw.memory_record import MemoryKind, _kind_from_key, decay_profile

# ── kind mapping ──


def test_user_approval_prefix_maps_to_approval_kind():
    assert _kind_from_key("user.approval.9f8e7d6c5b4a") is MemoryKind.APPROVAL
    assert MemoryKind.APPROVAL.value == "approval"


@pytest.mark.parametrize(
    "key,kind",
    [
        ("pref.editor", MemoryKind.SEMANTIC),
        ("lesson.tone", MemoryKind.LESSON),
        ("user.commitment.abc", MemoryKind.COMMITMENT),
        ("user.procedural.abc", MemoryKind.PROCEDURAL),
        ("user.persona.abc", MemoryKind.SELF_PERSONA),
        # Near-misses must NOT become approvals: the prefix is exact.
        ("user.approvals.abc", MemoryKind.SEMANTIC),
        ("approval.abc", MemoryKind.SEMANTIC),
    ],
)
def test_neighbouring_prefixes_unchanged(key, kind):
    assert _kind_from_key(key) is kind


def test_approval_kind_has_a_decay_decision():
    # decay_profile() raises for an unmapped kind, and KIND_MULTIPLIERS.get()
    # would silently hand an unmapped profile the reference rate — assert both.
    assert decay_profile(MemoryKind.APPROVAL) == "approval"
    assert "approval" in decay.KIND_MULTIPLIERS


def test_every_kind_is_mapped_everywhere():
    from personalclaw.memory_record import _DEFAULT_TIER

    for kind in MemoryKind:
        assert kind in _DEFAULT_TIER, f"{kind} missing from _DEFAULT_TIER"
        assert decay_profile(kind) in decay.KIND_MULTIPLIERS


# ── the non-fact exclusion, both directions ──


def _store(tmp_path):
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    store.init()
    return store


def test_approval_rows_never_enter_the_fact_block(tmp_path):
    store = _store(tmp_path)
    try:
        assert store.set_semantic("pref.editor", "vim", 0.9, source="user_explicit") is None
        assert (
            store.set_semantic(
                "user.approval.aabbccddeeff",
                {"pattern": "archive:sender:noreply.github.com", "verdict": "deny"},
                0.95,
                source="system:triage",
            )
            is None
        )
        ctx = store.get_semantic_context()
        # Direction 1: the ordinary fact still renders (guards an over-broad clause).
        assert "pref.editor" in ctx
        # Direction 2: the approval rule does not (guards a missing clause).
        assert "user.approval" not in ctx
        assert "noreply.github.com" not in ctx
        manifest = store.get_l1_manifest()
        assert "user.approval" not in manifest
    finally:
        store.close()


def test_approval_rows_are_still_readable_as_records(tmp_path):
    # Excluded from the fact block, NOT from the store: the triage lookup is an
    # exact prefix query over these rows, so iter_records must still yield them.
    store = _store(tmp_path)
    try:
        store.set_semantic(
            "user.approval.aabbccddeeff",
            {"pattern": "archive:sender:x", "verdict": "deny"},
            0.95,
            source="system:triage",
        )
        recs = list(store.iter_records(kinds={MemoryKind.APPROVAL.value}))
        assert [r.id for r in recs] == ["user.approval.aabbccddeeff"]
        assert recs[0].kind is MemoryKind.APPROVAL
        # And an approval row is not returned when only facts are asked for.
        facts = list(store.iter_records(kinds={MemoryKind.SEMANTIC.value}))
        assert all(not r.id.startswith("user.approval.") for r in facts)
    finally:
        store.close()


# ── the matcher: deny-wins, most-specific, total ──

from datetime import datetime, timedelta, timezone  # noqa: E402

from personalclaw.proactive.approval import (  # noqa: E402
    APPROVAL_KEY_PREFIX,
    COOLDOWN_LADDER_SECONDS,
    ApprovalRule,
    Decision,
    ReplyAction,
    SuppressionState,
    Verdict,
    clear_suppression,
    escalate_suppression,
    match_rules,
    parse_reply,
    rule_from_row,
    rule_key,
    rule_matches,
    rule_to_value,
    suppression_active,
)

NOW = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
ITEM = "archive:sender:noreply.github.com"


def _rule(pattern, verdict, **kw):
    return ApprovalRule(pattern=pattern, verdict=Verdict(verdict), **kw)


def test_no_match_is_no_decision_not_an_allow():
    # The dangerous default. An empty rule set must never auto-execute.
    res = match_rules([], ITEM, now=NOW)
    assert res.decision is Decision.NO_DECISION
    assert res.auto_executes is False
    assert res.rule is None
    other = match_rules([_rule("mute:channel:x", "approve")], ITEM, now=NOW)
    assert other.decision is Decision.NO_DECISION


def test_deny_wins_at_equal_specificity():
    allow = _rule(ITEM, "approve")
    deny = _rule(ITEM, "deny")
    for rules in ([allow, deny], [deny, allow]):  # order must not matter
        res = match_rules(rules, ITEM, now=NOW)
        assert res.decision is Decision.DENY, res
        assert res.rule is not None and res.rule.verdict is Verdict.DENY


def test_broader_deny_beats_narrower_approve():
    # "deny at ANY matching specificity wins" — the whole asymmetry.
    res = match_rules([_rule("archive", "deny"), _rule(ITEM, "approve")], ITEM, now=NOW)
    assert res.decision is Decision.DENY
    assert res.rule.pattern == "archive"


def test_most_specific_approve_wins_among_approves():
    broad = _rule("archive", "approve")
    narrow = _rule(ITEM, "approve")
    res = match_rules([broad, narrow], ITEM, now=NOW)
    assert res.decision is Decision.AUTO_APPROVE
    assert res.rule.pattern == ITEM


def test_equal_specificity_tie_break_is_stable():
    # Two same-verdict rules of equal specificity: the named rule must be the
    # same every time (it lands in a ledger row). Tie-break = (pattern, key).
    a = _rule("archive:sender", "approve")
    b = _rule("archive:SENDER", "approve")  # normalizes to the same pattern
    c = _rule("archive:other", "approve")
    assert a.key == b.key  # normalization ⇒ one pattern, one key
    picked = {
        match_rules(order, "archive:sender:x", now=NOW).rule.key
        for order in ([a, c], [c, a], [b, c])
    }
    assert len(picked) == 1


def test_matching_is_segment_wise_not_string_wise():
    r = _rule("archive:sender", "approve")
    assert rule_matches(r, "archive:sender:noreply.github.com") is True
    assert rule_matches(r, "archive:sender") is True
    assert rule_matches(r, "archive:sender-domain:x") is False
    assert rule_matches(r, "archive") is False
    assert rule_matches(r, "") is False


def test_expired_rules_never_match():
    stale = _rule(ITEM, "approve", expires_at=(NOW - timedelta(days=1)).isoformat())
    assert match_rules([stale], ITEM, now=NOW).decision is Decision.NO_DECISION
    live = _rule(ITEM, "approve", expires_at=(NOW + timedelta(days=1)).isoformat())
    assert match_rules([live], ITEM, now=NOW).decision is Decision.AUTO_APPROVE


def test_an_explicit_rule_beats_a_cooldown():
    cooling = _rule(
        ITEM,
        "suppressed",
        decline_count=1,
        cooldown_until=(NOW + timedelta(hours=12)).isoformat(),
    )
    assert match_rules([cooling], ITEM, now=NOW).decision is Decision.SUPPRESS
    assert (
        match_rules([cooling, _rule(ITEM, "approve")], ITEM, now=NOW).decision
        is Decision.AUTO_APPROVE
    )
    assert match_rules([cooling, _rule(ITEM, "deny")], ITEM, now=NOW).decision is Decision.DENY


def test_an_elapsed_cooldown_stops_suppressing():
    done = _rule(
        ITEM, "suppressed", decline_count=2, cooldown_until=(NOW - timedelta(1)).isoformat()
    )
    assert match_rules([done], ITEM, now=NOW).decision is Decision.NO_DECISION


# ── row encoding ──


def test_rule_key_and_roundtrip():
    r = _rule(ITEM, "deny", created_from_digest="run-7", hit_count=3)
    assert r.key.startswith(APPROVAL_KEY_PREFIX)
    assert len(r.key) == len(APPROVAL_KEY_PREFIX) + 12
    assert rule_key(ITEM.upper()) == rule_key(ITEM)  # normalization is part of the key
    back = rule_from_row(r.key, rule_to_value(r))
    assert back == r
    assert rule_from_row(r.key, json.dumps(rule_to_value(r))) == r
    assert r.action_type == "archive"


@pytest.mark.parametrize(
    "key,value",
    [
        ("pref.editor", {"pattern": "x", "verdict": "approve"}),  # wrong prefix
        ("user.approval.abc", {"pattern": "x"}),  # no verdict
        ("user.approval.abc", {"pattern": "x", "verdict": "allow"}),  # unknown verdict
        ("user.approval.abc", {"verdict": "approve"}),  # no pattern
        ("user.approval.abc", "not json"),
        ("user.approval.abc", None),
    ],
)
def test_undecodable_rows_are_dropped_not_repaired(key, value):
    assert rule_from_row(key, value) is None
    # …and a dropped row cannot auto-approve anything.
    from personalclaw.proactive.approval import rules_from_rows

    assert match_rules(rules_from_rows([(key, value)]), "x", now=NOW).decision is (
        Decision.NO_DECISION
    )


# ── suppression ladder ──


def test_cooldowns_escalate_24h_7d_30d_then_clamp():
    state = None
    seen = []
    for _ in range(4):
        state = escalate_suppression(state, pattern=ITEM, now=NOW)
        seen.append(state.rung_seconds)
    assert seen == [
        COOLDOWN_LADDER_SECONDS[0],
        COOLDOWN_LADDER_SECONDS[1],
        COOLDOWN_LADDER_SECONDS[2],
        COOLDOWN_LADDER_SECONDS[2],
    ]
    assert COOLDOWN_LADDER_SECONDS == (24 * 3600, 7 * 86400, 30 * 86400)
    first = escalate_suppression(None, pattern=ITEM, now=NOW)
    assert first.cooldown_until == (NOW + timedelta(hours=24)).isoformat()
    assert suppression_active(first, now=NOW) is True
    assert suppression_active(first, now=NOW + timedelta(hours=25)) is False
    assert suppression_active(None, now=NOW) is False
    assert suppression_active(SuppressionState(ITEM, 1, None), now=NOW) is False


def test_accepting_during_a_cooldown_clears_it():
    cooling = _rule(
        ITEM,
        "suppressed",
        decline_count=3,
        cooldown_until=(NOW + timedelta(days=30)).isoformat(),
    )
    cleared = clear_suppression(cooling)
    assert cleared.cooldown_until is None
    assert cleared.decline_count == 0
    assert match_rules([cleared], ITEM, now=NOW).decision is Decision.NO_DECISION
    # The run of declines is over: the next decline starts at 24h again.
    nxt = escalate_suppression(
        SuppressionState(cleared.pattern, cleared.decline_count, None), pattern=ITEM, now=NOW
    )
    assert nxt.rung_seconds == COOLDOWN_LADDER_SECONDS[0]


# ── reply grammar ──


@pytest.mark.parametrize(
    "text,action,ordinal",
    [
        ("3 yes", ReplyAction.APPROVE_ONCE, 3),
        ("yes 3", ReplyAction.APPROVE_ONCE, 3),
        ("3 no", ReplyAction.DENY_ONCE, 3),
        ("no 3", ReplyAction.DENY_ONCE, 3),
        ("  3   YES ", ReplyAction.APPROVE_ONCE, 3),
        ("3 y", ReplyAction.APPROVE_ONCE, 3),
        ("always yes 3", ReplyAction.APPROVE_ALWAYS, 3),
        ("always no 4", ReplyAction.DENY_ALWAYS, 4),
        ("ALWAYS NO 4", ReplyAction.DENY_ALWAYS, 4),
        ("yes all", ReplyAction.APPROVE_ALL, None),
        ("no all", ReplyAction.DENY_ALL, None),
        ("all no", ReplyAction.DENY_ALL, None),
        ("help", ReplyAction.HELP, None),
        ("?", ReplyAction.HELP, None),
    ],
)
def test_grammar_accepts_the_documented_forms(text, action, ordinal):
    parsed = parse_reply(text, max_ordinal=8)
    assert parsed.action is action, parsed
    assert parsed.ordinal == ordinal
    assert parsed.error is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        None,
        "yes",  # no target
        "3",  # no verb
        "sure, go ahead and archive number 3",
        "yes 3 no 4",
        "always yes all",  # a blanket rule from one word: refused
        "yes 0",
        "yes 99",  # outside the digest
        "yes three",
        "maybe 3",
        "3 yep",
        "yes -1",
        "archive 3",
    ],
)
def test_garbage_never_becomes_an_approval(text):
    parsed = parse_reply(text, max_ordinal=8)
    assert parsed.action is ReplyAction.UNPARSEABLE, parsed
    assert parsed.approves is False
    assert parsed.persists_rule is False
    assert parsed.applies_to_all is False
    assert parsed.error and "Reply with" in parsed.error


def test_only_explicit_yes_approves():
    for text in ("3 no", "no all", "always no 3", "help", "gibberish"):
        assert parse_reply(text, max_ordinal=8).approves is False
    for text in ("3 yes", "yes all", "always yes 3"):
        assert parse_reply(text, max_ordinal=8).approves is True


def test_always_forms_are_the_only_rule_writers():
    assert parse_reply("always yes 3", max_ordinal=8).persists_rule is True
    assert parse_reply("always no 3", max_ordinal=8).persists_rule is True
    for text in ("3 yes", "yes all", "3 no"):
        assert parse_reply(text, max_ordinal=8).persists_rule is False


def test_ordinal_bounds_need_a_digest_size():
    # Without max_ordinal only the 1-or-higher floor applies (no fabricated cap).
    assert parse_reply("yes 99").action is ReplyAction.APPROVE_ONCE
    assert parse_reply("yes 99", max_ordinal=8).action is ReplyAction.UNPARSEABLE


# ── ProactiveConfig: the two wiring points test_config_roundtrip cannot see ──


def test_every_proactive_field_is_patchable_or_deliberately_not():
    from dataclasses import fields

    from personalclaw.config.learning import ProactiveConfig
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    # The generic round-trip test covers dataclass/_meta + load() + to_dict(); it
    # stays green if the PATCH entry is dropped, so assert the write path here.
    for f in fields(ProactiveConfig):
        assert f"proactive.{f.name}" in _EDITABLE_CONFIG, f"proactive.{f.name} not patchable"
        assert f.metadata.get("label"), f"proactive.{f.name} has no _meta label"
        assert f.metadata.get("help"), f"proactive.{f.name} has no _meta help"


def test_proactive_defaults_are_fail_closed():
    from personalclaw.config.learning import ProactiveConfig

    cfg = ProactiveConfig()
    assert cfg.triage_enabled is False
    assert cfg.auto_execute_enabled is False
    # The spend floor is the one that defaults ON.
    assert cfg.classifier_gate_enabled is True
    assert cfg.max_auto_actions_per_run == 5
    assert cfg.decision_default_horizon_days == 90


def test_unreadable_proactive_values_fail_in_the_safe_direction(tmp_path, monkeypatch):
    from unittest.mock import patch as _patch

    from personalclaw.config.loader import AppConfig

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "proactive": {
                    "triage_enabled": "",
                    "auto_execute_enabled": None,
                    "classifier_gate_enabled": None,
                    "max_auto_actions_per_run": "not-a-number",
                    "decision_default_horizon_days": -4,
                    "digest_schedule": "",
                }
            }
        ),
        encoding="utf-8",
    )
    with _patch("personalclaw.config.loader.config_path", return_value=path):
        cfg = AppConfig.load()
    assert cfg.proactive.triage_enabled is False
    assert cfg.proactive.auto_execute_enabled is False
    # Gate fails OPEN — an unreadable value must not send every item to the model.
    assert cfg.proactive.classifier_gate_enabled is True
    assert cfg.proactive.max_auto_actions_per_run == 5
    assert cfg.proactive.decision_default_horizon_days == 1
    assert cfg.proactive.digest_schedule == "0 8 * * *"


def test_a_negative_action_cap_clamps_to_zero_not_unbounded():
    from personalclaw.config.learning import ProactiveConfig

    assert ProactiveConfig(max_auto_actions_per_run=-1).max_auto_actions_per_run == 0


# ── the triage_rules tool + its endpoints ──


def _svc(tmp_path):
    from personalclaw.memory_service import MemoryService
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=tmp_path / "m.db", embedding_dim=3)
    store.init()
    return MemoryService.over_vector_store(store), store


@pytest.fixture()
def rules_api(tmp_path, monkeypatch):
    """The three approval-rule handlers over a real store in tmp_path."""
    from types import SimpleNamespace

    from personalclaw.dashboard.handlers import memory as mem_handlers

    svc, store = _svc(tmp_path)
    monkeypatch.setattr(mem_handlers, "_get_service", lambda state: svc)
    monkeypatch.setattr(mem_handlers, "_is_restricted_session", lambda state, req: False)
    monkeypatch.setattr(
        mem_handlers,
        "_sel",
        lambda: SimpleNamespace(log_api_access=lambda **kw: None),
    )
    try:
        yield mem_handlers
    finally:
        store.close()


def _mocked(method, path, body=None, match_info=None):
    from aiohttp.test_utils import make_mocked_request

    payload = json.dumps(body or {}).encode()
    req = make_mocked_request(method, path, payload=None)
    req._payload = None
    req.app["state"] = object()
    if match_info:
        req._match_info.update(match_info)

    async def _json():
        if body is None:
            raise ValueError("no body")
        return body

    req.json = _json  # type: ignore[method-assign]
    assert payload  # the encoded form is unused; keep the shape explicit
    return req


async def _json_of(resp):
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_rule_add_list_revoke_roundtrip_with_provenance(rules_api):
    add = await rules_api.api_memory_approval_rule_add(
        _mocked(
            "POST",
            "/api/memory/approval-rules",
            {
                "pattern": "archive:sender:noreply.github.com",
                "verdict": "deny",
                "created_from_digest": "run-42",
            },
        )
    )
    assert add.status == 200
    key = (await _json_of(add))["rule"]["key"]
    assert key.startswith("user.approval.")

    listed = await rules_api.api_memory_approval_rules(_mocked("GET", "/api/memory/approval-rules"))
    rules = (await _json_of(listed))["rules"]
    assert len(rules) == 1
    assert rules[0]["verdict"] == "deny"
    assert rules[0]["created_from_digest"] == "run-42"  # provenance
    assert rules[0]["hit_count"] == 0
    assert rules[0]["created_at"]

    gone = await rules_api.api_memory_approval_rule_delete(
        _mocked("DELETE", f"/api/memory/approval-rules/{key}", match_info={"key": key})
    )
    assert gone.status == 200
    listed2 = await rules_api.api_memory_approval_rules(
        _mocked("GET", "/api/memory/approval-rules")
    )
    assert (await _json_of(listed2))["rules"] == []


@pytest.mark.asyncio
async def test_rule_add_refuses_a_suppressed_verdict_and_a_blank_pattern(rules_api):
    for body in (
        {"pattern": "archive", "verdict": "suppressed"},
        {"pattern": "archive", "verdict": "allow"},
        {"pattern": "   ", "verdict": "approve"},
        {"verdict": "approve"},
    ):
        resp = await rules_api.api_memory_approval_rule_add(
            _mocked("POST", "/api/memory/approval-rules", body)
        )
        assert resp.status in (400, 422), body


@pytest.mark.asyncio
async def test_revoke_is_scoped_to_approval_keys(rules_api):
    resp = await rules_api.api_memory_approval_rule_delete(
        _mocked(
            "DELETE",
            "/api/memory/approval-rules/pref.editor",
            match_info={"key": "pref.editor"},
        )
    )
    assert resp.status == 400


def test_triage_rules_tool_is_declared_and_schema_bound():
    from personalclaw import mcp_memory
    from personalclaw.validation import MCP_CORE_SCHEMAS, validate_tool_args

    assert "triage_rules" in {t["name"] for t in mcp_memory._list_tools()}
    schema = MCP_CORE_SCHEMAS["triage_rules"]
    with pytest.raises(Exception):
        validate_tool_args({"action": "add", "verdict": "suppressed"}, schema)
    with pytest.raises(Exception):
        validate_tool_args({"action": "nope"}, schema)


def test_triage_rules_branches(monkeypatch):
    from personalclaw import mcp_memory

    calls: list[tuple] = []
    monkeypatch.setattr(
        mcp_memory,
        "_get",
        lambda path: calls.append(("get", path))
        or {
            "rules": [
                {
                    "key": "user.approval.aabbccddeeff",
                    "pattern": "archive:sender:x",
                    "verdict": "deny",
                    "hit_count": 4,
                    "created_from_digest": "run-9",
                    "scope": "global",
                }
            ],
            "unreadable": ["user.approval.deadbeef0000"],
        },
    )
    monkeypatch.setattr(
        mcp_memory,
        "_post",
        lambda path, body: calls.append(("post", path, body))
        or {"ok": True, "rule": {"key": "user.approval.aabbccddeeff"}},
    )
    monkeypatch.setattr(
        mcp_memory,
        "_delete",
        lambda path, body=None: calls.append(("delete", path)) or {"ok": True},
    )

    out = mcp_memory._call_tool_inner("triage_rules", {"action": "list"})
    assert "deny" in out and "4 hits" in out and "from run-9" in out
    assert "unreadable" in out  # not swallowed

    out = mcp_memory._call_tool_inner(
        "triage_rules", {"action": "add", "pattern": "archive:sender:x", "verdict": "deny"}
    )
    assert "Added deny rule" in out
    assert calls[-1][2]["created_from_digest"] == "tool:triage_rules"

    out = mcp_memory._call_tool_inner(
        "triage_rules", {"action": "revoke", "id": "user.approval.aabbccddeeff"}
    )
    assert "Revoked" in out

    # An unknown action is an error, not a silent fallthrough to `list`.
    assert "unknown action" in mcp_memory._call_tool_inner("triage_rules", {"action": "wat"})
    # And `add` without its arguments reports failure rather than writing a partial rule.
    assert "Error" in mcp_memory._call_tool_inner("triage_rules", {"action": "add"})
    assert "Error" in mcp_memory._call_tool_inner("triage_rules", {"action": "add", "pattern": "x"})
    assert "Error" in mcp_memory._call_tool_inner("triage_rules", {"action": "revoke"})


def test_the_approval_rule_routes_are_registered():
    """A handler nobody can reach is not a feature."""
    import pathlib

    from personalclaw.dashboard import handlers, server

    src = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    for verb, name in (
        ("add_get", "api_memory_approval_rules"),
        ("add_post", "api_memory_approval_rule_add"),
        ("add_delete", "api_memory_approval_rule_delete"),
    ):
        assert hasattr(handlers, name)
        assert f"handlers.{name}" in src
        assert verb in src
    assert '"/api/memory/approval-rules"' in src
    assert '"/api/memory/approval-rules/{key:.+}"' in src
