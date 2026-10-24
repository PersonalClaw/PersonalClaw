"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_spawn_env_audit.py``

Its checks:
``tests/rails/test_spawn_env_audit_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast

from spawn_ceiling_audit_shared import _callee

_BUILT: dict[str, str] = {
    # The agent's own commands and what its runs execute.
    "agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::"
    "create_subprocess_limited": "the native agent's bash tool: the agent's command",
    "action_providers/bash_provider.py::BashActionProvider.execute::create_subprocess_limited": (
        "hook / cron / bash-action shell commands"
    ),
    "schedule_script.py::run_script_sandboxed::subprocess.run": "a scheduled script",
    "loop/gates.py::run_verify_command::create_subprocess_limited": (
        "a loop's persisted check command"
    ),
    "loop/worktree.py::_git::subprocess.run": (
        "a loop worktree's git: commit and merge run the repository's hooks"
    ),
    "workflows/provisioning.py::run_step::create_subprocess_limited": (
        "a workflow's setup/teardown step (the durable path starts it with the same env, env -i)"
    ),
    "workflows/effects.py::run_teardown::create_subprocess_limited": (
        "a workflow effect's teardown command"
    ),
    "evals/runner.py::_spawn_cell::subprocess.run": "an evals matrix cell",
    "artifacts/build.py::_run_esbuild::create_subprocess_limited": (
        "the bundler over an agent-authored react artifact"
    ),
    # Agent CLIs.
    "agents/runners.py::probe_runner::subprocess.run": (
        "a runner catalog CLI's --version probe: an agent CLI keeps what it inherits"
    ),
    "acp/cli_resolve.py::provision_acp_adapter::subprocess.run": (
        "npm installing an ACP adapter (npm's own settings added)"
    ),
    # What the gateway starts for an app.
    "apps/app_manager.py::_run_hook::subprocess.run": "an app's setup hook",
    "apps/app_python.py::_pip_install::subprocess.run": "pip installing an app's packages",
    "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen": "an app's backend",
    "apps/worker_runtime.py::WorkerSupervisor._spawn::subprocess.Popen": "an app's worker",
    "local_models/sidecar.py::SidecarRunner._spawn::subprocess.Popen": "a model sidecar",
    "local_models/sidecar.py::run_once::asyncio.create_subprocess_exec": (
        "one model call in a child of its own"
    ),
    "local_models/sidecar.py::SidecarInstall._run::subprocess.Popen": (
        "a model sidecar's venv and pip install"
    ),
    "knowledge_providers/pack_parse.py::run_parse_script::subprocess.run": (
        "a connector pack's parse script"
    ),
    "mcp_stdio.py::stdio_streams::create_subprocess_limited": (
        "an MCP stdio server, for the probe and an agent's connection alike (`stdio_spawn_env`: an "
        "app's server gets the allowlist; a server of the owner's own gets the gateway's "
        "environment, like a program the owner starts)"
    ),
    # Git that fetches someone else's repository (`net.git.git_env`, with the SSH agent).
    "apps/catalog.py::_read_git_registry::subprocess.run": (
        "the Store reading an app source's registry index"
    ),
    "apps/catalog.py::_scan_git_source::subprocess.run": "the Store scanning a multi-app source",
    "apps/source.py::_clone_git::subprocess.run": "an install cloning the owner's git URL",
    "net/git.py::run_git_guarded::subprocess.run": (
        "a registry listing's clone through the egress tunnel (`guarded_git_env`)"
    ),
    "net/git.py::_owner_auth_settings::subprocess.run": (
        "reading the owner's own git sign-in settings for a fetch"
    ),
    # PersonalClaw's own git in a repository an agent's shell can write (`net.git.git_env`, and
    # `git_argv` on the command line): a hook or any other program that repository names would
    # otherwise run with every secret the gateway holds.
    "dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec": "file browser git read",
    "workflows/review_service.py::_git::asyncio.create_subprocess_exec": (
        "a run workspace's git diff read"
    ),
    "triggers/liveness.py::_dirty_git_active::subprocess.run": "workspace git-dirty probe",
    "selfqa/triage.py::_git::subprocess.run": "read-only git on the watched repo",
    "selfqa/watch.py::_git::subprocess.run": "read-only git on the watched repo",
    "selfqa/fix_branch.py::_git::subprocess.run": "`git branch` on the owner's watched repo",
    "durability/state_history.py::_git::subprocess.run": (
        "PersonalClaw's own history repo (`_git_env`: no global/system config either)"
    ),
    "durability/state_history.py::_repo_usable::subprocess.run": "history repo probe",
    "durability/state_history.py::ensure_repo::subprocess.run": "history repo init",
    "durability/state_history.py::git_available::subprocess.run": "git presence probe",
    "net/git.py::_version_of::subprocess.run": (
        "the version of the git on PATH, which PersonalClaw's git refuses below 2.12"
    ),
    "cli_doctor.py::_git_is_inside_work_tree::subprocess.run": "doctor work-tree probe",
    "self_update.py::_run_git::subprocess.run": "git on PersonalClaw's own checkout",
    "self_update.py::commits_behind_upstream::asyncio.create_subprocess_exec": (
        "git fetch of PersonalClaw's own origin, with the owner's SSH agent and sign-in"
    ),
    "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec": (
        "update check git on PersonalClaw's own checkout"
    ),
    # PersonalClaw's own scanner, reading what someone uploaded: it needs no gateway secret.
    "uploads/content_scan.py::_ask_child::asyncio.create_subprocess_exec": (
        "the upload content scan: an upload's window on stdin, a verdict out"
    ),
}


