"""Shared pytest configuration and fixtures."""

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
import real_home_guard
from hypothesis import HealthCheck, settings

# NOTE: this suite is standalone — it must collect + pass on a clone of this
# package alone, with NO sibling apps/ directory. Channel/provider seams are
# exercised against in-tree fakes (tests/fakes.py); tests of app-INTERNAL
# behavior (slack_runtime, the ollama provider module) live with their apps
# (apps/slack-channel/tests/, apps/ollama-models/tests/). Workspace-layout
# tests (apps import-boundary lint, ACP bundles, web-tools app wiring) skip
# themselves when apps/ is absent.

# ── Hypothesis profiles ─────────────────────────────────────────────────
# Default (CI): fast iteration.  Run ``HYPOTHESIS_PROFILE=thorough make build test``
# for deeper coverage.
settings.register_profile(
    "default", max_examples=20, suppress_health_check=[HealthCheck.too_slow], deadline=None
)
settings.register_profile("thorough", max_examples=100)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))

_HAS_GIT = shutil.which("git") is not None

requires_git = pytest.mark.skipif(not _HAS_GIT, reason="git not available")


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure a current event loop exists for code that constructs asyncio
    primitives (e.g. Semaphore) at import/init time outside a running loop."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _isolate_real_home_writers(tmp_path_factory, monkeypatch):
    """Make an UNSPECIFIED home mean a per-test tmp dir instead of the developer's
    real ``~/.personalclaw`` (CRE-8).

    The hazard, measured rather than assumed: a plain
    ``pytest -k "project or memory or knowledge or recall"`` appended **44,402 bytes** of
    `artifact_save`/`tool_invocation` rows to the user's real
    ``~/.personalclaw/security_events.jsonl`` and created/rewrote 62 more real-home entries
    (`tasks/*.json`, `codegraph/*.db`, `workspace/_ext/*/memory/*.md`, `prompts/`,
    `prompt_snippets/`, `learning.db`, `session_search.db`, `tokenjuice_savings.json`, …).
    Thirteen distinct writer families, and every one of them reached the real home through
    the same two seams: ``config.loader.config_dir()`` (153 call sites) and SEL's own
    ``sel._default_dir()``. Patching thirteen subsystems one at a time would have been
    thirteen fixtures guarding one seam, and the fourteenth subsystem would leak again.

    So this redirects those two seams, and ONLY when the caller expressed no preference:

    * ``$PERSONALCLAW_HOME`` set (to anything, **including the real home**) → pass through
      untouched. Several rails deliberately point it at the real home and assert a refusal
      (``test_cli_gateway_flags``'s ``--approval yolo`` rails, ``test_seed``'s main-home
      rails); redirecting an explicit choice would make those rails vacuous.
    * ``$HOME``/``Path.home()`` repointed by the test → pass through untouched. That test
      already isolated itself, and its assertions read back from *its* home.
    * neither → the resolution would be the real home purely by default. Redirect.

    Deliberately NOT done, both previously rejected in this repo and re-rejected here:
    a global ``$PERSONALCLAW_HOME`` for pytest jobs (CRE-6 removed exactly that: it takes
    precedence over ``Path.home()`` inside ``config_dir``, so it defeats the tests that
    assert env precedence and the ones that assert the default resolution), and a blanket
    ``Path.home`` patch (see ``_isolate_session_map`` — it breaks the real-home safety
    rails, and it would also silently redefine the unrelated ``Path.home()/".aws"`` and
    ``Path.home()/".ssh"`` paths that the artifact/task sensitivity tests assert on).

    What a fixture CANNOT reach, and therefore was fixed at source instead: a home resolved
    into a module-level constant at import time. The rail below caught 147 real-home entries
    still landing in ``subagents/`` after this fixture was in place, because
    ``subagent_persistence`` froze ``config_dir() / "subagents"`` at first import — before any
    fixture exists. Three such constants were converted to call-time resolvers
    (``subagent_persistence._subagents_dir``, ``session_map._sessions_dir``, and a dead
    ``schedule._DEFAULT_DIR`` whose import-time ``config_dir()`` mkdir'd the real home merely
    by importing the module). If a new leak appears here, check for that shape first.

    Ordering matters: this fixture is declared BEFORE ``_reset_sel_singleton`` so it is set
    up first and torn down LAST. The singleton is cleared around every test, so the next
    ``sel()`` call constructs a fresh ``SecurityEventLog`` — and that construction must
    still find the redirected ``_default_dir``, or the leak comes straight back.
    """
    import personalclaw.config.loader as config_loader
    import personalclaw.sel as sel_mod

    real_home = real_home_guard.REAL_HOME
    holder: list[Path] = []

    def tmp_home() -> Path:
        # Created lazily: most tests never resolve an unspecified home, and eagerly
        # minting a tmp dir per test would add thousands of empty dirs to basetemp.
        if not holder:
            holder.append(tmp_path_factory.mktemp("pclaw-home"))
        return holder[0]

    def caller_chose_a_home() -> bool:
        return bool(os.environ.get("PERSONALCLAW_HOME")) or Path.home() != real_home.parent

    original_config_dir = config_loader.config_dir
    original_sel_dir = sel_mod._default_dir

    def guarded_config_dir() -> Path:
        if caller_chose_a_home():
            return original_config_dir()
        # NB: return the tmp dir WITHOUT delegating first — config_dir() mkdirs whatever
        # it resolves, so delegating would create ~/.personalclaw on a machine that has
        # none (the rail's own "absent home" case) before we could redirect it.
        return tmp_home()

    def guarded_sel_dir() -> Path:
        if caller_chose_a_home():
            return original_sel_dir()
        return tmp_home()

    monkeypatch.setattr(config_loader, "config_dir", guarded_config_dir)
    monkeypatch.setattr(sel_mod, "_default_dir", guarded_sel_dir)
    # `from ... import config_dir` at module scope binds the function object into the
    # importing module, where patching the loader can never reach it (58 such modules).
    # Re-point every binding of THIS function object — identity-matched, so nothing else
    # is touched. Function-local imports (95 sites, incl. every `as _cd` alias) resolve
    # from the loader at call time and are already covered by the patch above.
    for module in list(sys.modules.values()):
        if module is None or not getattr(module, "__name__", "").startswith("personalclaw"):
            continue
        if getattr(module, "config_dir", None) is original_config_dir:
            monkeypatch.setattr(module, "config_dir", guarded_config_dir)


