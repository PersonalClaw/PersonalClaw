"""CLI doctor subcommand — verify PersonalClaw setup and diagnose issues."""

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from personalclaw import __version__ as _pc_version
from personalclaw.agent import AGENT_FILENAME, AGENTS_DIR
from personalclaw.config import AppConfig
from personalclaw.config.loader import config_dir
from personalclaw.dashboard.origin import (
    is_local_bind,
    machine_hostname,
    parse_dashboard_url,
    resolve_bind_host,
)
from personalclaw.transcribe import ensure_ffmpeg_in_path

_MIN_NODE_VERSION = 18


def _doctor_providers() -> list[str]:
    """Run a health probe for each registered ProviderEntry.

    For ``acp_agent`` entries, spawns the configured command and completes
    the ACP ``initialize`` handshake.  For other entries, performs
    a lightweight capability check (import test + credential presence).
    Returns a list of issue strings for any entry that fails.
    """
    issues: list[str] = []
    try:
        from personalclaw.llm.registry import get_default_registry

        # Import the provider modules to trigger their registry.register_type()
        # calls so that all built-in types are visible.
        import personalclaw.llm.acp_agent  # noqa: F401

        registry = get_default_registry()
        entries = registry.list_entries()
    except Exception as exc:
        print(f"  registry:    ⚠️  could not load ({exc})")
        return issues

    if not entries:
        print("  entries:     ⏹  no provider entries configured")
        return issues

    for entry in entries:
        label = f"{entry.name} ({entry.type})"
        if entry.type == "acp_agent":
            _probe_acp_agent(entry, label, issues)
        else:
            # Any model provider type (ollama core-native, or an installed model
            # app: openai/anthropic/vllm/bedrock/…). The type is shown in the label;
            # no hardcoded core-native allow-list (that list went stale when the
            # model providers became apps).
            print(f"  {label}: ✅ registered")

    return issues


def _probe_acp_agent(entry: object, label: str, issues: list[str]) -> None:
    """Probe the acp_agent entry's readiness via the shared readiness probe."""
    import asyncio

    from personalclaw.llm.acp_agent import AcpAgentProvider

    options = getattr(entry, "options", {}) or {}

    try:
        status = asyncio.run(AcpAgentProvider.probe_readiness(options))
    except Exception as exc:
        print(f"  {label}: ⚠️  could not probe ({exc})")
        return

    icon = {
        "ready": "✅", "not_found": "❌", "needs_login": "🔑",
        "timeout": "⏳", "error": "❌",
    }.get(status.state, "⚠️")
    print(f"  {label}: {icon} {status.detail}")
    if not status.ready:
        issues.append(f"{label}: {status.state}")


