"""Rails for the desktop computer-use TARGET POLICY (`DCU-2`, steps 2 and 4 of the chain).

The atom's done-when, restated as the two things these tests must be able to fail on:
driving a non-allowlisted app refuses, and typing or set-value into a secure/password field
refuses. Both refusals are raised BY the policy — the SEL gate downstream records, it does
not decide — so every assertion here is on a raised
:class:`~personalclaw.computer_use.policy.ComputerUsePolicyRefusal` and the stable code it
carries, never on a log line.

Three deliberate anti-vacuity choices, because a policy suite is the easiest place in a
codebase to write tests that cannot fail:

* the allowlist is monkeypatched on its OWNING module and re-patched mid-test, so a policy
  that cached the list at import time would still look green on the positive case and red
  here. (``require_enabled``'s docstring records the measured version of this: two readers of
  one flag left every refusal test green.)
* NEAR-MISS app names are asserted, not just an obviously-wrong one. A rail that only proves
  ``"Terminal"`` refuses cannot detect a match that is too generous, which is the mistake a
  reasonable implementer actually makes.
* both screens are asserted to have a case they must NOT refuse. A screen that refuses
  everything satisfies every refusal test in this file and is useless.
"""

from __future__ import annotations

import pytest

from personalclaw.computer_use import enable_state, policy

TOOL = "computer_type"

#: An ordinary, obviously-safe destination: the shape every refusal case is a mutation of.
ORDINARY_FIELD = {"role": "AXTextField", "label": "Subject", "value": "Lunch on Tuesday"}


@pytest.fixture(autouse=True)
def _isolated_enable_path(tmp_path, monkeypatch):
    """Point the keystone path at ``tmp_path`` for every test in this module.

    The refusals quote :func:`enable_state.enable_file_path`, which falls back to
    ``config_dir()`` when the env override is unset — and a suite that resolves the REAL home
    to build an error message is one edit away from a suite that writes there. The override
    short-circuits before ``config_dir`` is even imported.
    """
    monkeypatch.setenv(enable_state.ENABLE_PATH_ENV, str(tmp_path / "computer_use.enable.json"))
    return tmp_path / "computer_use.enable.json"


def _allow(monkeypatch, *apps: str) -> None:
    """Install the operator allowlist the policy will read.

    Patched on ``enable_state`` with ``raising=False``: that module OWNS ``allowed_apps`` and
    the policy reads it through its owner rather than a ``from``-imported alias, so this is the
    one place a test has to reach. ``raising=False`` because the sibling atom that adds the
    real function lands separately — this suite pins the CONTRACT
    (``allowed_apps() -> tuple[str, ...]``, empty meaning nothing may be driven), and it keeps
    pinning it once the real implementation is underneath.
    """
    monkeypatch.setattr(enable_state, "allowed_apps", lambda: tuple(apps), raising=False)


def _refusal(callable_, *args, **kwargs) -> policy.ComputerUsePolicyRefusal:
    with pytest.raises(policy.ComputerUsePolicyRefusal) as excinfo:
        callable_(*args, **kwargs)
    return excinfo.value


# ── check_app: the allowlist ───────────────────────────────────────────────────────────


def test_allowlisted_app_returns_cleanly(monkeypatch):
    _allow(monkeypatch, "TextEdit", "Mail")

    assert policy.check_app("TextEdit", tool=TOOL) is None
    assert policy.check_app("Mail", tool=TOOL) is None


def test_non_allowlisted_app_refuses(monkeypatch):
    _allow(monkeypatch, "TextEdit")

    err = _refusal(policy.check_app, "Terminal", tool=TOOL).error

    assert err.code == policy.ERR_APP_NOT_ALLOWED
    assert "Terminal" in err.what
    # The refusal lists what WOULD have been accepted, so a corrected retry is possible
    # without a second round trip through the operator.
    assert err.suggestions == ("TextEdit",)


def test_empty_allowlist_refuses_every_app(monkeypatch):
    """Asserted, not assumed: an empty list is the shipped default and must mean NOTHING."""
    _allow(monkeypatch)

    for app in ("TextEdit", "Mail", "Terminal", "PersonalClaw", "Finder"):
        err = _refusal(policy.check_app, app, tool=TOOL).error
        assert err.code == policy.ERR_APP_NOT_ALLOWED
        assert "EMPTY" in err.what


