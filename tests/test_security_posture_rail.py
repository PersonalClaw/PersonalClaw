"""The rail on the one list of security-sensitive config fields.

The list is the set of `_EDITABLE_CONFIG` entries whose ``"security"`` holds a
``SecurityControl`` (`config/edit_spec.py` says what follows from being on it). These tests
keep it complete and honest:

* a field in a security SECTION cannot land without declaring which it is — a control, or a
  reviewed ``NotASecurityControl`` with its reason;
* every control is driven through BOTH refusals by `test_apps_cannot_relax_security.py`, whose
  loosening table must cover the list exactly;
* every control resolves to a value in effect, so the direction check never compares against
  nothing;
* the other tables that name security controls agree with the list rather than restating it.

The same two declarations reach past config fields, and are railed here too:

* **routes** — every WRITE route under an ``apps/permissions.SECURITY_ROUTE_FAMILIES`` root is
  owner-only (a subtree row) or declared in ``ROUTE_AUTHZ`` — owner-only, or app-allowed with the
  reason that is safe. A route added tomorrow fails here until someone decides which, and is
  refused to every app at runtime in the meantime (``undeclared_security_write``);
* **an automation's posture** — ``automation_posture.POSTURE_SPECS`` is driven by a consent case
  per key, and the run overlay's ``supervisor_policy.POLICY_OVERRIDE_SECURITY`` covers every knob
  and is held to what the engine consumes.
"""

from __future__ import annotations

import pytest
from test_apps_cannot_relax_security import LOOSENING_WRITES
from test_apps_cannot_run_code_or_bypass_approvals import AUTOMATION_POSTURE_CASES

from personalclaw.apps.permissions import SECURITY_ROUTE_FAMILIES
from personalclaw.automation_posture import POSTURE_SPECS
from personalclaw.config.edit_spec import (
    SECURITY_SECTIONS,
    NotASecurityControl,
    SecurityControl,
    security_control,
)
from personalclaw.config.loader import CONFIG_ON_DISCARDED_READ, AppConfig
from personalclaw.dashboard.handlers.core import _AGENT_PUT_FIELDS, _EDITABLE_CONFIG

CONTROLS = sorted(k for k, spec in _EDITABLE_CONFIG.items() if security_control(spec))


def _in_effect(path: str) -> object:
    node: object = AppConfig().to_dict()
    for part in path.split("."):
        assert isinstance(node, dict) and part in node, f"{path} does not resolve in to_dict()"
        node = node[part]
    return node


def test_the_list_is_not_vacuous() -> None:
    # A registry the scan cannot find reads as "nothing is sensitive", which is the hole.
    assert len(CONTROLS) >= 40, CONTROLS
    for named in ("agent.yolo", "agent.approval_mode", "auth.require_totp", "security.egress"):
        assert named in CONTROLS


@pytest.mark.parametrize(
    "field",
    sorted(k for k in _EDITABLE_CONFIG if k.split(".")[0] in SECURITY_SECTIONS),
)
def test_every_field_in_a_security_section_declares_what_it_is(field: str) -> None:
    declared = _EDITABLE_CONFIG[field].get("security")
    assert isinstance(declared, (SecurityControl, NotASecurityControl)), (
        f"{field} sits in a security section and declares no sensitivity. Add "
        '`"security": SecurityControl(...)` (and a case in LOOSENING_WRITES), or '
        '`"security": NotASecurityControl("why")` if changing it loosens nothing.'
    )


@pytest.mark.parametrize("field", sorted(k for k, s in _EDITABLE_CONFIG.items() if "security" in s))
def test_every_declaration_is_one_of_the_two_kinds(field: str) -> None:
    declared = _EDITABLE_CONFIG[field]["security"]
    assert isinstance(declared, (SecurityControl, NotASecurityControl))
    if isinstance(declared, NotASecurityControl):
        assert declared.reason.strip(), f"{field}: a reviewed exemption must say why"


@pytest.mark.parametrize("field", CONTROLS)
def test_every_control_resolves_to_a_value_in_effect(field: str) -> None:
    _in_effect(field)


@pytest.mark.parametrize("field", CONTROLS)
def test_every_consent_sentence_is_a_sentence(field: str) -> None:
    # It is dialog copy: a surface that did not ask its own question shows exactly this.
    consent = security_control(_EDITABLE_CONFIG[field]).consent
    assert consent[:1].isupper() and consent.endswith("."), consent


