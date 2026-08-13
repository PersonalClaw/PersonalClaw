"""A gateway secret does NOT reach a hook, cron-script or bash-action child (PHF-4).

🔴 WHAT WAS MEASURED BEFORE THE FIX. A real gateway process carried **121** environment
variables — almost all of them inherited from the launching shell (terminal, toolbox,
agent-CLI and cloud-SDK variables) — and `config/loader.py:4008` deliberately seeds `.env`
credentials into `os.environ` "so spawned children (sandboxed agents, MCP servers,
cron-fired subprocesses) inherit them". Against that population the three spawn sites had:

* **cron scripts** — a denylist of exactly ONE prefix (`PERSONALCLAW_SECRET`). Every other
  variable, credentials included, was readable by a user script via `os.environ`.
* **bash hooks / bash actions** — a name-PATTERN denylist (`_KEEP_NAMES` +
  `_SECRET_NAME_PATTERNS`), which keeps everything it does not recognise. Its own comment
  conceded the false negatives ("`MY_GITHUB_PAT` is kept").

Both are now built by ALLOWLIST (`sandbox.build_child_env`). These tests plant a
credential-shaped variable in the gateway's own environment and drive each site's REAL
child, asserting from the child's own view of `os.environ` that the secret never arrives.

Each site also carries a non-vacuity assertion: the child still receives PATH. A test that
only proves absence would pass just as well against a child that received nothing at all,
or never ran.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest

import personalclaw.schedule_script as ss
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.bash_provider import BashActionProvider
from personalclaw.hooks import HOOK_EVENT_USER_PROMPT_SUBMIT, ScriptHook, run_script_hook
from personalclaw.sandbox import CHILD_ENV_BASE_NAMES, build_child_env, env_name_is_sensitive

#: Two shapes of planted secret, both of which the OLD bash-provider pattern would have
#: caught, and neither of which the OLD cron-script one-prefix denylist would.
_PLANTED = "ACME_CLOUD_API_KEY"
#: A shape NO name-pattern denylist in the tree would have recognised — the false-negative
#: class the allowlist exists to close.
_PLANTED_UNGUESSABLE = "ACME_DEPLOY_PAT"

_SECRET_VALUE = "planted-secret-value-9f3a"

# Each run spawns a fresh interpreter through the sandbox wrapper; under full-suite xdist
# load a spawn that takes 0.3s alone can take tens of seconds of wall time from CPU
# contention. Same headroom the sibling schedule-script suite uses.
_TIMEOUT = 90


@pytest.fixture(autouse=True)
def _plant_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the secrets in the GATEWAY's environment — the position they really occupy."""
    monkeypatch.setenv(_PLANTED, _SECRET_VALUE)
    monkeypatch.setenv(_PLANTED_UNGUESSABLE, _SECRET_VALUE)


# ── site 1: a hook child (hooks.run_script_hook → the bash provider) ──


def test_a_hook_child_cannot_read_a_planted_gateway_secret() -> None:
    """Driven through the real hook dispatcher, not the provider alone."""
    hook = ScriptHook(
        id="phf4-hook",
        name="env-probe",
        event=HOOK_EVENT_USER_PROMPT_SUBMIT,
        provider="bash",
        provider_config={"command": "env"},
        timeout=_TIMEOUT,
        enabled=True,
    )
    result = asyncio.run(run_script_hook(hook, "phf4"))

    assert result.exit_code == 0, result.error or result.stderr
    assert "PATH=" in result.stdout, "the child got no PATH — the spawn, not the filter, is broken"
    assert _SECRET_VALUE not in result.stdout
    assert _PLANTED not in result.stdout
    assert _PLANTED_UNGUESSABLE not in result.stdout
    # The hook contract's own variables still arrive.
    assert "PERSONALCLAW_HOOK_EVENT=" in result.stdout


# ── site 2: a cron-script child (schedule_script.run_script_sandboxed) ──


