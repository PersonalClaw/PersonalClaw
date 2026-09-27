"""CLI setup subcommand — interactive credential and config wizard."""

import json
import logging
import os
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from personalclaw.app_cli import run_app_setup_steps
from personalclaw.cli_chat import _ensure_default_agent_in_config
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.config.loader import (  # noqa: F401 — re-exported for test patch seam
    DASHBOARD_PORT,
    _workspace_dir_file,
    default_workspace_root,
    env_path,
)
from personalclaw.config.transactions import mutate_config
from personalclaw.constants import DATA_WARNING
from personalclaw.env import browser_available
from personalclaw.orchestrator_skill import generate_orchestrator_skill
from personalclaw.skills import SkillsLoader

logger = logging.getLogger(__name__)

#: The commands that run a failed step again. Each is safe to repeat: a prompt answered with
#: Enter keeps what is already there, and the agent install keeps your own changes.
_RETRY_WIZARD = "personalclaw setup"
_RETRY_AGENT = "personalclaw setup --agent-only"


@dataclass(frozen=True)
class _Failure:
    """A setup step that did not do its job: which one, why, and the command that runs it again."""

    step: str
    reason: str
    retry: str


def _failed(reason: str) -> str:
    """Say on stderr that a step failed, and hand back why, for the summary the run ends on."""
    print(f"  ❌ {reason[:1].upper()}{reason[1:]}", file=sys.stderr)
    return reason


def _run_step(
    failures: list[_Failure], step: str, retry: str, run: Callable[[], str | None]
) -> None:
    """Run one step of the wizard, and record it when it fails.

    ``run`` returns why it failed, having said so, or None when it did its job or the user chose
    to skip it. A step that raises is named here, in one line, instead of ending the wizard with
    a traceback: the steps after it still run, and the run ends on every failure at once.
    """
    try:
        reason = run()
    except Exception as exc:  # noqa: BLE001 — one crashed step must not hide the steps after it
        logger.debug("setup step %r raised", step, exc_info=True)
        reason = f"{type(exc).__name__}: {exc}"
        print(f"  ❌ {step}: {reason}", file=sys.stderr)
    if reason:
        failures.append(_Failure(step, reason, retry))


def _end_if_failed(failures: list[_Failure]) -> None:
    """Exit 1 naming each failed step and the command that runs it again. Returns if none failed.

    This is what stands between a failed step and "Done!". The wizard goes on past a failure, so
    that one bad step does not cost the others, and the last thing it says is what failed.
    """
    if not failures:
        return
    count = f"{len(failures)} step" if len(failures) == 1 else f"{len(failures)} steps"
    print(f"\nSetup did not finish: {count} failed.", file=sys.stderr)
    for failure in failures:
        print(f"  ❌ {failure.step}: {failure.reason}", file=sys.stderr)
        print(f"     Run it again: {failure.retry}", file=sys.stderr)
    print("The steps that worked are saved; Enter at any prompt keeps its answer.", file=sys.stderr)
    sys.exit(1)


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


