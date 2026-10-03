"""Every place PersonalClaw runs a command someone wrote asks the shell denylist first.

Settings → Security → Shell denylist bound the native bash tool and nothing else: a loop's check, a
bash action, a workflow step and an app's setup hook each ran a command its owner's patterns refuse.
They now ask ``security.denied_command``, and this rail keeps every such site asking. It classifies
every spawn site in ``src/personalclaw`` (the census and keys of
``tests/test_spawn_ceiling_audit.py``: ``file::qualname::callee``) as one of two:

* ``_ASKS`` — the site runs a command someone wrote: the agent, an agent CLI's request, a loop, a
  workflow, an automation, an app's author. Checked in the code: the function holding the spawn
  calls ``denied_command`` before the spawn, itself or through a function of its own module.
* ``_RUNS_NO_WRITTEN_COMMAND`` — every other site, with what it runs instead: a program and
  arguments PersonalClaw composes (git, npm, ffmpeg, a probe), a program the owner installed and
  starts (an MCP server, an agent CLI, an app's code), the owner's own terminal, or a seam that runs
  what its caller handed it. Checked too: no such site may hand a shell a command it was given
  (``sh -c <text>``, ``shell=True``), the shape a written command takes.

A new spawn site reds this rail by name until it is classified, and a stale entry reds it too. The
falsification tests at the bottom run both checks on source written to fail them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from test_spawn_ceiling_audit import _callee, _census, _normalize, _src_root

THE_CHECK = "denied_command"

_BASH_TOOL = (
    "agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::create_subprocess_limited"
)

# ── the sites that run a command someone wrote ─────────────────────────────────────────────────
_ASKS: dict[str, str] = {
    _BASH_TOOL: "the agent's bash tool; Tools → Try it and a cron script's tool call reach it too",
    "loop/gates.py::run_verify_command::create_subprocess_limited": (
        "a loop's check, its judge's, a code stage's gate, a workflow's verify gate and guard"
    ),
    "action_providers/bash_provider.py::BashActionProvider.execute::create_subprocess_limited": (
        "a bash action: a schedule, a hook, Run now, a webhook, a workflow node, a proposal"
    ),
    "workflows/provisioning.py::run_step::create_subprocess_limited": (
        "a workflow's setup or teardown step, bare or in the run's durable session"
    ),
    "workflows/effects.py::run_teardown::create_subprocess_limited": "a workflow effect's teardown",
    "apps/app_manager.py::_run_hook::subprocess.run": "an app's setup hook",
}

_COMPOSED = "a program and arguments PersonalClaw composes"
_GIT = "git, with arguments PersonalClaw composes"
_PROBE = "a host probe with fixed arguments"

# ── every other site, and what it runs instead ─────────────────────────────────────────────────
_RUNS_NO_WRITTEN_COMMAND: dict[str, str] = {
    "_spawn_exec_shim.py::main::os.execvp": "the ceiling shim: execs the argv its spawner chose",
    "acp/cli_resolve.py::_npm_global_root::subprocess.run": _COMPOSED,
    "acp/cli_resolve.py::provision_acp_adapter::subprocess.run": _COMPOSED,
    "acp/cli_resolve.py::resolve_node_ge::subprocess.run": _PROBE,
    "acp/transport.py::_direct_children::subprocess.check_output": _PROBE,
    "acp/transport.py::_get_start_time::subprocess.check_output": _PROBE,
    "acp/transport.py::_is_our_child::subprocess.check_output": _PROBE,
    "acp/transport.py::_kill_escaped_children::subprocess.check_output": _PROBE,
    "agents/runners.py::probe_runner::subprocess.run": "an agent CLI's version probe",
    "apps/app_python.py::_pip_install::subprocess.run": "pip installing an app's declared packages",
    "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen": "an app's backend",
    "apps/catalog.py::_read_git_registry::subprocess.run": _GIT,
    "apps/catalog.py::_scan_git_source::subprocess.run": _GIT,
    "apps/quality.py::run_bundle_tests::subprocess.run": "pytest over an app bundle (CI tool)",
    "apps/source.py::_clone_git::subprocess.run": _GIT,
    "apps/worker_runtime.py::WorkerSupervisor._spawn::subprocess.Popen": "an app's worker",
    "artifacts/build.py::_run_esbuild::create_subprocess_limited": _COMPOSED,
    "cli_config.py::_edit_config::subprocess.run": "the owner's own editor, from the owner's CLI",
    "cli_doctor.py::_doctor::subprocess.run": _PROBE,
    "cli_doctor.py::_git_is_inside_work_tree::subprocess.run": _GIT,
    "cli_doctor.py::_probe_python_version::subprocess.run": _PROBE,
    "cli_doctor.py::_pip_row::subprocess.run": _PROBE,
    "cli_run.py::start_transient_gateway::subprocess.Popen": "PersonalClaw's own gateway",
    "cli_server.py::_install::subprocess.run": _COMPOSED,
    "cli_server.py::_logs_cmd::os.execvp": "journalctl or tail, from the owner's CLI",
    "cli_server.py::_logs_cmd::subprocess.run": _PROBE,
    "cli_server.py::_refresh_agent_config::subprocess.run": "PersonalClaw's own setup command",
    "cli_server.py::_spawn_detached_gateway::subprocess.Popen": "PersonalClaw's own gateway",
    "computer_use/macos_tcc.py::_probe::subprocess.run": _PROBE,
    "computer_use/service.py::_run_driver::create_subprocess_limited": "the computer-use driver",
    "dashboard/handlers/_shared.py::_list_marketplace_skills::asyncio.create_subprocess_exec": (
        _COMPOSED
    ),
    "dashboard/handlers/files.py::_content_search_rg::asyncio.create_subprocess_exec": _COMPOSED,
    "dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec": _GIT,
    "dashboard/handlers/files.py::api_reveal_path::subprocess.Popen": "the system file opener",
    "dashboard/handlers/files.py::api_screenshot::asyncio.create_subprocess_exec": _COMPOSED,
    "dashboard/handlers/files.py::api_upload::asyncio.create_subprocess_exec": _COMPOSED,
    "dashboard/handlers/terminal.py::api_terminal_ws::create_subprocess_limited": (
        "the owner's own terminal: an interactive shell with no command to judge before it runs"
    ),
    "dashboard/handlers/updates.py::_apply_pip_update._apply::asyncio.create_subprocess_exec": (
        _COMPOSED
    ),
    "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec": _GIT,
    "dashboard/handlers/updates.py::api_update_apply._apply::asyncio.create_subprocess_exec": (
        _COMPOSED
    ),
    "dashboard/handlers_system.py::_collect_gpu_metrics::subprocess.check_output": _PROBE,
    "dashboard/handlers_system.py::_collect_system_metrics::subprocess.check_output": _PROBE,
    "dashboard/handlers_system.py::_get_static_system_info::subprocess.check_output": _PROBE,
    "durability/state_history.py::_git::subprocess.run": _GIT,
    "durability/state_history.py::_repo_usable::subprocess.run": _GIT,
    "durability/state_history.py::ensure_repo::subprocess.run": _GIT,
    "durability/state_history.py::git_available::subprocess.run": _GIT,
    "evals/runner.py::_spawn_cell::subprocess.run": "an evals matrix cell (PersonalClaw's own)",
    "frontend.py::build_frontend_async::asyncio.create_subprocess_exec": _COMPOSED,
    "frontend.py::build_frontend_sync::subprocess.run": _COMPOSED,
    "gateway.py::GatewayOrchestrator._auto_apply_update::asyncio.create_subprocess_exec": (
        _COMPOSED
    ),
    "gateway.py::_wslview_open::subprocess.run": "the system browser opener",
    "knowledge/pipeline/nodes/media_nodes.py::_run_cmd::asyncio.create_subprocess_exec": (
        _COMPOSED
    ),
    "knowledge/pipeline/nodes/media_nodes.py::media_seconds::asyncio.create_subprocess_exec": (
        _PROBE
    ),
    "knowledge_providers/pack_parse.py::run_parse_script::subprocess.run": (
        "a connector pack's parse script (an app's code)"
    ),
    "local_models/fit.py::_probe_gpu::subprocess.check_output": _PROBE,
    "local_models/residency.py::_darwin_memory::subprocess.check_output": _PROBE,
    "local_models/sidecar.py::SidecarInstall._run::subprocess.Popen": "a model sidecar's install",
    "local_models/sidecar.py::SidecarRunner._spawn::subprocess.Popen": "a model sidecar (app code)",
    "local_models/sidecar.py::run_once::asyncio.create_subprocess_exec": (
        "one call of an app's worker (app code)"
    ),
    "loop/worktree.py::_git::subprocess.run": _GIT,
    "mcp_core.py::_get_ppid::subprocess.check_output": _PROBE,
    "mcp_shared.py::_resolve_excluded_tools._get_ppid::subprocess.check_output": _PROBE,
    "mcp_stdio.py::stdio_streams::create_subprocess_limited": (
        "an MCP server the owner set up or an app declares: its program and arguments"
    ),
    "net/git.py::_owner_auth_settings::subprocess.run": _GIT,
    "net/git.py::_version_of::subprocess.run": _GIT,
    "net/git.py::run_git_guarded::subprocess.run": _GIT,
    "process_facts.py::run_probe::subprocess.run": _PROBE,
    "providers/availability.py::AvailabilityBoard._probe::asyncio.create_subprocess_exec": _PROBE,
    "restart_request.py::start::os.execve": "PersonalClaw's own gateway, restarted",
    "sandbox.py::_probe_sandbox_exec::subprocess.run": _PROBE,
    "sandbox.py::_ssh_supports_accept_new::subprocess.run": _PROBE,
    "sandbox.py::create_subprocess_limited::asyncio.create_subprocess_exec": (
        "the spawner itself: runs the argv its caller hands it"
    ),
    "sandbox_providers/docker.py::_DockerHandle.cleanup::subprocess.run": _COMPOSED,
    "sandbox_providers/docker.py::_DockerHandle.exec::create_subprocess_limited": (
        "an isolation tier: runs the argv its caller wrapped"
    ),
    "sandbox_providers/docker.py::_daemon_ping::subprocess.run": _PROBE,
    "sandbox_providers/lima.py::_LimaHandle.exec::create_subprocess_limited": (
        "an isolation tier: runs the argv its caller wrapped"
    ),
    "sandbox_providers/lima.py::_probe::subprocess.run": _PROBE,
    "sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited": (
        "an isolation tier: runs the argv its caller wrapped"
    ),
    "schedule_script.py::run_script_sandboxed::subprocess.run": (
        "a scheduled Python script; its tool calls reach the shell through the bash tool"
    ),
    "self_update.py::_run_git::subprocess.run": _GIT,
    "self_update.py::commits_behind_upstream::asyncio.create_subprocess_exec": _GIT,
    "selfqa/evidence.py::_ffmpeg_ping::subprocess.run": _PROBE,
    "selfqa/evidence.py::_run_ffmpeg::subprocess.run": _COMPOSED,
    "selfqa/evidence.py::probe_duration_secs::subprocess.run": _PROBE,
    "selfqa/fix_branch.py::_git::subprocess.run": _GIT,
    "selfqa/triage.py::_git::subprocess.run": _GIT,
    "selfqa/watch.py::_git::subprocess.run": _GIT,
    "service/linux.py::_current_group::subprocess.run": _PROBE,
    "service/linux.py::_sudo_run::subprocess.run": "the service manager, from the owner's CLI",
    "service/linux.py::_systemctl::subprocess.run": "the service manager, from the owner's CLI",
    "service/linux.py::_write_unit_via_sudo::subprocess.run": "the service manager",
    "service/macos.py::_launchctl::subprocess.run": "the service manager",
    "session_pid.py::_is_managed_agent_process::subprocess.check_output": _PROBE,
    "subagent.py::_total_memory_gb::subprocess.check_output": _PROBE,
    "tmux_substrate.py::has_session::asyncio.create_subprocess_exec": _PROBE,
    "tmux_substrate.py::has_session_sync::subprocess.run": _PROBE,
    "tmux_substrate.py::kill_server::subprocess.run": _COMPOSED,
    "tmux_substrate.py::kill_session::asyncio.create_subprocess_exec": _COMPOSED,
    "tmux_substrate.py::list_sessions::asyncio.create_subprocess_exec": _PROBE,
    "tmux_substrate.py::new_session::asyncio.create_subprocess_exec": (
        "a durable session for an argv its caller composed (a workflow step `run_step` asked)"
    ),
    "tmux_substrate.py::pane_paths_sync::subprocess.run": _PROBE,
    "transcribe.py::_segment::asyncio.create_subprocess_exec": _COMPOSED,
    "transcribe.py::audio_seconds::subprocess.run": _PROBE,
    "triggers/liveness.py::_dirty_git_active::subprocess.run": _GIT,
    "uploads/content_scan.py::_ask_child::asyncio.create_subprocess_exec": (
        "PersonalClaw's own CLI, scanning an upload's window it reads on stdin"
    ),
    "voice_reply.py::stitch_wavs::asyncio.create_subprocess_exec": _COMPOSED,
    "workflows/container_env.py::_run_cli::create_subprocess_limited": _COMPOSED,
    "workflows/review_service.py::_git::asyncio.create_subprocess_exec": _GIT,
}

#: Programs that run their `-c` argument as a shell command.
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


# ── reading a function out of the tree ─────────────────────────────────────────────────────────


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    """Every function in *tree* by qualname, as the census names it (`Class.method`, `f.inner`)."""
    found: dict[str, ast.AST] = {}

    def walk(node: ast.AST, prefix: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = [*prefix, child.name]
                if not isinstance(child, ast.ClassDef):
                    found[".".join(name)] = child
                walk(child, name)
            else:
                walk(child, prefix)

    walk(tree, [])
    return found


def _called(node: ast.AST) -> list[tuple[str, int]]:
    """Every name *node* calls (`f(…)`, `x.f(…)`), with its line."""
    out = []
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            f = call.func
            name = (
                f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            )
            if name:
                out.append((name, call.lineno))
    return out


def _first_ask(qualname: str, functions: dict[str, ast.AST]) -> int | None:
    """The line where *qualname* first asks the check, itself or through a function of its own
    module that asks it directly (one hop); None when it never does."""
    node = functions[qualname]
    owner = qualname.rsplit(".", 1)[0] if "." in qualname else ""

    def asks_directly(fn: ast.AST) -> bool:
        return any(name == THE_CHECK for name, _ in _called(fn))

    lines = []
    for name, line in _called(node):
        helper = functions.get(f"{owner}.{name}" if owner else name) or functions.get(name)
        if name == THE_CHECK or (
            helper is not None and helper is not node and asks_directly(helper)
        ):
            lines.append(line)
    return min(lines) if lines else None


def _spawn_lines(node: ast.AST, callee: str) -> list[int]:
    return [
        c.lineno
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and _normalize(_callee(c)) == callee
    ]


def _module_strings(tree: ast.Module) -> set[str]:
    """Names bound at module level to a string literal: fixed text, not something handed in."""
    return {
        t.id
        for stmt in tree.body
        if isinstance(stmt, (ast.Assign, ast.AnnAssign))
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
        for t in (stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target])
        if isinstance(t, ast.Name)
    }


def _runs_a_handed_shell_command(node: ast.AST, fixed: set[str]) -> bool:
    """Whether *node* hands a shell a command it did not write itself: `shell=True` on a call, or
    a shell program, then a `-c` flag, then anything but fixed text."""

    def fixed_text(e: ast.AST) -> bool:
        return (isinstance(e, ast.Constant) and isinstance(e.value, str)) or (
            isinstance(e, ast.Name) and e.id in fixed
        )

    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if any(
                kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in sub.keywords
            ) or _callee(sub).endswith("create_subprocess_shell"):
                return True
            seq = sub.args
        elif isinstance(sub, (ast.List, ast.Tuple)):
            seq = sub.elts
        else:
            continue
        for i in range(len(seq) - 2):
            prog, flag, arg = seq[i], seq[i + 1], seq[i + 2]
            if not (isinstance(prog, ast.Constant) and isinstance(prog.value, str)):
                continue
            if prog.value.rsplit("/", 1)[-1] not in _SHELLS:
                continue
            if not (isinstance(flag, ast.Constant) and isinstance(flag.value, str)):
                continue
            if flag.value.startswith("-") and "c" in flag.value and not fixed_text(arg):
                return True
    return False


def _parsed(rel: str) -> ast.Module:
    return ast.parse((_src_root() / rel).read_text(encoding="utf-8"))


# ── the rail ───────────────────────────────────────────────────────────────────────────────────


def test_every_spawn_site_is_classified():
    census = set(_census())
    classified = set(_ASKS) | set(_RUNS_NO_WRITTEN_COMMAND)
    unmapped = sorted(census - classified)
    assert not unmapped, (
        "A spawn site nobody has classified. Decide whether it runs a command someone wrote (then "
        "it asks `security.denied_command` before it spawns, and joins _ASKS) or what it runs "
        "instead (_RUNS_NO_WRITTEN_COMMAND):\n" + "\n".join(f"  {k}" for k in unmapped)
    )
    stale = sorted(classified - census)
    assert not stale, "No such spawn site any more; remove:\n" + "\n".join(f"  {k}" for k in stale)
    assert not set(_ASKS) & set(_RUNS_NO_WRITTEN_COMMAND)


def test_every_site_that_runs_a_written_command_asks_the_denylist_before_it_spawns():
    for key in sorted(_ASKS):
        rel, qualname, callee = key.split("::")
        functions = _functions(_parsed(rel))
        asked = _first_ask(qualname, functions)
        spawned = min(_spawn_lines(functions[qualname], callee))
        assert asked is not None, f"{key} runs a written command and never asks {THE_CHECK}"
        assert asked < spawned, f"{key} asks {THE_CHECK} (line {asked}) after it spawns ({spawned})"


def test_no_other_site_hands_a_shell_a_command_it_was_given():
    for key in sorted(_RUNS_NO_WRITTEN_COMMAND):
        rel, qualname, _callee_name = key.split("::")
        tree = _parsed(rel)
        functions = _functions(tree)
        assert not _runs_a_handed_shell_command(functions[qualname], _module_strings(tree)), (
            f"{key} hands a shell a command it was given, so it runs a written command: make it "
            f"ask `security.denied_command` before it spawns, and move it to _ASKS"
        )


# ── falsification: each check, run on source written to fail it ────────────────────────────────

_BYPASS = """
async def run(cmd):
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""

