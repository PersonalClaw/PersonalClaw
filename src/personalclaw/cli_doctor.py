"""CLI doctor subcommand — verify PersonalClaw setup and diagnose issues."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from personalclaw import __version__ as _pc_version
from personalclaw import approval_grants, home_gateway, python_children
from personalclaw.agent import AGENT_FILENAME, agents_dir
from personalclaw.auth.modes import classify_auth_mode_request
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_backend_warning, credential_store_state
from personalclaw.dashboard.origin import (
    address_beyond_loopback,
    auth_is_off,
    is_local_bind,
    is_loopback,
    is_private_network,
    local_network_bypass_enabled,
    loopback_requires_token,
    machine_hostname,
    parse_dashboard_url,
    resolve_bind_host,
    tailnet_ip,
)
from personalclaw.python_support import python_support, rebuild_command


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


_MIN_NODE_VERSION = 18

#: The lifetime of the token doctor signs into a running gateway with: long enough for its two
#: reads, and over soon after. It is never printed.
_GATEWAY_TOKEN_TTL = "2m"
#: How long one read of the running gateway may take: its Maintenance read runs every Doctor check.
_GATEWAY_READ_TIMEOUT_SECS = 60.0


@dataclass(frozen=True)
class _GatewayReading:
    """What the running gateway of this home measured, for the sections a live one answers.

    Channel receivers, app backends and model-call breakers exist only in the gateway, and a
    provider's connection is tested there, so this command asks it rather than measuring its
    own process, where none of them runs: measured here, a Slack that was receiving read as a
    channel whose transport was down, and the score disagreed with the Doctor page's. ``why``
    says where the numbers came from when the gateway did not give them (no gateway, or one this
    command could not ask), in the words the output prints.
    """

    port: int | None = None
    #: Its ``GET /api/doctor/remediation`` body, or None.
    remediation: dict | None = None
    #: Its model-provider instances by name (``GET /api/model-providers``), or None.
    providers: dict[str, dict] | None = None
    why: str = ""
    #: The port named was not where this home's gateway answers (``home_gateway`` refused it):
    #: a gateway of this home may still run elsewhere, so none is said to be "not running".
    refused: bool = False


def _gateway_get(port: int, token: str, path: str) -> dict:
    """One authenticated read of this home's gateway. Raises on anything but a JSON object."""
    from personalclaw.cli_run import owner_headers

    request = urllib.request.Request(
        f"http://{home_gateway.LOOPBACK_HOST}:{port}{path}", headers=owner_headers(token)
    )
    with home_gateway.open_loopback(request, timeout=_GATEWAY_READ_TIMEOUT_SECS) as resp:
        body = json.loads(resp.read())
    if not isinstance(body, dict):
        raise ValueError(f"{path} did not answer with an object")
    return body


def _read_running_gateway() -> _GatewayReading:
    """Ask this home's running gateway (``home_gateway.reach``) for its own measurements, signed
    in the way ``personalclaw token`` is (the home's local secret), with a token that lasts two
    minutes."""
    from personalclaw.cli_run import RunError, mint_local_token

    try:
        gateway = home_gateway.reach()
    except home_gateway.NoGatewayRunning:
        return _GatewayReading(
            why="here, with no gateway of this home running: channels receive and app backends "
            "run only in a gateway, so their checks fail until one starts"
        )
    except home_gateway.GatewayError as exc:
        return _GatewayReading(
            why=f"here, not by a gateway of this home ({exc}): the checks of its channels, app "
            "backends and model calls read this command's process instead, where none of them runs",
            refused=True,
        )
    port = gateway.port
    try:
        token = mint_local_token(gateway, ttl=_GATEWAY_TOKEN_TTL)
        remediation = _gateway_get(port, token, "/api/doctor/remediation")
        listed = _gateway_get(port, token, "/api/model-providers").get("providers") or []
    except (RunError, OSError, ValueError) as exc:
        return _GatewayReading(
            port=port,
            why=f"here, not by the gateway running on port {port}, which could not be asked "
            f"({exc}): the checks of its channels, app backends and model calls read this "
            "command's process instead, where none of them runs",
        )
    providers = {str(p.get("name")): p for p in listed if isinstance(p, dict)}
    if "score" not in remediation:
        # `{"enabled": false}`: the Doctor is switched off in that gateway.
        return _GatewayReading(
            port=port,
            providers=providers,
            why=f"here: the gateway running on port {port} has its Doctor switched off "
            "(Settings → Doctor), so the checks of its channels and app backends read this "
            "command's process instead",
        )
    return _GatewayReading(port=port, remediation=remediation, providers=providers)


def _connection_row(name: str, gateway: _GatewayReading) -> str:
    """How a model-provider instance's connection reads, from the test the gateway ran on it."""
    from personalclaw.providers.connection import CHECKING, CONNECTED, FAILED, UNTESTABLE

    if gateway.providers is None:
        if gateway.port is not None:
            where = "the running gateway could not be asked"
        elif gateway.refused:
            where = "no gateway of this home answered to test it"
        else:
            where = "no gateway of this home is running to test it"
        return f"⏹  registered, connection not tested ({where})"
    connection = (gateway.providers.get(name) or {}).get("connection") or {}
    state = connection.get("state")
    detail = str(connection.get("detail") or "")
    if state == CONNECTED:
        return f"✅ {detail or 'connected'}"
    if state == FAILED:
        return f"⚠️  cannot be used: {detail or 'its connection test failed without saying why'}"
    if state == CHECKING:
        return "⏳ being tested now; its card in Settings → Providers shows the answer"
    if state == UNTESTABLE:
        return f"⏹  registered; {detail or 'its type has no connection test'}"
    return "⏹  registered; the running gateway lists no connection test for it"


def _doctor_providers(gateway: _GatewayReading, *, start_agent_clis: bool = False) -> list[str]:
    """Report each registered ProviderEntry. Returns an issue string for each that fails.

    An ``acp_agent`` entry is another agent's CLI, and doctor does not start it unless asked:
    by default it reports whether the CLI is installed and what its last Test found (the one on
    its card in Settings → Providers). ``start_agent_clis`` — ``personalclaw doctor
    --start-agent-clis`` — starts each one once, the same Test, and records the answer.
    A model-provider instance reads as the running gateway's connection test found it
    (:func:`_connection_row`): one that cannot be used says why in its own words, and none reads
    ✅ for being registered alone.
    """
    issues: list[str] = []
    try:
        # Import the provider modules to trigger their registry.register_type()
        # calls so that all built-in types are visible.
        import personalclaw.llm.acp_agent  # noqa: F401
        from personalclaw.llm.registry import get_default_registry

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
            _report_acp_agent(entry, label, issues, start=start_agent_clis)
        else:
            # Any model provider type (ollama core-native, or an installed model
            # app: openai/anthropic/vllm/bedrock/…). The type is shown in the label;
            # no hardcoded core-native allow-list (that list went stale when the
            # model providers became apps).
            print(f"  {label}: {_connection_row(entry.name, gateway)}")

    return issues


