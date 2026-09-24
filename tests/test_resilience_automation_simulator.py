"""Automation would-execute simulator on the trust surface (PLATFORM-RESILIENCE §3.3, PR2-7).

Every test here drives the REAL endpoint over the REAL handler
(``doctor.api_doctor_simulate_automation``) against a real ``TriggerStore`` row, because the
thing §3.3 asks for is a rendering a user can read on the trust surface — a formatter that
returns the right dict while nothing calls it is the "present but inert" shape this program
keeps finding. So the assertions are on the HTTP body, and each of the five facts has a named
test plus a vacuity case proving that test can fail.

The store lives under a redirected ``config_dir`` (both bindings) and the redirect is asserted,
so nothing here can reach the real ``~/.personalclaw``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import make_mocked_request

import personalclaw.config as config_pkg
import personalclaw.config.loader as config_loader
from personalclaw.dashboard.handlers import doctor as doctor_h
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

# ── harness ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    """A redirected home, patched at BOTH bindings.

    ``config/__init__.py`` re-exports ``config_dir`` and binds the function object at import,
    so patching only ``config.loader.config_dir`` leaves every import-bound store pointed at
    the real home. The asserts below are the vacuity check on the redirect itself: without
    them a mis-patched fixture would silently write ``triggers.json`` into ``~/.personalclaw``
    and the suite would still look green.
    """
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setattr(config_loader, "config_dir", lambda: root)
    monkeypatch.setattr(config_pkg, "config_dir", lambda: root)
    assert config_loader.config_dir() == root
    assert config_pkg.config_dir() == root
    return root


def _put(home: Path, trigger: Trigger) -> Trigger:
    return TriggerStore(base_dir=home).upsert(trigger)


def _simulate(trigger_id: Any) -> tuple[int, dict]:
    """POST /api/doctor/simulate/automation over the real handler."""
    req = make_mocked_request("POST", "/api/doctor/simulate/automation")

    async def _json():
        return {} if trigger_id is None else {"trigger_id": trigger_id}

    req.json = _json  # type: ignore[method-assign]
    resp = asyncio.run(doctor_h.api_doctor_simulate_automation(req))
    return resp.status, json.loads(resp.body.decode())


def _clock(
    trigger_id: str = "clock:deploy",
    *,
    provider: str = "bash",
    config: dict | None = None,
    **kw: Any,
) -> Trigger:
    """A clock trigger whose action is a flat ``{provider, config}`` (the S92 chat-tool shape)."""
    return Trigger(
        id=trigger_id,
        name="Deploy check",
        kind="clock",
        spec={"kind": "cron", "expr": "0 9 * * *", "timezone": "America/New_York"},
        workflow={"provider": provider, "config": dict(config or {"command": "echo hi"})},
        **kw,
    )


# ── the response is TOTAL over the five facts §3.3 names ──────────────────────


def test_response_carries_every_one_of_the_five_facts(home):
    _put(home, _clock())
    status, body = _simulate("clock:deploy")
    assert status == 200
    missing = [f for f in doctor_h.WOULD_EXECUTE_FACTS if f not in body]
    assert not missing, f"the would-execute description dropped {missing}"
    assert len(doctor_h.WOULD_EXECUTE_FACTS) == 5
    assert body["dry_run"] is True


# ── fact 1: resolved next-fire ────────────────────────────────────────────────


def test_next_fire_renders_the_armed_schedule(home):
    """The PERSISTED `next_fire_at` wins — the instant the tick will actually act on."""
    _put(home, _clock(next_fire_at="2031-03-04T14:00:00+00:00"))
    _status, body = _simulate("clock:deploy")
    fact = body["next_fire"]
    assert fact["armed"] is True
    assert fact["source"] == "armed"
    assert fact["at"].startswith("2031-03-04T14:00")
    # The cadence prose comes from the shipped `schedule.format_schedule`, not a second
    # formatter: a hand-rolled one rendered `0 9 * * * (America/New_York)`. The tz ABBREVIATION
    # is deliberately not pinned — `format_schedule` resolves it against today's DST offset, so
    # asserting "EST" would be a test that reds itself every March.
    assert fact["cadence"].startswith("At 9:00 AM E")
    assert "0 9 * * *" not in fact["cadence"]


def test_next_fire_reports_a_computed_answer_for_an_unarmed_row(home):
    """VACUITY for the test above: an unarmed row renders differently (and not as blank).

    `arm.needs_arming`'s whole population is enabled-but-inert rows with no `next_fire_at`;
    collapsing them into the armed case would hide exactly the automations a user is asking
    the trust surface about.
    """
    _put(home, _clock())  # no next_fire_at
    _status, body = _simulate("clock:deploy")
    fact = body["next_fire"]
    assert fact["armed"] is False
    assert fact["source"] == "computed"
    assert fact["epoch"] and fact["epoch"] > 0
    assert fact["at"]


# ── fact 2: rendered action_config with $vars ─────────────────────────────────


def _saved_prompt(home: Path, name: str, content: str, variables: list[dict]) -> None:
    from personalclaw.prompt_providers import get_default_provider
    from personalclaw.prompt_providers.base import PromptTemplate, PromptVariable
    from personalclaw.prompt_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    provider = get_default_provider()
    assert provider is not None
    provider.create_prompt(
        PromptTemplate(
            name=name,
            content=content,
            variables=[PromptVariable(**v) for v in variables],
        )
    )


def test_action_config_substitutes_vars_into_the_saved_prompt(home):
    _saved_prompt(
        home,
        "deploy-check",
        "Check {{service}} in {{env}}.",
        [{"name": "service", "required": True}, {"name": "env", "required": True}],
    )
    _put(
        home,
        _clock(
            provider="run-prompt",
            config={"prompt_id": "deploy-check", "vars": {"service": "gateway", "env": "prod"}},
        ),
    )
    _status, body = _simulate("clock:deploy")
    fact = body["action_config"]
    assert fact["provider"] == "run-prompt"
    assert fact["vars"] == {"service": "gateway", "env": "prod"}
    assert fact["rendered"] == "Check gateway in prod."
    assert fact["render_error"] == ""


def test_action_config_reports_the_render_error_a_real_fire_would_hit(home):
    """VACUITY: a required var the trigger never supplies previews as the FAILURE, not as a
    plausible prompt. `render_saved_prompt` normalizes PromptRenderError to ValueError and
    the provider turns that into a failed ActionResult — a preview that swallowed it would
    promise a run that cannot happen."""
    _saved_prompt(
        home,
        "deploy-check",
        "Check {{service}} in {{env}}.",
        [{"name": "service", "required": True}, {"name": "env", "required": True}],
    )
    _put(
        home,
        _clock(
            provider="run-prompt",
            config={"prompt_id": "deploy-check", "vars": {"service": "gateway"}},
        ),
    )
    _status, body = _simulate("clock:deploy")
    fact = body["action_config"]
    assert fact["rendered"] == ""
    assert "env" in fact["render_error"]


def test_secret_references_are_named_never_resolved(home, monkeypatch):
    """A `{{secret:KEY}}` in the config is NAMED in the preview and the credential store is
    never read — a would-execute description that resolved secrets would put a live token one
    JSON response away from a browser."""
    import personalclaw.triggers.secrets as secrets_mod

    def _boom(_key: str) -> str:  # pragma: no cover - asserted NOT to run
        raise AssertionError("the simulator resolved a credential")

    monkeypatch.setattr(secrets_mod, "default_resolver", _boom)
    _put(
        home,
        _clock(
            provider="bash",
            config={"command": "curl -H 'Authorization: Bearer {{secret:DEPLOY_TOKEN}}' x"},
        ),
    )
    _status, body = _simulate("clock:deploy")
    fact = body["action_config"]
    assert fact["secret_refs"] == ["DEPLOY_TOKEN"]
    rendered = fact["config"]["command"]
    assert "«secret:DEPLOY_TOKEN" in rendered
    assert "{{secret:DEPLOY_TOKEN}}" not in rendered


# ── fact 3: target session key ────────────────────────────────────────────────


def test_session_key_is_the_pinned_per_trigger_key(home):
    _put(home, _clock(session="pinned:cron:clock:deploy"))
    _status, body = _simulate("clock:deploy")
    fact = body["session_key"]
    assert fact["mode"] == "pinned"
    assert fact["key"] == "cron:clock:deploy"


def test_session_key_renders_a_conversation_binding_differently(home):
    """VACUITY: an in-chat nudge targets the LIVE conversation, not a per-trigger key."""
    _put(home, _clock(session="conversation:sess-42"))
    _status, body = _simulate("clock:deploy")
    fact = body["session_key"]
    assert fact["mode"] == "conversation"
    assert fact["key"] == "sess-42"


# ── fact 4: capability grants ────────────────────────────────────────────────


def test_capability_grants_refuse_a_write_capable_action_with_no_frozen_set(home):
    """`bash` is write-capable, so an empty `capabilities` block refuses it — and the reason
    is rendered, because a refusal a user cannot explain is one they work around by widening
    the allowlist far past what the automation needed."""
    _put(home, _clock(provider="bash", capabilities={}))
    _status, body = _simulate("clock:deploy")
    fact = body["capability_grants"]
    assert fact["requested"] == {"providers": ["bash"]}
    assert fact["needs_fence"] == {"providers": ["bash"]}
    assert fact["granted"] is False
    assert [r["value"] for r in fact["refused"]] == ["bash"]
    assert fact["refused"][0]["reason"]


def test_capability_grants_pass_a_read_only_action_with_no_frozen_set(home):
    """VACUITY for the test above, and decision 7's read-only default: `notify` is granted
    with no `capabilities` block at all. Rendering it as "nothing permitted" would be the
    false alarm that teaches users to widen fences."""
    _put(home, _clock(provider="notify", config={"title": "hi"}, capabilities={}))
    _status, body = _simulate("clock:deploy")
    fact = body["capability_grants"]
    assert fact["requested"] == {"providers": ["notify"]}
    assert fact["needs_fence"] == {}
    assert fact["granted"] is True
    assert fact["refused"] == []


def test_capability_grants_pass_a_write_capable_action_the_frozen_set_lists(home):
    """The third state: write-capable AND opted in. Three distinct renderings, so the panel
    can tell "fenced", "granted by default" and "granted by opt-in" apart."""
    _put(home, _clock(provider="bash", capabilities={"providers": ["bash"]}))
    _status, body = _simulate("clock:deploy")
    fact = body["capability_grants"]
    assert fact["declared"] == {"providers": ["bash"]}
    assert fact["granted"] is True
    assert fact["needs_fence"] == {"providers": ["bash"]}


# ── fact 5: observe-mode result (AUTOMATION-SUBSTRATE's dry fire + T9 honesty) ─


def test_observe_mode_reports_a_true_observe_run_for_run_prompt(home):
    _put(home, _clock(provider="run-prompt", config={"prompt_id": "nope"}))
    _status, body = _simulate("clock:deploy")
    fact = body["observe_mode"]
    assert fact["provider_known"] is True
    assert fact["supported"] is True
    assert fact["mode"] == "observe"
    assert fact["executed"] is False
    # The gate plan comes from AUTOMATION-SUBSTRATE's `manual_gate_plan`, not from here.
    assert fact["gate_plan"]["executes"] is False
    assert fact["gate_plan"]["dry_run"] is True
    assert "screen" in fact["gate_plan"]["enforced"]
    assert "nothing was executed" in fact["detail"]


def test_observe_mode_is_only_a_preview_for_a_deterministic_provider(home):
    """VACUITY, and the T9 honesty rule: `bash` has no observe mode, so this is a PREVIEW of
    what would run and says so. Labelling it "observe-mode result" would promise a safety
    property the provider does not have."""
    _put(home, _clock(provider="bash"))
    _status, body = _simulate("clock:deploy")
    fact = body["observe_mode"]
    assert fact["provider_known"] is True
    assert fact["supported"] is False
    assert fact["mode"] == "preview"
    assert fact["executed"] is False


def test_observe_mode_distinguishes_an_unknown_provider_from_a_missing_observe_mode(home):
    """A row naming a provider nobody registered is BROKEN, and reporting that as "this
    provider has no observe mode" would read as a deliberate design decision."""
    _put(home, _clock(provider="no-such-provider"))
    _status, body = _simulate("clock:deploy")
    fact = body["observe_mode"]
    assert fact["provider_known"] is False
    assert fact["supported"] is False


