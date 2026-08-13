"""The governance ceiling: ceiling ∩ profile under tightest-wins (PHF-8 / §5 SH5.1+SH5.3).

Four groups, one per done_when:

1. **A profile cannot widen the ceiling** — one test per ARCHETYPE (ordinal, ruleset,
   gate, map), each attempting a widening and asserting it does not take.
2. **Fail-closed boot** — an unknown matcher / scope / value / archetype and a corrupt or
   unreadable file each abort with WHAT/WHY/FIX instead of running unbounded.
3. **The ceiling is operator-owned** — no PATCH allowlist entry, no write handler, and the
   agent-reachable path checks refuse it.
4. **Live readers** — the seams resolve through the ceiling: the action denylist, the rung
   router, the tool-approval pick, the spawn grant and the egress plane.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.guardrails import ceiling as C
from personalclaw.guardrails.policy import (
    HEADLESS,
    INTERACTIVE,
    SafetyProfile,
    profile_for_session,
    unattended_dispatch_key,
)


def _write_ceiling(tmp_path, scopes: dict) -> None:
    """Point the process at an operator ceiling holding ``scopes``."""
    path = tmp_path / "governance" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "scopes": scopes}), encoding="utf-8")
    C.reset_ceiling()


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """An isolated PersonalClaw home for every ceiling read (never the real one)."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.delenv(C.CEILING_PATH_ENV, raising=False)
    C.reset_ceiling()
    return tmp_path


# ── 1. a profile cannot widen the ceiling — one test per archetype ────────────


def test_ordinal_archetype_cannot_be_widened():
    """OrdinalControl composes strictest-of, in BOTH directions."""
    ceiling = C.parse_ceiling({"scopes": {"approval": {"value": "ask"}}})
    # A profile asking for the loosest value on the scale gets the ceiling's value.
    widest = SafetyProfile(name="w", approval="auto")
    assert C.resolve(ceiling, widest).approval == "ask"
    # A profile that is ALREADY stricter keeps its own value (the profile may narrow).
    loose_ceiling = C.parse_ceiling({"scopes": {"approval": {"value": "auto"}}})
    assert C.resolve(loose_ceiling, SafetyProfile(name="s", approval="ask")).approval == "ask"
    # And the same holds for every other ordinal scope, so this is a property of the
    # ARCHETYPE and not of one field.
    egress = C.parse_ceiling({"scopes": {"egress": {"value": "off"}}})
    assert C.resolve(egress, SafetyProfile(name="w", egress_tier="all")).egress_tier == "off"
    scan = C.parse_ceiling({"scopes": {"scan": {"value": "block"}}})
    assert C.resolve(scan, SafetyProfile(name="w", scan_mode="warn")).scan_mode == "block"


def test_ruleset_archetype_cannot_be_widened():
    """ScopedRuleset: deny UNIONs (a profile cannot drop a ceiling deny) and allow
    INTERSECTS (a profile cannot allow a path the ceiling never allowed)."""
    ceiling = C.parse_ceiling(
        {
            "scopes": {
                "paths": {
                    "mode": "closed",
                    "allow": ["~/ws/**"],
                    "deny": ["**/.env*"],
                }
            }
        }
    )
    attempt = SafetyProfile(
        name="w",
        # A profile trying to escape confinement AND to drop the ceiling's deny by
        # simply not mentioning it.
        path_allowlist=("/etc/**", "~/ws/src/**"),
        denylist_extra=(),
    )
    out = C.resolve(ceiling, attempt)
    assert "/etc/**" not in out.path_allowlist, "a profile widened the confinement"
    assert out.path_allowlist == ("~/ws/src/**",), "a NARROWER profile entry must survive"
    assert "**/.env*" in out.denylist_extra, "a profile dropped a ceiling deny"
    # An empty profile allow-list means "no restriction from me" — it must not empty the
    # intersection, which would silently brick every path.
    assert C.resolve(ceiling, SafetyProfile(name="p")).path_allowlist == ("~/ws/**",)
    # A profile deny the ceiling never mentioned is KEPT (narrowing is always allowed).
    extra = C.resolve(ceiling, SafetyProfile(name="p", denylist_extra=("**/secret.txt",)))
    assert set(extra.denylist_extra) == {"**/.env*", "**/secret.txt"}


