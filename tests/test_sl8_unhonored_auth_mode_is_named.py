"""An auth mode the runtime cannot honor must be NAMED, not silently downgraded (SL-8).

`AuthConfig.from_env()` honors exactly one override — `none` — and returns the
`local_token` default for everything else. `api_key` and `oauth2` are declared on
`AuthMode` and their request-side halves are built (`dashboard/token_auth.py`
validates a Bearer key, `auth/oidc.py` verifies an OIDC JWT), but nothing
populates the per-mode fields, so no configuration can select them. An operator
who sets `PERSONALCLAW_AUTH_MODE=oauth2` believes they enforced IdP SSO and is in
fact on a shared bearer token — and before this atom the runtime said NOTHING.

**This atom changes no admission decision.** The effective `AuthMode` for every
input string, and `effective_bind` for every mode, are byte-identical to the
pre-SL-8 behaviour; the tests below pin that identity so a future "fix" cannot
quietly start enforcing (or refusing) something. What changes is legibility: a
warning log line at startup plus a `personalclaw doctor` row, each naming which
mode was requested, that it was NOT applied, and which mode is in force.

The vacuity floor: an assertion that merely greps for the word "oauth2" in the
output would pass on the pre-SL-8 tree too (the mode name appears in docstrings
and in the mode enum). So every assertion here demands all THREE facts together —
the requested value, an explicit not-applied statement, and the effective mode —
from a real emitted record or real captured stdout.
"""

from __future__ import annotations

import logging
import urllib.error
from unittest.mock import patch

import pytest

from personalclaw.auth.modes import (
    SELECTABLE_MODES,
    UNSELECTABLE_MODES,
    AuthConfig,
    AuthMode,
    classify_auth_mode_request,
    effective_bind,
)

_MODES_LOGGER = "personalclaw.auth.modes"


