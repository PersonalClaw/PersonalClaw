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
    # Loop verify gate (tool profile).
    "loop/gates.py::run_verify_command::create_subprocess_limited": (
        "loop verify command → tool ceiling (was create_subprocess_shell)"
    ),
    # Loop worktree git steps (build profile).
    "loop/worktree.py::_git::subprocess.run": (
        "loop worktree git → build ceiling via spawn_shim_argv"
    ),
    # App backend respawn (tool profile) — off the watchdog thread, argv-prepend not preexec_fn.
    "apps/backend_runtime.py::BackendSupervisor.start::subprocess.Popen": (
        "app backend → tool ceiling via spawn_shim_argv (argv-prepend; NOT preexec_fn)"
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
    # The ``none`` sandbox provider's handle exec (EI-1) — the single seam every routed spawn
    # now funnels through. EI-1 moved the direct create_subprocess_limited call out of
    # AcpProcess.spawn (session_host profile — the EMFILE fix, NOFILE raised, no OOM bias) and
    # into this provider handle, which composes the OS path sandbox with the resource ceilings;
    # the profile still rides on the SandboxSpec, so the ACP ceiling is unchanged.
    "sandbox_providers/none.py::_NoneHandle.exec::create_subprocess_limited": (
        "none sandbox provider → profile ceiling via create_subprocess_limited (post-exec shim); "
        "the single routed-spawn seam (subsumes the former AcpProcess.spawn session_host site)"
    ),
    # Interactive terminal — explicitly the ``none`` profile (routed for legibility; the
    # helper is a no-op there so no shim cost, but the site stays audited).
    "dashboard/handlers/terminal.py::api_terminal_ws::create_subprocess_limited": (
        "interactive terminal → none profile (user's own shell; helper is a no-op)"
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
    "cli_server.py::_update::subprocess.run": "operator: self-update git/pip",
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
    # Update machinery — operator/service; re-execs the gateway itself (must not be capped).
    "dashboard/handlers/updates.py::_do_update_check::asyncio.create_subprocess_exec": (
        "service: update check git"
    ),
    "dashboard/handlers/updates.py::_commits_behind_upstream::asyncio.create_subprocess_exec": (
        "service: update git rev-list"
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
    "dashboard/handlers_system.py::_collect_system_metrics::subprocess.check_output": (
        "host-fact: system metrics"
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
    # Cron/scheduled-script runner — operator-authored scheduled scripts, own sandbox wrap
    # (wrap_argv) + clean env. Not an agent-tool spawn; scheduling is an operator action.
    "schedule_script.py::run_script_sandboxed::subprocess.run": (
        "operator: scheduled-script runner (own sandbox wrap + clean env)"
    ),
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
    }
    missing = sorted(required - set(_CEILING_WRAPPED))
    assert not missing, f"agent seams not ceiling-wrapped: {missing}"
