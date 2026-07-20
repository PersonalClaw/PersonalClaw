"""PersonalClaw CLI — personal AI agent.

Commands:
    personalclaw chat -m "message"    Send a single message
    personalclaw chat                 Interactive chat mode
    personalclaw gateway              Start the PersonalClaw server (dashboard + channels)
    personalclaw gateway --seed NAME  Populate $PERSONALCLAW_HOME from fixture NAME, then start the gateway
    personalclaw status               Show runtime stats
    personalclaw update               Update PersonalClaw via git fetch + rebuild
    personalclaw cron list|add|remove Manage scheduled jobs
    personalclaw spawn run "task"     Spawn a background subagent
    personalclaw spawn list           List subagents
    personalclaw learn add|list|remove Save and manage learned corrections
    personalclaw setup                Interactive credential setup
    personalclaw doctor               Verify setup
"""

# Ensure SSL certs are found before any library caches its SSL context.
# The ``personalclaw`` entry-point (console_scripts) bypasses ``__main__.py``,
# so we must run this here as well.
from personalclaw._ssl_compat import _ensure_ssl_certs

_ensure_ssl_certs()

import argparse
import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from personalclaw import __version__
from personalclaw.config import AppConfig, config_dir
from personalclaw.config.loader import (
    DASHBOARD_PORT,
)
from personalclaw.seed import seed_cmd

BANNER = r"""
   __  __         _    ___ _
  |  \/  |___ ___| |_ / __| |__ ___ __ __
  | |\/| / -_|_-<| ' \ (__| / _` \ V  V /
  |_|  |_\___/__/|_||_\___|_\__,_|\_/\_/

  Your personal AI agent
"""

_PROJECT_MARKERS = ("agents", "skills")


def _project_dir_file() -> Path:
    """Return the path to the saved project_dir file, respecting PERSONALCLAW_HOME."""
    return config_dir() / "project_dir"


def _detect_project_dir() -> str | None:
    """Find the project root containing agents/ and skills/.

    Search order:
    1. Walk up from CWD
    2. Read saved path from config_dir()/project_dir (respects PERSONALCLAW_HOME)
    """
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        if all((d / m).is_dir() for m in _PROJECT_MARKERS):
            return str(d)
    pdf = _project_dir_file()
    if pdf.is_file():
        saved = pdf.read_text(encoding="utf-8").strip()
        p = Path(saved)
        if p.is_dir() and all((p / m).is_dir() for m in _PROJECT_MARKERS):
            return saved
    return None


