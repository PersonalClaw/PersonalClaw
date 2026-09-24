"""A self-sandboxing ACP runtime opts out of the host wrap, and a dying child
explains itself.

Two coupled defects, both found on the standing rig where ``acp:kiro-cli`` sat at
``handshake failed: ACP stdout EOF`` while ``acp:claude-code`` and ``acp:codex``
were warm:

* **The cause.** kiro-cli initializes its OWN OS sandbox around the child it execs,
  and that inner ``sandbox_apply`` is refused inside the host's generated profile.
  Under the default ``sandbox-exec`` wrap it therefore died during startup ("sandbox
  initialization failed: Operation not permitted") having written nothing to stdout.
  A bundle had no way to say so, because ``sandbox_mode`` was reachable only from a
  call site.
* **The symptom.** The child wrote its whole cause to *stderr*, the transport kept
  a redacted tail of it for exactly this moment, and nothing had ever called that
  accessor — so the provider card showed a protocol symptom that named no cause.

These tests are vendor-neutral on purpose: they use a fake CLI that reproduces the
failure shape, so they hold on a machine with no kiro-cli (i.e. in CI).
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from personalclaw.acp_bundles._register import register_acp_cli_entry
from personalclaw.llm.acp_agent import AcpAgentProvider
from personalclaw.llm.registry import get_default_registry, reset_default_registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Every test registers into a fresh registry and leaves none behind."""
    reset_default_registry()
    yield
    reset_default_registry()


