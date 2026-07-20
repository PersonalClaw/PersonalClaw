"""CLI setup subcommand — interactive credential and config wizard."""

import json
import os
import socket
from pathlib import Path
from zoneinfo import ZoneInfo

from personalclaw.app_cli import run_app_setup_steps
from personalclaw.atomic_write import atomic_write
from personalclaw.cli_chat import _ensure_default_agent_in_config
from personalclaw.config import AppConfig
from personalclaw.config.loader import (
    _WORKSPACE_DIR_NAME,
    DASHBOARD_PORT,
    _default_workspace_base,
    _workspace_dir_file,
    config_dir,
    config_path,
    env_path,
)
from personalclaw.constants import DATA_WARNING
from personalclaw.orchestrator_skill import generate_orchestrator_skill
from personalclaw.skills import SkillsLoader


def _fix_shell_profiles() -> None:
    """Remove stale PersonalClaw PATH entries from shell profiles."""
    home = Path.home()
    profiles = [
        home / ".zshrc",
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
    ]
    stale_markers = [
        ".personalclaw-app",
        "PersonalClaw/src/PersonalClaw/bin",
        "PersonalClaw/build/",
        "workspaces/PersonalClaw",
    ]
    cleaned_profiles: list[str] = []
    for profile in profiles:
        if not profile.is_file():
            continue
        try:
            lines = profile.read_text(encoding="utf-8").splitlines(keepends=True)
            cleaned = []
            removed = False
            for line in lines:
                if any(m in line for m in stale_markers) and "PATH" in line:
                    removed = True
                    continue
                cleaned.append(line)
            if removed:
                profile.write_text("".join(cleaned), encoding="utf-8")
                print(f"  🔧 Cleaned stale PersonalClaw PATH from {profile.name}")
                cleaned_profiles.append(profile.name)
        except OSError:
            pass
    if cleaned_profiles:
        sources = " or ".join(f"`source ~/{p}`" for p in cleaned_profiles)
        print(f"  ⚠️  Run {sources} or open a new terminal for PATH changes to take effect.")


def _setup(
    agent_only: bool = False,
    clean: bool = False,
    mode: str = "",
    provider: str = "",
    credential: str = "",
    only_app: str = "",
) -> None:
    """Install agent config and optionally configure credentials.

    ``mode`` selects the deployment model: ``service`` (systemd/launchd, default),
    ``docker`` (Compose-based), or ``none`` (manual / CI).
    ``provider`` wires a named registry entry as the default chat provider.
    ``credential`` registers a named credential in the credential store.
    ``only_app`` runs ONLY that installed app's ``cli.setup`` step, skipping the
    core steps and every other app (``personalclaw setup --app <name>``).
    """
    # `--app <name>`: run just that app's setup step, nothing else.
    if only_app:
        run_app_setup_steps(only_app=only_app)
        return

    from personalclaw.agent import rebuild_agent_config  # circular import: agent imports cli
    from personalclaw.cli import _project_dir_file  # circular import: cli -> cli_setup -> cli

    print("PersonalClaw Setup\n")
    print(f"  {DATA_WARNING.replace(chr(10), chr(10) + '  ')}\n")

    # Non-interactive mode/provider/credential flags (R8.8, R12.1)
    if mode or provider or credential:
        _setup_noninteractive(mode=mode, provider=provider, credential=credential)
        if not agent_only:
            return

    # 0. Save project dir so personalclaw works from anywhere
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR")
    if proj:
        _project_dir_file().parent.mkdir(parents=True, exist_ok=True)
        _project_dir_file().write_text(proj + "\n", encoding="utf-8")
        print(f"  ✅ Project dir saved: {proj}")

    # 1. Choose workspace directory (skip for agent-only — not relevant)
    if not agent_only:
        _setup_workspace_dir()

    # 2. Install agent config
    print("Installing agent config...")
    agent_path = rebuild_agent_config(clean=clean)
    print(f"  ✅ Agent installed: {agent_path}")

    # 2b. Ensure config.json has default PersonalClaw agent for fresh installs
    _ensure_default_agent_in_config()

    # 2c. Generate orchestrator skill if enabled (agent delegation).
    try:
        cfg = AppConfig.load()
        if cfg.agent.orchestrator_skill:
            generate_orchestrator_skill(SkillsLoader())
            print("  ✅ Orchestrator skill generated")
        else:
            # Clean up stale skill if previously enabled then disabled — cover both
            # the current orchestrator/ dir and the pre-rename conductor/ dir.
            for legacy in ("orchestrator", "conductor"):
                skill_path = SkillsLoader()._dir / legacy / "SKILL.md"
                if skill_path.exists():
                    skill_path.unlink()
    except Exception as exc:
        print(f"  ⚠️  Orchestrator skill generation failed: {exc}")

    if agent_only:
        print("\nDone! Try: personalclaw gateway")
        return

    # 3. App-contributed setup steps. Each installed + enabled app whose manifest
    # declares `cli.setup` runs its own interactive step here (alphabetical),
    # after the core credential/model steps. This is the generic seam that
    # replaced core's former hardcoded channel-app setup — a channel app now ships
    # its own token/config prompts via `cli.setup` (see PROVIDER-BOUNDARY-COMPLETION).
    run_app_setup_steps()

    # 4. Timezone
    _setup_timezone()

    # 5. Dashboard URL (remote access)
    _maybe_setup_dashboard_url()

    _maybe_setup_custom_domain()

    print("\nDone! Try: personalclaw doctor && personalclaw gateway")


