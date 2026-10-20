"""Where every spawned child's environment comes from — the sibling of the spawn-ceiling audit.

The gateway's environment holds every secret saved in PersonalClaw (``AppConfig.load_credentials``
exports them so its own children can read them), plus whatever the shell that started it had. A
child spawned with ``env`` left out, or with a copy of ``os.environ``, gets all of it. The rule is
``sandbox.build_child_env``: a child that runs code PersonalClaw did not write (an agent's command,
a hook, a loop's check, a workflow step, an app's installs and servers, an agent CLI), that
fetches someone else's repository, or that is any git PersonalClaw runs (a repository an agent can
write names programs git starts) begins from the child allowlist, never from the gateway's
environment.

So every spawn site in ``src/personalclaw`` (the SAME census and keys as
``tests/test_spawn_ceiling_audit.py``: ``file::qualname::callee``) is classified here, once:

* ``_BUILT`` — the child's environment is built by ``build_child_env``. Checked in the code, not
  taken on trust: the site passes ``env=`` and the value is a ``build_child_env`` call, a
  ``*env`` function that reaches one, or a name the enclosing function binds to either.
* ``_GATEWAY_ENV`` — the child deliberately runs with the gateway's own environment, with the
  reason it may. Checked too: such a site must NOT be built, so a site that becomes built has to
  move, and the reason on record stays true.
* ``_PASS_THROUGH`` — the seam spawns with the environment its CALLER chose (``**kwargs``), or is
  the ceiling shim handing its own environment on to its target.

A new spawn site reds this test by name until it is classified; a stale entry reds it too.
``_MUST_STAY_BUILT`` ratchets the sites whose children run code someone else wrote, so one cannot
be moved to the gateway environment without that list being edited as well.
"""

from __future__ import annotations

import ast

from spawn_env_audit_shared import _BUILT, _called_name, env_source

# ── BUILT: the child allowlist (`sandbox.build_child_env`) ─────────────────────────────────────

# ── GATEWAY ENV: runs with the gateway's own environment, and why it may ────────────────────────

# ── PASS-THROUGH: the environment is the caller's choice ────────────────────────────────────────

#: Children that run code PersonalClaw did not write, or fetch someone else's repository. Each
#: must stay built; moving one to `_GATEWAY_ENV` means editing this set too.
_MUST_STAY_BUILT = {
    "agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::create_subprocess_limited",
    "action_providers/bash_provider.py::BashActionProvider.execute::create_subprocess_limited",
    "schedule_script.py::run_script_sandboxed::subprocess.run",
    "loop/gates.py::run_verify_command::create_subprocess_limited",
    "loop/worktree.py::_git::subprocess.run",
    "workflows/provisioning.py::run_step::create_subprocess_limited",
    "workflows/effects.py::run_teardown::create_subprocess_limited",
    "agents/runners.py::probe_runner::subprocess.run",
    "apps/app_manager.py::_run_hook::subprocess.run",
    "apps/app_python.py::_pip_install::subprocess.run",
    "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen",
    "apps/worker_runtime.py::WorkerSupervisor._spawn::subprocess.Popen",
    "local_models/sidecar.py::SidecarRunner._spawn::subprocess.Popen",
    "local_models/sidecar.py::run_once::asyncio.create_subprocess_exec",
    "knowledge_providers/pack_parse.py::run_parse_script::subprocess.run",
    "apps/catalog.py::_read_git_registry::subprocess.run",
    "apps/catalog.py::_scan_git_source::subprocess.run",
    "apps/source.py::_clone_git::subprocess.run",
    "net/git.py::run_git_guarded::subprocess.run",
    "net/git.py::_owner_auth_settings::subprocess.run",
    # Every git PersonalClaw runs: the program a repository names runs as its child.
    "dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec",
    "workflows/review_service.py::_git::asyncio.create_subprocess_exec",
    "triggers/liveness.py::_dirty_git_active::subprocess.run",
    "selfqa/triage.py::_git::subprocess.run",
    "selfqa/watch.py::_git::subprocess.run",
    "selfqa/fix_branch.py::_git::subprocess.run",
    "durability/state_history.py::_git::subprocess.run",
    "durability/state_history.py::_repo_usable::subprocess.run",
    "durability/state_history.py::ensure_repo::subprocess.run",
    "durability/state_history.py::git_available::subprocess.run",
    "net/git.py::_version_of::subprocess.run",
    "cli_doctor.py::_git_is_inside_work_tree::subprocess.run",
    "self_update.py::_run_git::subprocess.run",
    "self_update.py::commits_behind_upstream::asyncio.create_subprocess_exec",
    "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec",
}


# ── the resolver ────────────────────────────────────────────────────────────────────────────


# ── the rails ───────────────────────────────────────────────────────────────────────────────


def test_the_children_that_run_others_code_stay_built():
    missing = sorted(_MUST_STAY_BUILT - set(_BUILT))
    assert not missing, f"must stay on the child allowlist: {missing}"


def test_the_resolver_tells_the_shapes_apart():
    """A positive control: the resolver itself, on every shape the tree uses."""
    src = """
def f(kw):
    subprocess.run(["x"])
    subprocess.run(["x"], env=None)
    subprocess.run(["x"], env=os.environ)
    subprocess.run(["x"], env={**os.environ, "A": "1"})
    subprocess.run(["x"], env=dict(os.environ))
    subprocess.run(["x"], env={**gateway_env(), "A": "1"})
    subprocess.run(["x"], env=installer_env())
    subprocess.run(["x"], env=build_child_env(site="s"))
    subprocess.run(["x"], env=git_env(site="s"))
    subprocess.run(["x"], **kw)
    built = build_child_env(site="s")
    subprocess.run(["x"], env=built)
    copied = {**os.environ}
    subprocess.run(["x"], env=copied)
"""
    func = ast.parse(src).body[0]
    calls = [n for n in ast.walk(func) if isinstance(n, ast.Call) and _called_name(n) == "run"]
    got = [env_source(c, func, {"build_child_env", "git_env"}) for c in calls]
    assert got == [
        "inherited",
        "inherited",
        "inherited",
        "inherited",
        "inherited",
        "inherited",
        "unknown",
        "built",
        "built",
        "passthrough",
        "built",
        "inherited",
    ]