# ── zero side effects ─────────────────────────────────────────────────────────


def test_the_dry_run_leaves_the_store_byte_identical(home):
    _put(home, _clock(next_fire_at="2031-03-04T14:00:00+00:00", run_count=3))
    path = TriggerStore(base_dir=home).path
    before = path.read_bytes()
    status, body = _simulate("clock:deploy")
    assert status == 200
    assert path.read_bytes() == before
    row = TriggerStore(base_dir=home).get("clock:deploy")
    assert row is not None
    assert row.trigger.run_count == 3
    assert row.trigger.next_fire_at == "2031-03-04T14:00:00+00:00"
    assert row.trigger.last_fired_at == ""
    assert row.trigger.last_run_id == ""
    assert body["observe_mode"]["executed"] is False


def test_no_action_executes_and_no_model_is_called(home, monkeypatch):
    """The two side effects that would matter, wired to explode.

    A dry fire must never reach `ActionProvider.execute` (that is the property
    `automation_run(dry_run)` exists to have) and must never spend a token.
    """
    import personalclaw.llm_helpers as llm
    from personalclaw.action_providers.bash_provider import BashActionProvider
    from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider

    async def _no_llm(*_a: Any, **_k: Any):  # pragma: no cover - asserted NOT to run
        raise AssertionError("the simulator called a model")

    async def _no_exec(*_a: Any, **_k: Any):  # pragma: no cover - asserted NOT to run
        raise AssertionError("the simulator executed an action")

    monkeypatch.setattr(llm, "one_shot_completion", _no_llm)
    monkeypatch.setattr(BashActionProvider, "execute", _no_exec)
    monkeypatch.setattr(RunPromptActionProvider, "execute", _no_exec)

    for provider in ("bash", "run-prompt"):
        _put(home, _clock(trigger_id=f"clock:{provider}", provider=provider))
        status, _body = _simulate(f"clock:{provider}")
        assert status == 200