#: How each readiness state reads in doctor's output.
_READINESS_ICONS = {
    "ready": "✅",
    "untested": "⏹",
    "not_found": "❌",
    "needs_login": "🔑",
    "timeout": "⏳",
    "error": "❌",
}


def _report_acp_agent(entry: object, label: str, issues: list[str], *, start: bool) -> None:
    """Report one agent runtime: from what is installed and its last Test, or — only when
    *start* — from a Test run now (``agents/runtime_tests.py``, the path the card's Test takes).
    """
    import asyncio

    from personalclaw.agents import runtime_tests

    try:
        if start:
            readiness = asyncio.run(runtime_tests.run_test(entry))
        else:
            readiness = runtime_tests.readiness(entry)
    except Exception as exc:
        print(f"  {label}: ⚠️  could not check ({exc})")
        return

    state = str(readiness.get("state") or "error")
    icon = _READINESS_ICONS.get(state, "⚠️")
    tested_at = readiness.get("tested_at")
    detail = str(readiness.get("detail") or "")
    if state == "untested":
        detail = (
            "installed, not started — run `personalclaw doctor --start-agent-clis` to start "
            "each agent CLI once, or press Test on its card in Settings → Providers"
        )
    elif tested_at and not start:
        detail = f"{detail} (last Test {tested_at})"
    print(f"  {label}: {icon} {detail}")
    # A CLI nobody started is not a failure — only a check that ran, and failed, is.
    if not readiness.get("ready") and state != "untested":
        issues.append(f"{label}: {state}")
    adapter = runtime_tests.adapter_install(entry)
    # Said, and never installed here: enabling the app again (Retry on its card) is what does.
    if adapter and adapter["error"]:
        print(
            "      its ACP adapter did not install when its app was enabled: "
            f"{adapter['error']}. Retry on its card in Settings → Providers installs it again."
        )
        issues.append(f"{label}: ACP adapter not installed")
    elif adapter:
        print(
            "      its ACP adapter is not installed here, so it runs through npx. Retry on its "
            "card in Settings → Providers installs it."
        )


def _doctor_paths() -> None:
    """Print the resolved install paths as machine-friendly ``key<TAB>path`` lines.

    The ``doctor get install-dir`` pattern: an external
    agent driving PersonalClaw locates the offline API reference — and the config,
    skills, and install dirs — from the binary alone, without knowing whether this
    is a wheel, an editable install, or a source checkout. Tab-separated so it
    parses trivially; the ``reference`` path is the one an agent reads for exact
    tool/route signatures (see ``reference/index.md``).
    """
    from personalclaw.manifest_reference import reference_dir
    from personalclaw.skills.loader import skills_dir

    paths: list[tuple[str, Path]] = [
        ("reference", reference_dir()),
        ("config", config_dir()),
        ("skills", skills_dir()),
        ("install", Path(__file__).resolve().parent),
    ]
    for label, path in paths:
        print(f"{label}\t{path}")


def _doctor_rebuild_routing_stats() -> None:
    """Refold ``routing_stats.json`` from the model-call audit — the rebuild path.

    🔑 THIS IS THE FLAG `routing/stats.py` ALREADY NAMED. Its :func:`~personalclaw.routing.
    stats.rebuild` docstring calls itself "the ``--rebuild-routing-stats`` maintenance path" and
    `routing/usage.py` notes in as many words that no argument implemented it — so the recovery
    function shipped tested and unreachable, with the audit rows it needs sitting on disk.

    That mattered because the two folds recover differently. The usage fold self-heals: every
    ``GET /api/usage`` calls ``usage.refresh``, which refolds. The routing fold has no such read
    path — ``GET /api/models/telemetry`` calls ``load_stats`` only, and a missing file reads as an
    empty fold rather than an error (correct, and never fatal). So a deleted or truncated
    ``routing_stats.json`` left the Routing & Efficiency view permanently blank and dropped the
    learned policy's per-ref sample counts below its ``n >= 5`` floor, silently stopping it from
    proposing — while ``model_calls.jsonl`` still held everything needed to restore both.

    Reports the row count, because the number is the finding: the audit JSONL is capped and
    rotated, so a rebuild recovers the retained tail rather than all history, and a caller who is
    not told how many rows were folded cannot tell a successful rebuild from an empty one.
    """
    from personalclaw.routing.stats import _stats_path, rebuild

    home = config_dir()
    folded = rebuild(home)
    print(f"routing stats: refolded {folded} attempt row(s) → {_stats_path(home)}")
    if not folded:
        # Not an error and not sys.exit(1): a fresh install has no audit rows, and an empty fold
        # is the honest result there. Saying so beats a bare success line that reads identically
        # to a recovered one.
        print("  (no attempt rows in the audit log — the fold is empty, not broken)")


def _git_is_inside_work_tree(path: Path) -> bool | None:
    """Ask git whether *path* sits in a work tree. ``None`` when git could not answer.

    ``None`` is a real third outcome, not a swallowed error: git missing from PATH, a git
    that refuses the directory (``safe.directory`` dubious-ownership), a timeout, or output
    this function does not recognise all leave the question OPEN. The caller must not turn
    an unanswered question into a verdict — doing exactly that is #2907.

    ``exit 128`` with ``not a git repository`` is an ANSWER, not a failure: it is how git
    says no.
    """
    if not shutil.which("git"):
        return None
    from personalclaw.net.git import git_argv, git_env

    try:
        result = subprocess.run(
            git_argv(["-C", str(path), "rev-parse", "--is-inside-work-tree"]),
            capture_output=True,
            text=True,
            timeout=5,
            env=git_env(site="doctor-git"),
        )
    except Exception:
        return None
    answer = (result.stdout or "").strip()
    if answer == "true":
        return True
    # "false" is git's answer for a bare repo or the inside of a .git dir — a real no.
    if answer == "false" or "not a git repository" in (result.stderr or "").lower():
        return False
    return None