@pytest.fixture(autouse=True)
def _isolate_session_map(tmp_path_factory, monkeypatch):
    """Point the SESSION MAP at a per-test tmp dir so nothing touches the real
    ~/.personalclaw/session_map.json. SessionManager.__init__ builds a SessionMap()
    that reads/prunes/REWRITES config_dir()/session_map.json at construction time — so
    any test that does SessionManager(cfg) without its own home patch mutates the USER's
    real session map (observed: a SessionMap key migration ran against the live file
    during a rename). Scoped to session_map.config_dir only (NOT a global Path.home
    patch, which breaks tests that assert real-home safety rails — seed/loop-validation).
    A test that patches session_map.config_dir itself still overrides this (last wins)."""
    map_home = tmp_path_factory.mktemp("pclaw-sessmap")
    monkeypatch.setattr("personalclaw.session_map.config_dir", lambda: map_home)


@pytest.fixture(autouse=True)
def _isolate_trigger_store(tmp_path_factory, monkeypatch):
    """Point the BOOT TRIGGER MIGRATION at a per-test tmp home (S98).

    Same hazard and same remedy as `_isolate_session_map` above. `GatewayOrchestrator._init_cron`
    now runs `boot_migrate.migrate_and_arm(config_dir())`, which imports `crons.json` into
    `triggers.json` and ARMS the imported clocks. Three pre-existing tests call `_init_cron` with no
    home isolation at all (`test_gateway`, `test_cron_acp_retry`, `test_cron_thread_routing`) — they
    were harmless only because that path never wrote before. Observed: a full-suite run migrated the
    USER's real crons into `~/.personalclaw/triggers.json`.

    Scoped to the two seams that build a store from the ACTIVE HOME rather than a global `Path.home`
    patch, for the reason the fixture above gives: a blanket patch breaks the tests that assert
    real-home safety rails. A test that patches either itself still overrides it (last wins), and
    every test that passes an explicit `base_dir` is unaffected.

    🔴 The second seam was added in S101: re-pointing the `/api/triggers` WRITES means the
    handler's `_trigger_store()` now persists a created/updated row, and four pre-existing
    dashboard tests call that handler with no home isolation. Observed on a full-suite run:
    `clock:t`, `clock:t-2`, `clock:t-3` and `clock:test` landed in the USER's real
    `~/.personalclaw/triggers.json`. Any new path that WRITES a store built from `config_dir()`
    has to be redirected here too.

    🔴 The THIRD seam was added in S108: `_init_cron` now runs the app-cron and digest reconcilers
    against a store built from `gateway.config_dir()`, so `test_gateway`'s unisolated `_init_cron`
    calls wrote `system:notification-digest` into the USER's real store (reproduced by deleting the
    file and running that one file). Four occurrences of this hazard now; the rule is the docstring
    above, and the check is `ls ~/.personalclaw` after any suite run that adds a writer."""
    store_home = tmp_path_factory.mktemp("pclaw-triggers")
    monkeypatch.setattr("personalclaw.triggers.boot_migrate.config_dir", lambda: store_home)
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.triggers.config_dir", lambda: store_home, raising=False
    )
    monkeypatch.setattr("personalclaw.gateway.config_dir", lambda: store_home, raising=False)


