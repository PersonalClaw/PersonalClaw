"""The sandbox on a Linux host that withholds unprivileged user namespaces, as it stands (#3918).

GitHub's Ubuntu 24.04 runners, like any Ubuntu 24.04 machine with
``kernel.apparmor_restrict_unprivileged_userns=1``, refuse the namespaces the sandbox's launcher
needs, and have no ``sandbox-exec``. There the sandbox has no OS backend (``detect_backend``
answers ``"none"``), and these tests read what it then does with the host's own answer, not a faked
one:

- a command whose run takes its network away is refused, since the sandbox cannot take it away here;
- a check of the host that does not answer is refused;
- any other command runs unwrapped, and "app-level checks only" is logged once. That is today's
  fail-open posture, pinned so that changing it is a decision made on purpose. Whether such a host
  should refuse instead is the owner's call.

They run where the namespaces are withheld (every suite shard on a Linux runner) and skip where the
host has an OS sandbox. The last test reads :func:`namespace_sandbox.needs_it` on every host.
"""

from __future__ import annotations

import inspect
import logging
import sys

import namespace_sandbox
import pytest

from personalclaw import sandbox
from personalclaw.net import policy

pytestmark = pytest.mark.security


def _withheld() -> bool:
    """Whether this host is one that withholds the namespaces and has no ``sandbox-exec``.

    A probe that does not answer (None) raises, never a skip, as
    :func:`namespace_sandbox.works` does: read as "this host has an OS sandbox", an unanswered
    check would skip the tests below on the very host they are for."""
    if sys.platform != "linux":
        return False
    answers = {"unshare": sandbox._probe_unshare(), "sandbox-exec": sandbox._probe_sandbox_exec()}
    unanswered = [name for name, answer in answers.items() if answer is None]
    if unanswered:
        raise RuntimeError(f"the sandbox's {' and '.join(unanswered)} check did not answer")
    return answers["unshare"] is False and answers["sandbox-exec"] is False


withheld_only = pytest.mark.skipif(
    not _withheld(), reason="this host has an OS sandbox; these read one that withholds it"
)


@pytest.fixture
def fresh_backend():
    """The host asked afresh, and the cached backend gone, before and after."""
    sandbox.reset_backend()
    yield
    sandbox.reset_backend()


@withheld_only
def test_the_host_gives_the_sandbox_no_os_backend(fresh_backend):
    assert sandbox.detect_backend(config_mode="standard") == "none"


@withheld_only
def test_a_command_whose_run_takes_its_network_away_is_refused(fresh_backend, monkeypatch):
    monkeypatch.setattr(
        policy, "no_network_for_commands", lambda session_key=None: "this run's egress is off"
    )
    with pytest.raises(sandbox.SandboxEnforcementUnavailable):
        sandbox.wrap_argv(["/bin/true"], "standard")


@withheld_only
def test_a_check_of_the_host_that_does_not_answer_is_refused(fresh_backend, monkeypatch):
    monkeypatch.setattr(sandbox, "_namespaces_take", lambda *names: None)
    with pytest.raises(sandbox.SandboxCheckUnanswered):
        sandbox.wrap_argv(["/bin/true"], "standard")


@withheld_only
def test_today_any_other_command_runs_unwrapped_and_says_so_only_in_the_log(
    fresh_backend, monkeypatch, caplog
):
    """Today's fail-open posture: no OS sandbox, the command runs, and only the log says so."""
    monkeypatch.setattr(policy, "no_network_for_commands", lambda session_key=None: "")
    monkeypatch.delattr(sandbox._wrapped, "_warned", raising=False)
    with caplog.at_level(logging.WARNING, logger=sandbox.logger.name):
        assert sandbox.wrap_argv(["/bin/true"], "standard") == (["/bin/true"], None)
    assert "No OS-level sandbox available" in caplog.text


def test_a_run_that_requires_the_namespaces_fails_each_test_where_they_are_withheld(monkeypatch):
    """The ``namespace-sandbox`` job sets the variable, so there a withheld sandbox fails each test
    that needs it, by name, never a skip; anywhere else it is a skip, and where the namespaces
    work the test runs as it is."""

    def a_test(home):
        return "ran"

    monkeypatch.setattr(namespace_sandbox.sys, "platform", "linux")
    monkeypatch.setattr(namespace_sandbox, "works", lambda: False)
    monkeypatch.delenv(namespace_sandbox.REQUIRE, raising=False)
    assert namespace_sandbox.needs_it().args == (True,)
    monkeypatch.setenv(namespace_sandbox.REQUIRE, "1")
    required = namespace_sandbox.needs_it()(a_test)
    assert required.__name__ == "a_test" and list(inspect.signature(required).parameters) == [
        "home"
    ]
    with pytest.raises(pytest.fail.Exception, match=namespace_sandbox.REQUIRE):
        required(None)
    monkeypatch.setattr(namespace_sandbox, "works", lambda: True)
    assert namespace_sandbox.needs_it()(a_test) is a_test
    monkeypatch.setattr(namespace_sandbox, "works", lambda: False)
    monkeypatch.setattr(namespace_sandbox.sys, "platform", "darwin")
    assert namespace_sandbox.needs_it().args == (True,)


@pytest.mark.parametrize("unanswered", ["_probe_unshare", "_probe_sandbox_exec"])
def test_a_probe_that_does_not_answer_raises_and_never_skips_these_tests(monkeypatch, unanswered):
    monkeypatch.setattr(sys, "platform", "linux")
    for probe in ("_probe_unshare", "_probe_sandbox_exec"):
        answer = None if probe == unanswered else False
        monkeypatch.setattr(sandbox, probe, lambda answer=answer: answer)
    with pytest.raises(RuntimeError, match="did not answer"):
        _withheld()
    monkeypatch.setattr(sandbox, unanswered, lambda: False)
    assert _withheld() is True