def test_gate_archetype_cannot_be_widened():
    """CapabilityGate: ``enabled`` is AND-composed, so a profile cannot re-enable it."""
    ceiling = C.parse_ceiling({"scopes": {"tools": {"enabled": False}}})
    attempt = SafetyProfile(name="w", tool_grants="read_write")
    assert C.resolve(ceiling, attempt).tool_grants == "read"
    # An enabled ceiling does not GRANT write to a read-only profile either — AND, not OR.
    open_ceiling = C.parse_ceiling({"scopes": {"tools": {"enabled": True}}})
    assert (
        C.resolve(open_ceiling, SafetyProfile(name="r", tool_grants="read")).tool_grants == "read"
    )
    # The nested ruleset intersects: a tool the ceiling never allowed cannot be added.
    scoped = C.parse_ceiling({"scopes": {"tools": {"allow": ["read_*"]}}})
    out = C.resolve(scoped, SafetyProfile(name="w", tool_allowlist=("read_file", "bash")))
    assert out.tool_allowlist == ("read_file",) and out.tool_grants == "custom"


def test_map_archetype_cannot_be_widened():
    """ScopedMap: per-key tightest-wins, with 0 meaning unlimited (so 0 never wins)."""
    from personalclaw.guardrails.budgets import Budget

    ceiling = C.parse_ceiling({"scopes": {"budget": {"max_tokens": 1000, "max_dollars": 1.0}}})
    attempt = SafetyProfile(name="w", budget=Budget(max_tokens=10_000_000, max_dollars=0.0))
    out = C.resolve(ceiling, attempt)
    assert out.budget.max_tokens == 1000, "a profile raised a token cap"
    assert out.budget.max_dollars == 1.0, "unlimited (0) must lose to a real cap"
    # A tighter profile cap survives.
    tighter = SafetyProfile(name="t", budget=Budget(max_tokens=10, max_dollars=0.5))
    tight_out = C.resolve(ceiling, tighter)
    assert tight_out.budget.max_tokens == 10 and tight_out.budget.max_dollars == 0.5
    assert isinstance(tight_out.budget.max_tokens, int), "the meter compares against int tokens"


def test_every_archetype_has_a_compose_function_and_a_scope():
    """The four archetypes are all reachable — a compose function no scope uses would be
    dead engine code, and a scope with no archetype handler would be ungoverned."""
    declared = {s.archetype for s in C.CEILING_SCOPES}
    assert declared == {
        C.ARCHETYPE_ORDINAL,
        C.ARCHETYPE_RULESET,
        C.ARCHETYPE_GATE,
        C.ARCHETYPE_MAP,
    }
    for name in declared:
        assert callable(C._COMPOSE[name])


def test_resolution_dispatches_on_archetype_not_scope_name():
    """Adding a scope must be DATA. Register a brand-new scope row reusing an existing
    archetype and assert it composes with no engine change."""
    spec = C.ScopeSpec(
        name="scan_probe",
        archetype=C.ARCHETYPE_ORDINAL,
        scale="scan",
        value_field="scan_mode",
    )
    control = C._PARSE[spec.archetype](spec, {"value": "block"})
    from_profile = C._FROM_PROFILE[spec.archetype](spec, SafetyProfile(name="p", scan_mode="warn"))
    composed = C._COMPOSE[spec.archetype](control, from_profile)
    assert C._TO_OVERRIDES[spec.archetype](spec, composed) == {"scan_mode": "block"}


# ── 2. fail-closed boot ───────────────────────────────────────────────────────


def _assert_what_why_fix(exc: pytest.ExceptionInfo) -> None:
    rendered = str(exc.value)
    for label in ("WHAT:", "WHY:", "FIX:"):
        assert label in rendered, f"the boot abort is missing {label}: {rendered}"


def test_unknown_matcher_aborts_governance_boot(home):
    """done_when: an unknown matcher aborts boot with a WHAT/WHY/FIX error."""
    _write_ceiling(home, {"paths": {"deny": ["/etc/**"], "matcher": "regex_i_invented"}})
    with pytest.raises(C.GovernanceBootError) as exc:
        C.ensure_governance_boot()
    _assert_what_why_fix(exc)
    assert "regex_i_invented" in str(exc.value)
    assert "path_glob" in str(exc.value), "the FIX line must name the valid matchers"