def test_empty_allowlist_refuses_personalclaw_itself(monkeypatch):
    """No implicit self-allowance. PersonalClaw's own windows carry its approval and settings
    surfaces, so an implicit self entry would be the one grant that raises the agent's own
    permissions. It must be an ordinary operator-listed entry like any other app."""
    _allow(monkeypatch)
    assert _refusal(policy.check_app, "PersonalClaw", tool=TOOL).error.code == (
        policy.ERR_APP_NOT_ALLOWED
    )

    _allow(monkeypatch, "PersonalClaw")
    assert policy.check_app("PersonalClaw", tool=TOOL) is None


@pytest.mark.parametrize(
    "nearby",
    [
        "TextEditor",  # a longer name that CONTAINS the allowed one → substring match
        "extEdi",  # a shorter name CONTAINED BY it → reversed substring match
        "textedit",  # same letters, wrong case → casefolded match
        "TEXTEDIT",
        "Text Edit",
        "TextEdit.app",  # the bundle name, not the application name
        "TextEdit2",
    ],
)
def test_nearby_app_name_refuses(monkeypatch, nearby):
    """A rail proving only the positive case cannot detect a match that is too generous."""
    _allow(monkeypatch, "TextEdit")

    assert _refusal(policy.check_app, nearby, tool=TOOL).error.code == policy.ERR_APP_NOT_ALLOWED


@pytest.mark.parametrize("blank", ["", "   ", None, 0, [], {"name": "TextEdit"}])
def test_unnamed_app_refuses(monkeypatch, blank):
    """An unnamed or non-string target is an unknown one, and unknown means no."""
    _allow(monkeypatch, "TextEdit")

    assert _refusal(policy.check_app, blank, tool=TOOL).error.code == policy.ERR_APP_NOT_ALLOWED


def test_surrounding_whitespace_is_not_a_different_app(monkeypatch):
    """Stripping is normalisation, not widening: ``TextEditor`` still refuses above."""
    _allow(monkeypatch, " TextEdit ")

    assert policy.check_app("TextEdit", tool=TOOL) is None
    assert policy.check_app("  TextEdit\n", tool=TOOL) is None


def test_allowlist_is_read_at_decision_time_not_cached(monkeypatch):
    """The anti-second-reader rail: re-patch mid-test and the answer must change.

    An implementation that snapshotted ``allowed_apps()`` into a module constant, or kept its
    own copy beside the owner's, passes every other test in this file.
    """
    _allow(monkeypatch, "TextEdit")
    assert policy.check_app("TextEdit", tool=TOOL) is None

    _allow(monkeypatch)  # the operator emptied the list
    assert _refusal(policy.check_app, "TextEdit", tool=TOOL).error.code == (
        policy.ERR_APP_NOT_ALLOWED
    )

    _allow(monkeypatch, "TextEdit")  # ...and put it back
    assert policy.check_app("TextEdit", tool=TOOL) is None


# ── check_input_target: secure fields, unknown shapes, sensitive text ──────────────────


def test_ordinary_text_field_passes():
    assert policy.check_input_target(ORDINARY_FIELD, tool=TOOL) is None
    assert policy.check_input_target({"role": "AXTextArea"}, tool=TOOL) is None
    assert (
        policy.check_input_target(
            {"role": "AXTextField", "subrole": "AXSearchField", "label": "Search"}, tool=TOOL
        )
        is None
    )
    # The raw AX spellings screen identically to the mapped ones.
    assert (
        policy.check_input_target({"AXRole": "AXTextField", "AXTitle": "Note"}, tool=TOOL) is None
    )


@pytest.mark.parametrize(
    "target",
    [
        {"role": "AXTextField", "subrole": "AXSecureTextField"},
        {"role": "AXTextField", "AXSubrole": "AXSecureTextField"},
        {"AXRole": "AXTextField", "AXSubrole": "AXSecureTextField", "AXTitle": "Password"},
    ],
)
def test_secure_field_refuses(target):
    """The atom's done-when: typing or set-value into a secure/password field refuses."""
    err = _refusal(policy.check_input_target, target, tool=TOOL).error

    assert err.code == policy.ERR_SECURE_FIELD
    assert "secure text field" in err.what