def _git_work_tree_row(proj: Path) -> str:
    """The ``git repo:`` row for *proj* — three states, none of them assumed (#2907).

    The check used to be ``(proj / ".git").is_dir()`` with a flat ``not a git repo`` on the
    false branch. In a linked worktree or a submodule ``.git`` is a FILE holding a
    ``gitdir:`` pointer, so a fully valid checkout — the layout this project's own dev flow
    runs on — reported as no repo at all. The same false branch also answered a question it
    never asked: ``proj`` being a SUBDIRECTORY of a checkout, where there is no marker here
    and git is still inside a work tree.

    So git is the authority (it is the thing that actually knows, and it is right about
    worktrees, submodules, subdirectories and bare repos), and the ``.git`` marker names the
    SHAPE plus answers alone when git cannot be asked. When neither establishes anything the
    row SAYS so: a doctor that reports "cannot tell" is correct, and one that reports "not a
    git repo" about a worktree is not.

    Advisory either way — no branch here appends to ``issues``, so doctor's exit status is
    unchanged by what it finds.
    """
    marker = proj / ".git"
    shape = ""
    try:
        if marker.is_dir():
            shape = "✅"
        elif marker.is_file():
            shape = "✅ linked worktree or submodule (.git is a gitdir pointer)"
    except OSError:
        shape = ""

    inside = _git_is_inside_work_tree(proj)
    if inside is True:
        return shape or "✅ inside a git work tree (no .git at the project dir itself)"
    if inside is False:
        # Covers both of git's noes — "not a git repository" and the bare-repo/inside-.git
        # `false` — so the sentence claims only what `--is-inside-work-tree` answers.
        return "⚠️  not a git work tree (git reports no work tree here)"
    return shape or "⏭  cannot tell — no .git here, and git could not be asked"


def _doctor_credentials() -> list[str]:
    """Print which credential store is holding the secrets; return any issues.

    Reports the RESOLVED backend, never the requested one. That distinction is the
    reason this line exists: an install that asks for a keychain on a box with no OS
    secret service keeps its credentials in ``.env`` at 0600, and echoing the request
    would tell that operator their secrets are somewhere they are not.

    It reports the ``.env`` file the same way — as observed. The row used to print the
    literal ``.env 0600`` for every non-keychain install without stat-ing anything, so a
    fresh home was told its credentials were stored in a file that did not exist, at a mode
    nothing had read, and a ``.env`` left at 0640 read as 0600 too (#2922). 0600 is the
    floor the fallback PROMISES; the promise is only ever printed in the future tense.
    Three observations, three sentences — no file yet, a file at the mode we measured, or a
    file we could not inspect.
    """
    state = credential_store_state()
    if state.backend == "keychain":
        print("  credentials: 🔐 OS keychain (keyring)")
    elif not state.env_readable:
        print(f"  credentials: ⏭  could not read {state.env_path} — its mode is unknown")
    elif not state.env_exists:
        print(
            "  credentials: ⏹  none stored yet — the file is created at 0600 on the "
            f"first write ({state.env_path})"
        )
    else:
        print(f"  credentials: 🔐 .env {state.env_mode} — {state.env_path}")
        if state.env_group_or_world_readable:
            # Now that the row prints the mode it MEASURED, a loose one shows up here for
            # the first time — and a bare `.env 0640` reads as fine. The
            # `security.credential_backend` probe already calls this actionable, so the
            # same sentence belongs on this surface rather than only in the dashboard.
            # Legibility only: this prints, it does not gate. Same call as the auth-mode
            # line — the fallback repairs the mode on the next credential read, so doctor's
            # exit status must not start failing installs it used to pass.
            print(
                f"               ⚠️  mode {state.env_mode} is group/world readable —"
                " repaired to 0600 on the next credential read"
            )
    if state.keychain is not None:
        # Whose items the keychain half reads and writes. Reads consult it whichever backend is
        # active, so it is named whenever a keychain answers.
        mark = "⚠️  " if state.keychain.scope == "unreadable" else ""
        print(f"               {mark}keychain namespace: {state.keychain_summary}")
    warning = credential_backend_warning()
    if not warning:
        return []
    print(f"               ⚠️  {warning}")
    return ["credential backend: keychain requested but unavailable"]


def _doctor_config_readable() -> list[str]:
    """Report a ``config.json`` that was DISCARDED on the load just above, or nothing (#3424).

    The surface half of the fail-closed fix. A corrupt config used to substitute the dataclass
    defaults with no signal at all, and two of those defaults were *less safe* than the values
    the operator had stored — so the instance ran wide open and every surface, this one included,
    displayed the substitute as if it were the stored setting. The `approval:` line above is the
    live case: it printed `auto` while the file said `interactive`.

    The substituted fields are rendered from ``CONFIG_ON_DISCARDED_READ`` rather than retyped, so
    a change to the fail-closed table cannot leave this advice describing the old one. Reads the
    marker left by the caller's own ``AppConfig.load()``; it never re-reads the file, so the two
    cannot disagree.
    """
    from personalclaw.config.loader import CONFIG_ON_DISCARDED_READ, config_discard

    state = config_discard()
    if state is None:
        return []
    print(f"  config file: ❌ UNREADABLE — {state.path}")
    print(f"               {state.reason}")
    print("               Your stored settings are NOT in effect. These are held at their")
    print("               most restrictive value until the file is repaired or removed:")
    for key, value in CONFIG_ON_DISCARDED_READ.items():
        print(f"                 {key} = {value!r}")
    print("               Fix: repair the JSON, or move it aside to start from defaults.")
    print("               The original bytes have not been overwritten.")
    return ["config.json unreadable — running on a fail-closed posture"]


def _doctor_timezone() -> list[str]:
    """Print the zone timed triggers resolve to; return an issue on a UTC fallback (#2520).

    Same rule as the credentials line above: report the RESOLVED zone, not the requested one.
    Before this, no surface answered "which zone will my 08:30 reminder fire at" — `server_tz`
    said `UTC` on a PDT host, and an 08:30 trigger fired at 01:30 with nothing warning.

    The fallback is a ⚠️  that names the consequence in hours, not an informational line: a user
    who reads "timezone source: utc-fallback" has no reason to act, and one who reads "timed
    triggers will fire 7 hours off your local time" does.
    """
    from personalclaw.timezones import zone_report

    facts = zone_report()
    print(
        f"  timezone:    🕐 {facts['resolved']} (from {facts['source']}, "
        f"UTC{facts['utc_offset_hours']:+g})"
    )
    if not facts["warning"]:
        return []
    print(f"               ⚠️  {facts['warning']}")
    return [f"timezone: unresolved — schedules fall back to {facts['resolved']}"]


def _doctor_session_lifetime(cfg: AppConfig) -> list[str]:
    """Print how long a sign-in lasts; return an issue when ``auth.session_ttl`` is over the
    90-day limit.

    Such a value is APPLIED as 90 days — a hand-edited file must never brick the box — so until
    it is fixed the file says one lifetime and every sign-in lasts another. The same report the
    Doctor page's ``security.session_lifetime`` row reads (``lifetimes.session_lifetime_report``).
    """
    from personalclaw.auth.lifetimes import exact_words, session_lifetime_report

    report = session_lifetime_report(str(getattr(cfg.auth, "session_ttl", "") or ""))
    shown = report.configured or "30d, the default"
    print(f"  sign-ins:    🔑 last {exact_words(report.applied_secs)} (auth.session_ttl = {shown})")
    if not report.over_limit:
        return []
    print(f"               ⚠️  {report.warning} {report.remedy}")
    return ["sign-in lifetime: auth.session_ttl is over the 90-day limit"]