@pytest.fixture(autouse=True)
def _reset_trust_mode():
    """Reset the process-global YOLO/auto-approve trust state around every test.

    ``personalclaw.trust_mode`` is a deliberate process singleton (one auto-approve
    posture per gateway). Tests that flip it must not leak into the next test, so we
    force it OFF before and after each test.
    """
    import personalclaw.trust_mode as _tm

    _tm._TRUST.disable()
    yield
    _tm._TRUST.disable()


@pytest.fixture(autouse=True)
def _reset_model_call_breakers():
    """Reset the process-global model-call circuit breakers around every test.

    ``guardrails.breaker`` keeps one breaker per provider name for the gateway's
    lifetime (in-process by design — a restart resetting it is acceptable for a
    single-user gateway). Under pytest-xdist a breaker tripped OPEN by one test
    would otherwise refuse calls in a later test in the same worker, so clear the
    registry before + after each test — the same discipline as the SEL singleton.
    """
    from personalclaw.guardrails.breaker import reset_breakers
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.incident import reset_incident_mirror

    reset_breakers()
    reset_meter()
    reset_incident_mirror()
    yield
    reset_breakers()
    reset_meter()
    reset_incident_mirror()


@pytest.fixture(autouse=True)
def _reset_context_engine_breakers():
    """Reset the context engine's process-global timeout counters around every test.

    ``context_engine`` keeps two module-level consecutive-timeout counters — one for
    active recall, one for the push reflex — that latch their feature OFF for the rest of
    the process once they reach 3. That is correct for a gateway (a slow memory store
    shouldn't be retried on every turn) and wrong for a test session: under xdist, three
    timeouts anywhere in a worker would silently disable recall/push for every later test
    in that worker, and the symptom would be an empty block rather than an error. Same
    discipline as the model-call breakers above.
    """
    import personalclaw.context_engine as ce

    ce._recall_consecutive_timeouts = 0
    ce._push_consecutive_timeouts = 0
    yield
    ce._recall_consecutive_timeouts = 0
    ce._push_consecutive_timeouts = 0


