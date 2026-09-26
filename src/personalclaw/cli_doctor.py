"""CLI doctor subcommand — verify PersonalClaw setup and diagnose issues."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from personalclaw import __version__ as _pc_version
from personalclaw.agent import AGENT_FILENAME, agents_dir
from personalclaw.atomic_write import atomic_write
from personalclaw.auth.modes import classify_auth_mode_request
from personalclaw.config import AppConfig
from personalclaw.config import loader as config_loader
from personalclaw.config.credentials import credential_backend_warning, credential_store_state
from personalclaw.dashboard.origin import (
    auth_is_off,
    is_local_bind,
    loopback_requires_token,
    machine_hostname,
    parse_dashboard_url,
    resolve_bind_host,
    tailnet_ip,
)
from personalclaw.transcribe import ensure_ffmpeg_in_path


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


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
        "ready": "✅",
        "not_found": "❌",
        "needs_login": "🔑",
        "timeout": "⏳",
        "error": "❌",
    }.get(status.state, "⚠️")
    print(f"  {label}: {icon} {status.detail}")
    if not status.ready:
        issues.append(f"{label}: {status.state}")


def _doctor_paths() -> None:
    """Print the resolved install paths as machine-friendly ``key<TAB>path`` lines.

    The ``doctor get install-dir`` pattern (PLATFORM-LEGIBILITY §3.1): an external
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
    """Refold ``routing_stats.json`` from the model-call audit — the §1.3 rebuild path.

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
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
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
    """Print which credential store is holding the secrets; return any issues (SH-1).

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
            # Legibility only: this prints, it does not gate. Same call as SL-8's auth-mode
            # line — the fallback repairs the mode on the next credential read, so doctor's
            # exit status must not start failing installs it used to pass.
            print(
                f"               ⚠️  mode {state.env_mode} is group/world readable —"
                " repaired to 0600 on the next credential read"
            )
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


def _doctor_maintenance() -> None:
    """Print the remediation engine's health score and the deficits behind it.

    🔴 THE SECOND SURFACE OF THE SAME DROPPED EVIDENCE. `/api/doctor/remediation` measures
    every deficit and the dashboard renders them; ``personalclaw doctor`` measured nothing
    and printed no part of that payload. On a home with 25 knowledge items and no embedder
    the CLI said exactly one thing about it — ``embeddings: ⏹ disabled`` — naming the
    missing PREREQUISITE while staying silent about its CONSEQUENCE, the 25 items now
    keyword-only. Whoever reads the CLI instead of the dashboard read "a feature is off",
    not "a backlog is stuck".

    Deliberately NOT appended to ``issues`` (which exits 1): a deficit is a maintenance
    backlog, not a broken setup, and a fresh install with unembedded notes must not fail its
    own doctor. Best-effort like every other probe here — a measure that raises prints one
    line and moves on, because a health read must never break the setup check that follows.
    """
    print("\nMaintenance")
    try:
        from personalclaw.config.loader import AppConfig as _Cfg
        from personalclaw.resilience.remediation import health_score, measure_deficits

        deficits = measure_deficits()
        target = float(_Cfg.load().resilience.remediation.target_score)
        score = health_score(deficits)
        mark = "✅" if score >= target else "⚠️ "
        print(f"  health:      {mark} score {score:g} / target {target:g}")
        # Zero-count sources are measurements, not problems — the same filter the panel
        # applies, for the same reason: listing them buries the real ones.
        present = [d for d in deficits if d.count > 0]
        if not present:
            print("  deficits:    none measured")
            return
        for d in sorted(present, key=lambda d: (not d.reachable, -d.penalty)):
            label = d.key.replace("_", " ")
            if d.reachable:
                print(f"  deficit:     ⚠️  {label} ×{d.count} (−{d.penalty:.1f}, fixable now)")
            else:
                # `blocked_by` is the producer's own sentence — the panel prints this exact
                # string, so the two surfaces cannot drift into two different explanations.
                # On its own continuation line (this file's established shape for a fix hint)
                # rather than appended: the sentence carries its own dash, and two in one row
                # reads as a stutter.
                print(f"  deficit:     ⏹  {label} ×{d.count}")
                print(f"               {d.blocked_by}")
        if any(d.reachable for d in present):
            print("               Fix: personalclaw doctor runs no jobs — use Settings → Doctor")
            print("               → Maintenance → Run now, or wait for the adaptive pass.")
    except Exception as exc:
        print(f"  health:      ⚠️  could not measure ({str(exc)[:120]})")