@pytest.mark.parametrize("field", CONTROLS)
def test_the_shipped_default_is_not_already_looser_than_itself(field: str) -> None:
    # A rule that calls "write what is already stored" a loosening would demand consent for a
    # no-op, which teaches the owner to click through the dialog.
    default = _in_effect(field)
    assert not security_control(_EDITABLE_CONFIG[field]).loosens(default, default)


def test_the_loosening_table_covers_every_control_exactly() -> None:
    covered = {field for field, _seed, _value in LOOSENING_WRITES}
    missing = sorted(set(CONTROLS) - covered)
    stale = sorted(covered - set(CONTROLS))
    assert not missing, f"security controls with no refusal case in LOOSENING_WRITES: {missing}"
    assert not stale, f"LOOSENING_WRITES names fields that are not security controls: {stale}"


def test_every_loosening_case_really_loosens() -> None:
    # A case whose value does NOT loosen would pass the owner test vacuously.
    for field, seed, value in LOOSENING_WRITES:
        current: object = seed
        for part in field.split("."):
            current = current.get(part) if isinstance(current, dict) else None
            if current is None:
                break
        if current is None:
            current = _in_effect(field)
        assert security_control(_EDITABLE_CONFIG[field]).loosens(current, value), (field, value)


@pytest.mark.parametrize("field", sorted(CONFIG_ON_DISCARDED_READ))
def test_a_discarded_read_substitutes_the_restrictive_end(field: str) -> None:
    # `CONFIG_ON_DISCARDED_READ` answers a different question (what to hold a field at when
    # config.json cannot be read), but its membership rule is "a security control", so every
    # entry must be on the list — and moving TO its value must never be a loosening.
    control = security_control(_EDITABLE_CONFIG[field])
    assert control is not None, f"{field} is held fail-closed but is not on the list"
    assert not control.loosens(_in_effect(field), CONFIG_ON_DISCARDED_READ[field])


def test_the_put_endpoint_writes_no_security_setting() -> None:
    # `PUT /api/config/personalclaw` carries neither refusal; that is only sound while none of
    # its fields is on the list.
    on_list = [k for k in _AGENT_PUT_FIELDS if security_control(_EDITABLE_CONFIG[f"agent.{k}"])]
    assert not on_list, on_list


def test_an_agent_profiles_approval_mode_is_the_same_control() -> None:
    from personalclaw.dashboard.handlers.agents import _AGENT_FIELD_SPECS

    per_agent = security_control(_AGENT_FIELD_SPECS["approval_mode"])
    assert per_agent is not None
    assert per_agent.loosens("", "auto") and per_agent.loosens("interactive", "trust_reads")
    assert not per_agent.loosens("auto", "interactive")
    assert not per_agent.loosens("", "")
    others = [k for k, s in _AGENT_FIELD_SPECS.items() if k != "approval_mode" and "security" in s]
    assert not others, f"a new agent-profile security field needs refusal tests: {others}"


# ── routes: every write in a security family declares who may reach it ───────────────


def _family_write_routes() -> list[tuple[str, str]]:
    """``(METHOD, canonical route)`` for every write route registered under a security family —
    from the same AST census the published route reference is rendered from, so no boot."""
    from personalclaw.apps.permissions import WRITE_METHODS, security_family
    from personalclaw.manifest_reference import _routes_from_ast

    return sorted(
        (r["method"], r["path"])
        for r in _routes_from_ast()
        if (r["method"] in WRITE_METHODS or r["method"] == "*") and security_family(r["path"])
    )


def test_the_route_census_is_not_vacuous() -> None:
    routes = _family_write_routes()
    assert len(routes) >= 100, f"only {len(routes)} write routes under the families — vacuous"
    assert ("PUT", "/api/mcp/servers/{name}") in routes
    assert ("POST", "/api/triggers") in routes


def test_every_write_route_in_a_security_family_declares_who_may_reach_it() -> None:
    from personalclaw.apps.permissions import ROUTE_AUTHZ, owner_only_api_reason

    undeclared = [
        f"{method} {route}"
        for method, route in _family_write_routes()
        if not owner_only_api_reason(route) and f"{method} {route}" not in ROUTE_AUTHZ
    ]
    assert not undeclared, (
        "write routes under a security family that declare nothing (an app is refused them at "
        "runtime until they do). Add each to apps/permissions.ROUTE_AUTHZ as OwnerOnly(what it "
        f"grants) or AppMay(why an app may): {undeclared}"
    )


def test_no_route_family_uses_a_wildcard_method() -> None:
    # A `*` route answers every verb, so a per-verb declaration cannot describe it.
    wild = [route for method, route in _family_write_routes() if method == "*"]
    assert not wild, wild