def _clear_auth_env(monkeypatch) -> None:
    monkeypatch.delenv("PERSONALCLAW_AUTH_MODE", raising=False)
    monkeypatch.delenv("PERSONALCLAW_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)


def _names_all_three(text: str, *, requested: str, effective: str) -> bool:
    """True only when one line carries request + refusal + mode-in-force."""
    lowered = text.lower()
    return (
        requested.lower() in lowered and "not applied" in lowered and effective.lower() in lowered
    )


# ── the signal: an unhonored request is warned about, loudly ──────────────────


@pytest.mark.parametrize("requested", ["oauth2", "api_key"])
def test_declared_but_unselectable_mode_warns(requested, monkeypatch, caplog):
    """REDS on the pre-SL-8 tree: `from_env()` returned LOCAL_TOKEN and emitted nothing."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", requested)
    with caplog.at_level(logging.WARNING, logger=_MODES_LOGGER):
        cfg = AuthConfig.from_env()

    # The admission decision is unchanged — this atom is legibility only.
    assert cfg.mode is AuthMode.LOCAL_TOKEN

    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == _MODES_LOGGER and r.levelno >= logging.WARNING
    ]
    assert warnings, (
        f"PERSONALCLAW_AUTH_MODE={requested} silently downgraded to local_token with no "
        "warning — an operator who asked for IdP SSO must not be left believing they got it"
    )
    assert any(
        _names_all_three(msg, requested=requested, effective="local_token") for msg in warnings
    ), f"warning must name the request, that it was NOT applied, and the mode in force: {warnings}"


def test_unknown_mode_string_warns(monkeypatch, caplog):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "banana")
    with caplog.at_level(logging.WARNING, logger=_MODES_LOGGER):
        cfg = AuthConfig.from_env()

    assert cfg.mode is AuthMode.LOCAL_TOKEN
    warnings = [r.getMessage() for r in caplog.records if r.name == _MODES_LOGGER]
    assert any(
        _names_all_three(msg, requested="banana", effective="local_token") for msg in warnings
    ), f"an unrecognised mode must be named too, not just the declared-but-unwired: {warnings}"


@pytest.mark.parametrize("requested", ["", "none", "local_token", "  NONE  ", "Local_Token"])
def test_an_honored_request_stays_silent(requested, monkeypatch, caplog):
    """Silence is correct when the operator got what they asked for — no new noise."""
    _clear_auth_env(monkeypatch)
    if requested:
        monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", requested)
    with caplog.at_level(logging.WARNING, logger=_MODES_LOGGER):
        AuthConfig.from_env()
    assert not [
        r.getMessage() for r in caplog.records if r.name == _MODES_LOGGER
    ], f"{requested!r} IS honored — warning about it would train operators to ignore the warning"


# ── the classifier: pure description, and it cannot drift from from_env ──────


@pytest.mark.parametrize(
    ("raw", "effective", "unhonored"),
    [
        ("", AuthMode.LOCAL_TOKEN, False),
        ("none", AuthMode.NONE, False),
        ("NONE", AuthMode.NONE, False),
        ("  none  ", AuthMode.NONE, False),
        ("local_token", AuthMode.LOCAL_TOKEN, False),
        ("api_key", AuthMode.LOCAL_TOKEN, True),
        ("oauth2", AuthMode.LOCAL_TOKEN, True),
        ("OAuth2", AuthMode.LOCAL_TOKEN, True),
        ("banana", AuthMode.LOCAL_TOKEN, True),
        ("local-token", AuthMode.LOCAL_TOKEN, True),
    ],
)
def test_classifier_matches_from_env_for_every_input(raw, effective, unhonored, monkeypatch):
    """The classifier's `effective` must equal what `from_env` actually selects."""
    request = classify_auth_mode_request(raw)
    assert request.effective is effective
    assert bool(request.unhonored_reason) is unhonored
    assert bool(request.detail) is unhonored

    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", raw)
    assert AuthConfig.from_env().mode is request.effective, (
        "the classifier and the admission path must never disagree — from_env consumes "
        "the classifier precisely so this identity holds"
    )


def test_every_declared_mode_is_classified_exactly_once():
    """A 5th `AuthMode` added without a wired selector must not slip through.

    An unclassified mode would silently take the `_UNKNOWN_REASON` path and be
    reported as "not a known auth mode" — wrong and confusing, since the enum
    does declare it. Force the author to put it in one bucket or the other.
    """
    declared = {m.value for m in AuthMode}
    classified = set(SELECTABLE_MODES) | set(UNSELECTABLE_MODES)
    assert declared == classified, (
        "every AuthMode must be either selectable or explicitly unselectable; "
        f"unclassified: {sorted(declared - classified)}"
    )
    assert not (set(SELECTABLE_MODES) & set(UNSELECTABLE_MODES)), "a mode cannot be both"
    for value, mode in SELECTABLE_MODES.items():
        assert mode.value == value, f"{value!r} maps to the wrong mode: {mode}"


def test_classifier_reads_the_environment_when_raw_is_omitted(monkeypatch):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", "oauth2")
    assert classify_auth_mode_request().unhonored_reason


def test_detail_names_all_three_facts():
    detail = classify_auth_mode_request("oauth2").detail
    assert _names_all_three(detail, requested="oauth2", effective="local_token"), detail


def test_an_absurdly_long_value_is_truncated_in_the_detail():
    """A pasted blob must not become a 4 KB log line (and never a leaked secret)."""
    detail = classify_auth_mode_request("z" * 4096).detail
    assert detail
    assert len(detail) < 400, f"detail must stay one bounded line, got {len(detail)} chars"
    assert "z" * 4096 not in detail


# ── no admission change: mode + bind identity, pinned against the old rule ───


_PRE_SL8_INPUTS = [
    "",
    "none",
    "NONE",
    " none ",
    "local_token",
    "api_key",
    "oauth2",
    "banana",
    "local-token",
    "oauth2 ",
    "!!!",
]


@pytest.mark.parametrize("raw", _PRE_SL8_INPUTS)
def test_effective_mode_and_bind_are_byte_identical_to_pre_sl8(raw, monkeypatch):
    """The pre-SL-8 rule, restated independently: NONE iff the value is `none`."""
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", raw)
    cfg = AuthConfig.from_env()

    expected = AuthMode.NONE if raw.strip().lower() == "none" else AuthMode.LOCAL_TOKEN
    assert cfg.mode is expected
    # Whole-config identity, not just the mode: no per-mode field may start
    # getting populated under the cover of a legibility change.
    assert cfg == AuthConfig(mode=expected)
    assert effective_bind(cfg) == ("127.0.0.1" if expected is AuthMode.NONE else cfg.bind_host)


# ── the doctor row a user actually sees ──────────────────────────────────────


def _doctor_output(capsys) -> str:
    """Run `_doctor()` with its probes stubbed and a forced local bind."""
    from personalclaw.cli_doctor import _doctor

    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch(
            "subprocess.run",
            return_value=type(
                "R",
                (),
                {
                    "returncode": 0,
                    "stdout": "Python 3.13.14",
                    "stderr": "",
                    "check_returncode": lambda self: None,
                },
            )(),
        ),
        patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no gateway")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    return capsys.readouterr().out


@pytest.mark.parametrize("requested", ["oauth2", "api_key", "banana"])
def test_doctor_prints_a_row_for_an_unhonored_mode(requested, monkeypatch, capsys):
    _clear_auth_env(monkeypatch)
    monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", requested)
    out = _doctor_output(capsys)
    row = [ln for ln in out.splitlines() if "not applied" in ln.lower()]
    assert row, f"`personalclaw doctor` must carry a row for an ignored auth mode:\n{out}"
    assert _names_all_three(
        row[0], requested=requested, effective="local_token"
    ), f"the doctor row must name request, refusal and mode-in-force: {row[0]!r}"


@pytest.mark.parametrize("requested", ["", "none", "local_token"])
def test_doctor_adds_no_row_when_the_request_was_honored(requested, monkeypatch, capsys):
    _clear_auth_env(monkeypatch)
    if requested:
        monkeypatch.setenv("PERSONALCLAW_AUTH_MODE", requested)
    out = _doctor_output(capsys)
    assert "not applied" not in out.lower(), out
