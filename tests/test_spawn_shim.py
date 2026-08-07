"""Post-exec resource-ceiling shim + profiles + config round-trip (PLATFORM-HARDENING-FLOORS §1).

Covers the three load-bearing properties of the PHF-1 mechanism:

* the shim is a **pure-stdlib leaf** — importing it drags in NO other ``personalclaw``
  submodule, so it runs in a bare child that has never touched the rest of the package;
* the shim **actually lowers the child's ceiling** — a child under it reports the reduced
  ``RLIMIT_NOFILE`` via ``resource.getrlimit`` (the ``ulimit -n`` the done-when names);
* the four **profiles** map to the right policy — ``tool`` caps NOFILE + biases OOM,
  ``session_host`` RAISES NOFILE to the inherited hard limit with no bias (the EMFILE fix
  for ACP hosts multiplexing many MCP pipes), ``build`` raises NOFILE and keeps the bias,
  ``none`` produces no policy and leaves argv unwrapped.

Plus the ``sandbox.*`` config four-point round-trip.

All child spawns target ``python -c`` / harmless binaries and never touch the real home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from personalclaw import _spawn_exec_shim as shim
from personalclaw.sandbox import (
    PROFILE_BUILD,
    PROFILE_NONE,
    PROFILE_SESSION_HOST,
    PROFILE_TOOL,
    ResourceCeilings,
    spawn_shim_argv,
)

_SRC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")


# ── the shim is a pure-stdlib leaf ────────────────────────────────────────────


def test_shim_imports_no_other_core_module():
    """Importing the shim in a fresh interpreter loads NO ``personalclaw`` submodule
    other than the package root and the shim itself — the property that lets it run in a
    bare exec'd child."""
    code = (
        "import sys, personalclaw._spawn_exec_shim as s;"
        "extra=sorted(m for m in sys.modules if m.startswith('personalclaw.') "
        "and m != 'personalclaw._spawn_exec_shim');"
        "print(repr(extra))"
    )
    env = {**os.environ, "PYTHONPATH": _SRC}
    out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True).strip()
    assert out == "[]", f"shim import pulled in extra core modules: {out}"


def test_shim_source_imports_only_stdlib():
    """The shim source references no ``personalclaw.*`` import (belt-and-braces AST check)."""
    import ast

    src = open(shim.__file__, encoding="utf-8").read()
    bad: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.startswith("personalclaw")]
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("personalclaw"):
            bad.append(node.module or "")
    assert not bad, f"shim imports core modules: {bad}"


# ── the shim actually lowers the child's ceiling ──────────────────────────────


@pytest.mark.skipif(shim._resource is None, reason="resource module unavailable (Windows)")
def test_shim_child_reports_lowered_nofile():
    """A child launched through the shim reports the reduced NOFILE soft cap — the
    ``ulimit -n`` the done-when calls for."""
    policy = json.dumps({"limits": {"RLIMIT_NOFILE": [128, "hard"]}, "oom_score_adj": None})
    child = "import resource; print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])"
    env = {**os.environ, "PYTHONPATH": _SRC}
    out = subprocess.check_output(
        [sys.executable, "-m", shim.__name__, policy, "--", sys.executable, "-c", child],
        env=env,
        text=True,
    ).strip()
    assert int(out) == 128


@pytest.mark.skipif(shim._resource is None, reason="resource module unavailable (Windows)")
def test_shim_hard_sentinel_resolves_to_inherited_hard_limit():
    """The ``"hard"`` sentinel raises the soft cap to the inherited hard limit (the
    session_host mechanism), never above it."""
    import resource

    _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    policy = json.dumps({"limits": {"RLIMIT_NOFILE": ["hard", "hard"]}, "oom_score_adj": None})
    child = "import resource; print(resource.getrlimit(resource.RLIMIT_NOFILE)[0])"
    env = {**os.environ, "PYTHONPATH": _SRC}
    out = subprocess.check_output(
        [sys.executable, "-m", shim.__name__, policy, "--", sys.executable, "-c", child],
        env=env,
        text=True,
    ).strip()
    assert int(out) == hard