def test_unknown_archetype_aborts_governance_boot():
    """A scope row naming an archetype with no compose function is a boot abort, not a
    silently skipped (and therefore ungoverned) scope."""
    with pytest.raises(C.GovernanceBootError) as exc:
        C.validate_scope_table(
            (*C.CEILING_SCOPES, C.ScopeSpec(name="oops", archetype="freeform_lambda")),
        )
    _assert_what_why_fix(exc)
    assert "freeform_lambda" in str(exc.value)


@pytest.mark.parametrize(
    ("scopes", "needle"),
    [
        ({"telepathy": {"value": "off"}}, "telepathy"),
        ({"approval": {"value": "whenever"}}, "whenever"),
        ({"approval": {"vlaue": "ask"}}, "vlaue"),
        ({"scan": {"value": "block", "extra": 1}}, "extra"),
        ({"paths": {"mode": "closed"}}, "allows nothing"),
        ({"paths": {"mode": "open", "allow": ["~/ws/**"]}}, "never be consulted"),
        ({"budget": {"max_tokens": "lots"}}, "non-negative number"),
        ({"tools": {"enabled": "yes"}}, "true or false"),
        ({"paths": {"deny": "not-a-list"}}, "list of strings"),
    ],
)
def test_bad_ceiling_content_aborts_boot(home, scopes, needle):
    """Every parse rejection is a boot abort: an unknown scope/key/value would otherwise
    be an operator tightening that silently did nothing."""
    _write_ceiling(home, scopes)
    with pytest.raises(C.GovernanceBootError) as exc:
        C.ensure_governance_boot()
    _assert_what_why_fix(exc)
    assert needle in str(exc.value)


def test_corrupt_json_aborts_boot(home):
    path = home / "governance" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "scopes": {', encoding="utf-8")
    C.reset_ceiling()
    with pytest.raises(C.GovernanceBootError) as exc:
        C.ensure_governance_boot()
    _assert_what_why_fix(exc)


def test_unreadable_ceiling_aborts_boot(home):
    """A ceiling that exists but cannot be read must NOT degrade to 'no ceiling' — that
    would turn a permissions problem into a privilege escalation."""
    path = home / "governance" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 1, "scopes": {}}', encoding="utf-8")
    path.chmod(0o000)
    C.reset_ceiling()
    try:
        with pytest.raises(C.GovernanceBootError) as exc:
            C.ensure_governance_boot()
        _assert_what_why_fix(exc)
    finally:
        path.chmod(0o600)


def test_future_version_aborts_boot(home):
    path = home / "governance" / "ceiling.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version": 9, "scopes": {}}', encoding="utf-8")
    C.reset_ceiling()
    with pytest.raises(C.GovernanceBootError) as exc:
        C.ensure_governance_boot()
    _assert_what_why_fix(exc)


def test_absent_ceiling_boots_open(home):
    """No file = no operator bound (the posture every release before this one shipped).
    Absence is not corruption, so it must not abort."""
    booted = C.ensure_governance_boot()
    assert booted.is_open
    assert C.resolve(booted, HEADLESS) is HEADLESS


def test_a_parse_failure_is_not_cached(home):
    """Every caller keeps failing closed until the file is fixed — a cached failure that
    resolved to OPEN on the second call would be a one-retry bypass."""
    _write_ceiling(home, {"approval": {"value": "nope"}})
    for _ in range(3):
        with pytest.raises(C.GovernanceBootError):
            C.active_ceiling()


def test_ceiling_is_read_once_so_a_mid_run_edit_cannot_widen(home):
    """The no-mid-run-widening property: a tamper after boot cannot widen the running
    process, only a restart an operator can see."""
    _write_ceiling(home, {"approval": {"value": "ask"}})
    assert C.ensure_governance_boot().control("approval").value == "ask"
    path = home / "governance" / "ceiling.json"
    path.write_text(json.dumps({"version": 1, "scopes": {"approval": {"value": "auto"}}}))
    # No reset — this is what a running gateway sees.
    assert C.active_ceiling().control("approval").value == "ask"
    assert profile_for_session("cron:x").approval == "ask"


# ── 3. the ceiling is operator-owned, not agent-owned ────────────────────────