def _fake_crons(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    crons = tmp_path / "crons"
    crons.mkdir()
    monkeypatch.setattr(ss, "_crons_dir", lambda: crons)
    monkeypatch.setattr(ss, "validate_file_path", lambda p: p)
    return crons


def test_a_cron_script_child_cannot_read_a_planted_gateway_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The script reports its OWN `os.environ`, so this is the child's view, not ours."""
    crons = _fake_crons(monkeypatch, tmp_path)
    (crons / "probe.py").write_text(textwrap.dedent("""
            import os

            def run(ctx):
                return "|".join(
                    [
                        "PATH" if "PATH" in os.environ else "NO_PATH",
                        os.environ.get("ACME_CLOUD_API_KEY", "-"),
                        os.environ.get("ACME_DEPLOY_PAT", "-"),
                    ]
                )
            """))
    r = ss.run_script_sandboxed(f"{crons / 'probe.py'}:run", "phf4-job", "msg", timeout=_TIMEOUT)

    assert r["status"] == "ok", r
    kept, planted, unguessable = r["message"].split("|")
    assert kept == "PATH", "the child got no PATH — the spawn, not the filter, is broken"
    assert planted == "-"
    assert unguessable == "-"


def test_the_cron_secret_channel_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deliberate channel must survive the tightening.

    The internal secret + port reach a cron script through an unlinked-on-read cfg file
    precisely so they are never in its environment. An allowlist that broke that would
    silently disable every script that calls back into the API.
    """
    crons = _fake_crons(monkeypatch, tmp_path)
    (crons / "chan.py").write_text(textwrap.dedent("""
            def run(ctx):
                return ctx.message
            """))
    r = ss.run_script_sandboxed(f"{crons / 'chan.py'}:run", "phf4-job", "carried", timeout=_TIMEOUT)
    assert r == {"status": "ok", "message": "carried"}


# ── site 3: a bash-action child (BashActionProvider.execute) ──


def test_a_bash_action_child_cannot_read_a_planted_gateway_secret() -> None:
    result = asyncio.run(
        BashActionProvider().execute(
            {"command": "env"},
            ActionContext(event="trigger.fired", context="phf4", payload={"item": "x"}),
            timeout=_TIMEOUT,
        )
    )
    assert result.success, result.error or result.stderr
    assert "PATH=" in result.stdout, "the child got no PATH — the spawn, not the filter, is broken"
    assert _SECRET_VALUE not in result.stdout
    assert _PLANTED not in result.stdout
    assert _PLANTED_UNGUESSABLE not in result.stdout
    # A trigger `$variable` still reaches the command.
    assert "item=x" in result.stdout


# ── the builder itself ──


def test_the_base_is_an_allowlist_not_a_copy() -> None:
    env = build_child_env(site="t", source={"PATH": "/bin", "ANYTHING_ELSE": "v"})
    assert env == {"PATH": "/bin"}


def test_the_base_covers_what_a_child_needs_to_run() -> None:
    """The non-outage floor: dropping any of these breaks scripts, not attackers."""
    for name in (
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "TZ",
        # The ceiling shim runs `python -m personalclaw._spawn_exec_shim`; without
        # PYTHONPATH that import can fail and take every spawn with it.
        "PYTHONPATH",
        # A corporate install reaches the network only through these.
        "HTTPS_PROXY",
        "REQUESTS_CA_BUNDLE",
        # Which install a child addresses.
        "PERSONALCLAW_HOME",
        "PERSONALCLAW_WORKSPACE",
        "PERSONALCLAW_PORT",
    ):
        assert name in CHILD_ENV_BASE_NAMES, name


def test_the_base_carries_no_credential_holder() -> None:
    """Nothing in the base may be a credential, by the floor's own test or by name."""
    for name in CHILD_ENV_BASE_NAMES:
        assert not env_name_is_sensitive(name), name
    for name in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "PERSONALCLAW_OWNER_ID", "SSH_AUTH_SOCK"):
        assert name not in CHILD_ENV_BASE_NAMES, name


def test_a_declared_name_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """The escape hatch a script with a legitimate extra need uses."""
    monkeypatch.setattr(
        "personalclaw.sandbox._declared_env_passthrough", lambda site: {"ACME_REGION"}
    )
    env = build_child_env(site="t", source={"PATH": "/bin", "ACME_REGION": "eu", "OTHER": "no"})
    assert env == {"ACME_REGION": "eu", "PATH": "/bin"}


def test_a_declared_name_is_read_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The declaration is OPERATOR config — wired to `sandbox.env_passthrough`, not a stub."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig()
    cfg.sandbox.env_passthrough = ["ACME_REGION", "AWS_SECRET_ACCESS_KEY", "not a name"]
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))

    env = build_child_env(
        site="t",
        source={"PATH": "/bin", "ACME_REGION": "eu", "AWS_SECRET_ACCESS_KEY": "s"},
    )
    # Declared and allowed; declared but refused by the floor; unparseable and ignored.
    assert env == {"ACME_REGION": "eu", "PATH": "/bin"}


def test_the_floor_cannot_be_lowered_by_a_declaration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforced at the BUILD site, so it holds even past the declaration parser.

    The parser already refuses a sensitive declaration with a warning. This patches the
    parser out of the way to prove the floor does not depend on it — a future caller that
    assembled names another way still cannot lower it.
    """
    monkeypatch.setattr(
        "personalclaw.sandbox._declared_env_passthrough",
        lambda site: {"AWS_SECRET_ACCESS_KEY", "GNUPGHOME"},
    )
    env = build_child_env(
        site="t", source={"AWS_SECRET_ACCESS_KEY": "s", "GNUPGHOME": "/g", "PATH": "/bin"}
    )
    assert env == {"PATH": "/bin"}


def test_an_unreadable_config_yields_the_narrower_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-open on config, which here means fail-CLOSED on the environment."""
    from personalclaw.config.loader import AppConfig

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("config.json is corrupt")

    monkeypatch.setattr(AppConfig, "load", classmethod(_boom))
    env = build_child_env(site="t", source={"PATH": "/bin", "ACME_REGION": "eu"})
    assert env == {"PATH": "/bin"}


def test_injected_values_cannot_be_credential_shaped() -> None:
    """The floor applies to what a CALL SITE injects too, not only to inheritance.

    A trigger payload becomes `extra`. `PROTECTED_ENV_NAMES` already stops it shadowing
    PATH or a loader variable; this stops it planting an AWS session that would redirect a
    hook's `aws` call to someone else's account.
    """
    env = build_child_env(
        site="t",
        source={"PATH": "/bin"},
        extra={"EVENT": "e", "AWS_SECRET_ACCESS_KEY": "attacker"},
    )
    assert env == {"EVENT": "e", "PATH": "/bin"}


def test_withheld_names_are_logged_so_a_missing_variable_is_diagnosable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dropped variable a script needed must be findable, never a silent mystery."""
    with caplog.at_level("DEBUG", logger="personalclaw.sandbox"):
        build_child_env(site="cron-script", source={"PATH": "/bin", "ACME_REGION": "eu"})
    assert any(
        "ACME_REGION" in r.getMessage() and "env_passthrough" in r.getMessage()
        for r in caplog.records
    )