def test_shim_execs_target_with_no_limits_when_resource_absent(monkeypatch):
    """With ``resource`` unavailable (Windows degradation), the shim applies no limits and
    still execs the target — it never crashes the child."""
    monkeypatch.setattr(shim, "_resource", None)
    # _apply_limits is a no-op; the child still runs. Prove via a real exec of `true`-like.
    env = {**os.environ, "PYTHONPATH": _SRC}
    policy = json.dumps({"limits": {"RLIMIT_NOFILE": [64, "hard"]}, "oom_score_adj": 1000})
    rc = subprocess.call(
        [sys.executable, "-m", shim.__name__, policy, "--", sys.executable, "-c", "pass"],
        env=env,
    )
    assert rc == 0


def test_shim_malformed_argv_exits_nonzero():
    """A missing ``--`` separator or empty target is the one hard failure — there is
    nothing to exec, so the shim exits non-zero with a diagnostic."""
    with pytest.raises(SystemExit):
        shim._split_argv(["{}"])  # no '--'
    with pytest.raises(SystemExit):
        shim._split_argv(["{}", "--"])  # nothing after '--'
    with pytest.raises(SystemExit):
        shim._split_argv(["--", "echo"])  # nothing before '--'


# ── the four profiles ─────────────────────────────────────────────────────────


def test_tool_profile_caps_nofile_and_biases_oom():
    cel = ResourceCeilings(nofile=1024, max_pids=256, max_rss_mb=0)
    p = cel.policy(PROFILE_TOOL)
    assert p["limits"]["RLIMIT_NOFILE"] == [1024, "hard"]
    assert p["limits"]["RLIMIT_NPROC"] == [256, 256]
    assert p["oom_score_adj"] == ResourceCeilings.OOM_BIAS


def test_session_host_raises_nofile_and_has_no_oom_bias():
    """The EMFILE-regression guard: session_host raises NOFILE to the inherited hard limit
    (never clamps it below the tool cap a many-pipe host needs) and carries NO OOM bias."""
    cel = ResourceCeilings(nofile=1024, max_pids=256, max_rss_mb=0)
    p = cel.policy(PROFILE_SESSION_HOST)
    assert p["limits"]["RLIMIT_NOFILE"] == ["hard", "hard"]  # raised, not the 1024 tool cap
    assert p["oom_score_adj"] is None  # a trusted host must not be the preferred kill target
    # NPROC still bounds fork bombs even on a host.
    assert p["limits"]["RLIMIT_NPROC"] == [256, 256]


def test_build_profile_raises_nofile_but_keeps_bias():
    cel = ResourceCeilings(nofile=1024, max_pids=256, max_rss_mb=0)
    p = cel.policy(PROFILE_BUILD)
    assert p["limits"]["RLIMIT_NOFILE"] == ["hard", "hard"]
    assert p["oom_score_adj"] == ResourceCeilings.OOM_BIAS


def test_none_profile_has_no_policy_and_leaves_argv_unwrapped():
    cel = ResourceCeilings(nofile=1024, max_pids=256, max_rss_mb=0)
    assert cel.policy(PROFILE_NONE) == {}
    argv = ["bash", "-l"]
    assert spawn_shim_argv(argv, PROFILE_NONE, ceilings=cel) == argv


def test_unknown_profile_is_fail_open_no_ceiling():
    """A profile typo must never BLOCK a spawn — it degrades to no ceiling, not a crash."""
    cel = ResourceCeilings(nofile=1024)
    assert cel.policy("bogus") == {}
    argv = ["echo", "hi"]
    assert spawn_shim_argv(argv, "bogus", ceilings=cel) == argv


def test_max_rss_translates_to_rlimit_as_bytes():
    cel = ResourceCeilings(nofile=0, max_pids=0, max_rss_mb=512)
    p = cel.policy(PROFILE_TOOL)
    assert p["limits"]["RLIMIT_AS"] == [512 * 1024 * 1024, 512 * 1024 * 1024]
    assert "RLIMIT_NOFILE" not in p["limits"]  # nofile=0 disables the cap