def _resolve_gateway_args(args: argparse.Namespace) -> dict:
    """Resolve the kwargs for `_gateway()` from parsed CLI args.

    Expands the `--test-mode` bundle (with explicit-flag-wins override
    semantics) and enforces the `--approval yolo` safety rail. On rail
    violation, prints a message to stderr and calls `sys.exit(2)`.
    Returned dict is safe to splat directly into `_gateway()`.
    """
    port = getattr(args, "port", None)
    json_ready = getattr(args, "json_ready", False)
    approval = getattr(args, "approval", None)
    no_open = getattr(args, "no_open", False)
    if getattr(args, "test_mode", False):
        # Bundle defaults; explicit flags above take precedence (they are
        # already populated in the locals when the user passed them).
        if port is None:
            port = "auto"
        if approval is None:
            approval = "reads"
        json_ready = True
        no_open = True

    # Validate --port at parse time so a typo (e.g. `--port AUTO`, `--port abc`,
    # `--port 99999`) fails fast with a clear message instead of crashing
    # mid-startup at `int(self._port_override)` after services are partially
    # initialized.
    if port is not None:
        if str(port).lower() == "auto":
            port = "auto"  # canonicalize for downstream comparisons
        else:
            try:
                port_int = int(port)
            except ValueError:
                print(
                    f"--port must be an integer or 'auto', got {port!r}.",
                    file=sys.stderr,
                )
                sys.exit(2)
            if not 1 <= port_int <= 65535:
                print(
                    f"--port {port_int} out of range (1..65535).",
                    file=sys.stderr,
                )
                sys.exit(2)
            port = str(port_int)

    if approval == "yolo":
        home_env = os.environ.get("PERSONALCLAW_HOME", "")
        if not home_env:
            print(
                "--approval yolo refused: PERSONALCLAW_HOME must be explicitly set "
                "to an isolated path (not the default ~/.personalclaw).",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            home_resolved = Path(home_env).expanduser().resolve()
            main_home = (Path.home() / ".personalclaw").resolve()
        except OSError as exc:
            print(
                f"--approval yolo refused: failed to resolve PERSONALCLAW_HOME: {exc}",
                file=sys.stderr,
            )
            sys.exit(2)
        if home_resolved == main_home:
            print(
                "--approval yolo refused: PERSONALCLAW_HOME resolves to the main "
                f"gateway home ({main_home}). Set PERSONALCLAW_HOME to an isolated "
                "path before re-running.",
                file=sys.stderr,
            )
            sys.exit(2)

    return {
        "no_dashboard": getattr(args, "headless", False),
        "no_crons": getattr(args, "no_crons", False),
        "no_open": no_open,
        "port_override": port,
        "json_ready": json_ready,
        "approval_mode": approval,
    }


def main() -> None:
    """Entry point — parse args and dispatch to the appropriate subcommand."""
    # Load .env from the project root (CWD or detected project dir) and from
    # PERSONALCLAW_HOME so credentials resolve via os.environ without requiring
    # users to manually copy .env into ~/.personalclaw.
    from dotenv import load_dotenv as _load_dotenv

    _cwd_env = Path.cwd() / ".env"
    if _cwd_env.is_file():
        _load_dotenv(_cwd_env, override=False)
    _home_env = config_dir() / ".env"
    if _home_env.is_file() and _home_env != _cwd_env:
        _load_dotenv(_home_env, override=False)

    # Validate PERSONALCLAW_PORT early — fail fast before anything else loads.
    _raw_port = os.environ.get("PERSONALCLAW_PORT")
    if _raw_port is not None:
        try:
            int(_raw_port)
        except ValueError:
            print(
                f"❌ PERSONALCLAW_PORT={_raw_port!r} is not a valid integer.\n"
                f"   Unset it or provide a numeric port (e.g. PERSONALCLAW_PORT=6777).",
                file=sys.stderr,
            )
            sys.exit(1)

    if not os.environ.get("PERSONALCLAW_PROJECT_DIR"):
        detected = _detect_project_dir()
        if detected:
            os.environ["PERSONALCLAW_PROJECT_DIR"] = detected

    parser = argparse.ArgumentParser(
        prog="personalclaw",
        description="PersonalClaw — personal AI agent",
    )
    parser.add_argument("--version", action="version", version=f"personalclaw {__version__}")
    parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="Increase log verbosity (-v INFO, -vv DEBUG)",
    )

    sub = parser.add_subparsers(dest="command")

    # Helper for commands with examples
    _fmt = argparse.RawDescriptionHelpFormatter

    # chat
    chat_parser = sub.add_parser(
        "chat",
        help="Chat with the agent",
        epilog="""
Examples:
  personalclaw chat                      # Interactive mode
  personalclaw chat -m 'check my PRs'    # Single message
  personalclaw chat --model claude-opus  # Use specific model
""",
        formatter_class=_fmt,
    )
    chat_parser.add_argument("-m", "--message", help="Single message (non-interactive)")
    chat_parser.add_argument("--model", help="Model to use (default: from config)")

    # doctor
    sub.add_parser("doctor", help="Verify PersonalClaw setup")

    # gateway
    gw_parser = sub.add_parser(
        "gateway", help="Start the PersonalClaw server (dashboard + channels)"
    )
    gw_parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        help="Headless mode — serve channels only; skip the dashboard web server and SSH tunnel instructions",
    )
    gw_parser.add_argument(
        "--no-crons",
        action="store_true",
        help="Skip cron scheduler — use when another instance handles cron execution",
    )
    gw_parser.add_argument(
        "--seed",
        metavar="FIXTURE",
        help=(
            "Seed $PERSONALCLAW_HOME from the named fixture BEFORE starting the "
            "gateway (dev tool). Fixture must exist under "
            "personalclaw/tests_fixtures/. The gateway then runs normally "
            "against the populated $PERSONALCLAW_HOME. Refuses when "
            "$PERSONALCLAW_HOME is the main gateway home (~/.personalclaw) or "
            "when the target is non-empty (use --seed-replace to wipe + re-seed)."
        ),
    )
    gw_parser.add_argument(
        "--seed-replace",
        action="store_true",
        help=(
            "When used with --seed, wipe $PERSONALCLAW_HOME (rmtree) before "
            "copying the fixture. Ignored without --seed. Does NOT "
            "override the main-gateway-home rail — ~/.personalclaw is refused "
            "regardless."
        ),
    )
    gw_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not auto-open the dashboard URL in the default browser on startup",
    )
    gw_parser.add_argument(
        "--port",
        metavar="PORT",
        help=(
            "Override the dashboard port. Pass an integer (e.g. --port 9999) "
            "for a fixed port, or --port auto to bind to an ephemeral port "
            "(OS-assigned). When omitted, falls back to the value in config "
            "(dashboard.url)."
        ),
    )
    gw_parser.add_argument(
        "--json-ready",
        action="store_true",
        help=(
            "Print a single line `PERSONALCLAW_READY:{...}` to stdout once the "
            "dashboard is bound. Payload includes port, token, pid, and "
            "PERSONALCLAW_HOME. Used by test harnesses to discover the bound "
            "ephemeral port and authenticate without polling. NOTE: the "
            "token grants gateway access for up to 20 hours — treat the "
            "READY line as sensitive and do not commit captured stdout to "
            "shared logs."
        ),
    )
    gw_parser.add_argument(
        "--approval",
        choices=["reads", "yolo", "interactive"],
        help=(
            "Default approval mode for tool invocations. 'reads' auto-approves "
            "read-only tools (read/list/get/search/* prefixes); 'yolo' "
            "auto-approves all tools (refused unless PERSONALCLAW_HOME is "
            "explicitly set to a non-default location); 'interactive' uses "
            "the standard channel/dashboard prompt flow. When omitted, current "
            "interactive behavior is preserved."
        ),
    )
    gw_parser.add_argument(
        "--test-mode",
        action="store_true",
        help=(
            "Convenience alias for --port auto --no-open --json-ready "
            "--approval reads. An explicit --port or --approval value "
            "overrides the bundle's default (e.g. --test-mode --approval "
            "yolo uses yolo). The boolean flags --no-open and --json-ready "
            "are forced on by --test-mode and cannot be opted out of."
        ),
    )

    # setup
    setup_parser = sub.add_parser("setup", help="Install agent config and configure credentials")
    setup_parser.add_argument(
        "--agent-only",
        action="store_true",
        help="Only install agent config, skip credential prompts",
    )
    setup_parser.add_argument(
        "--clean",
        action="store_true",
        help="Fresh install — don't merge MCP servers/tools from existing config",
    )
    setup_parser.add_argument(
        "--mode",
        choices=["docker", "service", "none"],
        default="",
        help="Deployment mode: docker (Compose), service (systemd/launchd), or none",
    )
    setup_parser.add_argument(
        "--provider",
        default="",
        metavar="NAME",
        help="Set the default chat provider by registry entry name",
    )
    setup_parser.add_argument(
        "--credential",
        default="",
        metavar="NAME[=VALUE]",
        help="Store a named credential (value from arg or env var)",
    )
    setup_parser.add_argument(
        "--app",
        default="",
        metavar="NAME",
        help="Run only the named installed app's cli.setup step (skip core + other apps)",
    )

    # cron
    cron_parser = sub.add_parser(
        "cron",
        help="Manage scheduled jobs",
        epilog="""
Examples:
  personalclaw cron list
  personalclaw cron add 'daily-status' 'show status' --every 86400
  personalclaw cron add 'weekday-9am' 'check open issues' --cron '0 9 * * MON-FRI' --approval-mode auto
  personalclaw cron update <job-id> --approval-mode auto
  personalclaw cron remove <job-id>
""",
        formatter_class=_fmt,
    )
    cron_sub = cron_parser.add_subparsers(dest="cron_action")
    cron_sub.add_parser("list", help="List cron jobs")
    cron_add = cron_sub.add_parser("add", help="Add a cron job")
    cron_add.add_argument("name", help="Job name")
    cron_add.add_argument("message", help="Message to send to agent")
    cron_add.add_argument("--every", type=int, help="Interval in seconds")
    cron_add.add_argument(
        "--cron", dest="cron_expr", help='Cron expression (e.g. "0 9 * * MON-FRI")'
    )
    cron_add.add_argument("--channel", help="Channel ID to post results to")
    cron_add.add_argument(
        "--approval-mode",
        dest="approval_mode",
        choices=["auto"],
        default="",
        help='Tool approval mode ("auto" to auto-approve all tools)',
    )
    cron_update = cron_sub.add_parser("update", help="Update a cron job")
    cron_update.add_argument("job_id", help="Job ID to update")
    cron_update.add_argument("--name", help="New job name")
    cron_update.add_argument("--message", help="New message")
    cron_update.add_argument("--every", type=int, dest="every_secs", help="New interval in seconds")
    cron_update.add_argument("--cron", dest="cron_expr", help="New cron expression")
    cron_update.add_argument("--channel", help="New channel ID")
    cron_update.add_argument(
        "--approval-mode",
        dest="approval_mode",
        choices=["auto", "default"],
        default=None,
        help='Tool approval mode ("auto" to auto-approve, "default" to reset)',
    )
    cron_rm = cron_sub.add_parser("remove", help="Remove a cron job")
    cron_rm.add_argument("job_id", help="Job ID to remove")
    cron_pause = cron_sub.add_parser("pause", help="Pause a cron job")
    cron_pause.add_argument("job_id", help="Job ID to pause")
    cron_resume = cron_sub.add_parser("resume", help="Resume a cron job")
    cron_resume.add_argument("job_id", help="Job ID to resume")
    cron_trigger = cron_sub.add_parser("trigger", help="Fire a cron job immediately")
    cron_trigger.add_argument("job_id", help="Job ID to trigger now")

    # spawn
    spawn_parser = sub.add_parser(
        "spawn",
        help="Manage background subagents",
        epilog="""
Examples:
  personalclaw spawn run 'check my open PRs'        # Wait for result
  personalclaw spawn run --async 'analyze logs'     # Fire-and-forget
  personalclaw spawn list                           # Show active subagents
""",
        formatter_class=_fmt,
    )
    spawn_sub = spawn_parser.add_subparsers(dest="spawn_action")
    subagent_run = spawn_sub.add_parser("run", help="Spawn a subagent")
    subagent_run.add_argument("task", help="Task for the subagent")
    subagent_run.add_argument(
        "--async",
        dest="fire_and_forget",
        action="store_true",
        help="Fire-and-forget (don't wait for result)",
    )
    spawn_sub.add_parser("list", help="List subagents")
    spawn_parser.add_argument("--port", type=int, default=DASHBOARD_PORT, help="Dashboard port")

    # snapshot / restore
    snap_parser = sub.add_parser("snapshot", help="Create a portable backup of PersonalClaw state")
    snap_parser.add_argument("output_dir", nargs="?", default=None)
    snap_parser.add_argument("--keep", type=int, default=7, help="Keep N most recent snapshots")
    snap_parser.add_argument(
        "--list", action="store_true", dest="list_snapshots", help="List existing snapshots"
    )

    rest_parser = sub.add_parser("restore", help="Restore PersonalClaw state from a snapshot")
    rest_parser.add_argument("snapshot", nargs="?", help="Path to snapshot .tar.gz")
    rest_parser.add_argument("--mode", choices=("replace", "merge"))
    rest_parser.add_argument("--dry-run", action="store_true")
    rest_parser.add_argument("--components", help="Comma-separated components to restore")
    rest_parser.add_argument("--list-components", action="store_true")
    rest_parser.add_argument(
        "--force", action="store_true", help="Restore even if gateway is running"
    )

    # security
    sec_parser = sub.add_parser("security", help="Security audit and deny list")

    # eval (benchmark harness)
    eval_parser = sub.add_parser(
        "eval",
        help="Run multi-session evaluation scenarios",
        epilog="""
Examples:
  personalclaw eval                         # smoke test (~30s)
  personalclaw eval memory_recall_basic     # specific scenario
  personalclaw eval --all                   # all scenarios (slow)
""",
        formatter_class=_fmt,
    )
    eval_parser.add_argument(
        "scenarios",
        nargs="*",
        default=[],
        help="Scenario names to run (without extension). Default: smoke_test",
    )
    eval_parser.add_argument(
        "--all", action="store_true", dest="all_scenarios", help="Run all scenarios"
    )
    eval_parser.add_argument("--judge", action="store_true", help="Enable LLM judge scoring")

    sec_sub = sec_parser.add_subparsers(dest="sec_action")
    sec_sub.add_parser("audit", help="Scan conversation history for suspicious tool usage")
    sec_sub.add_parser("deny-list", help="Show active deny patterns")
    sel_parser = sec_sub.add_parser("events", help="Show recent security event log entries")
    sel_parser.add_argument("-n", "--limit", type=int, default=20, help="Number of entries")
    sec_sub.add_parser("verify", help="Verify security event log HMAC integrity")

    sub.add_parser("update", help="Update PersonalClaw to the latest version")

    # stop
    stop_parser = sub.add_parser("stop", help="Stop a running PersonalClaw gateway")
    stop_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Dashboard port (default: resolved from PERSONALCLAW_PORT env or dashboard.url config)",
    )

    # restart
    restart_parser = sub.add_parser(
        "restart",
        help="Restart the PersonalClaw gateway (service if installed, else foreground)",
    )
    restart_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Dashboard port (default: resolved from PERSONALCLAW_PORT env or dashboard.url config)",
    )

    # consolidate — run skill/memory extraction over a session's transcript on
    # demand (the same path the idle poll and session-end triggers use).
    consolidate_parser = sub.add_parser(
        "consolidate",
        help="Extract skills/memory from a session transcript now",
    )
    consolidate_group = consolidate_parser.add_mutually_exclusive_group(required=True)
    consolidate_group.add_argument(
        "key", nargs="?", default=None, help="Session key to consolidate"
    )
    consolidate_group.add_argument(
        "--all", action="store_true", help="Consolidate every known session"
    )

    # service — install/uninstall/status as a system-level systemd unit (Linux,
    # /etc/systemd/system/, requires sudo) or launchd LaunchAgent (macOS,
    # ~/Library/LaunchAgents/, no sudo) so the gateway survives SSH disconnect,
    # auto-restarts on crash, and auto-starts on boot.
    svc_parser = sub.add_parser(
        "service",
        help="Manage the PersonalClaw gateway as a system service (requires sudo on Linux)",
    )
    svc_sub = svc_parser.add_subparsers(dest="service_action")
    svc_sub.add_parser("install", help="Install and start the gateway service (sudo on Linux)")
    svc_sub.add_parser("uninstall", help="Stop and remove the gateway service (sudo on Linux)")
    svc_sub.add_parser("status", help="Show service status (systemctl/launchctl)")

    # logs — tail the gateway log. Reads from the systemd journal when running
    # as a service on Linux, the launchd stdout file on macOS, or the
    # foreground gateway log file otherwise.
    logs_parser = sub.add_parser("logs", help="Show gateway logs")
    logs_parser.add_argument(
        "-f", "--follow", action="store_true", help="Follow log output (live tail)"
    )
    logs_parser.add_argument(
        "-n", "--lines", type=int, default=100, help="Number of lines to show (default: 100)"
    )

    # token
    token_parser = sub.add_parser("token", help="Print a dashboard access URL with auth token")

    # logout
    logout_parser = sub.add_parser("logout", help="Revoke all active dashboard sessions")
    logout_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Dashboard port (default: resolved from PERSONALCLAW_PORT env or dashboard.url config)",
    )
    token_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Dashboard port (default: resolved from PERSONALCLAW_PORT env or dashboard.url config)",
    )
    token_parser.add_argument("--ttl", default="20h", help="Token TTL, e.g. 1h, 30m (default: 20h)")

    # status
    status_parser = sub.add_parser("status", help="Show runtime stats")
    status_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Dashboard port (default: resolved from PERSONALCLAW_PORT env or dashboard.url config)",
    )

    # mcp-schedule (MCP server — spawned by ACP agent, not user-facing)
    sub.add_parser("mcp-schedule", help=argparse.SUPPRESS)

    # mcp-core (MCP server — spawned by ACP agent, not user-facing)
    sub.add_parser("mcp-core", help=argparse.SUPPRESS)

    # learn
    learn_parser = sub.add_parser(
        "learn",
        help="Save or manage learned corrections",
        epilog="""
Examples:
  personalclaw learn list
  personalclaw learn add 'use snake_case for variables' --category tool
  personalclaw learn remove 'snake_case'
""",
        formatter_class=_fmt,
    )
    learn_sub = learn_parser.add_subparsers(dest="learn_action")
    memory_remember = learn_sub.add_parser("add", help="Save a lesson")
    memory_remember.add_argument("rule", help="The rule or correction to remember")
    memory_remember.add_argument(
        "--category",
        choices=["tool", "preference", "knowledge"],
        default="knowledge",
        help="Lesson category (default: knowledge)",
    )
    memory_remember.add_argument("--negative", help="What NOT to do (optional)")
    learn_sub.add_parser("list", help="List all lessons")
    learn_rm = learn_sub.add_parser("remove", help="Remove lessons matching a substring")
    learn_rm.add_argument("query", help="Substring to match against lesson rules")

    # Memory
    mem_parser = sub.add_parser("memory", help="Manage vector memory system")
    mem_sub = mem_parser.add_subparsers(dest="mem_action")
    mem_sub.add_parser("list", help="Show semantic memory entries")
    mem_search = mem_sub.add_parser("search", help="Search episodic memories")
    mem_search.add_argument("query", help="Search query text")
    mem_sub.add_parser("stats", help="Show memory statistics")
    mem_sub.add_parser("audit", help="Scan memory for suspicious content")
    mem_export = mem_sub.add_parser("export", help="Export all memory to JSON")
    mem_export.add_argument("--output", "-o", help="Output file (default: stdout)")
    mem_sub.add_parser("migrate", help="Migrate legacy markdown memory to vector store")
    mem_import = mem_sub.add_parser("import", help="Import memory from JSON file")
    mem_import.add_argument("file", help="Path to JSON file (export format)")

    # agent
    agent_parser = sub.add_parser("agent", help="Manage PersonalClaw agent definitions")
    agent_sub = agent_parser.add_subparsers(dest="agent_action")
    agent_sub.add_parser("list", help="List PersonalClaw agents")
    agent_create = agent_sub.add_parser("create", help="Create a PersonalClaw agent")
    agent_create.add_argument("--name", required=True, help="Agent name")
    agent_create.add_argument(
        "--provider-agent",
        default="personalclaw",
        dest="provider_agent",
        help="Provider agent name",
    )
    agent_create.add_argument(
        "--default-dir",
        default="",
        dest="default_dir",
        help="Default working directory path (blank = workspace root)",
    )
    agent_create.add_argument("--memory-store", default="default", help="Memory store name")
    agent_update = agent_sub.add_parser("update", help="Update a PersonalClaw agent")
    agent_update.add_argument("name", help="Agent name to update")
    agent_update.add_argument(
        "--provider-agent",
        dest="provider_agent",
        help="New provider agent name",
    )
    agent_update.add_argument(
        "--default-dir",
        dest="default_dir",
        help="New default working directory path",
    )
    agent_update.add_argument("--memory-store", help="New memory store name")
    agent_delete = agent_sub.add_parser("delete", help="Delete a PersonalClaw agent")
    agent_delete.add_argument("name", help="Agent name to delete")

    # config
    cfg_parser = sub.add_parser(
        "config",
        help="Get or set configuration values",
        epilog="""
Examples:
  personalclaw config get                   # Show all config
  personalclaw config get dashboard.port    # Get specific value
  personalclaw config set dashboard.port 8888
  personalclaw config edit                  # Open in $EDITOR
""",
        formatter_class=_fmt,
    )
    cfg_sub = cfg_parser.add_subparsers(dest="config_action")
    cfg_get = cfg_sub.add_parser("get", help="Get a config value (or all if no key)")
    cfg_get.add_argument("key", nargs="?", help="Dot-separated key (e.g. dashboard.port)")
    cfg_set = cfg_sub.add_parser("set", help="Set a config value")
    cfg_set.add_argument("key", nargs="?", help="Dot-separated key (e.g. dashboard.port)")
    cfg_set.add_argument("value", nargs="?", help="Value to set")
    cfg_set.add_argument("--file", "-f", dest="file", help="Load full config from a JSON file")
    cfg_sub.add_parser("edit", help="Open config in $EDITOR")

    # skills
    skills_parser = sub.add_parser("skills", help="Manage skills from the skills marketplace")
    skills_sub = skills_parser.add_subparsers(dest="skills_command")
    skills_sub.add_parser("list", help="List locally installed skills")
    skills_search = skills_sub.add_parser("search", help="Search skills.sh marketplace")
    skills_search.add_argument("query", help="Search query")
    skills_search.add_argument(
        "--marketplace", default="skills.sh", help="Marketplace to search (default: skills.sh)"
    )
    skills_install = skills_sub.add_parser("install", help="Install a skill")
    skills_install.add_argument("id", help="Skill ID, e.g. vercel-labs/agent-skills/next-js")
    skills_install.add_argument(
        "--marketplace", default="skills.sh", help="Marketplace to install from"
    )
    skills_install.add_argument(
        "--target", default="", help="Install directory (default: ~/.agents/skills/)"
    )
    skills_install.add_argument(
        "--force",
        action="store_true",
        help="Install despite an overridable WARNING verdict from the supply-chain scan. "
        "A DANGEROUS verdict is never overridable.",
    )
    skills_remove = skills_sub.add_parser("remove", help="Remove a locally installed skill")
    skills_remove.add_argument("name", help="Skill directory name to remove")
    skills_curate = skills_sub.add_parser(
        "curate", help="Groom the auto/ skill library (age active→stale→archived by last-use)"
    )
    skills_curate.add_argument(
        "--dry-run", action="store_true", help="Report what would change without writing"
    )
    skills_sub.add_parser(
        "verify",
        help="Check installed skills' file hashes against their install baseline "
        "(.pclaw-lock.json) — detects a skill mutated/tampered after install",
    )

    args = parser.parse_args()

    # ``gateway --seed <fixture>`` populates $PERSONALCLAW_HOME from a hand-authored
    # fixture BEFORE the gateway starts — lets a dev spin up a pre-populated
    # server in one command. We run the seed here (post parse_args, but BEFORE
    # ``AppConfig.load()`` and the file-log handler attach at line ~603):
    # both of those call ``config_dir()`` which ``mkdir``s $PERSONALCLAW_HOME, which
    # would pre-populate the target and break ``shutil.copytree``'s
    # empty-target-only contract.  If seed fails, exit with the
    # seed's own exit code instead of continuing into the gateway — running
    # the gateway against a half-seeded or wrong-state $PERSONALCLAW_HOME would be
    # worse than a clean failure.
    #
    # ``is not None`` (not truthiness): argparse assigns ``""`` when the user
    # explicitly passes ``--seed ""``, and ``""`` is falsy. A truthiness check
    # would silently start the gateway without seeding — exactly the silent
    # wrong-state startup the rest of this block is set up to avoid.
    # ``_resolve_fixture("")`` has an explicit rail for this case.
    if args.command == "gateway" and getattr(args, "seed", None) is not None:
        _rc = seed_cmd(args)
        if _rc != 0:
            sys.exit(_rc)

    if args.verbose >= 2:
        level = logging.DEBUG
    elif args.verbose >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=logging.WARNING,  # third-party libs stay quiet
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # PersonalClaw loggers: --verbose CLI flag takes precedence, otherwise
    # fall back to the persistent log_level from config.
    if args.verbose == 0:
        try:
            _cfg = AppConfig.load()
            _persisted = _cfg.agent.log_level.upper()
            level = getattr(logging, _persisted, logging.WARNING)
        except Exception:
            pass  # config missing or corrupt — keep default WARNING
    logging.getLogger("personalclaw").setLevel(level)
    # App bundles log under their OWN top-level namespace (e.g. ``slack_runtime``),
    # not ``personalclaw`` — so the level + file handler below are applied to each
    # loaded app's logger root too, or an app's operational logs would be invisible.
    # Noisy third-party libs stay at WARNING (pinned below).
    from personalclaw.apps.catalog import installed_logger_roots as _installed_logger_roots

    _APP_LOGGER_ROOTS = _installed_logger_roots()
    for _lname in _APP_LOGGER_ROOTS:
        logging.getLogger(_lname).setLevel(level)
    for _noisy in ("slack_sdk", "aiohttp", "urllib3", "asyncio"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    # Persistent file log — respects the configured log_level
    _log_file = config_dir() / "gateway.log"
    _fh = RotatingFileHandler(_log_file, maxBytes=2 * 1024 * 1024, backupCount=3)
    _fh.setLevel(level)
    _fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    logging.getLogger("personalclaw").addHandler(_fh)
    for _lname in _APP_LOGGER_ROOTS:
        logging.getLogger(_lname).addHandler(_fh)

    if args.command == "chat":
        asyncio.run(_chat(args.message, args.model))
    elif args.command == "gateway":
        gw_kwargs = _resolve_gateway_args(args)
        asyncio.run(_gateway(**gw_kwargs))
    elif args.command == "setup":
        _setup(
            agent_only=getattr(args, "agent_only", False),
            clean=getattr(args, "clean", False),
            mode=getattr(args, "mode", ""),
            provider=getattr(args, "provider", ""),
            credential=getattr(args, "credential", ""),
            only_app=getattr(args, "app", ""),
        )
    elif args.command == "doctor":
        _doctor()
    elif args.command == "cron":
        _cron(args)
    elif args.command == "spawn":
        _spawn(args)
    elif args.command == "learn":
        _learn(args)
    elif args.command == "memory":
        _memory_cmd(args)
    elif args.command == "mcp-schedule":
        from personalclaw.mcp_schedule import run_mcp_server as run_mcp_schedule_server

        run_mcp_schedule_server()
    elif args.command == "mcp-core":
        from personalclaw.mcp_core import run_mcp_core_server

        run_mcp_core_server()
    elif args.command == "eval":
        asyncio.run(_run_eval(args))
    elif args.command == "security":
        _security(args)
    elif args.command == "update":
        _update()
    elif args.command == "stop":
        _stop(resolve_client_port(args.port))
    elif args.command == "restart":
        _restart(resolve_client_port(args.port))
    elif args.command == "consolidate":
        asyncio.run(_consolidate_cmd(args))
    elif args.command == "service":
        sys.exit(_service_cmd(args))
    elif args.command == "logs":
        _logs_cmd(args)
    elif args.command == "token":
        _token(args)
    elif args.command == "logout":
        _logout(resolve_client_port(args.port))
    elif args.command == "status":
        _status(args)
    elif args.command == "config":
        _config_cmd(args)
    elif args.command == "snapshot":
        from personalclaw.snapshot import snapshot_main

        rc = snapshot_main(parsed=args)
        if rc:
            raise SystemExit(rc)
    elif args.command == "restore":
        from personalclaw.snapshot import restore_main

        rc = restore_main(parsed=args)
        if rc:
            raise SystemExit(rc)
    elif args.command == "agent":
        _handle_agent(args)
    elif args.command == "skills":
        _handle_skills(args)
    else:
        print(BANNER)
        parser.print_help()


# ── Config ──


from personalclaw.cli_chat import _chat  # noqa: E402
from personalclaw.cli_commands import (  # noqa: E402
    _cron,
    _handle_agent,
    _learn,
    _memory_cmd,
    _run_eval,
    _security,
    _spawn,
)
from personalclaw.cli_config import _config_cmd  # noqa: E402
from personalclaw.cli_doctor import _doctor  # noqa: E402
from personalclaw.cli_server import (  # noqa: E402
    _consolidate_cmd,
    _gateway,
    _logout,
    _logs_cmd,
    _restart,
    _service_cmd,
    _status,
    _stop,
    _token,
    _update,
    resolve_client_port,
)
from personalclaw.cli_setup import (  # noqa: E402
    _setup,
)


def _handle_skills(args) -> None:  # noqa: ANN001
    """Dispatch personalclaw skills subcommands."""
    import shutil
    from pathlib import Path

    from personalclaw.agent import _all_skill_paths

    # skills.sh moved to a standalone app (apps/skills-sh/); it registers via the app
    # loader when installed, so core no longer eager-imports it here.
    from personalclaw.skills.marketplace import (
        DEFAULT_SKILLS_INSTALL_PATH,
        get_default_skills_registry,
        list_local_skills,
    )

    cmd = getattr(args, "skills_command", None)

    if cmd == "list" or cmd is None:
        skills = list_local_skills()
        if not skills:
            print("No skills installed. Run: personalclaw skills install <id>")
            return
        for s in skills:
            desc = s["description"]
            print(f"  {s['name']:<24} {desc[:60]}")
        return

    if cmd == "search":
        query = args.query
        marketplace_name = getattr(args, "marketplace", "skills.sh")
        try:
            mp = get_default_skills_registry().get(marketplace_name)
        except KeyError:
            print(f"❌ Marketplace '{marketplace_name}' not registered")
            return
        results = mp.search(query)
        if not results:
            print(f"No results for '{query}' on {marketplace_name}")
            return
        for r in results:
            print(f"  {r.id:<40} {r.description[:50]}")
        return

    if cmd == "install":
        skill_id = args.id
        marketplace_name = getattr(args, "marketplace", "skills.sh")
        target_str = getattr(args, "target", "")
        target = Path(target_str) if target_str else DEFAULT_SKILLS_INSTALL_PATH
        force = bool(getattr(args, "force", False))
        from personalclaw.skills.marketplace import SkillInstallRefused

        registry = get_default_skills_registry()
        try:
            registry.get(marketplace_name)
        except KeyError:
            print(f"❌ Marketplace '{marketplace_name}' not registered")
            return
        try:
            result = registry.install_guarded(marketplace_name, skill_id, target, force=force)
            n = len(result.report.findings)
            note = f" (scanned, tier={result.tier.value}" + (f", {n} finding(s))" if n else ")")
            print(f"✅ Installed: {result.path}{note}")
        except SkillInstallRefused as exc:
            print(f"❌ Install refused: {exc}")
            if not exc.dangerous:
                print("   This is an overridable warning — re-run with --force to install anyway.")
            else:
                print("   This is a dangerous verdict — it cannot be force-installed.")
            for f in exc.report.findings[:8]:
                print(
                    f"     - [{f.severity.value}] {f.rule} in {f.path or '(content)'}: {f.evidence[:80]}"
                )
        except Exception as exc:
            print(f"❌ Install failed: {exc}")
        return

    if cmd == "remove":
        name = args.name
        removed = False
        for base_str in _all_skill_paths():
            skill_dir = Path(base_str) / name
            if skill_dir.is_dir():
                shutil.rmtree(skill_dir)
                print(f"✅ Removed: {skill_dir}")
                removed = True
                break
        if not removed:
            print(f"❌ Skill '{name}' not found")
        return

    if cmd == "curate":
        from personalclaw.skills.curator import run_aging

        report = run_aging(dry_run=getattr(args, "dry_run", False))
        print(report.summary())
        for name in report.to_archived:
            print(f"  archived: {name}")
        for name in report.to_stale:
            print(f"  stale:    {name}")
        for name in report.reactivated:
            print(f"  active:   {name}")
        return

    if cmd == "verify":
        from personalclaw.skills.loader import skills_dir
        from personalclaw.skills.marketplace import verify_skill_integrity

        root = skills_dir()
        dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
        if not dirs:
            print("No installed skills to verify.")
            return
        tampered = 0
        for d in dirs:
            rep = verify_skill_integrity(d)
            # unlocked FIRST: an unverifiable skill (no baseline) is neither pass nor
            # fail — a green check would falsely imply "verified intact".
            mark = "·" if rep.unlocked else ("✅" if rep.ok else "⚠️")
            print(f"  {mark} {rep.summary()}")
            for f in rep.mutated:
                print(f"       mutated: {f}")
            for f in rep.missing:
                print(f"       missing: {f}")
            for f in rep.added:
                print(f"       added:   {f}")
            if not rep.ok and not rep.unlocked:
                tampered += 1
        print(f"\n{len(dirs)} skill(s) checked, {tampered} tampered.")
        return

    print("Usage: personalclaw skills [list|search|install|remove|curate|verify]")