_GATEWAY_ENV: dict[str, str] = {
    # PersonalClaw itself: the gateway, its CLI and its own modules.
    "cli_server.py::_spawn_detached_gateway::subprocess.Popen": "the gateway itself",
    "cli_run.py::start_transient_gateway::subprocess.Popen": "the gateway itself (`run`)",
    "restart_request.py::start::os.execve": (
        "the gateway starting itself again once its own stop is done (a Restart, an applied "
        "update, a staged auto-update)"
    ),
    "cli_server.py::_refresh_agent_config::subprocess.run": (
        "PersonalClaw's own `setup --agent-only`"
    ),
    "dashboard/handlers/_shared.py::_list_marketplace_skills::asyncio.create_subprocess_exec": (
        "PersonalClaw's own `skills list`"
    ),
    "providers/availability.py::AvailabilityBoard._probe::asyncio.create_subprocess_exec": (
        "PersonalClaw's own availability-probe CLI: it runs the app code the gateway imports "
        "in-process, and must see what the gateway sees to answer for it"
    ),
    "computer_use/service.py::_run_driver::create_subprocess_limited": (
        "PersonalClaw's own driver module (`python -m`), not code it did not write"
    ),
    "apps/quality.py::run_bundle_tests::subprocess.run": (
        "a CI/CLI tool with no gateway call site (the quality verifier)"
    ),
    # Installs and updates of PersonalClaw itself (`_installer.installer_env`, held to that by
    # tests/test_installer_resolution.py), and the git of its own checkout.
    "cli_server.py::_install::subprocess.Popen": "self-update package install",
    "cli_server.py::_install::subprocess.run": ("self-update package install, off the main thread"),
    "dashboard/handlers/updates.py::_upgrade_wheel::asyncio.create_subprocess_exec": (
        "self pip update"
    ),
    "checkout_update.py::_install::asyncio.create_subprocess_exec": (
        "the checkout's install, for the Update, the staged auto-update and `personalclaw "
        "update` (its git runs through `self_update._run_git`)"
    ),
    "frontend.py::build_frontend_async::asyncio.create_subprocess_exec": (
        "PersonalClaw's own frontend build"
    ),
    "acp/cli_resolve.py::_npm_global_root::subprocess.run": "`npm root -g` probe",
    # The owner at the keyboard: their shell, their editor, the OS's pickers and service manager.
    "dashboard/handlers/terminal.py::api_terminal_ws::create_subprocess_limited": (
        "the owner's own interactive terminal (a sandbox tier runs it inside the tier)"
    ),
    "cli_config.py::_edit_config::subprocess.run": "the owner's $EDITOR",
    "cli_server.py::_logs_cmd::subprocess.run": "`logs` source probe",
    "cli_server.py::_logs_cmd::os.execvp": "`logs` execs journalctl/tail in the owner's terminal",
    "dashboard/handlers/files.py::api_reveal_path::subprocess.Popen": "reveal in Finder/xdg-open",
    "dashboard/handlers/files.py::api_screenshot::asyncio.create_subprocess_exec": (
        "screencapture"
    ),
    "dashboard/handlers/files.py::api_upload::asyncio.create_subprocess_exec": "native file picker",
    "gateway.py::_wslview_open::subprocess.run": "open the browser on WSL",
    "service/linux.py::_current_group::subprocess.run": "service install id probe",
    "service/linux.py::_sudo_run::subprocess.run": "service install sudo",
    "service/linux.py::_systemctl::subprocess.run": "systemctl",
    "service/linux.py::_write_unit_via_sudo::subprocess.run": "write the systemd unit",
    "service/macos.py::_launchctl::subprocess.run": "launchctl",
    # Our own tmux server. Its control verbs run nothing; the one command it starts in a session,
    # a durable workflow step, sets its own environment (`sandbox.exact_env_argv`).
    "tmux_substrate.py::new_session::asyncio.create_subprocess_exec": (
        "our tmux client; the durable step it starts runs under env -i with the built env"
    ),
    "tmux_substrate.py::has_session::asyncio.create_subprocess_exec": "tmux probe",
    "tmux_substrate.py::has_session_sync::subprocess.run": "tmux probe",
    "tmux_substrate.py::list_sessions::asyncio.create_subprocess_exec": "tmux probe",
    "tmux_substrate.py::pane_paths_sync::subprocess.run": "tmux probe",
    "tmux_substrate.py::kill_session::asyncio.create_subprocess_exec": "tmux kill of our session",
    "tmux_substrate.py::kill_server::subprocess.run": "stop our own tmux server",
    # The owner's container daemon: a fixed verb set, no `-e`/`--env-file` is ever built, so no
    # variable reaches a container, and the client needs its DOCKER_* selection variables.
    "workflows/container_env.py::_run_cli::create_subprocess_limited": (
        "the container CLI talking to the owner's daemon"
    ),
    "dashboard/handlers/files.py::_content_search_rg::asyncio.create_subprocess_exec": (
        "ripgrep over the owner's files"
    ),
    # Host tools and host-fact probes: a fixed argv, and the program neither runs code someone
    # else wrote nor keeps or forwards its environment.
    "cli_doctor.py::_doctor::subprocess.run": "doctor host probes",
    "cli_doctor.py::_probe_python_version::subprocess.run": "`python --version`",
    "cli_doctor.py::_pip_row::subprocess.run": "`import pip` on the gateway's interpreter",
    "acp/cli_resolve.py::resolve_node_ge::subprocess.run": "`node --version`",
    "acp/transport.py::_direct_children::subprocess.check_output": "ps child probe",
    "acp/transport.py::_get_start_time::subprocess.check_output": "ps start-time probe",
    "acp/transport.py::_is_our_child::subprocess.check_output": "ps pid-recycle probe",
    "acp/transport.py::_kill_escaped_children::subprocess.check_output": "ps pgid scan",
    "process_facts.py::run_probe::subprocess.run": "lsof/ss/ps probe",
    "dashboard/handlers_system.py::_get_static_system_info::subprocess.check_output": "sysinfo",
    "dashboard/handlers_system.py::_collect_gpu_metrics::subprocess.check_output": "GPU metrics",
    "dashboard/handlers_system.py::_collect_system_metrics::subprocess.check_output": "metrics",
    "local_models/fit.py::_probe_gpu::subprocess.check_output": "GPU/VRAM capacity",
    "local_models/residency.py::_darwin_memory::subprocess.check_output": "sysctl/vm_stat",
    "mcp_core.py::_get_ppid::subprocess.check_output": "ppid probe",
    "mcp_shared.py::_resolve_excluded_tools._get_ppid::subprocess.check_output": "ppid probe",
    "session_pid.py::_is_managed_agent_process::subprocess.check_output": "managed-process probe",
    "subagent.py::_total_memory_gb::subprocess.check_output": "total RAM probe",
    "sandbox.py::_probe_sandbox_exec::subprocess.run": "sandbox-exec availability probe",
    "sandbox.py::_ssh_supports_accept_new::subprocess.run": "ssh version probe",
    "sandbox_providers/docker.py::_daemon_ping::subprocess.run": "docker daemon probe",
    "sandbox_providers/docker.py::_DockerHandle.cleanup::subprocess.run": (
        "docker rm -f of our own container"
    ),
    "sandbox_providers/lima.py::_probe::subprocess.run": "lima instance status probe",
    "computer_use/macos_tcc.py::_probe::subprocess.run": "`log show` TCC probe",
    "knowledge/pipeline/nodes/media_nodes.py::media_seconds::asyncio.create_subprocess_exec": (
        "ffprobe reads a video's length"
    ),
    "knowledge/pipeline/nodes/media_nodes.py::_run_cmd::asyncio.create_subprocess_exec": "ffmpeg",
    "transcribe.py::_segment::asyncio.create_subprocess_exec": "ffmpeg",
    "transcribe.py::audio_seconds::subprocess.run": "ffmpeg reads a recording's length",
    "voice_reply.py::stitch_wavs::asyncio.create_subprocess_exec": "ffmpeg wav stitch",
    "selfqa/evidence.py::_ffmpeg_ping::subprocess.run": "`ffmpeg -version`",
    "selfqa/evidence.py::_run_ffmpeg::subprocess.run": "ffmpeg contact sheet",
    "selfqa/evidence.py::probe_duration_secs::subprocess.run": "ffprobe duration",
}