@pytest.fixture(autouse=True)
def _reset_sel_singleton():
    """Reset the process-global Security Event Log singleton around every test.

    ``SecurityEventLog`` is a ``__new__``-based singleton whose ``__init__`` no-ops
    once ``_initialized`` — so the FIRST test to touch ``sel()`` pins ``_dir`` to its
    own home, and every later test in the same worker inherits that stale path. Under
    ``pytest-xdist`` which test lands first per worker varies, so SEL-reading/asserting
    tests (doctor STT, ACP-died recovery, auto-skill audit, …) failed nondeterministically.
    Clearing the class-level state before + after each test gives every test a fresh SEL
    bound to its own isolated home — the same discipline as ``_reset_trust_mode`` above.
    """
    from personalclaw.sel import SecurityEventLog as _SEL

    def _clear() -> None:
        _SEL._instance = None
        _SEL._initialized = False

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _isolate_single_flight_locks(tmp_path_factory, monkeypatch):
    """Point the cross-process single-flight lock dir at a per-test tmp dir.

    ``concurrency.single_flight(job_key)`` takes an OS ``flock`` on
    ``config_dir()/locks/<job_key>.lock`` so only one PROCESS consolidates a given
    key at a time — correct in production, but a cross-test hazard under xdist:
    all workers share one ``PERSONALCLAW_HOME`` (one ``config_dir()``), and several
    tests reuse the same consolidation key (e.g. ``consolidate:dashboard:chat-empty``).
    Two such tests landing on different workers then contend for the SAME lock file —
    the loser's ``single_flight`` returns False, its consolidation is skipped, and its
    SEL-audit assertions see an empty record (a rotating ~1-in-3 red). Isolating the
    lock DIR per test makes each test's keys resolve to their own files, so no two
    tests can collide regardless of worker placement. A test that patches the locks
    dir itself still overrides this (last wins)."""
    locks_home = tmp_path_factory.mktemp("pclaw-locks")
    monkeypatch.setattr("personalclaw.concurrency._locks_dir", lambda: locks_home)


@pytest.fixture(autouse=True)
def _disable_live_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-set PERSONALCLAW_DISABLE_LIVE_WRITES for the whole suite (§1.4).

    Live, hard-to-reverse writes (deleting a downloaded model, a non-GET egress to
    a non-loopback host) are refused with a typed error under this flag. PClaw was
    already bitten by exactly this: a destructive test with no models-dir
    monkeypatch deleted the user's real bound local model. A test that GENUINELY
    exercises a live-write path opts out explicitly
    (``monkeypatch.delenv('PERSONALCLAW_DISABLE_LIVE_WRITES', raising=False)``) —
    making the intent to write real state visible, never accidental."""
    monkeypatch.setenv("PERSONALCLAW_DISABLE_LIVE_WRITES", "1")


@pytest.fixture(autouse=True)
def _no_acp_provision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never auto-provision (npm-install) ACP adapters during tests — provisioning
    is a real network + filesystem side effect (writes to the managed prefix under
    the user's home). Bundles that would otherwise install an adapter fall back to
    the npx-fallback argv, which is exactly what the resolution tests assert on."""
    monkeypatch.setenv("PERSONALCLAW_ACP_NO_PROVISION", "1")


