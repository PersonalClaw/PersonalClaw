"""A scheduled script runs under the ``tool`` resource ceiling (EXECUTION-ISOLATION EI-3).

``run_script_sandboxed`` already had the OS path sandbox (``wrap_argv``) and, since PHF-4,
an allowlisted child environment. Neither is a resource ceiling: before EI-3 a cron script
could exhaust the gateway's file descriptors or fork-bomb in ways an agent bash command —
which has carried the ``tool`` ceiling since PHF-1 — already could not.

These tests **drive a real cron script** through the real spawn path rather than inspecting a
constructed argv, because the failure mode being guarded against is a ceiling that composes
wrongly with the sandbox wrap and so never reaches the child at all. The script reports its
own ``resource.getrlimit`` and its own ``OSError``, so every number here is the child's view.

Platform honesty: on macOS only **RLIMIT_NOFILE** is enforced. ``RLIMIT_NPROC`` (fork bound)
and ``RLIMIT_AS`` (address space) ship OFF by default — ``NPROC`` is a *per-user* cap that
would break a busy host, and the durable pids/memory bound is PHF-2's Linux cgroup tier, not
an rlimit. ``oom_score_adj`` is Linux-only and silently skipped here. So NOFILE is what is
driven below, and it is the only ceiling this seam enforces on this platform.

Second platform note: on macOS >= 26 ``sandbox-exec`` is refused for third-party callers, so
``detect_backend`` returns ``none`` and the OS wrap is a pass-through. The composition of the
ceiling with a real *exec wrapper* is therefore driven through a surrogate of the same shape
(see the last test) rather than left unexercised.
"""

from __future__ import annotations

import json
import resource
import textwrap
from pathlib import Path

import pytest

import personalclaw.schedule_script as ss
from personalclaw.sandbox import PROFILE_TOOL, spawn_shim_argv

# A cron run spawns a fresh interpreter through the sandbox, twice over (shim then target).
# Under full-suite xdist load that can take tens of seconds of wall time from pure CPU
# contention, so give the same wide headroom the rest of the cron suite uses.
_TIMEOUT = 90

#: The NOFILE soft cap to drive with. Deliberately not any plausible host default, so a
#: child reporting this number can only have got it from our ceiling.
_CEILING = 137

#: How many descriptors the probe script tries to open. Far above ``_CEILING`` and far below
#: this host's inherited soft limit, so the two outcomes are unmistakable.
_ATTEMPTS = 400


def _crons_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, body: str) -> str:
    """Write a cron script under a fake crons dir; return its ``path:func`` spec."""
    crons = tmp_path / "crons"
    crons.mkdir(exist_ok=True)
    monkeypatch.setattr(ss, "_crons_dir", lambda: crons)
    monkeypatch.setattr(ss, "validate_file_path", lambda p: p)
    script = crons / name
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return f"{script}:run"


def _set_sandbox_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **sandbox: int) -> None:
    """Point the live config at a tmp file carrying the given ``sandbox.*`` numbers.

    Goes through the real ``ResourceCeilings.from_config`` path (no patched ceilings object),
    so what is exercised is the config an operator actually edits.
    """
    from personalclaw.config import loader as loader_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"sandbox": sandbox}), encoding="utf-8")
    monkeypatch.setattr(loader_mod, "config_path", lambda: cfg_path)


# ── the ceiling reaches the child ────────────────────────────────────────────