def config_path() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_path`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_path()


def _ask(prompt: str) -> str:
    """Read one wizard answer, or ``""`` when stdin is not a terminal.

    Every ``setup`` prompt prints its default (or its skip behaviour) before
    asking, so a blank answer always means "take what you just showed me". A
    non-interactive stdin — a pipe, a redirect, a Dockerfile ``RUN``, CI —
    therefore accepts those defaults instead of aborting the wizard with a raw
    ``EOFError`` traceback partway through. `personalclaw setup` is the first
    command the getting-started guide hands a newcomer; it must not crash when
    it cannot prompt.
    """
    if not sys.stdin.isatty():
        print(f"{prompt}(non-interactive stdin — taking the default)")
        return ""
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return ""


def _print_dashboard_pointer() -> None:
    """Point at the dashboard's guided first run — one line (ONBOARDING-UX T1.4).

    The dashboard is the canonical onboarding surface: it installs a model provider,
    binds a chat model and runs a real first success without leaving the flow. This
    wizard stays credentials-first and unchanged — the plan's open question ("should
    ``setup`` gain full parity?") is answered "no, the dashboard owns it" — so setup
    does not duplicate that flow, it says where the flow is.

    Printed only where a browser can actually reach it: on a headless remote box the
    line would be an instruction the user cannot follow, so it is suppressed there and
    the ``doctor``/``gateway`` next-step line stands on its own.
    """
    if browser_available():
        print("  Guided first run — model provider, then a first success — is in the dashboard.")


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

    A step that fails never ends on "Done!". The wizard goes on to the steps after it, then
    names each failed step with the command that runs it again, and exits 1
    (:func:`_end_if_failed`). What the other steps did stays saved, and every one of them is
    safe to run again.
    """
    # `--app <name>`: run just that app's setup step, nothing else. A step that could not run
    # exits non-zero with the reason already printed: "unavailable" with exit 0 read as done.
    if only_app:
        if run_app_setup_steps(only_app=only_app):
            sys.exit(1)
        return

    from personalclaw.agent import rebuild_agent_config  # circular import: agent imports cli

    print("PersonalClaw Setup\n")
    print(f"  {DATA_WARNING.replace(chr(10), chr(10) + '  ')}\n")

    # Non-interactive mode/provider/credential flags (R8.8, R12.1). They are what an unattended
    # install runs, so a refused one exits 1 rather than letting the script carry on.
    if mode or provider or credential:
        if not _setup_noninteractive(mode=mode, provider=provider, credential=credential):
            sys.exit(1)
        if not agent_only:
            return

    failures: list[_Failure] = []

    # 1. Choose workspace directory (skip for agent-only — not relevant)
    if not agent_only:
        _run_step(failures, "Workspace directory", _RETRY_WIZARD, _setup_workspace_dir)

    # 2. Install agent config
    def _install_agent() -> None:
        print("Installing agent config...")
        print(f"  ✅ Agent installed: {rebuild_agent_config(clean=clean)}")

    _run_step(failures, "Agent config", _RETRY_AGENT, _install_agent)

    # 2b. Ensure config.json has default PersonalClaw agent for fresh installs
    _run_step(failures, "Default agent", _RETRY_AGENT, _add_default_agent)

    # 2c. Generate orchestrator skill if enabled (agent delegation).
    _run_step(failures, "Orchestrator skill", _RETRY_AGENT, _sync_orchestrator_skill)

    if agent_only:
        _end_if_failed(failures)
        print("\nDone! Try: personalclaw gateway")
        _print_dashboard_pointer()
        return

    # 3. App-contributed setup steps. Each installed + enabled app whose manifest
    # declares `cli.setup` runs its own interactive step here (alphabetical),
    # after the core credential/model steps. This is the generic seam that
    # replaced core's former hardcoded channel-app setup — a channel app now ships
    # its own token/config prompts via `cli.setup`.
    # One broken app never aborts the wizard; it is named at the end with its own retry.
    for app_name, why in run_app_setup_steps():
        failures.append(_Failure(f"App {app_name}", why, f"personalclaw setup --app {app_name}"))

    # 4. Timezone
    _run_step(failures, "Timezone", _RETRY_WIZARD, _setup_timezone)

    # 5. Dashboard URL (remote access)
    _run_step(failures, "Dashboard URL", _RETRY_WIZARD, _maybe_setup_dashboard_url)

    _maybe_setup_custom_domain()

    _end_if_failed(failures)
    print("\nDone! Try: personalclaw doctor && personalclaw gateway")
    _print_dashboard_pointer()


def _add_default_agent() -> str | None:
    """Step 2b: seed the default agent into config.json. Returns why it failed, having said so."""
    reason = _ensure_default_agent_in_config()
    return _failed(reason) if reason else None


def _sync_orchestrator_skill() -> None:
    """Step 2c: write the orchestrator skill when agent delegation is on, else remove a stale one.

    The cleanup covers both the current ``orchestrator/`` dir and the pre-rename ``conductor/``.
    """
    if AppConfig.load().agent.orchestrator_skill:
        generate_orchestrator_skill(SkillsLoader())
        print("  ✅ Orchestrator skill generated")
        return
    for legacy in ("orchestrator", "conductor"):
        skill_path = SkillsLoader()._dir / legacy / "SKILL.md"
        if skill_path.exists():
            skill_path.unlink()


def _setup_noninteractive(
    mode: str = "",
    provider: str = "",
    credential: str = "",
) -> bool:
    """Apply non-interactive setup flags (R8.8, R12.1). False when one was refused.

    ``--mode docker`` prints a ``docker compose up`` quick-start hint.
    ``--mode service`` prints a ``personalclaw service install`` hint.
    ``--mode none`` skips all deployment hints.
    ``--provider <name>`` wires a registry entry as the default chat provider
    in config.json (the entry must already be declared in the config).
    ``--credential <name=value>`` saves a secret under that name in the credential
    store Settings → Secrets lists (:func:`_store_named_credential`).

    Each refusal is said on stderr when it happens; the flags after it still run.
    """
    applied = True
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
        print(f"  ❌ Unknown --mode {mode!r}. Valid values: docker, service, none", file=sys.stderr)
        applied = False

    if provider:

        def _set_provider(data: dict) -> None:
            agent = data.get("agent")
            if not isinstance(agent, dict):
                agent = data["agent"] = {}
            agent["provider"] = provider

        try:
            mutate_config(_set_provider, path=config_path())
            print(f"  ✅ Provider set: {provider}")
        except Exception as exc:
            print(f"  ❌ Could not set provider: {exc}", file=sys.stderr)
            applied = False

    if credential and not _store_named_credential(credential):
        applied = False
    return applied


def _store_named_credential(credential: str) -> bool:
    """``--credential NAME=VALUE`` (or ``NAME``, the value read from the environment variable
    of that name): save the secret under NAME in the credential store, the one Settings →
    Secrets lists and every ``{{secret:NAME}}`` and provider ``credential`` reads. False, with
    the reason on stderr, when nothing was stored."""
    from personalclaw.config.credentials import save_credential
    from personalclaw.secrets_vault import is_reserved_key, valid_key_name

    if "=" in credential:
        cred_name, _, cred_val = credential.partition("=")
    else:
        cred_name, cred_val = credential, os.environ.get(credential, "")
    cred_name = cred_name.strip()
    refusal = ""
    if not valid_key_name(cred_name):
        refusal = (
            f"  ❌ --credential {cred_name!r}: a credential name is letters, digits and "
            "underscores, and does not start with a digit"
        )
    elif is_reserved_key(cred_name):
        refusal = (
            f"  ❌ --credential {cred_name!r}: that name is reserved for a key PersonalClaw "
            "manages itself; choose another"
        )
    elif not cred_val:
        refusal = f"  ❌ --credential {cred_name!r}: no value given and ${cred_name} is not set"
    else:
        try:
            save_credential(cred_name, cred_val)
        except OSError as exc:
            refusal = f"  ❌ Could not store credential {cred_name!r}: {exc}"
    if refusal:
        print(refusal, file=sys.stderr)
        return False
    print(f"  ✅ Stored {cred_name} in the credential store (listed in Settings → Secrets)")
    return True


def _setup_workspace_dir() -> str | None:
    """Ask where the workspace goes. Enter keeps the current one; only a typed folder is saved,
    so an owner who never chose one keeps the default inside the home.

    Returns why it failed, having said so. A typed folder that cannot be made or saved leaves
    the workspace where it was, and says where that is.
    """
    current = default_workspace_root()
    label = "Default"
    if _workspace_dir_file().is_file():
        configured = _workspace_dir_file().read_text(encoding="utf-8").strip()
        if configured:
            current = Path(configured)
            label = "Configured"
    print("── Workspace Directory ──\n")
    print("  LLM sessions and task output are stored in a workspace directory.")
    print(f"  {label}: {current}\n")
    answer = _ask(f"  Workspace path [{current}]: ")
    typed = answer.lower() not in ("", "y", "yes")
    chosen = Path(answer).expanduser() if typed else current
    try:
        chosen.mkdir(parents=True, exist_ok=True)
        if typed:
            _workspace_dir_file().parent.mkdir(parents=True, exist_ok=True)
            _workspace_dir_file().write_text(str(chosen) + "\n", encoding="utf-8")
    except OSError as exc:
        reason = _failed(f"cannot use {chosen} as the workspace: {exc}")
        if typed:
            print(f"  The workspace stays {current}.")
        print()
        return reason
    print(f"  ✅ Workspace: {chosen}\n")
    return None


_CUSTOM_DOMAIN = "personalclaw.localhost"


def _detect_system_timezone() -> str:
    """This machine's IANA zone name, or "" — through the one owner (#2520).

    This used to be its own `/etc/localtime` reader, and it returned `TZ` unvalidated: a
    `TZ=PDT` shell was "detected" as `PDT`, offered as the default, and then refused by the
    retry loop below. `timezones.machine_zone_name` validates every candidate through
    `ZoneInfo` first, so what is offered here is always something that can be saved.
    """
    from personalclaw.timezones import machine_zone_name

    return machine_zone_name()


def _setup_timezone() -> str | None:
    """Auto-detect timezone and save to config.json.

    Returns why it failed, having said so: the config could not be read or written, no zone can
    be checked, or three answers in a row were not zones. An empty answer is a skip, not that.
    """
    cfg_file = config_path()

    # Check if already configured
    data: dict = {}
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
        except Exception as exc:
            return _failed(f"could not read {cfg_file}: {exc}")
    current = data.get("timezone", "")

    # Auto-detect from system
    detected = _detect_system_timezone()

    print("── Timezone ──\n")
    if current:
        print(f"  Current: {current}")
        answer = _ask(f"  Timezone [{current}]: ")
        if not answer:
            print(f"  ✅ Keeping: {current}\n")
            return None
        tz_val = answer
    elif detected:
        print(f"  Detected: {detected}")
        answer = _ask(f"  Timezone [{detected}]: ")
        tz_val = answer or detected
    else:
        tz_val = _ask("  IANA timezone (e.g. America/Los_Angeles): ")
        if not tz_val:
            # Not "will show UTC" any more (#2520): with nothing configured AND nothing
            # detectable, UTC is the last resort and `personalclaw doctor` warns about it by
            # name. Naming that here keeps the two surfaces telling the same story.
            print("  ⏭  Skipped — schedules fall back to UTC; `personalclaw doctor` warns.\n")
            return None

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
    # The refusal point for a typo'd zone (#2520): `config.timezone` has no PATCH allowlist
    # entry, so this prompt is the only authoring surface for it, and a name that lands in the
    # file unvalidated is a silent hour-shift for every schedule that falls back to it.
    from personalclaw.timezones import TimeZoneDatabaseUnavailable, is_known_zone

    max_retries = 3
    for attempt in range(max_retries):
        try:
            known = is_known_zone(tz_val)
        except TimeZoneDatabaseUnavailable:
            # Every name, valid or not, fails here, so a re-prompt would blame the user for a
            # broken install. Say it once and stop.
            return _failed(
                "timezone database unavailable, so no zone can be checked: reinstall "
                "PersonalClaw, whose base package includes the Python `tzdata` database"
            )
        if known:
            break  # valid
        suggestion = abbrev_to_iana.get(tz_val.upper())
        if suggestion:
            print(f"  ❌ '{tz_val}' is an abbreviation, not an IANA timezone.", file=sys.stderr)
            print(f"     Did you mean: {suggestion}?", file=sys.stderr)
        else:
            print(f"  ❌ Unknown timezone '{tz_val}'.", file=sys.stderr)
            print("     Use IANA format, e.g. America/Los_Angeles, Europe/London", file=sys.stderr)
        if attempt < max_retries - 1:
            tz_val = _ask("  Timezone: ")
            if not tz_val:
                print("  ⏭  Skipped.\n")
                return None
        else:
            return _failed(f"no IANA timezone in {max_retries} tries, so it was not changed")

    # The zone alone, in the config transaction: the file was read before the prompt, and
    # writing that copy back would put back whatever changed while the user was typing.
    try:
        mutate_config(lambda document: document.update(timezone=tz_val), path=cfg_file)
    except Exception as exc:
        return _failed(f"could not save the timezone: {exc}")
    print(f"  ✅ Timezone saved: {tz_val}\n")
    return None


def _maybe_setup_dashboard_url() -> str | None:
    """Prompt for dashboard.url when running on a remote host with a channel
    configured (remote token auth is delivered through a channel — without one
    the dashboard is local-only, so no URL is needed). Returns why it failed, having said so."""

    import asyncio

    from personalclaw.channel_transports import configured_channels
    from personalclaw.providers.loader import build_channel_transports

    cfg_file = config_path()
    cfg = AppConfig.load()
    # Each channel app answers for itself (its own health), so a channel configured through
    # its settings counts; core used to look for two Slack credential names.
    if not asyncio.run(configured_channels(build_channel_transports())):
        return None  # No channel → local-only, no URL needed

    # Detect if this looks like a remote host
    try:
        ip = socket.gethostbyname(socket.gethostname())
        is_remote = not ip.startswith("127.")
    except OSError:
        is_remote = False

    if not is_remote and not cfg.dashboard.url:
        return None  # Localhost machine with no existing URL config — skip

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
    answer = _ask(f"  Dashboard URL (e.g. http://{hostname}:{DASHBOARD_PORT}){hint}: ")

    if answer == "" and current:
        print(f"  ✅ Keeping: {current}\n")
        return None
    if answer == "" and not current:
        print("  ⏭  Skipped. Dashboard will bind to localhost only.\n")
        return None

    def _set_url(data: dict) -> None:
        dashboard = data.get("dashboard")
        if not isinstance(dashboard, dict):
            dashboard = data["dashboard"] = {}
        dashboard["url"] = answer

    # Persist to config.json, in the config transaction.
    try:
        mutate_config(_set_url, path=cfg_file)
    except Exception as exc:
        return _failed(f"could not save the dashboard URL: {exc}")
    print(f"  ✅ Dashboard URL saved: {answer}")
    print("  Token auth will be required for all requests.\n")
    return None


def _maybe_setup_custom_domain() -> None:
    """Inform user about personalclaw.localhost dashboard URL."""
    print("\n── Custom Domain ──\n")
    print(f"  Dashboard available at http://{_CUSTOM_DOMAIN}:{DASHBOARD_PORT}")
    print("  (*.localhost resolves to 127.0.0.1 per RFC 6761 — no /etc/hosts edit needed)\n")