def _setup_noninteractive(
    mode: str = "",
    provider: str = "",
    credential: str = "",
) -> None:
    """Apply non-interactive setup flags (R8.8, R12.1).

    ``--mode docker`` prints a ``docker compose up`` quick-start hint.
    ``--mode service`` prints a ``personalclaw service install`` hint.
    ``--mode none`` skips all deployment hints.
    ``--provider <name>`` wires a registry entry as the default chat provider
    in config.json (the entry must already be declared in the config).
    ``--credential <name=value>`` stores a named credential via the
    credential store.
    """
    if mode == "docker":
        print(
            "  Deployment mode: docker\n"
            "  Quick-start:\n"
            "    cp .env.example .env   # fill in secrets\n"
            "    docker compose up -d\n"
        )
    elif mode == "service":
        print(
            "  Deployment mode: service\n" "  Quick-start:\n" "    personalclaw service install\n"
        )
    elif mode == "none":
        pass  # no deployment hints — CI / manual setup
    elif mode:
        print(f"  ⚠️  Unknown --mode {mode!r}. Valid values: docker, service, none")

    if provider:
        cfg_file = config_path()
        try:
            data: dict = {}
            if cfg_file.exists():
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
            data.setdefault("agent", {})["provider"] = provider
            atomic_write(cfg_file, json.dumps(data, indent=2) + "\n")
            print(f"  ✅ Provider set: {provider}")
        except Exception as exc:
            print(f"  ❌ Could not set provider: {exc}")

    if credential:
        # Expect name=value or name format (value from env fallback)
        if "=" in credential:
            cred_name, _, cred_val = credential.partition("=")
        else:
            cred_name = credential
            cred_val = os.environ.get(cred_name, "")
        if cred_name and cred_val:
            try:
                from personalclaw.llm.credentials import Credential, CredentialStore

                store = CredentialStore(config_dir() / "credentials.json")
                store.upsert(Credential(name=cred_name, value=cred_val))
                print(f"  ✅ Credential stored: {cred_name}")
            except Exception as exc:
                print(f"  ❌ Could not store credential {cred_name!r}: {exc}")
        elif cred_name:
            print(f"  ⚠️  --credential {cred_name!r}: no value provided and env var not set")


def _setup_workspace_dir() -> None:
    """Prompt user for workspace directory, falling back to platform default."""
    platform_default = _default_workspace_base() / _WORKSPACE_DIR_NAME
    default = platform_default
    label = "Default"
    if _workspace_dir_file().is_file():
        configured = _workspace_dir_file().read_text(encoding="utf-8").strip()
        if configured:
            default = Path(configured)
            label = "Configured"
    print("── Workspace Directory ──\n")
    print("  LLM sessions and task output are stored in a workspace directory.")
    print(f"  {label}: {default}\n")
    answer = input(f"  Workspace path [{default}]: ").strip()
    chosen = default if answer.lower() in ("", "y", "yes") else Path(answer).expanduser()
    try:
        chosen.mkdir(parents=True, exist_ok=True)
        _workspace_dir_file().parent.mkdir(parents=True, exist_ok=True)
        _workspace_dir_file().write_text(str(chosen) + "\n", encoding="utf-8")
        print(f"  ✅ Workspace: {chosen}\n")
    except OSError as e:
        print(f"  ❌ Cannot create {chosen}: {e}")
        print(f"  Falling back to platform default: {platform_default}\n")


_CUSTOM_DOMAIN = "personalclaw.localhost"


def _detect_system_timezone() -> str:
    """Return IANA tz name from TZ env var or /etc/localtime symlink, or empty string."""
    tz_env = os.environ.get("TZ", "").lstrip(":")
    if tz_env and not tz_env.startswith("/"):
        return tz_env
    try:
        p = Path("/etc/localtime")
        if p.is_symlink():
            target = str(p.resolve())
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except Exception:
        pass
    return ""