def _doctor_external_vector_store() -> list[str]:
    """Print the bound external chunk-vector index's own report, or nothing (#3139).

    ``VectorStoreProvider.describe()`` is the contract's diagnostics surface — every app
    implemented it, core called it NOWHERE, so the one question a user with an external
    backend asks ("is my store actually being used, and does it have my vectors?") had no
    answer on any surface. An empty-but-reachable index is the silent case: search simply
    returns no chunk hits, which is indistinguishable from "no matches".

    Silent when no backend is bound, because the bundled ``vec0`` path is the default and a
    row saying "not using a feature you did not enable" is noise on every install.

    Returns ``issues`` entries only for the two states a user must act on: unreachable, and
    reachable-but-empty while the local corpus holds embedded chunks. A count that merely
    DISAGREES prints a warning without failing — a backfill in flight is a normal transient.
    """
    try:
        from personalclaw.vector_stores.registry import active_provider

        provider = active_provider()
    except Exception:
        return []
    if provider is None:
        return []

    name = getattr(provider, "name", "?")
    try:
        # By contract describe() reports `reachable=False` rather than raising; a backend
        # that breaks that must not take the doctor down with it.
        info = provider.describe()
    except Exception as exc:
        print(f"  chunk index: ⚠️  {name}: describe() raised ({str(exc)[:80]})")
        return [f"vector store {name}: describe() raised"]

    local = None
    try:
        from personalclaw.knowledge import get_knowledge_store

        local = int(
            get_knowledge_store()
            .db.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
            .fetchone()[0]
        )
    except Exception:
        # A local read failure is the knowledge store's own problem, reported elsewhere.
        pass

    shape = f"{info.backend}/{info.collection}"
    if info.dimension is not None:
        shape += f" dim {info.dimension}"
    count = "unknown" if info.count is None else str(info.count)
    local_txt = "" if local is None else f", local {local}"
    if not info.reachable:
        print(f"  chunk index: ❌ {name} ({shape}) unreachable")
        # The provider's own sentence, which by contract carries the reason and no secret.
        if info.detail:
            print(f"               {info.detail}")
        print("               Knowledge chunk search returns nothing while it is down —")
        print("               it does NOT fall back to the built-in index.")
        return [f"vector store {name} unreachable"]

    print(f"  chunk index: ✅ {name} ({shape}) — {count} vector(s){local_txt}")
    if local and info.count == 0:
        print(f"               Empty while {local} local chunk(s) are embedded — searches")
        print("               find no chunks. Fix: re-enable the app to backfill, or")
        print("               reindex from Settings → Doctor → Maintenance.")
        return [f"vector store {name} empty"]
    if local is not None and info.count is not None and info.count != local:
        print("               ⚠️  count differs from local — a backfill may be in flight.")
    return []


def _doctor_backups() -> None:
    """Print whether the scheduled snapshot and export are working, in the Doctor's words.

    The Doctor's own check (``durability.backups``), not a second reading: this and the Doctor page
    say one failure one way, with what a restore can still bring back. It reads this home's backup
    record, which the running gateway writes, so no gateway has to be asked. Not appended to
    ``issues``: like a failed check under Maintenance, a failing backup is reported here, and the
    setup check that follows still runs.
    """
    print("\nBackups")
    try:
        import asyncio

        from personalclaw.resilience.doctor import DoctorContext, _probe_backups

        res = asyncio.run(_probe_backups(DoctorContext()))
    except Exception as exc:
        print(f"  backups:     ⚠️  could not read ({str(exc)[:120]})")
        return
    print(f"  backups:     {'✅' if res.ok else '❌'} {res.detail}")
    if res.remedy:
        # The remedy names its own next step ("No automatic fix — …"), on the continuation line
        # Maintenance prints a failed check's remedy on.
        print(f"               {res.remedy}")


#: The Fix line for a server entry the Doctor page's Fix can set up: this command repairs nothing.
_CORE_SERVER_FIX_LINES = (
    "Fix: personalclaw doctor repairs nothing. Settings → Doctor → Tools has a Fix",
    "for this, which shows what it writes and asks first. The gateway's next start",
    "sets the entry up too.",
)
#: Said when the server is missing from either list: a reader may look for a repair, and none comes.
_LISTS_ARE_YOURS = "Doctor and its fixes leave both lists as they are: they are yours to change."


def _doctor_core_server(agent_path: Path) -> list[str]:
    """Print where PersonalClaw's own server stands in the agent runtime config. Changes nothing.

    The Doctor page's own reading and words (``resilience.core_server``), so the two say one
    fault one way. A server entry that is missing, or whose command is not a program on
    this machine, is an issue the run exits by, and its repair is the Doctor page's confirm-gated
    Fix, which shows what it writes before it writes it. Where the server stands in ``tools`` and
    ``allowedTools`` is reported only: both lists are the owner's, and neither this command nor
    any Doctor Fix adds a tool to either. Returns the issues found.
    """
    from personalclaw.resilience.core_server import (
        core_server_command,
        core_server_detail,
        core_server_name,
        core_server_remedy,
        read_core_server,
    )

    reading = read_core_server(agent_path)
    ref = f"@{core_server_name()}"
    detail = core_server_detail(reading)
    if not reading.found:  # removed since the Agent row looked: nothing to check, nothing to fix
        print(f"  {ref}: ⏹  {detail}")
        return []
    if reading.unreadable:
        print(f"  {ref}: ❌ {detail}")
        print(f"               {core_server_remedy(reading)}")
        return ["agent config unreadable"]
    issues: list[str] = []
    if reading.set_up:
        print(f"  {ref}: ✅ {detail}")
    else:
        print(f"  {ref}: ❌ {detail}")
        fix = _CORE_SERVER_FIX_LINES if core_server_command() else (core_server_remedy(reading),)
        for line in fix:
            print(f"               {line}")
        issues.append(f"{ref} server entry")
    print(f"  tools:       {'lists' if reading.in_tools else 'does not list'} {ref}")
    if reading.in_allowed:
        print(f"  allowedTools: lists {ref}, so its tools run without asking")
    else:
        print(f"  allowedTools: does not list {ref}, so its tools are not among those that run")
        print("               without asking")
    if not (reading.in_tools and reading.in_allowed):
        print(f"               {_LISTS_ARE_YOURS}")
    return issues