_ASKED = """
async def run(cmd):
    if denied_command(cmd) is not None:
        return None
    return await create_subprocess_limited("/bin/sh", "-c", cmd)
"""

_ASKED_TOO_LATE = """
async def run(cmd):
    proc = await create_subprocess_limited("/bin/sh", "-c", cmd)
    denied_command(cmd)
    return proc
"""

_ASKED_ONE_HOP = """
def refused(cmd):
    return denied_command(cmd)

async def run(cmd):
    argv = ["bash", "-lc", cmd]
    if refused(cmd):
        return None
    return await create_subprocess_limited(*argv)
"""

_FIXED = """
_SCRIPT = "uname -a"

def run():
    subprocess.run(["/bin/sh", "-c", "uname -a"])
    return subprocess.run(["/bin/sh", "-c", _SCRIPT])
"""

_SHELL_TRUE = """
def run(cmd):
    return subprocess.run(cmd, shell=True)
"""


def _check(source: str, qualname: str = "run") -> tuple[int | None, bool]:
    tree = ast.parse(source)
    functions = _functions(tree)
    return (
        _first_ask(qualname, functions),
        _runs_a_handed_shell_command(functions[qualname], _module_strings(tree)),
    )


def test_a_site_that_runs_a_handed_command_without_asking_is_caught():
    asked, shell = _check(_BYPASS)
    assert asked is None and shell