def _setup_timezone() -> None:
    """Auto-detect timezone and save to config.json."""
    cfg_file = config_path()

    # Check if already configured
    data: dict = {}
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ⚠️  Could not read {cfg_file}: {exc}")
            return
    current = data.get("timezone", "")

    # Auto-detect from system
    detected = _detect_system_timezone()

    print("── Timezone ──\n")
    if current:
        print(f"  Current: {current}")
        answer = input(f"  Timezone [{current}]: ").strip()
        if not answer:
            print(f"  ✅ Keeping: {current}\n")
            return
        tz_val = answer
    elif detected:
        print(f"  Detected: {detected}")
        answer = input(f"  Timezone [{detected}]: ").strip()
        tz_val = answer or detected
    else:
        tz_val = input("  IANA timezone (e.g. America/Los_Angeles): ").strip()
        if not tz_val:
            print("  ⏭  Skipped. Cron schedules will show UTC.\n")
            return

    # Validate with retry
    abbrev_to_iana: dict[str, str] = {
        "PST": "America/Los_Angeles",
        "PDT": "America/Los_Angeles",
        "MST": "America/Denver",
        "MDT": "America/Denver",
        "CST": "America/Chicago",
        "CDT": "America/Chicago",
        "EST": "America/New_York",
        "EDT": "America/New_York",
        "GMT": "Etc/GMT",
        "BST": "Europe/London",
        "CET": "Europe/Berlin",
        "CEST": "Europe/Berlin",
        "IST": "Asia/Kolkata",
        "JST": "Asia/Tokyo",
        "AEST": "Australia/Sydney",
        "AEDT": "Australia/Sydney",
        "NZST": "Pacific/Auckland",
        "NZDT": "Pacific/Auckland",
    }
    max_retries = 3
    for attempt in range(max_retries):
        try:
            ZoneInfo(tz_val)
            break  # valid
        except (KeyError, Exception):
            suggestion = abbrev_to_iana.get(tz_val.upper())
            if suggestion:
                print(f"  ❌ '{tz_val}' is an abbreviation, not an IANA timezone.")
                print(f"     Did you mean: {suggestion}?")
            else:
                print(f"  ❌ Unknown timezone '{tz_val}'.")
                print("     Use IANA format, e.g. America/Los_Angeles, Europe/London")
            if attempt < max_retries - 1:
                tz_val = input("  Timezone: ").strip()
                if not tz_val:
                    print("  ⏭  Skipped.\n")
                    return
            else:
                print("  ⏭  Skipped after too many attempts.\n")
                return

    data["timezone"] = tz_val
    atomic_write(cfg_file, json.dumps(data, indent=2) + "\n")
    print(f"  ✅ Timezone saved: {tz_val}\n")


def _maybe_setup_dashboard_url() -> None:
    """Prompt for dashboard.url when running on a remote host with a channel
    configured (remote token auth is delivered through a channel — without one
    the dashboard is local-only, so no URL is needed)."""

    cfg_file = config_path()
    cfg = AppConfig.load()
    creds = cfg.load_credentials()
    has_channel = bool(creds.get("SLACK_APP_TOKEN") and creds.get("SLACK_BOT_TOKEN"))

    if not has_channel:
        return  # No channel → local-only, no URL needed

    # Detect if this looks like a remote host
    try:
        ip = socket.gethostbyname(socket.gethostname())
        is_remote = not ip.startswith("127.")
    except OSError:
        is_remote = False

    if not is_remote and not cfg.dashboard.url:
        return  # Localhost machine with no existing URL config — skip

    current = cfg.dashboard.url
    hostname = socket.gethostname()

    print("── Dashboard URL (remote access) ──\n")
    if is_remote:
        print(f"  This host ({hostname}) appears to be a remote machine.")
        print("  Setting a dashboard URL enables direct browser access with token auth.")
        print("  Leave blank for localhost-only (SSH tunnel required).\n")
    else:
        print("  Configure a custom dashboard URL for remote access.")
        print("  Leave blank for localhost-only.\n")

    hint = f" [{current}]" if current else ""
    answer = input(f"  Dashboard URL (e.g. http://{hostname}:{DASHBOARD_PORT}){hint}: ").strip()

    if answer == "" and current:
        print(f"  ✅ Keeping: {current}\n")
        return
    if answer == "" and not current:
        print("  ⏭  Skipped. Dashboard will bind to localhost only.\n")
        return

    # Persist to config.json
    try:
        data: dict = {}
        if cfg_file.exists():
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
        dashboard = data.setdefault("dashboard", {})
        dashboard["url"] = answer
        atomic_write(cfg_file, json.dumps(data, indent=2) + "\n")
        print(f"  ✅ Dashboard URL saved: {answer}")
        print("  Token auth will be required for all requests.\n")
    except Exception as e:
        print(f"  ❌ Failed to save: {e}\n")


def _maybe_setup_custom_domain() -> None:
    """Inform user about personalclaw.localhost dashboard URL."""
    print("\n── Custom Domain ──\n")
    print(f"  Dashboard available at http://{_CUSTOM_DOMAIN}:{DASHBOARD_PORT}")
    print("  (*.localhost resolves to 127.0.0.1 per RFC 6761 — no /etc/hosts edit needed)\n")