def _make_exec(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_cli(monkeypatch, tmp_path: Path, name: str, body: str | None = None) -> Path:
    """A fake executable on an otherwise-empty PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = _make_exec(bindir / name, body) if body else _make_exec(bindir / name)
    monkeypatch.setenv("PATH", str(bindir))
    return exe


# ── the declaration channel ────────────────────────────────────────────────────


def test_self_sandboxing_entry_declares_sandbox_off(monkeypatch, tmp_path):
    """A bundle that declares ``self_sandboxing`` gets ``sandbox_mode="off"`` on its
    entry. The bundle states a FACT about its binary; the core picks the policy —
    same split as ``requires_executable``."""
    fake = _fake_cli(monkeypatch, tmp_path, "selfbox-cli")
    register_acp_cli_entry(
        cli="selfbox-cli",
        dialect="default",
        command=[str(fake), "acp"],
        self_sandboxing=True,
    )
    entry = get_default_registry().get_entry("acp:selfbox-cli")
    assert entry.options["sandbox_mode"] == "off"


def test_default_runtime_keeps_the_host_wrap(monkeypatch, tmp_path):
    """The negative control that makes the test above mean something: a runtime that
    declares nothing carries NO ``sandbox_mode``, so the host wrap still applies.
    Without this, ``self_sandboxing=True`` could be a no-op and the assertion above
    would pass on a key that was always there."""
    fake = _fake_cli(monkeypatch, tmp_path, "plain-cli")
    register_acp_cli_entry(cli="plain-cli", dialect="default", command=[str(fake), "acp"])
    entry = get_default_registry().get_entry("acp:plain-cli")
    assert "sandbox_mode" not in entry.options


def test_declaration_reaches_the_spawn_unwrapped(monkeypatch, tmp_path):
    """End-to-end: the declared mode must survive the factory AND actually suppress
    the wrap. Asserting only on the entry option would not prove the spawn changed.

    ``wrap_argv(mode="off")`` is the seam ``AcpProcess.spawn`` calls through the
    sandbox provider, so an identical argv is the measurement that the child execs
    directly rather than under ``sandbox-exec``.
    """
    from personalclaw.llm.acp_agent import _factory
    from personalclaw.sandbox import reset_backend, wrap_argv

    fake = _fake_cli(monkeypatch, tmp_path, "selfbox-cli")
    argv = [str(fake), "acp"]
    register_acp_cli_entry(cli="selfbox-cli", dialect="default", command=argv, self_sandboxing=True)
    entry = get_default_registry().get_entry("acp:selfbox-cli")

    provider = _factory(entry=entry)
    assert provider._sandbox_mode == "off"
    assert provider.client._sandbox_mode == "off"

    reset_backend()
    wrapped, cleanup = wrap_argv(list(argv), mode=provider._sandbox_mode)
    reset_backend()
    assert wrapped == argv, "a self-sandboxing runtime must exec directly, not under a wrap"
    assert cleanup is None  # nothing to clean up because nothing was wrapped


def _spy_transport_mode(monkeypatch) -> list[str]:
    """Record the ``sandbox_mode`` every spawned transport is constructed with.

    ``AcpProcess`` is where the wrap is actually composed, so this is the narrowest
    place to prove a declared mode *arrived* — asserting on the entry option only
    proves it was written down.
    """
    from personalclaw.acp.transport import AcpProcess

    seen: list[str] = []
    orig_init = AcpProcess.__init__

    def spy_init(self, *args, **kwargs):
        seen.append(str(kwargs.get("sandbox_mode", "auto")))
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(AcpProcess, "__init__", spy_init)
    return seen


@pytest.mark.parametrize(
    ("self_sandboxing", "expected"), [(True, "off"), (False, "auto")], ids=["declared", "control"]
)
def test_the_readiness_probe_honours_the_declared_sandbox_mode(
    monkeypatch, tmp_path, self_sandboxing, expected
):
    """The probe spawns the real CLI, so it must read the mode off the entry.

    It did not: ``probe_readiness`` built the provider without ``sandbox_mode`` and
    got the ``auto`` default, so a self-sandboxing runtime was wrapped anyway and
    the provider card reported it dead — while the same runtime started fine through
    ``_factory``, which was the only reader that honoured the declaration. The
    ``control`` case is what makes ``"off"`` evidence rather than a constant.
    """
    fake = _fake_cli(monkeypatch, tmp_path, "selfbox-cli", _DIES_ON_STDERR)
    register_acp_cli_entry(
        cli="selfbox-cli",
        dialect="default",
        command=[str(fake)],
        self_sandboxing=self_sandboxing,
    )
    options = dict(get_default_registry().get_entry("acp:selfbox-cli").options)
    options["probe_timeout_secs"] = 20

    seen = _spy_transport_mode(monkeypatch)
    asyncio.run(AcpAgentProvider.probe_readiness(options))

    assert seen, "the probe never spawned a transport — nothing was measured"
    assert seen[0] == expected


def test_agent_discovery_honours_the_declared_sandbox_mode(monkeypatch, tmp_path):
    """Discovery is the second reader-less path: it spawns the CLI through
    ``AcpConnection.spawn`` and also dropped the declared mode, so a self-sandboxing
    runtime would discover zero agents even once its probe went green."""
    fake = _fake_cli(monkeypatch, tmp_path, "selfbox-cli", _DIES_ON_STDERR)
    register_acp_cli_entry(
        cli="selfbox-cli", dialect="default", command=[str(fake)], self_sandboxing=True
    )
    options = dict(get_default_registry().get_entry("acp:selfbox-cli").options)
    options["probe_timeout_secs"] = 20
    options["runtime_id"] = "acp:selfbox-cli"

    seen = _spy_transport_mode(monkeypatch)
    asyncio.run(AcpAgentProvider.discover_agents(options))

    assert seen, "discovery never spawned a transport — nothing was measured"
    assert seen[0] == "off"


def test_every_options_spawn_path_reads_the_mode_through_one_helper():
    """Guard against a FOURTH reader drifting. The defect was three call sites each
    deciding for itself; ``options.get("sandbox_mode")`` must therefore appear exactly
    once in the module — inside :func:`options_sandbox_mode`."""
    from pathlib import Path as _Path

    import personalclaw.llm.acp_agent as mod

    source = _Path(mod.__file__).read_text()
    assert source.count('options.get("sandbox_mode")') == 1
    assert source.count("options_sandbox_mode(options)") == 3  # probe, discovery, factory


def test_a_plain_runtime_is_still_wrapped_when_a_backend_exists():
    """Paired control for the assertion above: on a host that HAS a sandbox backend,
    the default mode does change the argv. This is what makes ``wrapped == argv``
    above evidence of the opt-out rather than evidence of a sandbox-less host.

    Deliberately does NOT use ``_fake_cli``: that helper replaces ``PATH`` with the
    tmp bindir, which hides ``sandbox-exec`` from ``detect_backend``'s own probe and
    turns this control into an unconditional skip — i.e. into no control at all.
    """
    from personalclaw.sandbox import detect_backend, reset_backend, wrap_argv

    argv = ["/bin/echo", "hi"]
    reset_backend()
    backend = detect_backend(config_mode="auto")
    if backend == "none":
        reset_backend()
        pytest.skip("host has no OS sandbox backend — nothing to wrap")
    wrapped, _cleanup = wrap_argv(list(argv), mode="auto")
    reset_backend()
    assert wrapped != argv
    assert argv[0] in wrapped  # the real target is still in there, just wrapped


# ── the legibility fix ─────────────────────────────────────────────────────────

# Writes its cause to stderr and dies without speaking ACP — the shape of every
# startup death, kiro's nested-sandbox EPERM included.
_DIES_ON_STDERR = """#!/bin/sh
echo "sandbox initialization failed: Operation not permitted" >&2
echo "Error: Failed to spawn child process" >&2
exit 1
"""


def test_handshake_error_quotes_the_child_stderr(monkeypatch, tmp_path):
    """The failure a user reads must name the cause. The child's stderr is the only
    place it exists, and the transport's tail of it was previously dropped on the
    floor — leaving a bare ``handshake failed: ACP process pipe broken: Connection
    lost`` (this fake dies fast enough that ``initialize``'s write to its stdin is
    what first observes the death, not a stdout read)."""
    fake = _fake_cli(monkeypatch, tmp_path, "dying-cli", _DIES_ON_STDERR)
    options = {"command": [str(fake)], "dialect": "default", "probe_timeout_secs": 20}

    status = asyncio.run(AcpAgentProvider.probe_readiness(options))

    assert status.ready is False
    assert status.state == "error"
    assert "handshake failed" in status.detail
    assert "sandbox initialization failed: Operation not permitted" in status.detail


def test_silent_death_still_reports_the_protocol_symptom(monkeypatch, tmp_path):
    """A child that says nothing at all leaves the bare protocol symptom and appends
    no empty 'agent stderr:' clause — the diagnostic is additive, never noise."""
    fake = _fake_cli(monkeypatch, tmp_path, "silent-cli", "#!/bin/sh\nexit 1\n")
    options = {"command": [str(fake)], "dialect": "default", "probe_timeout_secs": 20}

    status = asyncio.run(AcpAgentProvider.probe_readiness(options))

    assert status.state == "error"
    assert "handshake failed" in status.detail
    assert "agent stderr" not in status.detail


def test_stderr_tail_is_read_before_teardown_clears_it(monkeypatch, tmp_path):
    """Ordering regression. ``AcpProcess.teardown()`` clears the stderr deque, so a
    tail captured AFTER the probe's ``provider.shutdown()`` is always empty. This
    pins the capture ahead of the shutdown by proving a non-empty tail survives into
    the reported detail even though shutdown ran."""
    from personalclaw.acp.transport import AcpProcess

    fake = _fake_cli(monkeypatch, tmp_path, "dying-cli", _DIES_ON_STDERR)
    options = {"command": [str(fake)], "dialect": "default", "probe_timeout_secs": 20}

    torn_down = {"hit": False}
    orig_teardown = AcpProcess.teardown

    def spy_teardown(self):
        torn_down["hit"] = True
        return orig_teardown(self)

    monkeypatch.setattr(AcpProcess, "teardown", spy_teardown)
    status = asyncio.run(AcpAgentProvider.probe_readiness(options))

    assert torn_down["hit"] is True, "teardown must have run — otherwise nothing is pinned"
    assert "Operation not permitted" in status.detail


def test_stderr_tail_passthrough_is_redacted(monkeypatch, tmp_path):
    """The tail reaches a UI surface, so it goes through the transport's redaction
    rather than around it — a dying CLI can echo a token in its error."""
    from personalclaw.acp.client import AcpClient
    from personalclaw.acp.dialect import get_dialect

    client = AcpClient(
        work_dir=tmp_path,
        command=["/bin/true"],
        dialect=get_dialect("default"),
    )
    client._stderr_lines.append("failed with key sk-ant-api03-DEADBEEFDEADBEEFDEADBEEFDEADBEEF")
    tail = client.stderr_tail()
    assert "failed with key" in tail
    assert "DEADBEEFDEADBEEFDEADBEEFDEADBEEF" not in tail


def test_stderr_tail_is_empty_when_nothing_was_written(tmp_path):
    """No stderr → empty string, not a crash and not a stray separator."""
    from personalclaw.acp.client import AcpClient
    from personalclaw.acp.dialect import get_dialect

    client = AcpClient(
        work_dir=tmp_path,
        command=["/bin/true"],
        dialect=get_dialect("default"),
    )
    assert client.stderr_tail() == ""


def test_the_host_profile_forbids_a_nested_sandbox(tmp_path):
    """The mechanism itself, measured — no vendor binary required.

    A process that applies its own sandbox cannot do so inside the host's generated
    profile: ``sandbox_apply`` returns ``Operation not permitted`` and the child dies
    with an empty stdout, which is all the ACP reader can see (hence ``stdout EOF``).

    The narrower claim matters. A normal macOS host does NOT refuse nesting — nesting a
    bare ``(allow default)`` profile inside another succeeds, and this test asserts
    that as its own control. It is the host profile's ``deny`` rules that make the
    inner ``sandbox_apply`` fail, which is why the fix is an opt-out for runtimes
    that sandbox themselves rather than a tweak to the profile.

    That control is also a *capability precondition*, not merely a nicety: "not in general"
    is a claim about the host, and it is FALSE on some images. State the narrow fact, because
    the obvious wrong reading costs a whole CI cycle: GitHub's ``macos-14`` runner does NOT
    deny ``sandbox_apply`` outright. It PERMITS a single-level application — which is exactly
    why ``detect_backend``'s own probe (one profile, one child) passes there and the skip
    above does not fire — and refuses only a NESTED one, with rc 71. So the precondition has
    to probe *nesting*; a single-level probe would answer "capable" and the leg would stay
    red. On such a host this test's attribution cannot be established at all, so it skips on
    the measured refusal rather than reporting a pass it did not earn. See the branch below.
    """
    import subprocess

    from personalclaw.sandbox import detect_backend, reset_backend, wrap_argv

    reset_backend()
    backend = detect_backend(config_mode="auto")
    if backend != "sandbox-exec":
        reset_backend()
        pytest.skip(f"needs the macOS sandbox-exec backend (got {backend!r})")

    permissive = tmp_path / "permissive.sb"
    permissive.write_text("(version 1)\n(allow default)\n")
    # The inner command IS a sandbox application — a stand-in for any CLI that
    # sandboxes the child it execs.
    inner = ["sandbox-exec", "-f", str(permissive), "/bin/echo", "started"]

    # Control: nesting a permissive profile inside a permissive profile is allowed,
    # so a failure below is attributable to the host profile, not to nesting per se.
    control = subprocess.run(
        ["sandbox-exec", "-f", str(permissive), *inner],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if control.returncode != 0 and "sandbox_apply: Operation not permitted" in control.stderr:
        # A MEASURED capability precondition, narrower than the backend check above and
        # deliberately not a platform/image string: `sandbox-exec` being present (and
        # applying ONE profile, which `detect_backend`'s own probe just proved) does not
        # imply a NESTED `sandbox_apply` is permitted. GitHub's `macos-14` image permits the
        # single level and refuses only the nested one — it does NOT deny `sandbox_apply`
        # outright, which is why this has to probe nesting rather than availability, and why
        # `detect_backend` above answers "capable" there. On such a host the claim does not exist
        # to be measured: the claim is the NARROW one — that the host profile's *deny rules*
        # are what refuse the inner `sandbox_apply` — and a blanket refusal of nesting is
        # indistinguishable from a profile-attributable one. The assertions below would
        # still pass there, for the wrong reason, which is exactly what must not be
        # reported as this test passing.
        #
        # Scope: fires only on the observed rc + `sandbox_apply` refusal, both quoted into
        # the skip so CI names the cause rather than a bare "skipped". Any OTHER control
        # failure falls through to the hard assertion below and still reds.
        reset_backend()
        pytest.skip(
            "host PERMITS a single-level sandbox_apply (the backend probe above passed) but "
            "REFUSES a nested one, so a nesting-specific refusal is indistinguishable from a "
            "blanket one and this test's host-profile attribution is unmeasurable here: "
            f"rc={control.returncode} stderr={control.stderr.strip()!r}"
        )
    assert control.returncode == 0, f"nesting is not refused per se: {control.stderr}"
    assert "started" in control.stdout

    wrapped, cleanup = wrap_argv(list(inner), mode="auto")
    reset_backend()
    try:
        under_host = subprocess.run(
            wrapped, capture_output=True, text=True, env={**os.environ}, timeout=30
        )
    finally:
        if cleanup:
            try:
                os.unlink(cleanup)
            except OSError:
                pass

    assert under_host.returncode != 0
    assert "Operation not permitted" in under_host.stderr
    # Nothing on stdout is the whole reason the host reported only "ACP stdout EOF".
    assert under_host.stdout == ""