def test_a_site_that_asks_first_passes_and_one_that_asks_after_does_not():
    asked, _ = _check(_ASKED)
    assert asked is not None and asked < min(
        _spawn_lines(_functions(ast.parse(_ASKED))["run"], "create_subprocess_limited")
    )
    late, _ = _check(_ASKED_TOO_LATE)
    spawn = min(
        _spawn_lines(_functions(ast.parse(_ASKED_TOO_LATE))["run"], "create_subprocess_limited")
    )
    assert late is not None and late > spawn


def test_asking_through_a_helper_of_the_same_module_counts():
    asked, shell = _check(_ASKED_ONE_HOP)
    assert asked is not None and shell


def test_fixed_shell_text_is_no_handed_command_and_shell_true_always_is():
    assert _check(_FIXED) == (None, False)
    assert _check(_SHELL_TRUE)[1] is True


def test_the_shell_detector_sees_every_real_shell_site():
    """Vacuity control: the detector recognises the written-command shells the tree really has."""
    for key in (
        _BASH_TOOL,
        "loop/gates.py::run_verify_command::create_subprocess_limited",
        "action_providers/bash_provider.py::BashActionProvider.execute::create_subprocess_limited",
        "apps/app_manager.py::_run_hook::subprocess.run",
    ):
        rel, qualname, _ = key.split("::")
        tree = _parsed(rel)
        assert _runs_a_handed_shell_command(_functions(tree)[qualname], _module_strings(tree)), key


def test_the_census_is_not_empty_and_holds_every_asking_site():
    census = _census()
    assert len(census) > 100, len(census)
    assert set(_ASKS) <= set(census)
    assert (
        Path(_src_root(), "security.py").read_text(encoding="utf-8").count(f"def {THE_CHECK}(") == 1
    )