def test_ceiling_has_no_config_patch_surface():
    """It is not config: nothing in the dashboard's PATCH allowlist can reach it."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    flat = json.dumps(_EDITABLE_CONFIG, default=str)
    assert "ceiling" not in flat and "governance" not in flat


def test_ceiling_path_is_refused_by_the_agent_path_checks():
    """``governance/`` is in the built-in sensitive-path denylist, so every agent-reachable
    path check (action denylist, files area, bash hooks) refuses it."""
    from personalclaw.security import is_sensitive_path

    assert is_sensitive_path("~/.personalclaw/governance/ceiling.json")
    assert is_sensitive_path("~/.personalclaw/governance")


def test_action_denylist_refuses_to_write_the_ceiling():
    from personalclaw.guardrails.denylist import check_action

    d = check_action("bash", {"path": "~/.personalclaw/governance/ceiling.json"})
    assert d.blocked and d.matched == "builtin:sensitive_path"


def test_ceiling_path_is_env_overridable_for_a_real_trust_root(tmp_path, monkeypatch):
    """The only way to a trust root the agent's own uid cannot rewrite: a path outside the
    home, which an operator can own as another uid and chmod 0444."""
    external = tmp_path / "operator" / "ceiling.json"
    external.parent.mkdir(parents=True)
    external.write_text('{"version": 1, "scopes": {"approval": {"value": "ask"}}}')
    monkeypatch.setenv(C.CEILING_PATH_ENV, str(external))
    C.reset_ceiling()
    assert C.ceiling_path() == external
    assert C.active_ceiling().control("approval").value == "ask"


def test_ceiling_path_is_not_frozen_at_import_time(home):
    """A module-scope constant built from ``config_dir()`` would bind the real home at
    import and no fixture could reach it (a recorded landmine in this repo)."""
    assert C.ceiling_path() == home / "governance" / "ceiling.json"


# ── 4. live readers: the seams resolve through the ceiling ────────────────────


def test_profile_for_session_composes_the_ceiling(home):
    """One composition site, every seam: ``profile_for_session`` is what the rung router,
    the denylist, the approval pick and the egress plane all call."""
    assert profile_for_session("cron:x").approval == "hook_based"  # HEADLESS, unbounded
    _write_ceiling(home, {"approval": {"value": "ask"}, "scan": {"value": "block"}})
    bounded = profile_for_session("cron:x")
    assert bounded.approval == "ask" and bounded.scan_mode == "block"
    # ...and an interactive session is bounded by the same machine-wide ceiling.
    assert profile_for_session("chat-1").approval == "ask"


def test_unattended_dispatch_key_resolves_headless():
    """A sessionless dispatch (a trigger/hook fire) is unattended by construction — the
    seams passed "" before, which classified as ATTENDED and resolved INTERACTIVE."""
    from personalclaw.guardrails.policy import is_unattended_session

    key = unattended_dispatch_key("trigger:t1")
    assert is_unattended_session(key)
    assert profile_for_session(key).name == HEADLESS.name
    assert profile_for_session("").name == INTERACTIVE.name


def test_denylist_confinement_is_a_live_reader(home):
    """The ceiling's ``paths`` allow plane bites at the action denylist — the seam a hook
    and an event trigger both dispatch through."""
    from personalclaw.guardrails.denylist import check_action

    _write_ceiling(home, {"paths": {"mode": "closed", "allow": ["/srv/allowed/**"]}})
    key = unattended_dispatch_key("trigger:t1")
    blocked = check_action("bash", {"path": "/srv/elsewhere/x.txt"}, session_key=key)
    assert blocked.blocked and blocked.matched == "ceiling:paths.allow"
    allowed = check_action("bash", {"path": "/srv/allowed/x.txt"}, session_key=key)
    assert not allowed.blocked


def test_rung_router_narrows_under_the_ceiling(home):
    """The rung route reads the composed profile, so a ceiling of ``ask`` pulls an
    autonomous action type down to a route that keeps a human in the loop."""
    from personalclaw.guardrails.autonomy import ActionTypeSpec, register_action_type
    from personalclaw.guardrails.rungs import route_provider_action

    register_action_type(
        ActionTypeSpec(
            key="test.ceiling_probe",
            providers=("ceiling-probe",),
            floor="autonomous",
            ceiling="autonomous",
        )
    )
    key = unattended_dispatch_key("trigger:t1")
    assert route_provider_action("ceiling-probe", session_key=key).rung == "auto_with_undo"
    _write_ceiling(home, {"approval": {"value": "ask"}})
    route = route_provider_action("ceiling-probe", session_key=key)
    assert route.rung == "autonomous", "an 'ask' ceiling leaves the type's own ceiling"


def test_approval_pick_reads_the_ceiling(home):
    from personalclaw.guardrails.policy import approval_policy_for_session
    from personalclaw.llm_helpers import ToolApprovalPolicy

    _write_ceiling(home, {"approval": {"value": "ask"}})
    assert approval_policy_for_session("cron:x") is ToolApprovalPolicy.HOOK_BASED


def test_spawn_grant_is_refused_by_the_ceiling(home):
    """The five widening branches in ``subagent._run_inner`` can only set "auto"; the
    ceiling is what can refuse it."""
    from personalclaw.guardrails.policy import ceiling_permits_approval

    assert ceiling_permits_approval("auto") is True
    _write_ceiling(home, {"approval": {"value": "ask"}})
    assert ceiling_permits_approval("auto") is False


def test_spawn_call_site_consults_the_ceiling():
    """Assert the CALL SITE, not just the helper: a helper with no caller is the inert
    control this atom exists to remove."""
    import ast
    import inspect
    import textwrap

    import personalclaw.subagent as subagent

    tree = ast.parse(textwrap.dedent(inspect.getsource(subagent.SubagentManager._run_inner)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "ceiling_permits_approval" in called


# ── done_when 4/5: drive a REAL unattended trigger, not a constructed object ──


def _fire_a_real_event_trigger(tmp_path, monkeypatch, action_config: dict):
    """Fire a real `web`/memory event trigger through the real engine + the real seam.

    Deliberately NOT a constructed profile call: `EventTriggerEngine.on_event` →
    `event_triggers._fire` → `guardrails.denylist.enforce_action` is the production path,
    and the point is that the seam itself now hands the guardrails a session identity.
    """
    import asyncio

    from personalclaw.event_triggers import (
        MEMORY_KEY_PATTERN,
        SOURCE_MEMORY,
        EventTrigger,
        EventTriggerEngine,
        EventTriggerStore,
    )

    executed: list[dict] = []

    class _Provider:
        async def execute(self, cfg, ctx, timeout=30):
            executed.append(dict(cfg))

    monkeypatch.setattr("personalclaw.action_providers.get_action_provider", lambda n: _Provider())
    store = EventTriggerStore(path=tmp_path / "event_triggers.json")
    store.upsert(
        EventTrigger(
            id="real-trigger",
            pattern=MEMORY_KEY_PATTERN,
            key_glob="x.*",
            action_provider="bash",
            action_config=action_config,
            debounce_secs=0,
        )
    )
    engine = EventTriggerEngine(store=store)

    async def go():
        engine.on_event(source=SOURCE_MEMORY, event_type="create", key="x.y", value="v", now=10.0)
        await asyncio.sleep(0.05)

    asyncio.run(go())
    return executed


def test_a_real_unattended_trigger_resolves_through_headless(home, monkeypatch):
    """done_when: "a real unattended trigger resolves through the headless profile with a
    live reader". The spy delegates to the real resolver, so this asserts WHAT THE SEAM
    ASKED — the defect was that it asked with "" and got INTERACTIVE."""
    import personalclaw.guardrails.policy as policy_mod

    asked: list[str] = []
    real = policy_mod.profile_for_session

    def _spy(key: str):
        asked.append(key)
        return real(key)

    monkeypatch.setattr(policy_mod, "profile_for_session", _spy)
    executed = _fire_a_real_event_trigger(home, monkeypatch, {"command": "echo hi"})

    assert executed, "the fire must still execute when nothing narrows it"
    assert asked, "the dispatch seam resolved no profile at all — the reader is dead"
    assert all(k.startswith("unattended:") for k in asked), asked
    assert all(real(k).name == HEADLESS.name for k in asked)


def test_a_narrower_ceiling_bites_a_real_trigger_fire(home, monkeypatch):
    """done_when: "a narrower profile bites ... confirmed from logs/SEL". Same real fire,
    one ceiling scope added: the action is refused before the provider runs."""
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kw):
            rows.append(kw)

    import personalclaw.sel as sel_mod

    monkeypatch.setattr(sel_mod, "sel", lambda: _Sel())
    _write_ceiling(home, {"paths": {"mode": "closed", "allow": ["/srv/allowed/**"]}})
    executed = _fire_a_real_event_trigger(home, monkeypatch, {"path": "/srv/elsewhere/x.txt"})

    assert executed == [], "the ceiling did not bite — the action ran anyway"
    denials = [r for r in rows if r.get("operation") == "guardrails.denylist"]
    assert denials and denials[0]["outcome"] == "blocked"
    assert "ceiling:paths.allow" in denials[0]["resources"]


def test_the_same_fire_is_allowed_inside_the_confinement(home, monkeypatch):
    """The confinement is a bound, not a brick: an in-scope path still fires. Without this
    the test above would pass for an implementation that blocks everything."""
    _write_ceiling(home, {"paths": {"mode": "closed", "allow": ["/srv/allowed/**"]}})
    executed = _fire_a_real_event_trigger(home, monkeypatch, {"path": "/srv/allowed/x.txt"})
    assert executed and executed[0]["path"] == "/srv/allowed/x.txt"


def test_the_gateway_trigger_seam_passes_an_unattended_identity():
    """The third seam (`_fire_store_trigger` — every clock/file/webhook trigger) is source-
    asserted because driving it needs a whole orchestrator: the property is WHICH key it
    passes, and it passed `session_key=""` before."""
    import ast
    import inspect
    import textwrap

    from personalclaw.gateway import GatewayOrchestrator

    # Parsed, not grepped: the comment above the call QUOTES the old `session_key=""` it
    # replaced, so a substring check trips on the code's own prose.
    tree = ast.parse(textwrap.dedent(inspect.getsource(GatewayOrchestrator._fire_store_trigger)))
    session_kwargs = [
        kw.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "session_key"
    ]
    assert session_kwargs, "the seam passes no session identity at all"
    for value in session_kwargs:
        assert not (
            isinstance(value, ast.Constant) and value.value == ""
        ), "the seam still resolves its posture as if a human were watching"
    assert "unattended_dispatch_key" in {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


# ── egress: the tier finally has a production reader ─────────────────────────


def test_egress_policy_for_profile_narrows_and_never_widens():
    from personalclaw.net.policy import CONNECTOR, STRICT, egress_policy_for_profile

    assert egress_policy_for_profile(STRICT, "off") is None
    assert egress_policy_for_profile(STRICT, "all") is STRICT
    registry = egress_policy_for_profile(CONNECTOR, "registry")
    assert registry.allow_only and "pypi.org" in registry.allow_hosts
    # The tier must not RAISE the surface's caps: CONNECTOR's 10MB survives REGISTRY's 100MB.
    assert registry.max_bytes == CONNECTOR.max_bytes
    assert registry.timeout_s == min(CONNECTOR.timeout_s, 60.0)
    # A surface's own allow-listed host survives the narrowing.
    homelab = STRICT.with_overrides(allow_hosts=("lab.local",))
    listed = egress_policy_for_profile(homelab, "listed")
    assert listed.allow_only and listed.allow_hosts == ("lab.local",)


def test_guard_enforces_the_exclusive_allow_list():
    from personalclaw.net.guard import evaluate
    from personalclaw.net.policy import egress_policy_for_profile

    policy = egress_policy_for_profile(
        __import__("personalclaw.net.policy", fromlist=["STRICT"]).STRICT, "registry"
    )
    denied = evaluate("https://example.com/x", policy, resolver=lambda h: ["93.184.216.34"])
    assert not denied.allow and "allow-list" in denied.reason
    allowed = evaluate("https://pypi.org/simple", policy, resolver=lambda h: ["151.101.0.223"])
    assert allowed.allow


@pytest.mark.asyncio
async def test_web_fetch_refuses_when_the_ceiling_turns_egress_off(home):
    """The agent's primary fetch surface reads the tier — ``egress_tier`` had NO reader at
    all before this, so "headless by construction" held only in tests."""
    from personalclaw.web.fetch import web_fetch

    _write_ceiling(home, {"egress": {"value": "off"}})
    outcome = await web_fetch(
        "https://example.com/x", session_key="cron:x", require_provenance=False
    )
    assert not outcome.ok and "egress is off" in outcome.error


@pytest.mark.asyncio
async def test_web_fetch_narrows_to_the_ceilings_allow_list(home, monkeypatch):
    from personalclaw.web import fetch as fetch_mod

    _write_ceiling(home, {"egress": {"value": "listed"}})
    seen: dict[str, object] = {}

    async def _fake_fetch(url, *, policy=None, **kw):
        seen["policy"] = policy
        raise RuntimeError("stop here — the policy is the assertion")

    # `net_fetch` is bound at import in web/fetch.py, so that name is the seam.
    monkeypatch.setattr(fetch_mod, "net_fetch", _fake_fetch)
    await fetch_mod.web_fetch(
        "https://example.com/x", session_key="cron:x", require_provenance=False
    )
    policy = seen.get("policy")
    assert policy is not None and policy.allow_only is True
    assert policy.allow_hosts == (), "an empty operator allow-list means nothing is reachable"


def test_web_poll_resolves_its_egress_through_the_profile(home):
    """The watched-source poll hardcoded ``STRICT``, so an operator's ``deny_hosts`` never
    reached the headless tier and the run's tier reached nothing."""
    from personalclaw.triggers import web_poll

    assert web_poll._poll_egress_policy("t1").name == "source"
    _write_ceiling(home, {"egress": {"value": "off"}})
    assert web_poll._poll_egress_policy("t1") is None
    _write_ceiling(home, {"egress": {"value": "registry"}})
    narrowed = web_poll._poll_egress_policy("t1")
    assert narrowed.allow_only and "pypi.org" in narrowed.allow_hosts