@pytest.fixture(autouse=True)
def _no_app_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn (or orphan-reap) the user's REAL app backends from a test.
    Any test that reaches load_all_extensions() → start_enabled_app_backends()
    against the real config dir would otherwise launch backends for the user's
    installed apps — and its reaper killed the live gateway's backends once.
    Tests that exercise the backend lifecycle explicitly (test_app_api) call
    the supervisor directly and are unaffected by this flag."""
    monkeypatch.setenv("PERSONALCLAW_SKIP_APP_BACKENDS", "1")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure git commits succeed in environments without a global git identity."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


@pytest.fixture(autouse=True)
def _restore_provider_registry() -> object:
    """Undo any provider-registry ENTRY a test registers into the process-global singleton.

    `get_default_registry()` is a module-level singleton, so an entry a test registers outlives it
    and lands in whatever test shares the worker next. Snapshot-and-restore rather than a list of
    known names: three separate files leak (`test_can_resolve_use_case`, `test_provider_resolution_
    unify`, `test_provider_create_bedrock`), each under names of its own, and a name list silently
    stops covering the next one added.

    Measured symptoms — both deterministic in an xdist mix, both invisible in isolation, both in
    files with nothing to do with provider resolution:

    * a leaked CHAT-capable entry makes `workflows.preflight`'s `can_resolve_use_case` probe
      succeed, so `test_workflows_api.py`'s preflight-422 test got a 202 — a workflow run STARTED
      because another file had left a model provider behind;
    * a leaked `acp_agent` entry made `cli_doctor` exit 1 in `test_cli.py`.

    Registered TYPES are deliberately left alone: `register_type` is how a test simulates an
    installed provider app, it is idempotent, and a type with no entry resolves nothing.
    """
    from personalclaw.llm.registry import get_default_registry

    entries = getattr(get_default_registry(), "_entries", None)
    before = set(entries) if isinstance(entries, dict) else set()
    yield
    if isinstance(entries, dict):
        for name in set(entries) - before:
            entries.pop(name, None)


@pytest.fixture(autouse=True)
def _restore_workflow_def_registry() -> object:
    """Undo any workflow DEF provider a test registers into the process-global registry.

    `workflows.defs` holds a module-level provider dict, so a test that registers one leaks it into
    whatever test shares the worker next. Measured: `test_workflows_grill_protocol.py` calls
    `register_bundled_provider()` (18 bundled templates) and never removes it, which makes
    `test_workflows_api.py`'s `test_listing_is_empty_with_no_providers` see 18 instead of 0 and
    `test_save_then_list_then_get` see 19 instead of 1 — deterministically for a given xdist
    distribution, and invisible when either file runs alone. Reproduced on a clean tree, so it is
    pre-existing; ANY change to the suite's test count can surface or hide it.

    Snapshot-and-restore rather than a name list, for the reason the provider-registry guard above
    records: a list stops covering the next name someone adds.
    """
    from personalclaw.workflows import defs as _defs

    before = set(_defs.list_providers())
    yield
    for name in set(_defs.list_providers()) - before:
        _defs.unregister_provider(name)


# (The slack-suite autouse fixtures — enterprise bypass, emoji reset, allowlist
# reset — moved to apps/slack-channel/tests/conftest.py with the slack tests.)


# ── Real-home rail (CRE-8) ──────────────────────────────────────────────
# `_isolate_real_home_writers` above fixes the leaks that exist today; this pair of
# hooks is what NOTICES the next one. Detection lives in `tests/real_home_guard.py`
# so it can be driven against a fake root and proven to fire
# (`tests/test_real_home_guard.py`) — a guard that only ever runs against the tree it
# guards cannot be distinguished from a guard that never fires.
#
# TEETH, deliberately: this fails the run rather than printing a warning. The
# population after the fixture above is ZERO (measured over the full suite), so the
# rail has nothing to grandfather, and a report nobody is forced to read is how the
# 44,402-byte leak survived long enough to need this atom. `ALLOWED_RESIDUE` exists
# for a NAMED, individually justified residue and is currently empty; a blanket
# allowance would turn the rail back into a baseline.
_real_home_since_ns: int | None = None


def pytest_sessionstart(session):
    """Arm the rail. Controller only — xdist workers share the one real home, so a
    per-worker arm/report would multiply one leak into N identical reports."""
    global _real_home_since_ns
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    _real_home_since_ns = time.time_ns()


def pytest_sessionfinish(session, exitstatus):
    """Report anything the run created/modified/grew under the REAL home, and fail."""
    if _real_home_since_ns is None:
        return
    root = real_home_guard.REAL_HOME
    changes = real_home_guard.scan_changes(root, _real_home_since_ns)
    report = real_home_guard.format_report(root, changes)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "real-home rail", red=bool(changes))
        reporter.write_line(report)
    else:  # pragma: no cover - only when the terminal plugin is disabled
        print(report)
    if changes:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