def _ffmpeg_install_hint(platform: str | None = None) -> str:
    """The ffmpeg install line for THIS platform.

    Doctor's whole job on a fault line is to hand back a command that works where it is
    read, and `brew` works on exactly one of the three platforms this ships to. Measured
    inside the published Linux container: `Fix: brew install ffmpeg`, on a machine with no
    brew and no way to get one. Pure + parameterised so every branch is testable without
    faking `sys.platform` globally.
    """
    plat = sys.platform if platform is None else platform
    if plat == "darwin":
        return "brew install ffmpeg"
    if plat.startswith("win"):
        return "winget install ffmpeg"
    return "apt install ffmpeg (or your distribution's package manager)"


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

    # SQLite driver + capabilities (PLATFORM-REACH PR-1): FTS5/JSON1 are what the
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
        print(f"  git repo:    {_git_work_tree_row(Path(proj))}")
    elif not stale_project:
        # Only a source checkout has a project root (a dir holding both agents/ and
        # skills/). Wheel, uv, pipx and Docker installs never have one, and `setup`
        # only records the path when `_detect_project_dir` already found it — so the
        # old "run personalclaw setup from project root" advice named a directory
        # most users do not have and a re-run could never create. Report it as
        # not-applicable, matching how doctor reports other inert-by-design rows.
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
    print(f"  approval:    {cfg.agent.approval_mode}")
    issues.extend(_doctor_config_readable())

    issues.extend(_doctor_timezone())
    issues.extend(_doctor_credentials())

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

    # Dashboard auth mode. Whether a channel can carry a remote token is each channel app's
    # own answer (its health, over the transports doctor's provider bootstrap registered),
    # not a lookup of two Slack credential names.
    import asyncio

    from personalclaw.channel_transports import configured_channels

    _has_channel = bool(asyncio.run(configured_channels()))
    _bind_host = resolve_bind_host()
    _local = is_local_bind(_bind_host)
    if _local:
        print("  bind:        127.0.0.1 (local-only, SSH tunnel for remote)")
        # A local BIND is not a token-free loopback: the default `local_token`
        # gateway still returns 403 {"error": "Token required"} to a tokenless
        # loopback request. Only claim "no token required" when the token gate is
        # actually bypassed (AuthMode.NONE / PERSONALCLAW_DEV_NO_AUTH=1, or
        # PERSONALCLAW_BYPASS_LOCAL_NETWORKS=1) — mirror the middleware, don't
        # infer from the bind alone (#2860).
        if loopback_requires_token():
            print(
                "  auth:        🔒 token required (run: personalclaw token, for a signed-in link)"
            )
        else:
            print("  auth:        loopback trusted (no token required)")
    else:
        print("  bind:        0.0.0.0 (all interfaces)")
        print("  auth:        ✅ token auth required (via !dashboard)")
        if not _has_channel:
            print("  auth:        ⚠️  no channel configured — token generation unavailable")
            issues.append("dashboard auth: remote bind without a channel")

    # An auth mode the runtime cannot honor must be NAMED here, not just left to
    # the startup log (SL-8). `from_env` silently returned the `local_token`
    # default for `api_key`/`oauth2`/any unknown value, so an operator who
    # believed they had enforced IdP SSO was on a shared bearer token with
    # nothing said. Legibility only: this prints, it does not gate. The
    # misconfiguration fails CLOSED (lost access, not weakened auth) and is a
    # documented pre-1.0 limitation, so it is not an `issues` entry — doctor's
    # exit status still means what it meant before.
    _mode_request = classify_auth_mode_request()
    if _mode_request.detail:
        print(f"  auth mode:   ⚠️  {_mode_request.detail}")

    # ── Remote access (MOBILE-COMPANION S1) ──
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
    else:
        print("  remote:      local-only — see docs/guides/remote-access.md")

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
        for ref in ("@personalclaw-core",):
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
            atomic_write(agent_path, json.dumps(agent_data, indent=2) + "\n")
            if mcp_fixed:
                print("  → Auto-fixed tools/allowedTools in personalclaw.json")
                issues = [i for i in issues if "config" not in i]
            if mcp_cmd_fixed:
                print("  → Auto-fixed stale binary path(s) in personalclaw.json")

    # ── Python Runtime ──
    print("\nRuntime")
    print(f"  python:      ✅ {sys.executable} ({sys.version.split()[0]})")
    print(f"  backend:     ✅ {_pc_version}")
    if venv_py is not None:
        try:
            ver = _probe_python_version(venv_py)
            print(f"  venv python: ✅ {venv_py} ({ver})")
        except Exception as exc:
            print(f"  venv python: ❌ broken: {exc}")
            issues.append("venv python")
        else:
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
    else:
        # Non-venv install: fall back to checking the system python.
        sys_py = shutil.which("python3")
        if sys_py:
            try:
                print(f"  fallback:    ⚠️  {sys_py} ({_probe_python_version(sys_py)})")
            except Exception as exc:
                # The probe shares the venv row's policy now that it shares its
                # helper: report the failure as a row, never fail the doctor.
                print(f"  fallback:    ⚠️  {sys_py} (version unavailable: {exc})")
            try:
                subprocess.run(
                    [sys_py, "-c", "import websockets, aiohttp"],
                    capture_output=True,
                    timeout=5,
                ).check_returncode()
                print("  deps:        ✅ websockets, aiohttp available")
            except Exception:
                print("  deps:        ❌ missing modules (websockets/aiohttp)")
                issues.append("python deps")
        else:
            # Same probe as the `fallback:` row above — its other outcome, so it
            # carries the same label rather than a second "python:".
            print("  fallback:    ⚠️  python3 not found on PATH")

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

    _doctor_maintenance()

    # ── Speech-to-Text ──
    # STT resolves through the typed registry: enabled lives in
    # use_case_settings/stt.json, the active model in active_models.json.
    print("\nSpeech-to-Text")
    from personalclaw.providers.use_cases import load_use_case_settings
    from personalclaw.stt.registry import active_stt

    stt_active = bool(load_use_case_settings("stt").get("enabled", True))
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

    ensure_ffmpeg_in_path()
    ffmpeg_bin = shutil.which("ffmpeg")
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
        print(f"               Fix: {_ffmpeg_install_hint()}")
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
            print("               Fix: pip install faster-whisper")
            issues.append("faster_whisper missing")

    # ── App-contributed doctor probes ──
    # Each installed + enabled app whose manifest declares `cli.doctor` renders its
    # own section here (bounded by a hard timeout + exception guard). This is the
    # generic seam that replaced core's former hardcoded channel-app section — a
    # channel app now ships its own probe via `cli.doctor` (see
    # PROVIDER-BOUNDARY-COMPLETION). Core's doctor names no vendor.
    from personalclaw.app_cli import run_app_doctor_probes

    issues.extend(run_app_doctor_probes())

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