def _doctor_maintenance(gateway: _GatewayReading) -> None:
    """Print the remediation engine's health score and the deficits behind it.

    🔴 THE SECOND SURFACE OF THE SAME DROPPED EVIDENCE. `/api/doctor/remediation` measures
    every deficit and the dashboard renders them; ``personalclaw doctor`` measured nothing
    and printed no part of that payload. On a home with 25 knowledge items and no embedder
    the CLI said exactly one thing about it — ``embeddings: ⏹ disabled`` — naming the
    missing PREREQUISITE while staying silent about its CONSEQUENCE, the 25 items now
    keyword-only. Whoever reads the CLI instead of the dashboard read "a feature is off",
    not "a backlog is stuck".

    The running gateway's own measurement when it gave one, so this and the Doctor page read
    the same score; otherwise measured here, and the ``measured:`` row says so and why.

    Deliberately NOT appended to ``issues`` (which exits 1): a deficit is a maintenance
    backlog, not a broken setup, and a fresh install with unembedded notes must not fail its
    own doctor. Best-effort like every other probe here — a measure that raises prints one
    line and moves on, because a health read must never break the setup check that follows.
    """
    print("\nMaintenance")
    try:
        from personalclaw.config.loader import AppConfig as _Cfg
        from personalclaw.resilience.remediation import (
            deficit_rows,
            health_score,
            measure_deficits,
        )

        if gateway.remediation is not None:
            score = float(gateway.remediation.get("score") or 0.0)
            target = float(gateway.remediation.get("target_score") or 0.0)
            deficits = [d for d in gateway.remediation.get("deficits") or [] if isinstance(d, dict)]
            print(
                f"  measured:    by the gateway running on port {gateway.port}, as its Doctor page"
            )
        else:
            measured = measure_deficits()
            score = health_score(measured)
            target = float(_Cfg.load().resilience.remediation.target_score)
            deficits = deficit_rows(measured)
            print(f"  measured:    {gateway.why}")
        mark = "✅" if score >= target else "⚠️ "
        print(f"  health:      {mark} score {score:g} / target {target:g}")
        # Zero-count sources are measurements, not problems — the same filter the panel
        # applies, for the same reason: listing them buries the real ones.
        present = [d for d in deficits if int(d["count"]) > 0]
        if not present:
            print("  deficits:    none measured")
            return
        for d in sorted(present, key=lambda d: (not d["reachable"], -float(d["penalty"]))):
            # A failed Doctor check carries its probe title; a measured deficit only a key.
            label = d["title"] or str(d["key"]).replace("_", " ")
            count, penalty = int(d["count"]), float(d["penalty"])
            if d["reachable"]:
                print(f"  deficit:     ⚠️  {label} ×{count} (−{penalty:.1f}, fixable now)")
            else:
                # `blocked_by` is the producer's own sentence — the panel prints this exact
                # string, so the two surfaces cannot drift into two different explanations.
                # On its own continuation line (this file's established shape for a fix hint)
                # rather than appended: the sentence carries its own dash, and two in one row
                # reads as a stutter. The penalty is shown because it COUNTS: the score is the
                # home's health, not only the part maintenance can repair.
                print(f"  deficit:     ⏹  {label} ×{count} (−{penalty:.1f})")
                print(f"               {d['blocked_by']}")
        if any(d["reachable"] for d in present):
            print("               Fix: personalclaw doctor runs no jobs — use Settings → Doctor")
            print("               → Maintenance → Run now, or wait for the adaptive pass.")
    except Exception as exc:
        print(f"  health:      ⚠️  could not measure ({str(exc)[:120]})")


def _venv_interpreter() -> Path | None:
    """The interpreter of a venv installed beside the sources, or None.

    This is the single input that selects which Runtime rows doctor prints: an
    editable checkout has `<repo>/.venv`, while a pipx or system install does not.
    Naming it is what lets the Runtime rail drive BOTH branches — as a bare
    `is_file()` inside `_doctor()` it was decided by whatever happened to be on the
    developer's disk, so CI (always a venv) never rendered the other branch.
    """
    venv_py = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python3"
    return venv_py if venv_py.is_file() else None


def _address_to_check_from(configured_host: str, bind_host: str) -> str:
    """The address the beyond-loopback token check asks, or ``""`` when there is none to ask.

    It has to be one a request can reach the gateway at WITHOUT arriving from loopback. So:
    the host ``dashboard.url`` names, unless that is itself loopback; else the one interface
    the gateway is bound to, when the bind names one; else, for an all-interfaces bind, this
    machine's own address (:func:`~personalclaw.dashboard.origin.address_beyond_loopback`).
    """
    if configured_host and not is_loopback(configured_host):
        return configured_host
    if bind_host not in ("0.0.0.0", "::", "") and not is_loopback(bind_host):
        return bind_host
    return address_beyond_loopback()


def _probe_python_version(python: str | Path) -> str:
    """Run ``<python> --version`` and return the BARE version, e.g. "3.13.14".

    ``--version`` prints "Python 3.13.14", and every caller renders the result inside
    a row whose label already says python — so the prefix reads "(Python 3.13.14)".
    Stripping it here, at the one place the string is obtained, is deliberate: the
    venv row stripped it and the fallback row did not, which is exactly the drift a
    second `removeprefix` at a second call site invites back.

    Raises on a failed or slow probe; both call sites report that as a row rather
    than letting a probe failure fail the doctor.
    """
    result = subprocess.run([str(python), "--version"], capture_output=True, text=True, timeout=5)
    result.check_returncode()
    return result.stdout.strip().removeprefix("Python ").strip()


def _interpreter_row(label: str, interpreter: object, version: str, issues: list[str]) -> None:
    """One interpreter's row, judged against the installed release's own ``Requires-Python``.

    The ✅ is earned by that comparison and names the range it was made against. Outside the
    range the row fails the doctor and hands back the fix: uv does not enforce the upper bound,
    so an install can be running on a Python the release was never tested on and not know it.
    With no range to compare with (a source tree run without an install) the row says so and
    certifies nothing.
    """
    head = f"  {label + ':':<13}"
    support = python_support(version)
    if support.supported is None:
        print(f"{head}⏹  {interpreter} ({support.version}; {support.unknown})")
        return
    if support.supported:
        print(f"{head}✅ {interpreter} ({support.version}; supported: {support.requires})")
        return
    print(
        f"{head}❌ {interpreter} ({support.version}) — PersonalClaw {_pc_version} supports "
        f"Python {support.requires} only"
    )
    fix = rebuild_command(support.requires) or (
        f"recreate this environment on a Python matching {support.requires}, "
        "then reinstall PersonalClaw into it"
    )
    print(f"               Fix: {fix}")
    issues.append(f"{label} version")