def test_spawn_shim_argv_wraps_with_shim_module():
    cel = ResourceCeilings(nofile=1024, max_pids=64)
    wrapped = spawn_shim_argv(["bash", "-lc", "echo hi"], PROFILE_TOOL, ceilings=cel)
    assert wrapped[0] == sys.executable
    assert wrapped[1:3] == ["-m", "personalclaw._spawn_exec_shim"]
    assert "--" in wrapped
    sep = wrapped.index("--")
    assert wrapped[sep + 1 :] == ["bash", "-lc", "echo hi"]
    # The policy is valid JSON carrying the tool ceiling.
    policy = json.loads(wrapped[3])
    assert policy["limits"]["RLIMIT_NOFILE"] == [1024, "hard"]


# ── config four-point round-trip ──────────────────────────────────────────────


def test_sandbox_config_roundtrips(tmp_path, monkeypatch):
    """sandbox.nofile / max_pids / max_rss_mb survive load → to_dict → load and land in
    config.json under the ``sandbox`` section."""
    from personalclaw.config import loader as loader_mod
    from personalclaw.config.loader import AppConfig

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(loader_mod, "config_path", lambda: cfg_path)

    cfg = AppConfig()
    cfg.sandbox.nofile = 2048
    cfg.sandbox.max_pids = 128
    cfg.sandbox.max_rss_mb = 256
    cfg.save()

    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw["sandbox"]["nofile"] == 2048
    assert raw["sandbox"]["max_pids"] == 128
    assert raw["sandbox"]["max_rss_mb"] == 256

    reloaded = AppConfig.load()
    assert reloaded.sandbox.nofile == 2048
    assert reloaded.sandbox.max_pids == 128
    assert reloaded.sandbox.max_rss_mb == 256


def test_sandbox_config_in_to_dict():
    d = AppConfig_to_dict()
    assert "sandbox" in d
    assert set(d["sandbox"]) == {"nofile", "max_pids", "max_rss_mb"}


def AppConfig_to_dict():
    from personalclaw.config.loader import AppConfig

    return AppConfig().to_dict()


def test_sandbox_keys_in_editable_config_allowlist():
    """The PATCH write path accepts each sandbox key (the fourth of the four points)."""
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    for key in ("sandbox.nofile", "sandbox.max_pids", "sandbox.max_rss_mb"):
        assert key in _EDITABLE_CONFIG, f"{key} missing from _EDITABLE_CONFIG"
        assert _EDITABLE_CONFIG[key]["type"] == "int"


def test_default_ceilings_do_not_emit_nproc():
    """RLIMIT_NPROC is a PER-USER cap counting every process the user already runs, not
    just this child's subtree — so an absolute default would break a busy host/CI runner
    ('cannot fork' in a git worktree or npm build). The default ceiling therefore emits
    NOFILE only, never NPROC. (Real per-subtree fork-bomb containment is the cgroup tier.)
    """
    cel = ResourceCeilings()  # ship defaults: nofile=4096, max_pids=0, max_rss_mb=0
    p = cel.policy(PROFILE_TOOL)
    assert "RLIMIT_NPROC" not in p["limits"], "default ceiling must not set a per-user NPROC cap"
    assert p["limits"]["RLIMIT_NOFILE"] == [4096, "hard"]


@pytest.mark.skipif(shim._resource is None, reason="resource module unavailable (Windows)")
def test_default_ceiling_still_lets_a_child_fork(tmp_path):
    """End-to-end guard for the regression that a default NPROC cap caused: a child under
    the DEFAULT tool ceiling can still fork a grandchild (a build/git worktree pattern)."""
    from personalclaw.sandbox import spawn_shim_argv as _wrap

    argv = _wrap(
        [
            sys.executable,
            "-c",
            "import subprocess,sys; subprocess.run([sys.executable,'-c','pass'],check=True)",
        ],
        PROFILE_TOOL,
        ceilings=ResourceCeilings(),
    )
    env = {**os.environ, "PYTHONPATH": _SRC}
    rc = subprocess.call(argv, env=env)
    assert rc == 0, "a child under the default ceiling must still be able to fork"


def test_from_config_reads_live_sandbox(tmp_path, monkeypatch):
    from personalclaw.config import loader as loader_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"sandbox": {"nofile": 777, "max_pids": 42, "max_rss_mb": 0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader_mod, "config_path", lambda: cfg_path)
    cel = ResourceCeilings.from_config()
    assert cel.nofile == 777
    assert cel.max_pids == 42
    assert cel.max_rss_mb == 0