def test_every_route_declaration_is_real_and_says_why() -> None:
    from personalclaw.apps.permissions import (
        ROUTE_AUTHZ,
        AppMay,
        OwnerOnly,
        owner_only_api_reason,
        security_family,
    )

    registered = {f"{m} {r}" for m, r in _family_write_routes()}
    stale = sorted(k for k in ROUTE_AUTHZ if k not in registered)
    assert not stale, f"ROUTE_AUTHZ names routes nothing registers: {stale}"
    for key, authz in ROUTE_AUTHZ.items():
        _method, route = key.split(" ", 1)
        assert security_family(route), f"{key} is outside every security family"
        # A subtree row already decides it; a second declaration could only disagree.
        assert not owner_only_api_reason(route), f"{key} is shadowed by an owner-only subtree"
        assert isinstance(authz, (OwnerOnly, AppMay)), key
        text = authz.capability if isinstance(authz, OwnerOnly) else authz.reason
        assert text.strip(), f"{key}: a declaration must say what it grants or why it is safe"


@pytest.mark.parametrize("family", sorted(SECURITY_ROUTE_FAMILIES))
def test_a_new_write_in_any_family_fails_closed(family: str) -> None:
    # Refused either way: by a subtree row that covers every route under it, or by the
    # undeclared-write check until someone declares it.
    from personalclaw.apps.permissions import owner_only_api_reason, undeclared_security_write

    route = f"{family}/{{id}}/a-route-added-tomorrow"
    assert owner_only_api_reason(route) or undeclared_security_write("POST", route)
    assert not undeclared_security_write("GET", route), "a READ is the allowlist's business"


# ── an automation's posture ───────────────────────────────────────────────────────────


def test_every_automation_posture_key_is_driven_by_a_consent_case() -> None:
    covered = {key for posture, _field in AUTOMATION_POSTURE_CASES for key in posture}
    assert covered == set(POSTURE_SPECS), (covered, set(POSTURE_SPECS))


@pytest.mark.parametrize("key", sorted(POSTURE_SPECS))
def test_an_automation_posture_key_is_a_control_with_a_sentence(key: str) -> None:
    control = security_control(POSTURE_SPECS[key])
    assert control is not None
    assert control.consent[:1].isupper() and control.consent.endswith("."), control.consent
    # Unset is the shipped state of every step; re-writing it must never ask.
    assert not control.loosens("", "")


def test_the_run_overlay_declares_every_knob() -> None:
    from personalclaw.workflows.supervisor_policy import (
        _OVERRIDE_READERS,
        OVERRIDABLE_POLICY_KEYS,
        POLICY_OVERRIDE_SECURITY,
    )

    assert set(POLICY_OVERRIDE_SECURITY) == set(OVERRIDABLE_POLICY_KEYS)
    for knob, declared in POLICY_OVERRIDE_SECURITY.items():
        assert isinstance(declared, (SecurityControl, NotASecurityControl)), knob
        if isinstance(declared, SecurityControl):
            assert knob in _OVERRIDE_READERS, f"{knob} is a control with no value reader"


#: One loosest value per overlay knob, for the behaviour check below.
_OVERLAY_PROBES = {
    "max_cycles": 0,
    "autopilot": True,
    "attended": False,
    "idle_secs": 86_400,
    "success_criteria": "anything at all",
}


@pytest.mark.parametrize("knob", sorted(_OVERLAY_PROBES))
def test_an_overlay_declaration_matches_what_the_engine_consumes(knob: str) -> None:
    # `tick_config` is the one consumer of a run's resolved policy (`RunController._on_loop_trip`).
    # A control must change what it produces, and an exemption citing `_NOT_CONSUMED` must not —
    # so wiring `autopilot` into the engine turns this red until it is declared a control.
    from personalclaw.workflows.supervisor_policy import (
        _NOT_CONSUMED,
        POLICY_OVERRIDE_SECURITY,
        apply_policy_overrides,
        parse_supervisor_policy,
        tick_config,
    )

    assert set(_OVERLAY_PROBES) == set(POLICY_OVERRIDE_SECURITY)
    base = parse_supervisor_policy({"budget": {"max_cycles": 3}})
    changed = tick_config(
        apply_policy_overrides(base, {knob: _OVERLAY_PROBES[knob]})
    ) != tick_config(base)
    declared = POLICY_OVERRIDE_SECURITY[knob]
    if isinstance(declared, SecurityControl):
        assert changed, f"{knob} is declared a control but changes nothing the engine reads"
    elif declared.reason == _NOT_CONSUMED:
        assert not changed, f"{knob} now changes the engine; declare it a SecurityControl"
