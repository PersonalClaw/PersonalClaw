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
"""

from __future__ import annotations

import pytest
from test_apps_cannot_relax_security import LOOSENING_WRITES

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