# ── AUTO-R15's typed issue records reach the surface ──────────────────────────


def test_a_near_miss_row_surfaces_its_closest_match_suggestion(home):
    """AUTO-R15: an agent that wrote `debounce_seconds` is told which key it meant. The
    would-execute description carries the typed issue records verbatim, `closest` included —
    that suggestion IS the contract, and a preview that dropped it would leave the user with
    "invalid" and no next step."""
    store = TriggerStore(base_dir=home)
    store.upsert(_clock())
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    rows = raw["triggers"] if isinstance(raw, dict) else raw
    rows[0]["gates"] = {"debounce_seconds": 30}
    store.path.write_text(json.dumps(raw), encoding="utf-8")

    _status, body = _simulate("clock:deploy")
    suggestions = [i["closest"] for i in body["trigger"]["issues"] if i["closest"]]
    assert "debounce_secs" in suggestions


# ── the registered error envelope ─────────────────────────────────────────────


def test_missing_trigger_id_uses_the_registered_wire_code(home):
    status, body = _simulate(None)
    assert status == 400
    assert body["error"]["code"] == "trigger_id_required"


def test_unknown_trigger_uses_the_registered_wire_code_and_names_the_id(home):
    _put(home, _clock())
    status, body = _simulate("clock:not-a-thing")
    assert status == 404
    assert body["error"]["code"] == "unknown_trigger"
    assert body["error"]["trigger_id"] == "clock:not-a-thing"


def test_both_new_codes_are_in_the_append_only_registry():
    """The rail the append-only registry exists for — a code emitted but unregistered would
    ship as a code no client can look up."""
    from personalclaw.http_errors import HTTP_ERROR_CODES

    assert "trigger_id_required" in HTTP_ERROR_CODES
    assert "unknown_trigger" in HTTP_ERROR_CODES


def test_the_guard_flag_turns_the_simulator_off(home, monkeypatch):
    """Same guard class as every other Doctor surface: `resilience.doctor_enabled` off → 404."""

    class _Off:
        doctor_enabled = False

    monkeypatch.setattr(doctor_h, "_resilience_cfg", lambda: _Off())
    _put(home, _clock())
    status, body = _simulate("clock:deploy")
    assert status == 404
    assert body["error"]["code"] == "doctor_disabled"