_PASS_THROUGH: dict[str, str] = {
    "sandbox.py::create_subprocess_limited::asyncio.create_subprocess_exec": (
        "the ceiling helper: `env` arrives in **kwargs from its caller"
    ),
    "sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited": (
        "the sandbox provider seam: the ACP transport builds the env it passes"
    ),
    "sandbox_providers/docker.py::_DockerHandle.exec::create_subprocess_limited": (
        "the sandbox provider seam: the caller's env"
    ),
    "sandbox_providers/lima.py::_LimaHandle.exec::create_subprocess_limited": (
        "the sandbox provider seam: the caller's env"
    ),
    "_spawn_exec_shim.py::main::os.execvp": (
        "the ceiling shim execs its target with the environment its spawner built"
    ),
}


def _called_name(call: ast.Call) -> str:
    return _callee(call).split(".")[-1]


def _reads_environ(node: ast.AST) -> bool:
    """Whether *node* reads ``os.environ`` anywhere (a copy, a splat, the mapping itself), or
    ``env.gateway_env()``, which is that copy with ``PATH`` as the process started with it."""
    return any(
        (
            isinstance(sub, ast.Attribute)
            and sub.attr == "environ"
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "os"
        )
        or (isinstance(sub, ast.Call) and _called_name(sub) == "gateway_env")
        for sub in ast.walk(node)
    )


def _judge(value: ast.AST, builders: set[str]) -> str:
    if isinstance(value, ast.Constant) and value.value is None:
        return "inherited"
    if isinstance(value, ast.Call) and _called_name(value) in builders:
        return "built"
    if _reads_environ(value):
        return "inherited"
    return "unknown"


def env_source(call: ast.Call, func: ast.AST | None, builders: set[str]) -> str:
    """``built``, ``inherited``, ``passthrough`` or ``unknown`` for one spawn call."""
    env = [k for k in call.keywords if k.arg == "env"]
    if not env:
        return "passthrough" if any(k.arg is None for k in call.keywords) else "inherited"
    value = env[0].value
    if not isinstance(value, ast.Name):
        return _judge(value, builders)
    verdicts: set[str] = set()
    for node in ast.walk(func) if func is not None else ():
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == value.id for t in node.targets):
                verdicts.add(_judge(node.value, builders))
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == value.id
            and node.value is not None
        ):
            verdicts.add(_judge(node.value, builders))
    if "inherited" in verdicts:
        return "inherited"
    return "built" if "built" in verdicts else "unknown"