@pytest.mark.parametrize(
    "label",
    [
        "Password",
        "New password",
        "Passphrase",
        "Passcode",
        "PIN",
        "Secret",
        "API Key",
        "api_key",
        "Access token",
        "CVV",
        "Verification code",
        "Recovery code",
        "Seed phrase",
    ],
)
def test_secret_bearing_label_refuses_without_a_secure_subrole(label):
    """The commonest real password field: a web-view ``AXTextField`` with no secure subrole."""
    target = {"role": "AXTextField", "label": label}

    assert _refusal(policy.check_input_target, target, tool=TOOL).error.code == (
        policy.ERR_SECURE_FIELD
    )


@pytest.mark.parametrize(
    "label",
    [
        "Search",
        "Subject",
        "Message body",
        "Pinned messages",  # 'pin' as a substring must not be a match
        "Shipping address",
        "Spinner value",
        "To",
        "Recipient",
        "Note title",
    ],
)
def test_ordinary_label_is_not_refused(label):
    """A screen that refuses everything is as useless as one that refuses nothing."""
    assert policy.check_input_target({"role": "AXTextField", "label": label}, tool=TOOL) is None


@pytest.mark.parametrize(
    ("shape", "target"),
    [
        ("None", None),
        ("a list", []),
        ("a string", "AXTextField"),
        ("an int", 7),
        ("an empty dict", {}),
        ("no role key at all", {"label": "Subject", "value": "hello"}),
        ("an empty role", {"role": ""}),
        ("a whitespace role", {"role": "   "}),
        ("a non-text role", {"role": "AXButton"}),
        ("a role this build has never seen", {"role": "AXFutureWidget"}),
        ("a non-string role", {"role": 5}),
        ("a role that is a list", {"role": ["AXTextField"]}),
        ("an unrecognised subrole", {"role": "AXTextField", "subrole": "AXFutureSubrole"}),
        ("a non-string subrole", {"role": "AXTextField", "subrole": 7}),
        ("a non-string label", {"role": "AXTextField", "label": 3}),
        ("a non-string value", {"role": "AXTextField", "value": []}),
        # Both spellings present and disagreeing: screening whichever came first would let the
        # caller pick which of its two claims gets enforced.
        ("contradictory role spellings", {"role": "AXTextField", "AXRole": "AXButton"}),
        (
            "contradictory subrole spellings",
            {"role": "AXTextField", "subrole": "", "AXSubrole": "AXSecureTextField"},
        ),
    ],
)
def test_unrecognised_or_malformed_target_refuses(shape, target):
    """Unknown means NO, per shape. A screen that only recognises the shapes it was shown is a
    screen with a hole, so this is parametrized rather than asserted once."""
    err = _refusal(policy.check_input_target, target, tool=TOOL).error

    assert err.code == policy.ERR_SECURE_FIELD, shape
    assert err.what.strip(), shape