def test_poll_refuses_visibly_when_egress_is_off(home, tmp_path):
    """A refusal must be a REASON on the ledger row, not a silent skip."""
    from personalclaw.triggers import web_poll

    _write_ceiling(home, {"egress": {"value": "off"}})

    class _T:
        id = "t1"
        spec = {"url": "https://example.com/feed"}

    out = web_poll.poll_one(_T(), now=1_000_000.0, base_dir=tmp_path / "state")
    assert out.payload is None and "denies all network egress" in out.reason
    assert not out.fetched, "the refusal happens BEFORE a request is spent"


# ── observability: a clamp is never silent ───────────────────────────────────


def test_a_clamp_is_logged_and_sel_audited(home, caplog):
    """Silent downgrades are a standing finding here, so every narrowing is logged AND
    SEL-audited (once per distinct clamp per process, since this is a hot path)."""
    _write_ceiling(home, {"approval": {"value": "ask"}})
    C.reset_clamp_reports()
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kw):
            rows.append(kw)

    import personalclaw.sel as sel_mod

    original = sel_mod.sel
    sel_mod.sel = lambda: _Sel()
    try:
        with caplog.at_level("WARNING"):
            assert profile_for_session("cron:x").approval == "ask"
    finally:
        sel_mod.sel = original
    assert any("ceiling narrowed" in r.getMessage() for r in caplog.records)
    clamps = [r for r in rows if r.get("operation") == "guardrails.ceiling_clamp"]
    assert clamps and clamps[0]["outcome"] == "narrowed"
    assert "approval" in clamps[0]["resources"]


