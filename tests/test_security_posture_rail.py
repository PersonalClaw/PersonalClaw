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

* **routes** — every WRITE route under an ``apps/permissions.SECURITY_ROUTE_FAMILIES`` root, and
  every READ under one of the ``READ_DECLARED_FAMILIES`` (your conversations), is owner-only (a
  subtree row) or declared in ``ROUTE_AUTHZ`` — owner-only, or app-allowed with the reason that is
  safe. A route added tomorrow fails here until someone decides which, and is refused to every app
  at runtime in the meantime (``undeclared_security_route``);
* **an automation's posture** — ``automation_posture.POSTURE_SPECS`` is driven by a consent case
  per key, and the run overlay's ``supervisor_policy.POLICY_OVERRIDE_SECURITY`` covers every knob
  and is held to what the engine consumes.
"""

from __future__ import annotations

import functools

import pytest
import route_census
from fenced_rows import fences_unchanged
from table_rows import row
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
from personalclaw.config.editable import _EDITABLE_CONFIG
from personalclaw.config.loader import CONFIG_ON_DISCARDED_READ, AppConfig
from personalclaw.dashboard.handlers.core import _AGENT_PUT_FIELDS

pytestmark = pytest.mark.security


CONTROLS = sorted(k for k, spec in _EDITABLE_CONFIG.items() if security_control(spec))


def _controls_in_the_registry() -> int:
    """How many controls ``_EDITABLE_CONFIG`` holds, counted apart from :data:`CONTROLS`, so a
    table that skipped one cannot agree with it."""
    return sum(1 for spec in _EDITABLE_CONFIG.values() if security_control(spec) is not None)


@functools.cache
def _defaults() -> dict:
    """What a fresh config resolves to, read once: every row below reads it and none writes it."""
    return AppConfig().to_dict()


def _in_effect(path: str) -> object:
    node: object = _defaults()
    for part in path.split("."):
        assert isinstance(node, dict) and part in node, f"{path} does not resolve in to_dict()"
        node = node[part]
    return node


def test_every_control_on_the_list_declares_itself_and_resolves(subtests) -> None:
    """The list itself: it is not vacuous, every field of a security section and every
    declaration says which kind it is, and every control resolves to a value in effect, asks
    in a sentence and ships no looser than itself."""
    with row(subtests, check="the list is not vacuous"):
        # A registry the scan cannot find reads as "nothing is sensitive", which is the hole.
        assert len(CONTROLS) >= 40, CONTROLS
        for named in ("agent.yolo", "agent.approval_mode", "auth.require_totp", "security.egress"):
            assert named in CONTROLS
    check = "every field in a security section declares what it is"
    with row(subtests, check=check):
        fields = sorted(k for k in _EDITABLE_CONFIG if k.split(".")[0] in SECURITY_SECTIONS)
        checked = 0
        for field in fields:
            with row(subtests, check=check, field=field), fences_unchanged():
                declared = _EDITABLE_CONFIG[field].get("security")
                assert isinstance(declared, (SecurityControl, NotASecurityControl)), (
                    f"{field} sits in a security section and declares no sensitivity. Add "
                    '`"security": SecurityControl(...)` (and a case in LOOSENING_WRITES), or '
                    '`"security": NotASecurityControl("why")` if changing it loosens nothing.'
                )
            checked += 1
        in_sections = sum(1 for k in _EDITABLE_CONFIG if k.partition(".")[0] in SECURITY_SECTIONS)
        assert checked == in_sections, "a field of a security section was left out of the table"
    check = "every declaration is one of the two kinds"
    with row(subtests, check=check):
        checked = 0
        for field in sorted(k for k, s in _EDITABLE_CONFIG.items() if "security" in s):
            with row(subtests, check=check, field=field), fences_unchanged():
                declared = _EDITABLE_CONFIG[field]["security"]
                assert isinstance(declared, (SecurityControl, NotASecurityControl))
                if isinstance(declared, NotASecurityControl):
                    assert declared.reason.strip(), f"{field}: a reviewed exemption must say why"
            checked += 1
        declaring = sum(1 for spec in _EDITABLE_CONFIG.values() if "security" in spec)
        assert checked == declaring, "a declaration was left out of the table"
    check = "every control resolves to a value in effect"
    with row(subtests, check=check):
        checked = 0
        for field in CONTROLS:
            with row(subtests, check=check, field=field), fences_unchanged():
                _in_effect(field)
            checked += 1
        assert checked == _controls_in_the_registry(), "a control was left out of the table"
    check = "every consent sentence is a sentence"
    with row(subtests, check=check):
        # It is dialog copy: a surface that did not ask its own question shows exactly this.
        checked = 0
        for field in CONTROLS:
            with row(subtests, check=check, field=field), fences_unchanged():
                consent = security_control(_EDITABLE_CONFIG[field]).consent
                assert consent[:1].isupper() and consent.endswith("."), consent
            checked += 1
        assert checked == _controls_in_the_registry(), "a control was left out of the table"
    check = "the shipped default is not already looser than itself"
    with row(subtests, check=check):
        # A rule that calls "write what is already stored" a loosening would demand consent for a
        # no-op, which teaches the owner to click through the dialog.
        checked = 0
        for field in CONTROLS:
            with row(subtests, check=check, field=field), fences_unchanged():
                default = _in_effect(field)
                assert not security_control(_EDITABLE_CONFIG[field]).loosens(default, default)
            checked += 1
        assert checked == _controls_in_the_registry(), "a control was left out of the table"


def test_every_control_is_driven_through_its_refusals(subtests) -> None:
    """Every control has a loosening case that loosens, a field held fail-closed holds it at
    its restrictive end, and the writes that carry no refusal write no control."""
    with row(subtests, check="the loosening table covers every control exactly"):
        covered = {field for field, _seed, _value in LOOSENING_WRITES}
        missing = sorted(set(CONTROLS) - covered)
        stale = sorted(covered - set(CONTROLS))
        assert not missing, f"security controls with no refusal case in LOOSENING_WRITES: {missing}"
        assert not stale, f"LOOSENING_WRITES names fields that are not security controls: {stale}"
    with row(subtests, check="every loosening case really loosens"):
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
    check = "a discarded read substitutes the restrictive end"
    with row(subtests, check=check):
        # `CONFIG_ON_DISCARDED_READ` answers a different question (what to hold a field at when
        # config.json cannot be read), but its membership rule is "a security control", so every
        # entry must be on the list — and moving TO its value must never be a loosening.
        checked = 0
        for field in sorted(CONFIG_ON_DISCARDED_READ):
            with row(subtests, check=check, field=field), fences_unchanged():
                control = security_control(_EDITABLE_CONFIG[field])
                assert control is not None, f"{field} is held fail-closed but is not on the list"
                assert not control.loosens(_in_effect(field), CONFIG_ON_DISCARDED_READ[field])
            checked += 1
        assert checked == len(CONFIG_ON_DISCARDED_READ), "a held field was left out of the table"
    with row(subtests, check="the put endpoint writes no security setting"):
        # `PUT /api/config/personalclaw` carries neither refusal; that is only sound while none of
        # its fields is on the list.
        on_list = [k for k in _AGENT_PUT_FIELDS if security_control(_EDITABLE_CONFIG[f"agent.{k}"])]
        assert not on_list, on_list
    with row(subtests, check="an agent profiles approval mode is the same control"):
        from personalclaw.dashboard.handlers.agents import _AGENT_FIELD_SPECS

        per_agent = security_control(_AGENT_FIELD_SPECS["approval_mode"])
        assert per_agent is not None
        assert per_agent.loosens("", "auto") and per_agent.loosens("interactive", "trust_reads")
        assert not per_agent.loosens("auto", "interactive")
        assert not per_agent.loosens("", "")
        # The other agent-profile control, the tool list, driven through its refusals in
        # `test_widening_an_agents_tool_list_asks_first.py`. Empty is every tool.
        tools = security_control(_AGENT_FIELD_SPECS["tools"])
        assert tools is not None
        assert tools.loosens(["read_file"], ["read_file", "bash"])
        assert tools.loosens(["read_file"], [])
        assert not tools.loosens([], ["bash"])
        assert not tools.loosens(["read_file", "bash"], ["bash"])
        assert not tools.loosens([], [])
        others = [
            k
            for k, s in _AGENT_FIELD_SPECS.items()
            if k not in ("approval_mode", "tools") and "security" in s
        ]
        assert not others, f"a new agent-profile security field needs refusal tests: {others}"


# ── routes: every write in a security family declares who may reach it ───────────────


def _registered_routes() -> tuple[dict, ...]:
    """Every route the source registers, from the same AST census the published route reference
    is rendered from, so no boot. Read once per process for every module that asks
    (``tests/route_census.py``), and again when a route is planted."""
    return route_census.routes()


def _family_write_routes() -> list[tuple[str, str]]:
    """``(METHOD, canonical route)`` for every write route registered under a security family."""
    from personalclaw.apps.permissions import WRITE_METHODS, security_family

    return sorted(
        (r["method"], r["path"])
        for r in _registered_routes()
        if (r["method"] in WRITE_METHODS or r["method"] == "*") and security_family(r["path"])
    )


def _family_read_routes(*, declared_families_only: bool = True) -> list[tuple[str, str]]:
    """``(METHOD, canonical route)`` for every READ registered under a family whose reads are
    declared route by route (the conversation families) — or, with ``declared_families_only``
    off, under any security family, where a read MAY carry a row (``owns`` on a draft skill)."""
    from personalclaw.apps.permissions import READ_DECLARED_FAMILIES, READ_METHODS, security_family

    return sorted(
        (r["method"], r["path"])
        for r in _registered_routes()
        if r["method"] in READ_METHODS
        and security_family(r["path"])
        and (not declared_families_only or security_family(r["path"]) in READ_DECLARED_FAMILIES)
    )


def test_every_route_in_a_security_family_is_declared_and_a_new_one_fails_closed(subtests) -> None:
    """The route census: not vacuous, every write in a family and every read in a
    conversation family declared, every declaration real and saying why, and a route added
    tomorrow refused until it is declared."""
    with row(subtests, check="the route census is not vacuous"):
        routes = _family_write_routes()
        assert len(routes) >= 100, f"only {len(routes)} write routes under the families — vacuous"
        assert ("PUT", "/api/mcp/servers/{name}") in routes
        assert ("POST", "/api/triggers") in routes
        # A repair to your setup and an update of the code that runs it are in the census as well.
        assert ("POST", "/api/doctor/fix/{fix_id}") in routes
        assert ("POST", "/api/doctor/remediation/run") in routes
        assert ("POST", "/api/update") in routes
        # So are the switches on your tools and the write of what a project's sessions are given.
        assert ("POST", "/api/tools/toggle") in routes
        assert ("POST", "/api/tools/provider-toggle") in routes
        assert ("PUT", "/api/legibility/always-on/doc") in routes
        # And so are a project's: its brief, instructions and folder, and the instruction files
        # in it.
        assert ("POST", "/api/projects") in routes
        assert ("PUT", "/api/projects/{project_id}") in routes
        assert ("POST", "/api/projects/{project_id}/context-adapters/regenerate") in routes
    with row(subtests, check="the read census is not vacuous"):
        reads = _family_read_routes()
        vacuous = f"only {len(reads)} reads under the conversation families — vacuous"
        assert len(reads) >= 30, vacuous
        assert ("GET", "/api/chat/sessions/{session}") in reads
        assert ("GET", "/api/sessions/search") in reads
    with row(subtests, check="the read declared families are your conversations"):
        from personalclaw.apps.permissions import READ_DECLARED_FAMILIES

        assert READ_DECLARED_FAMILIES <= set(SECURITY_ROUTE_FAMILIES)
        conversations = {"/api/chat", "/api/sessions", "/api/session", "/api/rooms", "/api/inbox"}
        assert conversations <= READ_DECLARED_FAMILIES
    with row(subtests, check="every read in a conversation family declares who may reach it"):
        from personalclaw.apps.permissions import ROUTE_AUTHZ, owner_only_api_reason

        undeclared = [
            f"{method} {route}"
            for method, route in _family_read_routes()
            if not owner_only_api_reason(route) and f"{method} {route}" not in ROUTE_AUTHZ
        ]
        assert not undeclared, (
            "reads under a conversation family that declare nothing (an app is refused them at "
            "runtime until they do). Add each to apps/permissions.ROUTE_AUTHZ as OwnerOnly(what it "
            "shows) or AppMay(why an app may, with `owns` when it names one conversation): "
            f"{undeclared}"
        )
    with row(subtests, check="every write route in a security family declares who may reach it"):
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
    with row(subtests, check="no route family uses a wildcard method"):
        # A `*` route answers every verb, so a per-verb declaration cannot describe it.
        wild = [route for method, route in _family_write_routes() if method == "*"]
        assert not wild, wild
    with row(subtests, check="every route declaration is real and says why"):
        from personalclaw.apps.permissions import (
            ROUTE_AUTHZ,
            AppMay,
            OwnerOnly,
            owner_only_api_reason,
            security_family,
        )

        # A read row is legal under any family: every read in a conversation family carries one,
        # and elsewhere a read that names a conversation does (the draft skills a chat produced).
        registered = {
            f"{m} {r}"
            for m, r in _family_write_routes() + _family_read_routes(declared_families_only=False)
        }
        stale = sorted(k for k in ROUTE_AUTHZ if k not in registered)
        assert not stale, f"ROUTE_AUTHZ names routes nothing registers: {stale}"
        for key, authz in ROUTE_AUTHZ.items():
            method, route = key.split(" ", 1)
            assert security_family(route), f"{key} is outside every security family"
            # A subtree row already decides it; a second declaration could only disagree.
            assert not owner_only_api_reason(route), f"{key} is shadowed by an owner-only subtree"
            # HEAD answers from the GET row (`route_authz`), so a HEAD row could only disagree.
            assert method != "HEAD", f"{key}: declare the GET; HEAD is the same read"
            assert isinstance(authz, (OwnerOnly, AppMay)), key
            text = authz.capability if isinstance(authz, OwnerOnly) else authz.reason
            assert text.strip(), f"{key}: a declaration must say what it grants or why it is safe"
            # A field of the body that is the owner's is refused in its own words, so it has some.
            for owned in authz.owner_only_fields if isinstance(authz, AppMay) else ():
                assert owned.field and owned.capability.strip(), f"{key}: {owned}"
    check = "a new route in any family fails closed"
    with row(subtests, check=check):
        # A write is refused either way: by a subtree row that covers every route under it, or by
        # the undeclared-route check until someone declares it. A read is refused the same way
        # under the conversation families, and is the allowlist's business anywhere else.
        from personalclaw.apps.permissions import (
            READ_DECLARED_FAMILIES,
            owner_only_api_reason,
            undeclared_security_route,
        )

        checked = 0
        for family in sorted(SECURITY_ROUTE_FAMILIES):
            with row(subtests, check=check, family=family), fences_unchanged():
                route = f"{family}/{{id}}/a-route-added-tomorrow"
                assert owner_only_api_reason(route) or undeclared_security_route("POST", route)
                if family in READ_DECLARED_FAMILIES:
                    assert undeclared_security_route("GET", route), "a read of yours is declared"
                    assert undeclared_security_route("HEAD", route), "HEAD is the same read"
                else:
                    assert not undeclared_security_route("GET", route), "a READ is the allowlist's"
            checked += 1
        assert checked == len(SECURITY_ROUTE_FAMILIES), "a family was left out of the table"


# ── an automation's posture ───────────────────────────────────────────────────────────


def test_every_automation_posture_key_is_a_control_driven_by_consent(subtests) -> None:
    """Every key of an automation's posture has a consent case, and is a control that asks in
    a sentence."""
    with row(subtests, check="every automation posture key is driven by a consent case"):
        covered = {key for posture, _field in AUTOMATION_POSTURE_CASES for key in posture}
        assert covered == set(POSTURE_SPECS), (covered, set(POSTURE_SPECS))
    check = "an automation posture key is a control with a sentence"
    with row(subtests, check=check):
        checked = 0
        for key in sorted(POSTURE_SPECS):
            with row(subtests, check=check, key=key), fences_unchanged():
                control = security_control(POSTURE_SPECS[key])
                assert control is not None
                consent = control.consent
                assert consent[:1].isupper() and consent.endswith("."), consent
                # Unset is the shipped state of every step; re-writing it must never ask.
                assert not control.loosens("", "")
            checked += 1
        assert checked == len(POSTURE_SPECS), "a posture key was left out of the table"


#: One loosest value per overlay knob, for the behaviour check below.
_OVERLAY_PROBES = {
    "max_cycles": 0,
    "autopilot": True,
    "attended": False,
    "idle_secs": 86_400,
    "success_criteria": "anything at all",
}


def test_the_run_overlay_declares_each_knob_as_the_engine_consumes_it(subtests) -> None:
    """Every knob of a run's overlay is declared, and a declaration matches what the engine
    consumes."""
    with row(subtests, check="the run overlay declares every knob"):
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
    check = "an overlay declaration matches what the engine consumes"
    with row(subtests, check=check):
        # `tick_config` is the one consumer of a run's resolved policy
        # (`RunController._on_loop_trip`). A control must change what it produces, and an exemption
        # citing `_NOT_CONSUMED` must not — so wiring `autopilot` into the engine turns this red
        # until it is declared a control.
        from personalclaw.workflows.supervisor_policy import (
            _NOT_CONSUMED,
            POLICY_OVERRIDE_SECURITY,
            apply_policy_overrides,
            parse_supervisor_policy,
            tick_config,
        )

        assert set(_OVERLAY_PROBES) == set(POLICY_OVERRIDE_SECURITY)
        base = parse_supervisor_policy({"budget": {"max_cycles": 3}})
        checked = 0
        for knob in sorted(_OVERLAY_PROBES):
            with row(subtests, check=check, knob=knob), fences_unchanged():
                changed = tick_config(
                    apply_policy_overrides(base, {knob: _OVERLAY_PROBES[knob]})
                ) != tick_config(base)
                declared = POLICY_OVERRIDE_SECURITY[knob]
                if isinstance(declared, SecurityControl):
                    inert = f"{knob} is declared a control but changes nothing the engine reads"
                    assert changed, inert
                elif declared.reason == _NOT_CONSUMED:
                    undeclared = f"{knob} now changes the engine; declare it a SecurityControl"
                    assert not changed, undeclared
            checked += 1
        assert checked == len(POLICY_OVERRIDE_SECURITY), "a knob was left out of the table"