@pytest.mark.parametrize(
    "existing",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "sk-ant-api03-" + "a" * 32,
        "ghp_" + "b" * 24,
        "xoxb-1234567890-abcdefghij",
        "api_key=super-secret-value",
        "Authorization: Bearer abcdefghijklmnop.qrstuvwx",
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_field_already_holding_credential_shaped_text_refuses(existing):
    """The 'sensitive text' leg, screened with the repo's ONE definition of a credential-shaped
    string (:func:`personalclaw.security.redact_credentials`) rather than a second vocabulary
    invented here. A string the system redacts on the way OUT is one this tool will not type
    INTO."""
    target = {"role": "AXTextField", "label": "Value", "value": existing}

    err = _refusal(policy.check_input_target, target, tool=TOOL).error

    assert err.code == policy.ERR_SECURE_FIELD
    assert "credential-shaped" in err.what
    # The refusal must not quote the secret it just refused to touch.
    assert existing not in err.render()


@pytest.mark.parametrize(
    "existing",
    [
        "Lunch with Dana on Tuesday",
        "https://example.com/docs/getting-started",
        "The quick brown fox jumps over the lazy dog",
        "42",
        "",
    ],
)
def test_ordinary_existing_text_is_not_refused(existing):
    """The other half of the sensitive-text screen: it must let ordinary prose through."""
    target = {"role": "AXTextField", "label": "Note", "value": existing}

    assert policy.check_input_target(target, tool=TOOL) is None


def test_secure_field_inside_an_allowlisted_app_still_refuses(monkeypatch):
    """The two screens are not redundant: an approved app contains login forms."""
    _allow(monkeypatch, "Safari")
    assert policy.check_app("Safari", tool=TOOL) is None

    target = {"role": "AXTextField", "subrole": "AXSecureTextField", "label": "Password"}
    assert _refusal(policy.check_input_target, target, tool=TOOL).error.code == (
        policy.ERR_SECURE_FIELD
    )


# ── the message contract, and the vacuity floor ────────────────────────────────────────


def _refusals(monkeypatch):
    """One refusal of each kind, for the assertions that apply to both."""
    _allow(monkeypatch, "TextEdit")
    return [
        _refusal(policy.check_app, "Terminal", tool=TOOL).error,
        _refusal(
            policy.check_input_target,
            {"role": "AXTextField", "subrole": "AXSecureTextField"},
            tool=TOOL,
        ).error,
    ]


def test_every_refusal_carries_what_why_fix(monkeypatch):
    for err in _refusals(monkeypatch):
        assert err.what.strip(), err.code
        assert err.why.strip(), err.code
        assert err.fix.strip(), err.code
        # The rendered form is what a model actually reads.
        rendered = err.render()
        for label in ("WHAT:", "WHY:", "FIX:"):
            assert label in rendered, (err.code, label)
        assert TOOL in err.what, err.code


def test_every_fix_names_the_out_of_band_file(monkeypatch, _isolated_enable_path):
    """Substance, not one exact wording: the FIX must point at the operator-owned file (and
    the env var that relocates it), never at a settings screen that does not exist."""
    for err in _refusals(monkeypatch):
        assert str(_isolated_enable_path) in err.fix, err.code
        assert "human" in err.fix.lower(), err.code
        assert "settings screen" not in err.fix.lower(), err.code

    app_err, _ = _refusals(monkeypatch)
    # The app refusal additionally has to say HOW to add an app, or the operator is stuck.
    assert policy.ALLOWLIST_KEY in app_err.fix
    assert enable_state.ENABLE_PATH_ENV in app_err.fix


def test_error_codes_are_distinct_and_non_empty():
    """The vacuity floor: two codes that were equal, or empty, would make every
    ``err.code == ...`` assertion above pass for the wrong reason."""
    codes = (policy.ERR_APP_NOT_ALLOWED, policy.ERR_SECURE_FIELD)

    assert len(set(codes)) == 2
    for code in codes:
        assert code
        assert code == code.upper()
        assert code.startswith("ERR_COMPUTER_USE_")
    # ...and distinct from the keystone's code, which is a different refusal entirely.
    assert enable_state.ERR_DISABLED not in codes


def test_neither_path_produces_a_bogus_code(monkeypatch):
    """No refusal in this module carries a code outside the declared two."""
    for err in _refusals(monkeypatch):
        assert err.code in (policy.ERR_APP_NOT_ALLOWED, policy.ERR_SECURE_FIELD)
        assert err.code != "ERR_COMPUTER_USE_BOGUS"


def test_the_policy_raises_rather_than_returning_a_verdict(monkeypatch):
    """Steps 2 and 4 DECIDE; step 5 only records. A policy that returned a falsy verdict for
    the SEL gate to act on would make an audit sink — correctly fail-open — the enforcement
    point, and this is the rail that says so."""
    _allow(monkeypatch)

    assert issubclass(policy.ComputerUsePolicyRefusal, Exception)
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        policy.check_app("TextEdit", tool=TOOL)
    with pytest.raises(policy.ComputerUsePolicyRefusal):
        policy.check_input_target({"role": "AXButton"}, tool=TOOL)