def test_boot_sel_audits_the_resolved_source(home):
    """Tamper evidence: the source + digest are recorded, so a changed ceiling is
    attributable after the fact."""
    _write_ceiling(home, {"approval": {"value": "ask"}})
    rows: list[dict] = []

    class _Sel:
        def log_api_access(self, **kw):
            rows.append(kw)

    import personalclaw.sel as sel_mod

    original = sel_mod.sel
    sel_mod.sel = lambda: _Sel()
    try:
        booted = C.ensure_governance_boot()
    finally:
        sel_mod.sel = original
    boot_rows = [r for r in rows if r.get("operation") == "guardrails.governance_boot"]
    assert boot_rows and boot_rows[0]["outcome"] == "bounded"
    assert booted.digest and booted.digest in boot_rows[0]["resources"]


def test_gateway_boot_calls_governance_first():
    """The abort has to happen BEFORE any service exists — a gateway that booted its cron
    loop and then found a corrupt ceiling has already dispatched under no bound."""
    import ast
    import inspect

    from personalclaw.gateway import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator.run)
    tree = ast.parse(src.lstrip())
    body = tree.body[0].body  # the function's statements, docstring first
    called_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.append(node.func.id)
    assert "ensure_governance_boot" in called_names
    # It is the first executable statement group: the import + call precede _init_services.
    first_calls = [
        n.func.id
        for stmt in body[:4]
        for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "ensure_governance_boot" in first_calls