def _doctor() -> None:
    """Verify PersonalClaw setup — check dependencies, config, credentials, connectivity."""

    print("PersonalClaw Doctor\n")
    issues: list[str] = []

    # ── Dependencies ──
    print("Dependencies")

    git = shutil.which("git")
    if git:
        print(f"  git:         ✅ {git}")
    else:
        print("  git:         ❌ not found (needed for personalclaw update)")
        issues.append("git")

    node = shutil.which("node")
    if node:
        try:
            node_ver_result = subprocess.run(
                ["node", "-v"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            major = int(node_ver_result.stdout.strip().lstrip("v").split(".")[0])
            if major >= _MIN_NODE_VERSION:
                print(f"  node:        ✅ {node} (v{major})")
            else:
                print(
                    f"  node:        ⚠️  v{major} < {_MIN_NODE_VERSION} (frontend needs Node {_MIN_NODE_VERSION}+)"
                )
                print("               Fix: install Node.js >= 16")
        except Exception:
            print(f"  node:        ✅ {node}")
    else:
        print(f"  node:        ⚠️  not found (frontend needs Node {_MIN_NODE_VERSION}+)")
        print("               Fix: install Node.js >= 16")

    # Unified venv detection — used for runtime section
    venv_py = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python3"
    is_venv_install = venv_py.is_file()

    # ── Project ──
    print("\nProject")
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    stale_project = False
    if not proj:
        # Check saved project_dir file
        saved_proj = config_dir() / "project_dir"
        if saved_proj.is_file():
            saved = saved_proj.read_text(encoding="utf-8").strip()
            if saved and Path(saved).is_dir():
                proj = saved
            else:
                print(f"  project dir: ❌ stale — points to deleted {saved}")
                print(f"               Fix: rm {config_dir() / 'project_dir'}")
                issues.append("stale project_dir")
                stale_project = True
    if proj and Path(proj).is_dir():
        print(f"  project dir: ✅ {proj}")
        git_dir = Path(proj) / ".git"
        if git_dir.is_dir():
            print("  git repo:    ✅")
        else:
            print("  git repo:    ⚠️  not a git repo")
    elif not stale_project:
        print("  project dir: ⚠️  not set (run personalclaw setup from project root)")

    # ── Agent config ──
    print("\nAgent")
    agent_path = AGENTS_DIR / AGENT_FILENAME
    if agent_path.exists():
        print(f"  config:      ✅ {agent_path}")
    else:
        print("  config:      ❌ not found (run personalclaw setup)")
        issues.append("agent config")

    # ── Config ──
    print("\nConfiguration")
    cfg_dir = config_dir()
    cfg = AppConfig.load()
    if cfg_dir.exists():
        print(f"  config dir:  ✅ {cfg_dir}")
    else:
        print(f"  config dir:  📁 {cfg_dir} (will be created)")
    print(f"  provider:    {cfg.agent.provider}")
    # The chat model is governed by active_models.json (Settings → Models),
    # not a config field — report the live binding.
    try:
        from personalclaw.providers.use_cases import active_model_refs

        _refs = active_model_refs("chat")
        print(f"  chat model:  {_refs[0] if _refs else '(none bound)'}")
    except Exception:
        print("  chat model:  (unresolved)")
    print(f"  approval:    {cfg.agent.approval_mode}")
    _host: str = ""
    _port: int | None = None
    try:
        _host, _port = parse_dashboard_url(cfg.dashboard.url)
    except Exception:
        print("  dashboard:   ⚠️  cannot parse dashboard URL from config")
        issues.append("dashboard URL misconfigured")
    _display_host = _host or "localhost"
    if _port:
        print(f"  dashboard:   http://{_display_host}:{_port}")

    # Dashboard auth mode
    creds = cfg.load_credentials()
    _has_slack = bool(creds.get("SLACK_APP_TOKEN") and creds.get("SLACK_BOT_TOKEN"))
    _local = is_local_bind(resolve_bind_host())
    if _local:
        print("  bind:        127.0.0.1 (local-only, SSH tunnel for remote)")
        print("  auth:        loopback trusted (no token required)")
    else:
        print("  bind:        0.0.0.0 (all interfaces)")
        print("  auth:        ✅ token auth required (via !dashboard)")
        if not _has_slack:
            print("  auth:        ⚠️  no channel configured — token generation unavailable")
            issues.append("dashboard auth: remote bind without a channel")

    # ── MCP Tools ──
    print("\nMCP Tools")
    if agent_path.exists():

        try:
            agent_data = json.loads(agent_path.read_text(encoding="utf-8"))
        except Exception:
            agent_data = {}
        tools = agent_data.get("tools", [])
        allowed = agent_data.get("allowedTools", [])
        mcps = agent_data.get("mcpServers", {})
        mcp_fixed = False
        mcp_cmd_fixed = False
        for ref in ("@personalclaw-schedule", "@personalclaw-core"):
            name = ref[1:]
            in_tools = ref in tools
            in_allowed = ref in allowed
            in_servers = name in mcps
            if in_tools and in_allowed and in_servers:
                cmd = mcps[name].get("command", "")
                exists = Path(cmd).is_file() if cmd else False
                if exists:
                    print(f"  {ref}: ✅")
                else:
                    resolved = shutil.which("personalclaw")
                    if resolved:
                        mcps[name]["command"] = resolved
                        mcp_cmd_fixed = True
                        print(f"  {ref}: 🔧 fixed stale path: {cmd} → {resolved}")
                    else:
                        print(f"  {ref}: ❌ binary not found: {cmd}")
                        issues.append(f"{ref} binary")
            else:
                missing: list[str] = []
                if not in_servers:
                    missing.append("mcpServers")
                if not in_tools:
                    missing.append("tools")
                if not in_allowed:
                    missing.append("allowedTools")
                print(f"  {ref}: ❌ missing from {', '.join(missing)}")
                issues.append(f"{ref} config")
                # Auto-fix
                if not in_tools:
                    tools.append(ref)
                if not in_allowed:
                    allowed.append(ref)
                mcp_fixed = True
        if mcp_fixed or mcp_cmd_fixed:
            agent_data["tools"] = tools
            agent_data["allowedTools"] = allowed
            agent_path.write_text(json.dumps(agent_data, indent=2) + "\n", encoding="utf-8")
            if mcp_fixed:
                print("  → Auto-fixed tools/allowedTools in personalclaw.json")
                issues = [i for i in issues if "config" not in i]
            if mcp_cmd_fixed:
                print("  → Auto-fixed stale binary path(s) in personalclaw.json")

    # ── Python Runtime ──
    print("\nRuntime")
    print(f"  python:      ✅ {sys.executable} ({sys.version.split()[0]})")
    print(f"  backend:   ✅ {_pc_version}")
    if is_venv_install:
        try:
            py_result = subprocess.run(
                [str(venv_py), "--version"], capture_output=True, text=True, timeout=5
            )
            py_result.check_returncode()
            ver = py_result.stdout.strip()
            print(f"  python:      ✅ {venv_py} ({ver})")
        except Exception as exc:
            print(f"  python:      ❌ venv python broken: {exc}")
            issues.append("venv python")
        else:
            try:
                subprocess.run(
                    [str(venv_py), "-c", "import websockets, slack_sdk, aiohttp"],
                    capture_output=True,
                    timeout=5,
                ).check_returncode()
                print("  deps:        ✅ websockets, slack_sdk, aiohttp available")
            except Exception:
                print("  deps:        ❌ missing modules (websockets/slack_sdk/aiohttp)")
                issues.append("python deps")
    else:
        # Non-venv install: fall back to checking the system python.
        sys_py = shutil.which("python3")
        if sys_py:
            py_result = subprocess.run(
                [sys_py, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            ver = py_result.stdout.strip()
            print(f"  fallback:    ⚠️  {sys_py} ({ver})")
            try:
                subprocess.run(
                    [sys_py, "-c", "import websockets, slack_sdk, aiohttp"],
                    capture_output=True,
                    timeout=5,
                ).check_returncode()
                print("  deps:        ✅ websockets, slack_sdk, aiohttp available")
            except Exception:
                print("  deps:        ❌ missing modules" " (websockets/slack_sdk/aiohttp)")
                issues.append("python deps")
        else:
            print("  python:      ⚠️  python3 not found on PATH")

    # ── Vector Memory / embeddings ──
    # Provider-agnostic: model providers (incl. Ollama, now the ollama-models app)
    # report their own availability via the Provider Health section above + each
    # app's availability() probe. Core's doctor no longer special-cases any vendor's
    # binary/install here — it just reports whether an embedding model is selected.
    print("\nVector Memory")
    from personalclaw.embedding_providers.registry import _active_embedding_spec
    if _active_embedding_spec():
        print("  embeddings:  ✅ enabled")
    else:
        print("  embeddings:  ⏹ disabled (pick an embedding model in Settings → Models)")

    # ── Speech-to-Text ──
    # STT resolves through the typed registry: enabled lives in
    # use_case_settings/stt.json, the active model in active_models.json.
    print("\nSpeech-to-Text")
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.stt.registry import active_stt

    stt_active = bool(load_use_case_settings("stt").get("enabled", True))
    resolved = active_stt()

    if not stt_active:
        print("  status:      ⏹ disabled (enable in Settings → Voice)")
    elif resolved is None:
        # Not a failure: STT backends are opt-in apps now (e.g. the faster-whisper
        # app) plus remote OpenAI-family providers. With none installed/bound, STT is
        # simply unconfigured — report it, but don't fail the doctor (a fresh core is
        # expected to boot without media backends).
        print("  status:      ⏹  no STT model configured (install an STT app or bind one in Settings → Models)")
    else:
        print(f"  model:       ✅ {resolved[0].name}:{resolved[1]}")

    ensure_ffmpeg_in_path()
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        print(f"  ffmpeg:      ✅ {ffmpeg_bin}")
    elif stt_active:
        print("  ffmpeg:      ❌ not found")
        print("               Fix: brew install ffmpeg")
        issues.append("ffmpeg")
    else:
        print("  ffmpeg:      ⏭  not installed (not needed)")

    # faster-whisper runtime dep. Declared in pyproject.toml extras, but a dev
    # environment created before the dep landed may not have it until
    # `pip install -e .[stt]` is re-run. Catching that here avoids a blank mic
    # click at runtime.
    if stt_active and resolved is not None:
        try:
            import faster_whisper  # noqa: F401

            print("  faster_whisper: ✅ importable")
        except ImportError:
            print("  faster_whisper: ❌ missing")
            print("               Fix: pip install faster-whisper")
            issues.append("faster_whisper missing")

    # ── Slack channel app (optional) ──
    # Presence check only: the slack-channel app's token/owner credentials in the
    # generic cred store. Live workspace validation (auth.test / origin binding)
    # is owned by the app and surfaced via its "Test" action on the Channels
    # page — core's doctor does not import the app's runtime.
    print("\nSlack Channel App")
    creds = cfg.load_credentials()
    has_slack = bool(creds.get("SLACK_APP_TOKEN") and creds.get("SLACK_BOT_TOKEN"))
    if has_slack:
        has_owner = bool(creds.get("PERSONALCLAW_OWNER_ID"))
        print("  tokens:      ✅ configured")
        if has_owner:
            print(f"  owner:       ✅ {creds['PERSONALCLAW_OWNER_ID']}")
        else:
            print("  owner:       ⚠️  PERSONALCLAW_OWNER_ID not set")
        print("  workspace:   ℹ️  use the Channels page → Slack → Test to verify the token")
    else:
        print("  status:      ⏭  not configured (dashboard-only mode)")
        print("  setup:       run 'personalclaw setup' to add Slack tokens")

    # ── Provider Health ──
    print("\nProvider Health")
    _provider_issues = _doctor_providers()
    issues.extend(_provider_issues)

    # ── Connectivity ──
    print("\nConnectivity")
    # Check if gateway is running — connect to 127.0.0.1 (loopback)
    # to avoid DNS resolution issues with the configured hostname.
    # Any HTTP response (even 401/403 from token auth) means the gateway is up.
    is_remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))

    if _port:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{_port}/api/status")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
            print(f"  gateway:     ✅ running (uptime {data.get('uptime', '?')})")
        except urllib.error.HTTPError as he:
            # 401/403 means gateway is running but requires token auth
            if he.code in (401, 403):
                print("  gateway:     ✅ running (token auth enabled)")
            else:
                print(f"  gateway:     ⚠️  HTTP {he.code}")
        except (urllib.error.URLError, OSError):
            print("  gateway:     ⏹  not running")
        except Exception:
            print("  gateway:     ⚠️  running but returned unexpected response")

        # SSH tunnel hint for remote hosts
        if is_remote:
            mh = machine_hostname() or "this-host"
            print("\n  💡 Remote access: Run on your LOCAL machine:")
            print(f"     ssh -L {_port}:localhost:{_port} {mh}")
            print("     Then run: personalclaw token")

    # Verify token auth is enforced on non-loopback (security check)
    if _port and not _local:
        if not _host:
            issues.append("cannot verify dashboard auth (host unknown)")
        else:
            try:
                ext_req = urllib.request.Request(f"http://{_host}:{_port}/api/status")
                try:
                    with urllib.request.urlopen(ext_req, timeout=2) as resp:
                        # 200 without token = auth is NOT enforced
                        print("  auth check:  ❌ external access allowed without token!")
                        issues.append("dashboard auth: no token required on external interface")
                except urllib.error.HTTPError as he:
                    if he.code in (401, 403):
                        print("  auth check:  ✅ token required on external interface")
                    else:
                        print(f"  auth check:  ⚠️  HTTP {he.code}")
            except Exception:
                print("  auth check:  ⏭  could not reach external interface")

    # ── Summary ──
    print()
    if issues:
        print(f"❌ Fix these issues: {', '.join(issues)}")
        sys.exit(1)
    else:
        print("✅ PersonalClaw is ready!")