def _pip_row(python: str | Path, issues: list[str]) -> None:
    """The pip *python* (the interpreter the gateway runs on) has: every app's Python packages and
    every app's engine install with it. Without it the row fails the doctor, in the words the
    installer refuses with (``_installer.missing_pip``)."""
    from personalclaw._installer import missing_pip

    try:
        probe = subprocess.run(
            [str(python), "-c", "import pip; print(pip.__version__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = probe.stdout.strip() if probe.returncode == 0 else ""
    except Exception:
        version = ""
    if version:
        print(f"  pip:         ✅ {version} — installs the Python packages apps declare")
        return
    problem, fix = missing_pip(str(python))
    print(f"  pip:         ❌ {problem}")
    print(f"               Fix: {fix}")
    issues.append("pip")


#: What the gateway imports to serve at all, which the dependency row checks.
_GATEWAY_MODULES = ("websockets", "aiohttp")


def _bundle_rows(issues: list[str]) -> None:
    """The dependency rows of the desktop app, whose bundle is the interpreter doctor runs on and
    starts no other Python (``python_children``): what the bundle carries, checked in this
    process (asking it to run ``-c`` reached its CLI's parser, and the row read missing modules),
    and that it has no pip, which is not a fault there but the desktop app's limit."""
    missing = [name for name in _GATEWAY_MODULES if importlib.util.find_spec(name) is None]
    if missing:
        print(f"  deps:        ❌ missing from the desktop app: {', '.join(missing)}")
        issues.append("python deps")
    else:
        print(f"  deps:        ✅ {', '.join(_GATEWAY_MODULES)} available")
    print(
        "  pip:         ⏹  not in the desktop app, which installs no Python packages (the version "
        f"you install with `{python_children.INSTALL_COMMAND}` does)"
    )


def _doctor(*, start_agent_clis: bool = False) -> None:
    """Verify PersonalClaw setup — check dependencies, config, credentials, connectivity.

    Starts no agent CLI unless ``start_agent_clis`` (``--start-agent-clis``) says to.
    """

    print("PersonalClaw Doctor\n")
    issues: list[str] = []

    # ── Dependencies ──
    print("Dependencies")

    from personalclaw.net.git import MIN_GIT_VERSION, git_problem, git_version

    git = shutil.which("git")
    need = ".".join(str(part) for part in MIN_GIT_VERSION)
    have = ".".join(str(part) for part in (git_version() if git else None) or ())
    if not git:
        print(f"  git:         ❌ not found (needed for personalclaw update; git {need} or newer)")
        issues.append("git")
    elif git_problem():
        # PersonalClaw's git refuses it (`net.git.GitTooOld`): an older git ignores the settings
        # that stop a repository's own configuration from running a program.
        print(f"  git:         ❌ {git} is git {have}; PersonalClaw needs git {need} or newer")
        print(f"               Fix: install git {need} or newer")
        issues.append("git")
    else:
        print(f"  git:         ✅ {git}" + (f" (git {have})" if have else ""))

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
                    f"  node:        ⚠️  v{major} < {_MIN_NODE_VERSION} (frontend needs Node {_MIN_NODE_VERSION}+)"  # noqa: E501
                )
                # Both halves of the sentence come from the one constant the comparison
                # above uses. The Fix line hardcoded `>= 16` while the warning interpolated
                # 18, so the pair stated one requirement as two numbers and neither the
                # reader nor a future edit could tell which was enforced (#2908).
                print(f"               Fix: install Node.js >= {_MIN_NODE_VERSION}")
        except Exception:
            # `node -v` answered something this cannot parse, so the version — the only
            # thing this check exists to establish — is unknown. A ✅ here claimed the
            # check had passed on evidence it never got.
            print(f"  node:        ⏭  {node} (version unknown: `node -v` did not parse)")
            print(f"               Fix: confirm Node.js >= {_MIN_NODE_VERSION} is installed")
    else:
        print(f"  node:        ⚠️  not found (frontend needs Node {_MIN_NODE_VERSION}+)")
        print(f"               Fix: install Node.js >= {_MIN_NODE_VERSION}")

    # SQLite driver + capabilities: FTS5/JSON1 are what the
    # knowledge + memory search paths need, and the bundled stdlib build lacks them
    # on some platforms — so report the resolved driver and whether they're present.
    from personalclaw.sqlite_compat import probe as _sqlite_probe

    _sq = _sqlite_probe()
    _fts = "✅" if _sq.fts5 else "❌"
    _json1 = "✅" if _sq.json1 else "❌"
    print(f"  SQLite:      {_sq.driver} {_sq.version}, FTS5 {_fts}, JSON1 {_json1}")
    if not _sq.fts5:
        print("               Fix: pip install pysqlite3-binary (bundles FTS5)")
        issues.append("sqlite fts5")

    # Unified venv detection — used for runtime section
    venv_py = _venv_interpreter()

    # ── Project ──
    print("\nProject")
    proj = os.environ.get("PERSONALCLAW_PROJECT_DIR", "")
    if proj and Path(proj).is_dir():
        print(f"  project dir: ✅ {proj}")
        print(f"  git repo:    {_git_work_tree_row(Path(proj))}")
    else:
        # Only a source checkout has a project dir: the one the running package is imported
        # from (`self_update.source_checkout`), which the CLI exports at start. Wheel, uv, pipx
        # and Docker installs never have one, and the working directory does not give them one.
        # Report it as not-applicable, matching how doctor reports other inert-by-design rows.
        print("  project dir: ⏹  not set (source checkouts only — not needed here)")

    # ── Agent config ──
    print("\nAgent")
    agent_path = agents_dir() / AGENT_FILENAME
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
    # In words, as the Doctor's own row says it (`approval_grants.setting_sentence`): a bare
    # "auto" said nothing of what it lets an agent no chat started do.
    print(f"  approval:    {approval_grants.setting_sentence()}")
    issues.extend(_doctor_config_readable())

    issues.extend(_doctor_timezone())
    issues.extend(_doctor_credentials())
    issues.extend(_doctor_session_lifetime(cfg))

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

    # Dashboard auth mode, mirroring the token gate on either kind of bind. No channel enters
    # into it: `personalclaw token` mints a sign-in link from the running gateway with none,
    # which is the one-container `docker run`'s documented way in (`docker exec … personalclaw
    # token`). A channel app's own sign-in command is that app's to describe.
    _bind_host = resolve_bind_host()
    _local = is_local_bind(_bind_host)
    if _local:
        print("  bind:        127.0.0.1 (local-only, SSH tunnel for remote)")
        # A local BIND is not a token-free loopback: the default `local_token`
        # gateway still refuses a tokenless loopback request (403 `session_required`,
        # saying how to sign in). Only claim "no token required" when the token gate is
        # actually bypassed (AuthMode.NONE, or PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1) —
        # mirror the middleware, don't infer from the bind alone (#2860).
        if loopback_requires_token():
            print(
                "  auth:        🔒 token required (run: personalclaw token, for a signed-in link)"
            )
        else:
            print("  auth:        loopback trusted (no token required)")
    else:
        _span = "all interfaces" if _bind_host in ("0.0.0.0", "::") else "this interface"
        print(f"  bind:        {_bind_host} ({_span})")
        # The same three answers the loopback branch gives, from the same predicates the
        # middleware short-circuits on. Auth off is not an issue HERE: the remote row below
        # counts it, once.
        if auth_is_off():
            print("  auth:        ❌ off — every request is served without a token")
        elif local_network_bypass_enabled():
            print(
                "  auth:        ⚠️  no token needed from a private-network address"
                " (PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1); a token everywhere else"
            )
        else:
            print(
                "  auth:        🔒 token required on every interface"
                " (run: personalclaw token, for a signed-in link)"
            )

    # An auth mode the runtime cannot honor must be NAMED here, not just left to
    # the startup log. `from_env` silently returned the `local_token`
    # default for `api_key`/`oauth2`/any unknown value, so an operator who
    # believed they had enforced IdP SSO was on a shared bearer token with
    # nothing said. Legibility only: this prints, it does not gate. The
    # misconfiguration fails CLOSED (lost access, not weakened auth) and is a
    # documented pre-1.0 limitation, so it is not an `issues` entry — doctor's
    # exit status still means what it meant before.
    _mode_request = classify_auth_mode_request()
    if _mode_request.detail:
        print(f"  auth mode:   ⚠️  {_mode_request.detail}")

    # ── Remote access ──
    # Reuse the shared tailnet-detection helper so this line and the doctor
    # `remote.reachability` probe agree. No token is minted here — we print the
    # base URL and point at `personalclaw token` for the signed-in link.
    _tnet = tailnet_ip()
    _remote_port = _port or 0
    if not _local and auth_is_off():
        print(
            "  remote:      ❌ bound beyond loopback with auth OFF — set a password"
            " (see docs/guides/remote-access.md) or bind loopback"
        )
        issues.append("remote access: exposed beyond loopback without auth")
    elif _tnet:
        _phone_url = f"http://{_tnet}:{_remote_port}" if _remote_port else f"http://{_tnet}"
        print(f"  remote:      ✅ tailnet {_tnet} — open {_phone_url} on your phone")
        print("               (run: personalclaw token, for the signed-in link)")
    elif _local:
        print("  remote:      local-only — see docs/guides/remote-access.md")
    else:
        # What reaches a bind beyond loopback — this machine's interfaces, or the port a
        # container published — is not something doctor can see, so it says what it established.
        print("  remote:      no tailnet address found — see docs/guides/remote-access.md")

    # ── MCP Tools ──
    print("\nMCP Tools")
    if agent_path.exists():
        issues.extend(_doctor_core_server(agent_path))

    # ── Python Runtime ──
    print("\nRuntime")
    _interpreter_row("python", sys.executable, sys.version.split()[0], issues)
    print(f"  backend:     ✅ {_pc_version}")
    if not python_children.available():
        _bundle_rows(issues)
    elif venv_py is not None:
        try:
            ver = _probe_python_version(venv_py)
        except Exception as exc:
            print(f"  venv python: ❌ broken: {exc}")
            issues.append("venv python")
        else:
            _interpreter_row("venv python", venv_py, ver, issues)
            try:
                subprocess.run(
                    [str(venv_py), "-c", "import websockets, aiohttp"],
                    capture_output=True,
                    timeout=5,
                ).check_returncode()
                print("  deps:        ✅ websockets, aiohttp available")
            except Exception:
                print("  deps:        ❌ missing modules (websockets/aiohttp)")
                issues.append("python deps")
            _pip_row(venv_py, issues)
    else:
        # No checkout beside the sources — pipx, `uv tool`, the container image. Such an install
        # has ONE interpreter, the one running doctor (the `python:` row), so its dependencies
        # are checked there. This used to check whatever `python3` the PATH named, under a
        # `fallback:` row that warned with no words — in the image, about the same venv as the
        # row above it — and no part of PersonalClaw runs its gateway on that interpreter.
        try:
            subprocess.run(
                [sys.executable, "-c", "import websockets, aiohttp"],
                capture_output=True,
                timeout=5,
            ).check_returncode()
            print("  deps:        ✅ websockets, aiohttp available")
        except Exception:
            print("  deps:        ❌ missing modules (websockets/aiohttp)")
            issues.append("python deps")
        _pip_row(sys.executable, issues)

    # WSL note: the background service depends on systemd, which WSL2 only runs
    # when /etc/wsl.conf opts in. Detect it here so a Windows user knows whether
    # `personalclaw service install` will actually persist. Best-effort; never
    # raises — a probe failure must not fail the doctor.
    try:
        from personalclaw.env import _is_wsl
        from personalclaw.service.common import Platform, current_platform

        if _is_wsl():
            print("  platform:    🪟 WSL detected (Windows Subsystem for Linux)")
            if current_platform() is Platform.SYSTEMD:
                print("  service:     ✅ systemd active — `personalclaw service install` works")
            else:
                print("  service:     ⚠️  systemd not active — background service won't persist")
                print("               Fix: add `[boot]\\nsystemd=true` to /etc/wsl.conf, then")
                print("               run `wsl --shutdown` from Windows and reopen the shell.")
                print("               Without it, run the gateway in a foreground shell (or via")
                print("               Windows Task Scheduler). See docs/guides/platforms.md.")
    except Exception:
        pass

    # What the running gateway measures, for the two sections a live one answers (Maintenance,
    # Provider Health): asked once, before either prints.
    gateway = _read_running_gateway()

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
    issues.extend(_doctor_external_vector_store())

    _doctor_backups()
    _doctor_maintenance(gateway)

    # ── Speech-to-Text ──
    # STT resolves through the typed registry: enabled lives in
    # use_case_settings/stt.json, the active model in active_models.json.
    print("\nSpeech-to-Text")
    from personalclaw.providers.use_cases import load_use_case_settings, use_case_enabled
    from personalclaw.stt.registry import active_stt

    stt_active = use_case_enabled("stt", load_use_case_settings("stt"))
    stt_resolved = active_stt()

    if not stt_active:
        print("  status:      ⏹ disabled (enable in Settings → Voice)")
    elif stt_resolved is None:
        # Not a failure: STT backends are opt-in apps now (e.g. the faster-whisper
        # app) plus remote OpenAI-family providers. With none installed/bound, STT is
        # simply unconfigured — report it, but don't fail the doctor (a fresh core is
        # expected to boot without media backends).
        print(
            "  status:      ⏹  no STT model configured (install an STT app or bind one in Settings → Models)"  # noqa: E501
        )
    else:
        print(f"  model:       ✅ {stt_resolved[0].name}:{stt_resolved[1]}")

    from personalclaw.ffmpeg_binary import find_ffmpeg
    from personalclaw.ffmpeg_binary import install_hint as ffmpeg_install_hint

    ffmpeg_bin = find_ffmpeg()
    if ffmpeg_bin:
        print(f"  ffmpeg:      ✅ {ffmpeg_bin}")
    elif stt_active and stt_resolved is not None:
        # Gated on a RESOLVED model, the same predicate the faster-whisper probe below
        # uses — and the one the `stt_resolved is None` branch above already reasoned out
        # loud: "a fresh core is expected to boot without media backends". Gated on
        # `stt_active` alone it was not, because that flag DEFAULTS TO TRUE, so every
        # install with no ffmpeg and no STT model — a slim container, the published image,
        # any machine where nobody ran a package manager — exited `❌ Fix these issues:
        # ffmpeg`, demanding a transcoder for a feature that has nothing to transcode with.
        # Two branches of one check must not disagree about whether unconfigured STT is a
        # fault.
        print("  ffmpeg:      ❌ not found")
        print(f"               Fix: {ffmpeg_install_hint()}")
        issues.append("ffmpeg")
    elif stt_active:
        print("  ffmpeg:      ⏭  not installed (not needed until an STT model is bound)")
    else:
        print("  ffmpeg:      ⏭  not installed (not needed)")

    # faster-whisper runtime dep. Declared in pyproject.toml extras, but a dev
    # environment created before the dep landed may not have it until
    # `pip install -e .[stt]` is re-run. Catching that here avoids a blank mic
    # click at runtime.
    #
    # PRESENCE ONLY — never `import faster_whisper` here (#3324). That import pulls
    # torch, whose bundled libomp.dylib is a SECOND copy of the OpenMP runtime; a
    # process holding both it and faiss's copy aborts (SIGABRT, `OMP: Error #15`) the
    # next time faiss enters a parallel region, which is the `search` every episodic
    # write does to dedup. This line was the suite's only torch-residency site, so it
    # aborted whichever xdist worker later ran a faiss-searching test — reported as
    # `worker 'gwN' crashed` against an unrelated test name. `find_spec` answers the
    # question this probe actually asks, "is the dep installed in this env", without
    # executing the package. The trade-off is deliberate: a package that is installed
    # but broken now reads as present here, and surfaces at first use instead. The
    # invariant is enforced by tests/native_omp_guard.py.
    if stt_active and stt_resolved is not None:
        try:
            found = importlib.util.find_spec("faster_whisper") is not None
        except (ImportError, ValueError):
            found = False
        if found:
            print("  faster_whisper: ✅ installed")
        else:
            print("  faster_whisper: ❌ missing")
            # Through the extra, which bounds the PyAV faster-whisper decodes with: its own
            # `av>=11` admits a release its decoder cannot call.
            print("               Fix: pip install 'personalclaw[stt]'")
            issues.append("faster_whisper missing")

    # ── App-contributed doctor probes ──
    # Each installed + enabled app whose manifest declares `cli.doctor` renders its
    # own section here (bounded by a hard timeout + exception guard). This is the
    # generic seam that replaced core's former hardcoded channel-app section — a
    # channel app now ships its own probe via `cli.doctor`. Core's doctor names no vendor.
    from personalclaw.app_cli import run_app_doctor_probes

    issues.extend(run_app_doctor_probes())

    # ── Provider Health ──
    print("\nProvider Health")
    _provider_issues = _doctor_providers(gateway, start_agent_clis=start_agent_clis)
    issues.extend(_provider_issues)

    # ── Connectivity ──
    print("\nConnectivity")
    # This home's gateway, as the reading above reached it (`home_gateway.reach`): the port it
    # recorded, or the one PERSONALCLAW_PORT names, once it showed it serves this home. Whatever
    # answers on the configured port may be another home's, and is not this home's to report on.
    # Its status is asked at 127.0.0.1; any HTTP answer (a 401/403 from token auth too) means up.
    is_remote = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))
    _live = gateway.port
    _absent = (
        "the port named is not where this home's gateway answers (see measured: above)"
        if gateway.refused
        else "no gateway of this home is running"
    )

    if _live is None:
        print(f"  gateway:     ⏹  {_absent}")
    else:
        try:
            req = urllib.request.Request(f"http://{home_gateway.LOOPBACK_HOST}:{_live}/api/status")
            with home_gateway.open_loopback(req, timeout=2) as resp:
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

    # SSH tunnel hint for remote hosts: the port this home's gateway listens on, else the one it
    # is configured to bind.
    _tunnel = _live or _port
    if _tunnel and is_remote:
        mh = machine_hostname() or "this-host"
        print("\n  💡 Remote access: Run on your LOCAL machine:")
        print(f"     ssh -L {_tunnel}:localhost:{_tunnel} {mh}")
        print("     Then run: personalclaw token")

    # Verify a request with no token is refused beyond loopback (security check). It is asked of
    # an address that is not loopback, because a request from loopback is not the one a bind
    # beyond it exposes: the host `dashboard.url` names, else the one interface the gateway is
    # bound to, else this machine's own address — the container's, in the one-container
    # `docker run`, which names no host. With none of those there is nothing to ask from, and
    # the row says so rather than failing a check it never ran. It asks this home's gateway, at
    # the port it listens on; with none running there is nothing to ask.
    if not _local and _live is None:
        print(f"  auth check:  ⏭  not checked — {_absent}")
    elif not _local:
        _probe = _address_to_check_from(_host, _bind_host)
        if not _probe:
            print(
                "  auth check:  ⏭  not checked — found no address of this machine beyond loopback"
            )
        else:
            _url_host = f"[{_probe}]" if ":" in _probe else _probe
            try:
                ext_req = urllib.request.Request(f"http://{_url_host}:{_live}/api/status")
                try:
                    with urllib.request.urlopen(ext_req, timeout=2):
                        # 200 without a token: the gate let this address in.
                        if local_network_bypass_enabled() and is_private_network(_probe):
                            # What the auth row above already said the bypass does — the
                            # same legibility-only line the loopback branch prints for it.
                            print(
                                f"  auth check:  ⚠️  {_probe} answered a request with no token"
                                " — the private-network bypass lets it in"
                            )
                        else:
                            print(f"  auth check:  ❌ {_probe} answered a request with no token")
                            issues.append("dashboard auth: no token required on external interface")
                except urllib.error.HTTPError as he:
                    if he.code in (401, 403):
                        print(f"  auth check:  ✅ a request with no token is refused at {_probe}")
                    else:
                        print(f"  auth check:  ⚠️  HTTP {he.code} from {_probe}")
            except Exception:
                print(f"  auth check:  ⏭  could not reach {_probe}:{_live} to check")

    # ── Summary ──
    print()
    if issues:
        print(f"❌ Fix these issues: {', '.join(issues)}")
        sys.exit(1)
    else:
        print("✅ PersonalClaw is ready!")
