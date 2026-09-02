"""Spawn-ceiling audit tripwire (PLATFORM-HARDENING-FLOORS §1, SH1.3a).

Every process-spawning call site in ``src/personalclaw`` must be *accounted for*: either
it is **ceiling-wrapped** (routed through the post-exec shim via ``create_subprocess_limited``
/ ``spawn_shim_argv``, so an agent-influenced child carries a resource ceiling) or it is
**operator-exempt** (an operator-initiated spawn — the frontend build, the service/update
machinery, the interactive terminal, host-fact probes — which must NOT be constrained).

The census is an AST walk (no import side effects) over every ``subprocess.Popen/run/call/
check_output/check_call``, ``asyncio.create_subprocess_exec/shell``, ``os.execv*``, and
``StdioServerParameters`` site. Each is keyed by ``file::qualname::callee``. The keys are
partitioned across two hardcoded allowlists below. **A new, unmapped spawn site reds this
test naming its file:line** — the author must consciously classify it. That conscious step
is the control: an agent-influenced spawn that forgets the ceiling cannot slip in silently.

Adding a site → add its key to exactly one allowlist with a one-line reason. Removing a
site → drop its key. The test fails if the census and the union of the allowlists disagree
in either direction, so a stale allowlist entry is caught too.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SPAWN_CALLEES = {
    "subprocess.Popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_output",
    "subprocess.check_call",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.execv",
    "os.execvp",
    "os.execve",
    "StdioServerParameters",
    # The ceiling helper itself is a spawn callee: a routed async seam calls this instead
    # of the raw create_subprocess_exec, so it must be censused and classified too.
    "create_subprocess_limited",
}

# ── CEILING-WRAPPED: agent-influenced spawns routed through the post-exec shim ──
# Each is where an agent (a tool call, a hook/cron command, a loop, an app backend, an
# MCP server, an ACP session) can influence the command, so it MUST carry a ceiling. The
# actual spawn happens inside sandbox.create_subprocess_limited (the two helper sites), and
# these call sites reach it via that helper or via spawn_shim_argv (argv-prepend).
_CEILING_WRAPPED: dict[str, str] = {
    # WF2WOR-12's container backend CLI verbs (docker / nerdctl / Apple container). The argv
    # carries manifest-derived values — image names, build contexts, mount sources an agent can
    # author into a workflow's workspace manifest — so it is agent-influenced and takes the tool
    # ceiling. The CLI client mostly streams to the daemon; the engine's own wait_for bounds it.
    "workflows/container_env.py::_run_cli::create_subprocess_limited": (
        "container backend CLI verbs — manifest-derived (agent-authorable) argv → tool ceiling"
    ),
    # sandbox.py is the helper itself — the one create_subprocess_exec that every routed
    # async seam funnels through (argv already shim-prepended).
    "sandbox.py::create_subprocess_limited::asyncio.create_subprocess_exec": (
        "the ceiling helper — spawns the shim-prepended argv for every routed async seam"
    ),
    # The shim's own execv — replaces the child image with the real target after setrlimit.
    "_spawn_exec_shim.py::main::os.execvp": (
        "the shim's post-exec handoff to the real target (limits already applied)"
    ),
    # Native bash tool (tool profile).
    "agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::"
    "create_subprocess_limited": "native bash tool → tool ceiling via create_subprocess_limited",
    # Bash action provider — hook/cron shell commands (tool profile).
    "action_providers/bash_provider.py::BashActionProvider.execute::"
    "create_subprocess_limited": "bash action provider → tool ceiling",
    # Desktop computer-use driver (tool profile) — DESKTOP-COMPUTER-USE §3.5, `DCU-4`. The
    # driver is a subprocess precisely BECAUSE of this ceiling: an accessibility call into an
    # unresponsive application can block inside the OS for a long time, and the plan requires a
    # wedged/looping driver to be "bounded by the kernel, not just a userspace timeout". The
    # operation is model-chosen (which app, which element, which named AX action), so it is
    # agent-influenced in the fullest sense — this is the one spawn in the tree whose child
    # touches the operator's physical input layer.
    "computer_use/service.py::_run_driver::create_subprocess_limited": (
        "desktop computer-use driver → tool ceiling via create_subprocess_limited"
    ),
    # Loop verify gate (tool profile).
    "loop/gates.py::run_verify_command::create_subprocess_limited": (
        "loop verify command → tool ceiling (was create_subprocess_shell)"
    ),
    # Loop worktree git steps (build profile).
    "loop/worktree.py::_git::subprocess.run": (
        "loop worktree git → build ceiling via spawn_shim_argv"
    ),
    # React artifact bundle (build profile) — PRODUCT-EXPERIENCE-PARITY `PEP-9`. The source
    # handed to the bundler is a model- or user-authored artifact body, so this is
    # agent-influenced in the fullest sense and is exactly the unbounded build spawn §1
    # exists to bound. It is the ONLY spawn in artifacts/, and there is no unwrapped
    # sibling path.
    "artifacts/build.py::_run_esbuild::create_subprocess_limited": (
        "react artifact bundle → build ceiling via create_subprocess_limited"
    ),
    # App backend respawn (tool profile) — off the watchdog thread, argv-prepend not preexec_fn.
    "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen": (
        "app backend → tool ceiling via spawn_shim_argv (argv-prepend; NOT preexec_fn)"
    ),
    # App background worker (tool profile) — APE-3. Same ceiling and the same argv-prepend as
    # the backend above, and for a sharper reason: a worker is LONG-LIVED and unattended, so an
    # unceilinged one is the fork bomb nobody is watching. `preexec_fn` is again refused (PHF-1)
    # because this spawn can run off the watchdog thread.
    "apps/worker_runtime.py::WorkerSupervisor._spawn::subprocess.Popen": (
        "app background worker → tool ceiling via spawn_shim_argv (argv-prepend; NOT preexec_fn)"
    ),
    # MCP stdio discovery probe (tool profile).
    "mcp_discovery.py::probe_server::create_subprocess_limited": (
        "MCP probe → tool ceiling via create_subprocess_limited"
    ),
    # MCP stdio client — the SDK spawns; we shim-prepend the command in the argv.
    "mcp_client.py::McpServerConn._open_transport::StdioServerParameters": (
        "MCP stdio client → tool ceiling via spawn_shim_argv baked into StdioServerParameters"
    ),
    # Workflow BYOI teardown command (tool profile).
    "workflows/effects.py::run_teardown::create_subprocess_limited": (
        "workflow BYOI teardown → tool ceiling via create_subprocess_limited"
    ),
    # Run-workspace setup/teardown steps (WORK-CONTAINERS §4.1). Agent-influenced by the same
    # reasoning as the BYOI teardown above: the command text comes from a workflow template an
    # agent can author. Same shape deliberately — `shlex.split`, no shell, the binary resolved up
    # front for a typed not-found, and the ceiling delivered post-exec by the shim.
    "workflows/provisioning.py::run_step::create_subprocess_limited": (
        "workspace setup/teardown step → tool ceiling via create_subprocess_limited"
    ),
    # The ``none`` sandbox provider's handle exec (EI-1) — the single seam every routed spawn
    # now funnels through. EI-1 moved the direct create_subprocess_limited call out of
    # AcpProcess.spawn (session_host profile — the EMFILE fix, NOFILE raised, no OOM bias) and
    # into this provider handle, which composes the OS path sandbox with the resource ceilings;
    # the profile still rides on the SandboxSpec, so the ACP ceiling is unchanged.
    "sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited": (
        "none sandbox provider → profile ceiling via create_subprocess_limited (post-exec shim); "
        "the single routed-spawn seam (subsumes the former AcpProcess.spawn session_host site)"
    ),
    # Cron/scheduled-script runner (EI-3). Agent-influenced: an agent authors the file under
    # `crons/` and the job that selects it, so the child gets the same `tool` ceiling an agent
    # bash command does. Its earlier exemption reasoned from the OS sandbox wrap + clean env —
    # both real, neither a ceiling — which left a user script able to exhaust descriptors or
    # fork-bomb where a bash tool call could not. Sync site, so the ceiling arrives via
    # spawn_shim_argv (argv-prepend) rather than the async helper; the shim goes OUTSIDE the
    # wrap_argv sandbox, matching the none-provider seam.
    "schedule_script.py::run_script_sandboxed::subprocess.run": (
        "cron/scheduled script → tool ceiling via spawn_shim_argv, prepended outside the "
        "OS-sandbox wrap (sync site; rlimits inherit through exec)"
    ),
    # Interactive terminal — explicitly the ``none`` profile (routed for legibility; the
    # helper is a no-op there so no shim cost, but the site stays audited).
    "dashboard/handlers/terminal.py::api_terminal_ws::create_subprocess_limited": (
        "interactive terminal → none profile (user's own shell; helper is a no-op)"
    ),
    # WS-8: a connector pack's PARSE-ONLY script. The most agent-influenced spawn in the tree
    # by provenance — the code came from a third-party app in a Store — so it takes the same
    # `tool` ceiling a bash tool call gets, delivered the same way `schedule_script.py` does it
    # (sync site → spawn_shim_argv argv-prepend, OUTSIDE the wrap_argv sandbox so the shim's own
    # import does not have to survive the profile). The ceiling is one of four bounds here and
    # the only one this audit owns: the wall-clock timeout, the stdin cap and the stdout cap live
    # in `run_parse_script`, and the no-network fence lives in the in-spawn harness.
    "knowledge_providers/pack_parse.py::run_parse_script::subprocess.run": (
        "connector-pack parse script → tool ceiling via spawn_shim_argv, prepended outside "
        "the OS-sandbox wrap (sync site; rlimits inherit through exec)"
    ),
    # Model sidecar child (LMMV §3.1) — third-party native model code in its own venv, so
    # agent-influenced: the ``tool`` profile also gives it the OOM-first bias, which is the
    # disposition wanted for a process holding a multi-gigabyte model. argv-prepend, not
    # preexec_fn: this can run off the watchdog thread (the backend_runtime hazard).
    # Enforced structurally, not just described: test_local_model_sidecar's
    # `test_every_spawn_in_sidecar_py_is_ceiling_wrapped` AST-checks that both sites here
    # pass a `spawn_shim_argv` result, so a raw argv reds even with this entry in place.
    "local_models/sidecar.py::SidecarRunner._spawn::subprocess.Popen": (
        "model sidecar child → tool ceiling via spawn_shim_argv (argv-prepend)"
    ),
    # Sidecar install (venv + pip) — user-initiated but runs third-party setup code, so it
    # carries the ``build`` profile (NOFILE raised; a pip install opens many fds).
    "local_models/sidecar.py::SidecarInstall._run::subprocess.run": (
        "sidecar venv/pip install → build ceiling via spawn_shim_argv"
    ),
}

# ── OPERATOR-EXEMPT: operator-initiated spawns that must NOT carry an agent ceiling ──
# Frontend build, service install/update, host-fact probes, the CLI, media/knowledge
# pipeline tools, tmux session management, screenshot/upload pickers. These are driven by
# the operator (or are internal host-fact reads), not by agent-controlled input, and
# several MUST stay uncapped (a build opens thousands of fds; the updater re-execs the
# gateway itself).
_OPERATOR_EXEMPT: dict[str, str] = {
    # ACP CLI resolution / provisioning — operator setup, host-fact probes.
    "acp/cli_resolve.py::_npm_root_global_bin::subprocess.run": "operator: npm prefix probe",
    "acp/cli_resolve.py::resolve_node_ge::subprocess.run": "operator: node version probe",
    "acp/cli_resolve.py::provision_acp_adapter::subprocess.run": "operator: ACP adapter install",
    # ACP PID-tree host-fact probes (ps/proc reads, not spawns of agent code).
    "acp/transport.py::_direct_children::subprocess.check_output": "host-fact: child PID probe",
    "acp/transport.py::_get_start_time::subprocess.check_output": (
        "host-fact: process start-time probe"
    ),
    "acp/transport.py::_is_our_child::subprocess.check_output": "host-fact: PID-recycle probe",
    "acp/transport.py::_kill_escaped_children::subprocess.check_output": (
        "host-fact: pgid membership scan for escaped children"
    ),
    # Runner-catalog health probe (EI-5) — `<CLI> --version` and nothing else. The argv is
    # assembled ONLY from catalog data (the shipped runner_catalog.json or an operator's own
    # runners/<id>.json), never from a model or a turn, and it runs no agent code: it reads a
    # host fact about a CLI the operator installed, exactly like the node/npm probes above.
    "agents/runners.py::probe_runner::subprocess.run": "host-fact: runner --version probe",
    # EI-8's localhost preview probe. Every argv is a CONSTANT list — `lsof -nP -iTCP
    # -sTCP:LISTEN -FpPn`, `ss -lntpH`, `ps -o comm= -p <pid>`, `lsof -a -p <pids> -d cwd -Fn`
    # — assembled from literals plus pids the scan itself just read, never from a model, a
    # turn, or a workflow input. No shell, `check=False`, and a 4s timeout. It reads a host
    # fact (which ports are listening, and whose cwd) and runs no agent code, exactly like the
    # PID and --version probes above.
    "workflows/web_preview.py::_run::subprocess.run": "host-fact: listening-port/cwd probe",
    # DAS-9's state-history git runner. Every verb in the argv is a module constant; the only
    # caller-supplied value that reaches git is a commit sha, hex-validated
    # (`re.fullmatch(r"[0-9a-fA-F]{4,64}")`, state_history.py:581) before use, and the root is a
    # closed enum of five paths. No shell. The environment is deliberately hostile to
    # third-party code: `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`,
    # `core.hooksPath=` and `commit.gpgsign=false`, so a user's global hooks or signing config
    # cannot execute on a history commit. Operator/host-fact, not agent-influenced.
    "durability/state_history.py::_git::subprocess.run": "operator: state-history git runner",
    # SV-9's Self-QA commit watcher and its triage classifier. Both run READ-ONLY git inspection
    # (`rev-parse`, `rev-list`, `show --name-only`) with a fixed argv, no shell, `check=False` and
    # a 30s timeout, and neither runs any agent code. The repo root is the operator's
    # `agent.self_qa.watched_repo`. The one caller-supplied value that reaches git is a commit
    # ref, and it is hex-validated before use (`selfqa/triage.py::_SHA_RE`) so nothing
    # option-shaped can pose as a sha — the same discipline as the state-history runner above.
    "selfqa/triage.py::_git::subprocess.run": "host-fact: read-only git commit inspection",
    "selfqa/scripts/selfqa_commit_watch.py::_git::subprocess.run": (
        "host-fact: read-only git HEAD probe"
    ),
    "durability/state_history.py::ensure_repo::subprocess.run": "operator: state-history repo init",
    "durability/state_history.py::git_available::subprocess.run": "host-fact: git presence probe",
    # App install — operator-initiated (Store install), scanned+vetted.
    "apps/app_manager.py::_run_hook::subprocess.run": "operator: app install setup hook",
    "apps/app_manager.py::_install_python_deps::subprocess.run": "operator: app dep install",
    "apps/catalog.py::_read_git_registry::subprocess.run": "operator: git app registry read",
    "apps/catalog.py::_scan_git_source::subprocess.run": "operator: git app source scan",
    "apps/source.py::_clone_git::subprocess.run": "operator: git app clone",
    # CLI commands — operator at a terminal.
    "cli_config.py::_config_cmd::os.execvp": "operator: opens $EDITOR on config",
    "cli_doctor.py::_doctor::subprocess.run": "operator: doctor host probes",
    "cli_server.py::_stop::subprocess.check_output": "operator: stop — pid lookup",
    "cli_server.py::_is_personalclaw_process::subprocess.check_output": (
        "operator: pid identity probe"
    ),
    "cli_server.py::_spawn_detached_gateway::subprocess.Popen": (
        "operator: launch the gateway itself"
    ),
    # `personalclaw run` boots a transient gateway when none is listening. Same class as
    # `_spawn_detached_gateway` above: the operator typed the command, and the argv is a
    # fixed literal (`-m personalclaw gateway --port auto --no-open --json-ready`). The
    # agent-controlled value — the prompt — travels over loopback HTTP and never reaches
    # argv, so there is no agent-influenced input for a ceiling to cap. Capping it would
    # also be wrong: the child IS a gateway, and a gateway must not run under an agent's
    # fd/memory ceiling.
    "cli_run.py::start_transient_gateway::subprocess.Popen": (
        "operator: launch the gateway itself (headless `run` bootstrap)"
    ),
    "cli_server.py::_install::subprocess.run": "operator: self-update package install",
    "cli_server.py::_refresh_agent_config::subprocess.run": (
        "operator: post-update `setup --agent-only` re-run"
    ),
    "cli_server.py::_logs_cmd::subprocess.run": "operator: logs source probe",
    "cli_server.py::_logs_cmd::os.execvp": "operator: exec journalctl/tail for `logs`",
    # Dashboard marketplace/skills CLI shellouts — operator actions via the UI.
    "dashboard/handlers/_shared.py::_list_marketplace_skills::asyncio.create_subprocess_exec": (
        "operator: `personalclaw skills list`"
    ),
    "dashboard/handlers/mcp.py::api_mcp_remove::asyncio.create_subprocess_exec": (
        "operator: `personalclaw skills mcp uninstall`"
    ),
    # Files handlers — operator's own file browser / git diff / native pickers.
    "dashboard/handlers/files.py::_content_search_rg::asyncio.create_subprocess_exec": (
        "operator: file search (rg)"
    ),
    "dashboard/handlers/files.py::_git::asyncio.create_subprocess_exec": (
        "operator: file browser git read"
    ),
    "dashboard/handlers/files.py::api_file_git_original::asyncio.create_subprocess_exec": (
        "operator: git show for diff view"
    ),
    # EI-9's review-triage diff read. Same class as the file browser's `_git` above: a fixed
    # read-only argv (`diff` / `ls-files`), `cwd` the run's OWN workspace path off the run record,
    # and never a shell. Nothing agent-authored reaches the argv — the reviewer's finding is only
    # ever COMPARED against this command's output, never passed to it.
    "workflows/review_service.py::_git::asyncio.create_subprocess_exec": (
        "operator: run workspace git diff read"
    ),
    "dashboard/handlers/files.py::api_reveal_path::subprocess.Popen": (
        "operator: reveal in Finder/xdg-open"
    ),
    "dashboard/handlers/files.py::api_screenshot::asyncio.create_subprocess_exec": (
        "operator: screencapture"
    ),
    "dashboard/handlers/files.py::api_upload::asyncio.create_subprocess_exec": (
        "operator: native file picker"
    ),
    # Terminal tmux session management — operator's own persistent shells.
    "dashboard/handlers/terminal.py::_kill_tmux_session::asyncio.create_subprocess_exec": (
        "operator: kill user's tmux session"
    ),
    "dashboard/handlers/terminal.py::_list_tmux_sessions::asyncio.create_subprocess_exec": (
        "operator: list user's tmux sessions"
    ),
    # EI-6 tmux substrate — the same class as the terminal entries above, and for the same
    # reason: the argv is FIXED (`tmux -L personalclaw <subcommand>`), no element of it is
    # agent-influenced, and every one of these is a bounded read of our own tmux server. A
    # resource ceiling on a `has-session` probe would cap the boot sweep's ability to ask
    # whether a run's worker is still alive, which is the opposite of the safety it buys.
    "tmux_substrate.py::has_session::asyncio.create_subprocess_exec": (
        "operator: probe our own tmux server for a session"
    ),
    "tmux_substrate.py::has_session_sync::subprocess.run": (
        "operator: probe our own tmux server for a session (sync boot sweep)"
    ),
    "tmux_substrate.py::list_sessions::asyncio.create_subprocess_exec": (
        "operator: list sessions on our own tmux server"
    ),
    "tmux_substrate.py::pane_paths_sync::subprocess.run": (
        "operator: read pane cwds from our own tmux server"
    ),
    "tmux_substrate.py::kill_session::asyncio.create_subprocess_exec": (
        "operator: kill a session on our own tmux server"
    ),
    # Update machinery — operator/service; re-execs the gateway itself (must not be capped).
    # The install-kind decision + the shared git/pip primitives live in core self_update.py
    # (DIST-13); the dashboard and the CLI both drive them.
    "self_update.py::_run_git::subprocess.run": (
        "service: the one git seam every sync self-update probe funnels through"
    ),
    "self_update.py::commits_behind_upstream::asyncio.create_subprocess_exec": (
        "service: update git fetch + rev-list"
    ),
    "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec": (
        "service: update check git"
    ),
    "dashboard/handlers/updates.py::_apply_pip_update._apply::asyncio.create_subprocess_exec": (
        "service: self pip update"
    ),
    "dashboard/handlers/updates.py::api_update_apply::asyncio.create_subprocess_exec": (
        "service: update git"
    ),
    "dashboard/handlers/updates.py::api_update_apply._apply::asyncio.create_subprocess_exec": (
        "service: update git/pip"
    ),
    "dashboard/handlers/updates.py::_graceful_reexec::os.execve": (
        "service: re-exec the gateway itself"
    ),
    # System metrics — host-fact probes (sysctl/ps/vm_stat/netstat).
    "dashboard/handlers_system.py::_get_static_system_info::subprocess.check_output": (
        "host-fact: static sysinfo"
    ),
    "dashboard/handlers_system.py::_collect_gpu_metrics::subprocess.check_output": (
        "host-fact: GPU metrics"
    ),
    # The ONE GPU/VRAM capacity probe (LMMV-8) — `nvidia-smi` + `system_profiler`, both
    # static argv host-fact READS. Capacity detection lives here so a fit verdict cannot
    # disagree with the metrics widget; the widget keeps only its live-telemetry query.
    "local_models/fit.py::_probe_gpu::subprocess.check_output": ("host-fact: GPU/VRAM capacity"),
    "dashboard/handlers_system.py::_collect_system_metrics::subprocess.check_output": (
        "host-fact: system metrics"
    ),
    # Memory-pressure snapshot (LMMV §7) — `sysctl -n hw.memsize` + `vm_stat`, both static
    # argv host-fact READS with no agent-influenced input. Capping a read that exists to
    # report on memory pressure would be self-defeating.
    "local_models/residency.py::_darwin_memory::subprocess.check_output": (
        "host-fact: macOS memory-pressure probe (sysctl/vm_stat, static argv)"
    ),
    # Evals cell child — a seeded, sandboxed evals worker (own isolation, not agent argv).
    "evals/runner.py::_spawn_cell::subprocess.run": (
        "operator: evals matrix cell (own env isolation)"
    ),
    # Frontend build — operator/service; must stay uncapped (thousands of fds).
    "frontend.py::build_frontend_sync::subprocess.run": "operator: frontend npm build",
    "frontend.py::build_frontend_async::asyncio.create_subprocess_exec": (
        "operator: frontend npm build"
    ),
    # Gateway auto-update — service; re-execs the gateway itself.
    "gateway.py::GatewayOrchestrator._auto_apply_update::asyncio.create_subprocess_exec": (
        "service: auto-update git/pip"
    ),
    "gateway.py::GatewayOrchestrator._auto_apply_update::os.execv": (
        "service: re-exec the gateway itself"
    ),
    "gateway.py::_wslview_open::subprocess.run": "operator: open browser on WSL",
    # Knowledge media pipeline — ffprobe/ffmpeg on operator-ingested media (host tools).
    "knowledge/pipeline/executor.py::PipelineExecutor._media_duration::subprocess.run": (
        "host tool: ffprobe"
    ),
    "knowledge/pipeline/nodes/media_nodes.py::VideoClassifyNode._dense_regions::subprocess.run": (
        "host tool: ffprobe scene detect"
    ),
    "knowledge/pipeline/nodes/media_nodes.py::_run_cmd::asyncio.create_subprocess_exec": (
        "host tool: ffmpeg"
    ),
    # MCP/session host-fact PPID probes.
    "mcp_core.py::_get_ppid::subprocess.check_output": "host-fact: ppid probe",
    "mcp_shared.py::_resolve_excluded_tools._get_ppid::subprocess.check_output": (
        "host-fact: ppid probe"
    ),
    "session_pid.py::_is_managed_agent_process::subprocess.check_output": (
        "host-fact: managed-process probe"
    ),
    "subagent.py::_total_memory_gb::subprocess.check_output": "host-fact: total RAM probe",
    # Sandbox availability probes — internal probes of the sandbox mechanism itself.
    "sandbox.py::_probe_sandbox_exec::subprocess.run": (
        "host-fact: sandbox-exec availability probe"
    ),
    "sandbox.py::_ssh_supports_accept_new::subprocess.run": "host-fact: ssh version probe",
    # Service install/control — operator with sudo (launchd/systemd).
    "service/linux.py::_current_group::subprocess.run": "operator: service install id probe",
    "service/linux.py::_sudo_run::subprocess.run": "operator: service install sudo",
    "service/linux.py::_systemctl::subprocess.run": "operator: systemctl control",
    "service/linux.py::_write_unit_via_sudo::subprocess.run": "operator: write systemd unit",
    "service/macos.py::_launchctl::subprocess.run": "operator: launchctl control",
    # Trigger liveness git-dirty probe — a read-only host-fact probe of the workspace.
    "triggers/liveness.py::_dirty_git_active::subprocess.run": (
        "host-fact: workspace git-dirty probe"
    ),
    # Voice/transcribe — operator media (ffmpeg/whisper host tools), operator-initiated.
    "transcribe.py::_transcribe_segmented::asyncio.create_subprocess_exec": (
        "host tool: transcription ffmpeg"
    ),
    "transcribe.py::_transcribe_segmented_detailed::asyncio.create_subprocess_exec": (
        "host tool: transcription ffmpeg"
    ),
    "voice_reply.py::stitch_wavs::asyncio.create_subprocess_exec": "host tool: wav stitch ffmpeg",
    # APE-4's quality verifier runs an app bundle's OWN pytest to check a `tested: true`
    # declaration. Exempt on two grounds, and the second is the load-bearing one:
    #   1. Not agent-influenced — the argv is `[sys.executable, "-m", "pytest", <dir>]`,
    #      where <dir> comes from the CLI's own positional argument. No model, no turn,
    #      no workflow input reaches it, and there is no shell.
    #   2. Not reachable from the gateway at all. It is a CI/CLI tool
    #      (`python -m personalclaw.apps.quality`) with ZERO importers in the runtime —
    #      pinned by `test_the_verifier_has_no_gateway_call_site` in
    #      tests/test_app_quality_enforcement.py, so wiring it into a request path breaks
    #      that rail and forces this classification to be re-argued rather than
    #      inherited. A ceiling would also be wrong on the merits: a bundle's own suite
    #      legitimately spawns and opens files like any test run.
    "apps/quality.py::run_bundle_tests::subprocess.run": (
        "CI tool: app-bundle pytest for a quality declaration (no gateway call site)"
    ),
}


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _callee(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        parts = [f.attr]
        v = f.value
        while isinstance(v, ast.Attribute):
            parts.append(v.attr)
            v = v.value
        if isinstance(v, ast.Name):
            parts.append(v.id)
        return ".".join(reversed(parts))
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _normalize(callee: str) -> str:
    tail = callee.split(".")[-1]
    if tail in {"create_subprocess_exec", "create_subprocess_shell"}:
        return "asyncio." + tail
    if tail == "create_subprocess_limited":
        return "create_subprocess_limited"
    if tail in {"Popen", "run", "call", "check_output", "check_call"} and "subprocess" in callee:
        return "subprocess." + tail
    if tail in {"execv", "execvp", "execve"}:
        return "os." + tail
    if tail == "StdioServerParameters":
        return "StdioServerParameters"
    return callee


def _census() -> dict[str, list[int]]:
    """Map ``file::qualname::callee`` → sorted line numbers for every spawn site."""
    out: dict[str, list[int]] = {}
    root = _src_root()
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(root).as_posix()

        class V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.q: list[str] = []

            def visit_FunctionDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

            def visit_ClassDef(self, n: ast.AST) -> None:
                self.q.append(n.name)  # type: ignore[attr-defined]
                self.generic_visit(n)
                self.q.pop()

            def visit_Call(self, n: ast.Call) -> None:
                c = _normalize(_callee(n))
                if c in _SPAWN_CALLEES:
                    key = f"{rel}::{'.'.join(self.q) or '<module>'}::{c}"
                    out.setdefault(key, []).append(n.lineno)
                self.generic_visit(n)

        V().visit(tree)
    return out


def test_every_spawn_site_is_classified():
    """Every spawn site is in exactly one allowlist; a new/unmapped site reds CI by name."""
    census = _census()
    allow = set(_CEILING_WRAPPED) | set(_OPERATOR_EXEMPT)

    unmapped = sorted(set(census) - allow)
    lines_for = lambda k: ", ".join(f"{k.split('::')[0]}:{ln}" for ln in census[k])  # noqa: E731
    assert not unmapped, (
        "Unmapped spawn site(s) — classify each in tests/test_spawn_ceiling_audit.py as "
        "ceiling-wrapped (agent-influenced → route through create_subprocess_limited/"
        "spawn_shim_argv) or operator-exempt:\n"
        + "\n".join(f"  {k}  ({lines_for(k)})" for k in unmapped)
    )

    stale = sorted(allow - set(census))
    assert not stale, (
        "Stale allowlist entr(y/ies) — no such spawn site exists anymore; remove from the "
        "allowlist:\n" + "\n".join(f"  {k}" for k in stale)
    )


def test_ceiling_wrapped_and_operator_exempt_are_disjoint():
    """A site cannot be both wrapped and exempt."""
    both = set(_CEILING_WRAPPED) & set(_OPERATOR_EXEMPT)
    assert not both, f"sites in both allowlists: {sorted(both)}"


def test_agent_influenced_seams_are_all_ceiling_wrapped():
    """The named agent-influenced seams from PLATFORM-HARDENING-FLOORS §1 are each present
    in the ceiling-wrapped set (a regression guard so one cannot be quietly re-exempted)."""
    required = {
        "agents/native/builtin_tools.py::NativeBuiltinToolProvider._t_bash::"
        "create_subprocess_limited",
        "action_providers/bash_provider.py::BashActionProvider.execute::"
        "create_subprocess_limited",
        "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen",
        "mcp_discovery.py::probe_server::create_subprocess_limited",
        "mcp_client.py::McpServerConn._open_transport::StdioServerParameters",
        # EI-1 routed the ACP session_host spawn through the sandbox provider handle — the
        # single seam every routed spawn now funnels through — so the ACP ceiling is asserted
        # here rather than at the former AcpProcess.spawn site.
        "sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited",
        "loop/gates.py::run_verify_command::create_subprocess_limited",
        "loop/worktree.py::_git::subprocess.run",
        # EI-3 closed the last of the seven named seams: the cron/scheduled-script runner had
        # the OS sandbox and the PHF-4 clean env but no ceiling. Ratcheted in here so the
        # exemption cannot come back.
        "schedule_script.py::run_script_sandboxed::subprocess.run",
        # WS-8: third-party parser code from a Store app. Ratcheted in for the same reason —
        # this is the site where an exemption would be least defensible.
        "knowledge_providers/pack_parse.py::run_parse_script::subprocess.run",
        # PEP-9: bundling a react artifact runs a bundler over agent-authored source.
        # Ratcheted in so the artifact deploy path can never grow a second, unwrapped build.
        "artifacts/build.py::_run_esbuild::create_subprocess_limited",
    }
    missing = sorted(required - set(_CEILING_WRAPPED))
    assert not missing, f"agent seams not ceiling-wrapped: {missing}"