def test_a_cron_script_child_reports_the_configured_nofile_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The child's own ``getrlimit`` equals the operator's ``sandbox.nofile``.

    This is the end-to-end proof that the shim survives composition with the OS-sandbox wrap
    and the PHF-4 environment allowlist: if the shim's ``python -m
    personalclaw._spawn_exec_shim`` import had not survived (PYTHONPATH is allowlisted for
    exactly this reason), the run would come back ``error`` instead of a number.
    """
    parent_soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    assert parent_soft != _CEILING, (
        "this host's own NOFILE soft limit is the number under test, so the assertion below "
        "could pass without any ceiling being applied — pick a different _CEILING"
    )
    _set_sandbox_config(monkeypatch, tmp_path, nofile=_CEILING, max_pids=0, max_rss_mb=0)
    spec = _crons_with(
        monkeypatch,
        tmp_path,
        "limits.py",
        """
        import resource

        def run(ctx):
            return str(resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        """,
    )

    r = ss.run_script_sandboxed(spec, "ei3-job", "", timeout=_TIMEOUT)

    assert r["status"] == "ok", r
    assert r["message"] == str(_CEILING), (
        f"child reported NOFILE soft={r['message']}, expected {_CEILING} "
        f"(host default is {parent_soft}) — the ceiling did not reach the child"
    )


# ── the ceiling CONTAINS a script that tries to exceed it ────────────────────

#: Opens ``/dev/null`` over and over — a pure descriptor drive with no dependency on what
#: the OS-sandbox profile lets the child WRITE, so a failure here can only be the FD ceiling.
_FD_BOMB = """
    import os

    def run(ctx):
        opened = []
        err = ""
        try:
            for _ in range({attempts}):
                opened.append(os.open("/dev/null", os.O_RDONLY))
        except OSError as exc:
            err = type(exc).__name__
        finally:
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass
        return "%d|%s" % (len(opened), err)
    """


def test_a_cron_script_that_exceeds_the_fd_ceiling_is_contained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A script asking for 400 descriptors under a 137 ceiling is stopped at the ceiling."""
    _set_sandbox_config(monkeypatch, tmp_path, nofile=_CEILING, max_pids=0, max_rss_mb=0)
    spec = _crons_with(monkeypatch, tmp_path, "bomb.py", _FD_BOMB.format(attempts=_ATTEMPTS))

    r = ss.run_script_sandboxed(spec, "ei3-job", "", timeout=_TIMEOUT)

    assert r["status"] == "ok", r
    opened, err = r["message"].split("|")
    assert err == "OSError", (
        f"the script opened {opened} of {_ATTEMPTS} descriptors without ever failing — "
        "it was not contained"
    )
    assert (
        int(opened) < _CEILING
    ), f"the script opened {opened} descriptors, at or above the {_CEILING} ceiling"
    # It failed for want of descriptors, not at the first open — a ceiling that admitted
    # nothing would also "contain" the script while breaking every real cron job.
    assert int(opened) > 8, f"only {opened} descriptors were available at all"


def test_the_same_script_is_uncontained_when_the_operator_disables_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``sandbox.nofile = 0`` (cap OFF) lets the identical script open all 400.

    The falsification leg for the test above: same script, same spawn path, same shim (the
    ``tool`` policy still carries the OOM bias so the argv is still wrapped) — only the FD cap
    removed. So the containment measured above is attributable to the ceiling and not to
    tmpfile limits, the OS sandbox, or the host.
    """
    _set_sandbox_config(monkeypatch, tmp_path, nofile=0, max_pids=0, max_rss_mb=0)
    spec = _crons_with(monkeypatch, tmp_path, "bomb.py", _FD_BOMB.format(attempts=_ATTEMPTS))

    r = ss.run_script_sandboxed(spec, "ei3-job", "", timeout=_TIMEOUT)

    assert r["status"] == "ok", r
    opened, err = r["message"].split("|")
    assert (int(opened), err) == (
        _ATTEMPTS,
        "",
    ), f"with the cap off the script still stopped at {opened} descriptors ({err or 'no error'})"


# ── composition order ────────────────────────────────────────────────────────


def test_the_ceiling_shim_wraps_the_sandbox_and_not_the_reverse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shim is the OUTERMOST layer of the argv the cron runner spawns.

    The drives above cannot see this on a host whose sandbox backend is ``none`` (the two
    orders are then identical), but the order is load-bearing: inside the wrap, the shim's own
    interpreter import has to survive the seatbelt/namespace profile. Assert the shape at the
    real call site by capturing the argv ``subprocess.run`` receives.
    """
    _set_sandbox_config(monkeypatch, tmp_path, nofile=_CEILING, max_pids=0, max_rss_mb=0)
    spec = _crons_with(
        monkeypatch,
        tmp_path,
        "noop.py",
        """
        def run(ctx):
            return "ok"
        """,
    )
    seen: list[list[str]] = []
    real_run = ss.subprocess.run

    def _capture(argv, **kwargs):
        seen.append(list(argv))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(ss.subprocess, "run", _capture)
    r = ss.run_script_sandboxed(spec, "ei3-job", "", timeout=_TIMEOUT)
    assert r["status"] == "ok", r

    assert len(seen) == 1, seen
    argv = seen[0]
    # The shim prefix leads: <python> -m personalclaw._spawn_exec_shim <policy> -- <rest>
    assert argv[1:3] == ["-m", "personalclaw._spawn_exec_shim"], argv[:4]
    sep = argv.index("--")
    inner = argv[sep + 1 :]
    assert inner, "nothing after the shim's '--' separator"
    # ...and whatever the OS-sandbox wrap produced sits INSIDE it, never around it.
    assert (
        "personalclaw._spawn_exec_shim" not in inner[1:]
    ), f"the shim appears inside the sandbox wrap, not outside it: {argv}"
    # Immediately inside the shim sits whatever wrap_argv produced: the seatbelt/namespace
    # wrapper on a host that has one, and the bare launcher on a host that has none.
    assert inner[0] in ("env", "unshare", "sandbox-exec", "python3"), (
        f"expected the OS-sandbox wrapper (or the bare launcher) inside the shim, "
        f"got {inner[0]!r}"
    )


def test_the_ceiling_survives_an_intervening_os_sandbox_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ceiling set outside an exec-wrapper still reaches the script inside it.

    This is the property that makes the outside ordering correct — rlimits inherit through
    ``exec``, so the shim does not need to run inside the sandbox to bound what the sandboxed
    child consumes. It cannot be driven through the real backend on every host (macOS >= 26
    denies ``sandbox_apply`` for third-party callers, so ``detect_backend`` returns ``none``
    there and both orderings produce the same argv), so stand in a wrapper with the same
    shape the real macOS wrap has — ``env -u NAME <argv>``, the literal prefix
    ``sandbox_exec_argv`` builds — and check the ceiling still lands on the far side of it.
    """
    _set_sandbox_config(monkeypatch, tmp_path, nofile=_CEILING, max_pids=0, max_rss_mb=0)
    monkeypatch.setattr(
        ss,
        "wrap_argv",
        lambda argv, mode="auto": (["/usr/bin/env", "-u", "PC_EI3_ABSENT", *argv], None),
    )
    spec = _crons_with(
        monkeypatch,
        tmp_path,
        "through_wrapper.py",
        """
        import resource

        def run(ctx):
            return str(resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        """,
    )

    r = ss.run_script_sandboxed(spec, "ei3-job", "", timeout=_TIMEOUT)

    assert r["status"] == "ok", r
    assert r["message"] == str(_CEILING), (
        f"through an intervening exec wrapper the child reported {r['message']}, "
        f"expected {_CEILING}"
    )


def test_the_tool_policy_is_never_empty_so_the_site_cannot_silently_unshim() -> None:
    """Even with every numeric cap disabled, the ``tool`` profile still wraps the argv.

    The fail-loud property the call-site comment claims: this seam has no configuration under
    which it degrades to a bare spawn that merely *looks* ceilinged.
    """
    from personalclaw.sandbox import ResourceCeilings

    off = ResourceCeilings(nofile=0, max_pids=0, max_rss_mb=0)
    wrapped = spawn_shim_argv(["/bin/true"], PROFILE_TOOL, off)
    assert wrapped[1:3] == ["-m", "personalclaw._spawn_exec_shim"], wrapped
