"""A trigger payload KEY may not override a protected env var (§7/R4 rule e — S129).

🔴 THE DEFECT. `bash_provider` passes the trigger payload as ENV rather than string-templating it
into the command, and its own docstring says why: *"a payload value like `last_result` can hold
arbitrary text — substituting it into the command line would be a shell injection vector."* That
defence works. But `_payload_env` merges **after** `os.environ`, so a payload KEY shadows the real
variable. Driven end to end before a line was written:

    payload {"PATH": "<dir containing a fake `date`>"},  command "date"
      → stdout: HIJACKED

So a payload value could not become code, but a payload key could change *which code runs* — the
same outcome by a different route. Latent rather than live today (every shipped payload key is a
hardcoded literal, verified for `web_poll`/`chain`/`pull_on_view`), which is exactly when it is
cheapest to close: the next kind that derives a payload key from external data would have made it
live without anyone noticing.

**A denylist here, deliberately unlike S126's payload allowlist.** `$variables` are the trigger's
documented user-facing surface (`$now`, `$job_id`, `$last_result`, plus every key a kind
carries), so
an allowlist would have to enumerate them all and would silently drop a new kind's variables. The
dangerous set — loader hijacks, resolution paths, interpreter entry points, harness roots — is
small,
well-known and stable.
"""

from __future__ import annotations

import asyncio
import pathlib
import stat

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.bash_provider import (
    PROTECTED_ENV_NAMES,
    BashActionProvider,
    _payload_env,
)


def _ctx(**payload) -> ActionContext:
    return ActionContext(event="trigger.fired", context="web_watch:w", payload=payload)


# ── the defect, end to end ──


def test_a_payload_PATH_does_NOT_hijack_binary_resolution(tmp_path):
    """🔴 THE DEFECT, pinned by driving a real subprocess. This printed HIJACKED before the fix."""
    fake = tmp_path / "date"
    fake.write_text("#!/bin/sh\necho HIJACKED\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    result = asyncio.run(
        BashActionProvider().execute({"command": "date"}, _ctx(PATH=str(tmp_path)), timeout=20)
    )
    assert "HIJACKED" not in (result.stdout or "")


def test_the_command_still_RUNS_normally(tmp_path):
    """The fix must not break the provider: a real command still resolves and executes."""
    result = asyncio.run(
        BashActionProvider().execute({"command": "echo ok"}, _ctx(item="x"), timeout=20)
    )
    assert (result.stdout or "").strip() == "ok"


# ── the filter ──


@pytest.mark.parametrize("name", sorted(PROTECTED_ENV_NAMES))
def test_EVERY_protected_name_is_filtered(name):
    """A declared list is not a control until something reads it — this program's most-repeated
    lesson, so every entry is asserted rather than trusted."""
    assert name not in _payload_env(_ctx(**{name: "attacker-value"}))


def test_the_protected_set_covers_the_LOADER_hijacks():
    """The classic ones. `LD_PRELOAD`/`DYLD_INSERT_LIBRARIES` inject code into every child process
    without touching the command at all."""
    for name in ("LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"):
        assert name in PROTECTED_ENV_NAMES


def test_the_protected_set_covers_INTERPRETER_entry_points():
    """`BASH_ENV` is sourced by a non-interactive shell before the command; `PYTHONSTARTUP`,
    `NODE_OPTIONS` and `GIT_SSH_COMMAND` are the same shape for their runtimes."""
    for name in ("BASH_ENV", "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "GIT_SSH_COMMAND"):
        assert name in PROTECTED_ENV_NAMES


def test_the_protected_set_covers_the_HARNESS_roots():
    """A payload that could move `PERSONALCLAW_HOME` would point the harness at a store it
    controls — worse than running one wrong binary."""
    for name in ("HOME", "PERSONALCLAW_HOME", "PERSONALCLAW_WORKSPACE"):
        assert name in PROTECTED_ENV_NAMES


# ── ordinary payload variables still work ──


def test_an_ORDINARY_payload_key_still_becomes_an_env_var():
    """`$variables` are the trigger's documented surface. Breaking them to close this would trade a
    latent hole for a live regression."""
    env = _payload_env(_ctx(job_id="j1", last_result="done", new_count="3"))
    assert env["job_id"] == "j1"
    assert env["last_result"] == "done"
    assert env["new_count"] == "3"


def test_EVENT_and_CONTEXT_are_still_provided():
    env = _payload_env(_ctx())
    assert env["EVENT"] == "trigger.fired"
    assert env["CONTEXT"] == "web_watch:w"


def test_a_NON_IDENTIFIER_key_is_still_skipped():
    """Pre-existing behaviour, re-asserted: `bad-key` is not a valid shell identifier."""
    assert "bad-key" not in _payload_env(_ctx(**{"bad-key": "x"}))


def test_a_hostile_payload_VALUE_is_still_just_a_VALUE():
    """The original defence, unbroken. The value reaches the command as data, never as code."""
    evil = '"; rm -rf /tmp/pwned; echo "'
    result = asyncio.run(
        BashActionProvider().execute({"command": 'echo "got=[$item]"'}, _ctx(item=evil), timeout=20)
    )
    assert "rm -rf" in (result.stdout or ""), "the text arrived intact"
    assert not pathlib.Path("/tmp/pwned").exists(), "and was not executed"


# ── the reachability claim, pinned ──


def test_shipped_payload_keys_are_LITERALS_not_derived_from_input():
    """🔴 Why this was latent rather than live, asserted so it stays that way. Every payload key in
    the shipped pollers is a hardcoded literal; a kind that derived a key from fetched content would
    make the hole live, and this test is where that shows up."""
    import inspect

    from personalclaw.triggers import chain, pull_on_view, web_poll

    for module in (web_poll, chain, pull_on_view):
        src = inspect.getsource(module)
        # No dict-comprehension or dynamic key assignment into a payload dict.
        assert "payload[" not in src, f"{module.__name__} assigns a dynamic payload key"


def test_the_filter_is_LOGGED_not_silent():
    """An ignored key must be visible: a user whose `$PATH` variable silently vanished would have no
    way to tell this control from a bug."""
    import inspect

    src = inspect.getsource(_payload_env)
    assert "logger.warning" in src
