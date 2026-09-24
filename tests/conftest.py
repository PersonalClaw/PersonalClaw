"""Shared pytest configuration and fixtures."""

import asyncio
import importlib
import os
import shutil
import sys
import time
from pathlib import Path

import native_omp_guard
import pycache_guard
import pytest
import real_home_guard
from hypothesis import HealthCheck, settings

# ── Bytecode-cache rail (#2659) ─────────────────────────────────────────
# FIRST STATEMENT AFTER THE IMPORTS, DELIBERATELY. Point this interpreter's bytecode
# cache at a fresh per-run directory *before* anything a mutation could touch is
# imported — `personalclaw`, `harness`, the test modules, and pytest's own rewritten
# test bytecode all resolve their cache through the prefix this sets. Without it
# CPython validates a `.pyc` on `(int(mtime), size)`, so a same-length edit made
# inside one second — the exact shape of a mutation-testing cycle — runs the PREVIOUS
# bytecode and reports a result for code that is not on disk. `-B` does not fix that
# (it stops the interpreter WRITING a cache, not reading one). Nothing enforced this
# before, so no past "N mutations caught" claim was self-certifying; from here the run
# enforces the bytecode half — an interrupted run that leaves its mutation ON DISK is a
# different defect this cannot see (#2710). Rationale, measurements, the rejected
# alternative and the three files outside the rail: tests/pycache_guard.py. Proof that
# it works: tests/test_pycache_guard.py.
PYCACHE_PREFIX = pycache_guard.activate()

# ── Imported-checkout provenance rail (#2634) ──────────────────────────
# An editable install points at a mutable working tree. In a git worktree, that can make
# pytest import ``personalclaw`` from the shared checkout while collecting tests from this
# checkout, producing plausible results for the wrong branch. Import only after the
# bytecode-cache rail above is active, then require the package root to belong to the
# checkout whose conftest pytest loaded. Editable installs remain valid when they point
# inside this same checkout.
_INVOKING_REPO_ROOT = Path(__file__).resolve().parents[1]
_PERSONALCLAW = importlib.import_module("personalclaw")
_IMPORTED_PACKAGE_ROOT = Path(_PERSONALCLAW.__file__).resolve().parent
if not _IMPORTED_PACKAGE_ROOT.is_relative_to(_INVOKING_REPO_ROOT):
    raise RuntimeError(
        "pytest imported personalclaw from outside the invoking repository root:\n"
        f"  imported package root: {_IMPORTED_PACKAGE_ROOT}\n"
        f"  invoking repository root: {_INVOKING_REPO_ROOT}"
    )

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

_WORKFLOWS_TEST_TIMEOUT_SECONDS = 46


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give workflow tests a measured ceiling without replacing an explicit one."""
    for item in items:
        if (
            item.path.name.startswith("test_workflows_")
            and item.get_closest_marker("timeout") is None
        ):
            item.add_marker(pytest.mark.timeout(_WORKFLOWS_TEST_TIMEOUT_SECONDS))


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure a current event loop exists for code that constructs asyncio
    primitives (e.g. Semaphore) at import/init time outside a running loop."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


@pytest.fixture(autouse=True)
def _forbid_second_openmp_runtime(request):
    """Torch-free-core rail (#3324): fail the test that makes ``torch`` resident.

    Two libomp.dylib copies in one process abort it the next time faiss enters a
    parallel region — i.e. the next episodic-write dedup `search` — which xdist
    then reports as `worker 'gwN' crashed` against whatever unrelated test held
    the worker. Blaming the *transition* absent→resident is what names the real
    culprit; a presence check would red every later test in the poisoned worker
    instead. Rationale, the measured mask that hides the abort, and why this is a
    rail rather than a one-line import fix: tests/native_omp_guard.py. Proof that
    it fires: tests/test_native_omp_guard.py.
    """
    before = native_omp_guard.resident(sys.modules)
    yield
    new = tuple(m for m in native_omp_guard.resident(sys.modules) if m not in before)
    if new:
        pytest.fail(native_omp_guard.explain(new, request.node.nodeid), pytrace=False)


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

    Also clears the ``guardrails.autonomy`` action-type registry, which is
    process-global for the same reason: a rung ladder registered by one test would
    otherwise decide ``resolve_rung`` in the next one.
    """
    from personalclaw.guardrails.autonomy import reset_action_types
    from personalclaw.guardrails.breaker import reset_breakers
    from personalclaw.guardrails.budgets import reset_meter
    from personalclaw.guardrails.ceiling import reset_ceiling, reset_clamp_reports
    from personalclaw.guardrails.incident import reset_incident_mirror

    reset_breakers()
    reset_meter()
    reset_incident_mirror()
    reset_action_types()
    # The governance ceiling is read once per PROCESS and cached (that caching is the
    # no-mid-run-widening property). Under xdist a ceiling written by one test's tmp_path
    # would otherwise bound every later test in the same worker, and the clamp-report
    # dedup would swallow the second test's SEL assertion.
    reset_ceiling()
    reset_clamp_reports()
    yield
    reset_breakers()
    reset_meter()
    reset_incident_mirror()
    reset_action_types()
    reset_ceiling()
    reset_clamp_reports()


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
def _reset_session_restrictions():
    """Clear the process-global per-session memory-restriction registry around every test.

    ``session_restrictions`` keeps two module-level ``OrderedDict``s (``_temporary`` /
    ``_incognito``) — one process-wide registry of which session keys are incognito or
    temporary, by design (a restriction set on a live gateway must outlive the turn that
    set it). It is a cross-test hazard under xdist for the same reason as the singletons
    above: a test that ``mark_incognito``/``mark_temporary``s a key and does not clear it
    leaks that key into whatever test shares the worker next.

    Measured, invisible in isolation, deterministic-per-schedule in a mix: several tests
    reuse the key ``"k"``, and ``test_session_restrictions.TestSessionRestrictions`` clears
    the registry only in ``setup_method`` (before each test, never after) — so once it has
    run ``test_incognito``/``test_temporary`` on a worker, ``"k"`` stays restricted, and
    ``test_session_search``'s ``test_persistent_mode_indexes_normally`` then sees
    ``index_session("k", …, "persistent")`` refused (``is_restricted`` True) and reds. Same
    discipline as ``_reset_channel_delivery_registry``: cleared, not snapshot-restored,
    because outside a live gateway the correct state is empty.
    """
    import personalclaw.session_restrictions as sr

    sr._temporary.clear()
    sr._incognito.clear()
    yield
    sr._temporary.clear()
    sr._incognito.clear()


@pytest.fixture(autouse=True)
def _restore_personalclaw_logging():
    """Snapshot + restore the ``personalclaw`` logger namespace around every test.

    Two process-global logging mutations leak across tests and are invisible in
    isolation but deterministic-per-schedule in an xdist mix — the same shape as the
    resets above. Both reach the SAME logger, ``logging.getLogger("personalclaw")``:

    * ``cli.main()`` (exercised by every ``test_cli_*`` that calls it) runs the CLI's
      logging setup, which ``setLevel(WARNING)`` on that logger (the persisted default)
      and *appends* a ``RotatingFileHandler`` to it;
    * ``dashboard.handlers.updates.apply_log_level`` / the ``agent.log_level`` PATCH set
      that logger's level LIVE.

    Neither restores. ``caplog.set_level(...)`` only touches the ROOT logger, not this
    one, so once a worker has run a ``cli.main`` test the ``personalclaw`` logger stays
    pinned at WARNING for the rest of that worker — and every later observability test
    that expects its own DEBUG/INFO records to be captured (e.g.
    ``test_channel_inbound_drop_reporting``) silently loses them and reds. Sharding
    exposed this: a leaker and a victim that used to sit in different halves of one long
    serial run now land on the same worker in the same shard.

    Levels are snapshotted for the whole ``personalclaw.*`` namespace (not a name list —
    the same reason the registry guards above snapshot rather than enumerate) and any
    descendant created during the test is reset to ``NOTSET``. Handlers ADDED to the
    ``personalclaw`` logger during the test are removed and closed at teardown, so a
    worker does not accumulate a stale open ``gateway.log`` file handle per ``cli.main``
    test. The root logger is deliberately left to ``caplog``, which owns it.
    """
    import logging

    def _pclaw_loggers() -> dict[str, logging.Logger]:
        out: dict[str, logging.Logger] = {}
        for name, obj in list(logging.Logger.manager.loggerDict.items()):
            if (name == "personalclaw" or name.startswith("personalclaw.")) and isinstance(
                obj, logging.Logger
            ):
                out[name] = obj
        return out

    root = logging.getLogger("personalclaw")
    levels_before = {name: lg.level for name, lg in _pclaw_loggers().items()}
    levels_before["personalclaw"] = root.level
    handlers_before = list(root.handlers)

    yield

    for name, lg in _pclaw_loggers().items():
        lg.setLevel(levels_before.get(name, logging.NOTSET))
    root.setLevel(levels_before["personalclaw"])
    for handler in list(root.handlers):
        if handler not in handlers_before:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # pragma: no cover - close() is best-effort cleanup
                pass


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
def _forbid_real_model_roots(monkeypatch):
    """Make the bound-model-deletion incident unreproducible BY CONSTRUCTION (LMMV SC-10).

    ``local_models/layouts.py`` is the one seam every download probe and the single
    deletion sweep go through, so wrapping its entry points for the whole suite is enough
    to state the invariant structurally: **no fs-touching test can reach a real model dir
    or cache root — only ``tmp_path``.** The incident was a real delete against a real HF
    cache root; the convention "always pass tmp_path" was already in force when it
    happened, which is exactly why this is a fixture and not a review note.

    Scoped to the NAMED real roots (see ``real_model_root_guard.FORBIDDEN_SUBPATHS``)
    rather than to all of ``$HOME``: a developer's checkout usually lives under ``$HOME``,
    so a blanket home-rejection would fire on an ordinary relative path and get disabled.
    Detection is a separate module so it can be driven against a fake root and proven to
    fire (``tests/test_local_model_root_guard.py``) — the same reason the real-home rail
    keeps its detection in ``real_home_guard``.

    The reach is ONE attribute lookup deep, which is the rail's one soft edge: a module-level
    ``from ...layouts import delete_all_layouts`` captures the unwrapped object before this
    fixture ever runs. Each original is recorded in ``real_model_root_guard.ORIGINALS`` so
    that shape is testable rather than assumed, and a companion rail
    (``test_no_test_module_import_binds_a_guarded_layouts_name``) keeps the suite from
    growing one.
    """
    import real_model_root_guard

    from personalclaw.local_models import layouts

    for fn_name in real_model_root_guard.GUARDED_FUNCTIONS:
        original = getattr(layouts, fn_name, None)
        if original is None:  # pragma: no cover — a renamed entry point must be re-listed
            raise AssertionError(
                f"layouts.{fn_name} no longer exists; update GUARDED_FUNCTIONS so the "
                f"model-root rail keeps covering every cache-root entry point."
            )

        real_model_root_guard.ORIGINALS[fn_name] = original

        def _guarded(cache_root, *args, _original=original, _name=fn_name, **kwargs):
            real_model_root_guard.assert_safe(_name, cache_root)
            return _original(cache_root, *args, **kwargs)

        monkeypatch.setattr(layouts, fn_name, _guarded)


@pytest.fixture(autouse=True)
def _reset_knowledge_store_singleton():
    """Drop the process-wide ``KnowledgeStore`` between tests (SH6.2).

    ``knowledge.get_knowledge_store()`` memoizes one store in a module global, resolved
    from ``config_dir()`` on FIRST use — so the first test in a worker to touch it pins
    every later test in that worker to the first test's tmp home. Found by driving, not
    reading: once :func:`_close_sqlite_connections` began closing what each test opened,
    ``test_inbound_mcp.py::TestToolBehavior::test_empty_stores_answer_honestly`` failed
    with ``Cannot operate on a closed database`` — it had been searching an EARLIER
    test's knowledge DB all along and passing only because that DB happened not to
    contain its query string. Clearing the global gives each test its own store, the
    same discipline as ``_reset_sel_singleton``.
    """
    import personalclaw.knowledge as knowledge_pkg

    def _clear() -> None:
        knowledge_pkg._store = None

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _close_sqlite_connections(monkeypatch):
    """Close every SQLite connection a test opens, at that test's teardown (SH6.2).

    Measured on this tree before the fixture: one full suite run printed **1,596**
    ``ResourceWarning: unclosed database in <sqlite3.Connection …>`` lines, attributed
    to **95 test files** — knowledge, memory, durability, codegraph, learning, lexicon,
    session-search, snapshot, loop. The shape is the same everywhere and it is not one
    store's bug: a fixture builds a store, returns it, and nothing ever calls
    ``close()``, so the connection survives the test and is finalized whenever a later
    ``gc.collect()`` gets to it (the warning is raised from pytest's own
    ``unraisableexception`` plugin, i.e. attributed to a *bystander* test). Every
    connection held that way is a live OS handle and a WAL reader on a tmp dir the test
    is done with, and under ``-n auto`` each worker accumulates its own backlog.

    Closing them one fixture at a time would be ~95 edits guarding one seam, and the
    96th store would leak again — the same argument :func:`_isolate_real_home_writers`
    makes about ``config_dir()``. So this wraps the seam every store shares: the
    ``connect`` of the sqlite driver module. Both bindings are patched — the stdlib
    module (six stores still ``import sqlite3`` directly) and the one
    ``sqlite_compat`` resolved (which is ``pysqlite3`` when that wheel is installed, so
    patching only the stdlib would miss every store that goes through the shared
    binding — see the driver-mismatch hazard in ``sqlite_compat``'s docstring).

    Deliberately NOT done: closing on a weak reference (a connection already collected
    has already warned), and swallowing every teardown error. ``ProgrammingError`` is
    the one documented case that is not this fixture's business — a connection opened
    with the default ``check_same_thread=True`` inside a worker thread may only be
    closed by that thread — and it is the ONLY exception passed over.

    This fixture alone did NOT reach zero: 12 warnings survived, from five production
    sites using ``with sqlite3.connect(...)``, whose context manager ends the
    TRANSACTION and leaves the connection open — and which, being opened inside worker
    threads, are exactly the ``ProgrammingError`` case above. Those five were fixed at
    source with ``contextlib.closing`` and are now held there by
    ``test_sqlite_compat.py::test_no_production_site_uses_a_bare_with_on_a_connection``.
    """
    import sqlite3 as stdlib_sqlite3

    from personalclaw import sqlite_compat

    drivers = {id(stdlib_sqlite3): stdlib_sqlite3, id(sqlite_compat.sqlite3): sqlite_compat.sqlite3}
    opened: list = []

    for driver in drivers.values():
        real_connect = driver.connect

        def tracking_connect(*args, _real=real_connect, **kwargs):
            conn = _real(*args, **kwargs)
            opened.append(conn)
            return conn

        monkeypatch.setattr(driver, "connect", tracking_connect)

    programming_errors = tuple(d.ProgrammingError for d in drivers.values())

    yield

    for conn in opened:
        try:
            conn.close()
        except programming_errors:
            pass
    opened.clear()


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
def _no_app_child_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never spawn (or orphan-reap) the user's REAL app child processes from a test.
    Any test that reaches load_all_extensions() → start_enabled_app_backends()
    against the real config dir would otherwise launch backends for the user's
    installed apps — and its reaper killed the live gateway's backends once.
    Tests that exercise the backend lifecycle explicitly (test_app_api) call
    the supervisor directly and are unaffected by this flag.

    The SAME boot block also starts APE-3's app-WORKER watchdog, whose sweep spawns,
    stops and PPID-reaps a second family of children. worker_runtime declares the
    matching escape hatch and says of it "set by a harness that must not have app
    workers spawned underneath it" — and nothing set it: the flag had exactly one
    mention in the repo, its own definition. Latent only because no app on disk
    declares `backgroundTasks` yet, so today's sweep finds nothing to spawn; the day
    one does, an unflagged suite would drive the real home's workers from a daemon
    thread that outlives the test that started it. test_app_worker_runtime drives the
    sweep on purpose and clears this flag in its own fixture."""
    monkeypatch.setenv("PERSONALCLAW_SKIP_APP_BACKENDS", "1")
    monkeypatch.setenv("PERSONALCLAW_SKIP_APP_WORKERS", "1")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure git commits succeed in environments without a global git identity."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")


@pytest.fixture(autouse=True)
def _restore_provider_registry() -> object:
    """Undo any provider-registry ENTRY a test registers into the process-global singleton, and
    restore the singleton ITSELF if a test reset or swapped it.

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

    The singleton IDENTITY is restored too, and this is what the sharded suite exposed. Provider
    modules register their TYPES at IMPORT time — `personalclaw.llm.__init__` eager-imports
    `acp_agent`, wiring the `acp_agent` type — and those modules are then cached in `sys.modules`.
    So a test that calls `reset_default_registry()` / `set_default_registry(...)` (several do, in
    their own autouse fixtures: `test_ea5_capture_proxy`, `test_ea5_capture_client_upstream`,
    `test_scripted_provider_binding`, `test_evals_cell_provider`, `test_seed_local_model`) swaps in
    a FRESH, TYPELESS registry that the cached modules never re-populate — and the next test on
    the worker then dies with `unknown provider type 'acp_agent'`. It was invisible until #2720
    sharded the suite: with fewer xdist workers a resetting test and
    `test_provider_resolution_unify`'s `acp_agent` cases land on the same worker in sequence.
    Restoring the original object (which still carries its import-time type registrations) heals
    it; the `is` check makes the restore a no-op for the tests that already save/restore the
    singleton themselves (`test_acp_bundles`, `test_agent_providers_endpoint`). Registered TYPES on
    the original are still left alone (there is no `unregister_type`, so a mutation that would drop
    a type can only be a reset/swap, which this catches): `register_type` is how a test simulates
    an installed provider app, it is idempotent, and a type with no entry resolves nothing.
    """
    from personalclaw.llm import registry as _registry_mod

    original = _registry_mod.get_default_registry()
    entries = getattr(original, "_entries", None)
    before = set(entries) if isinstance(entries, dict) else set()
    yield
    if _registry_mod.get_default_registry() is not original:
        _registry_mod.set_default_registry(original)
    entries = getattr(original, "_entries", None)
    if isinstance(entries, dict):
        for name in set(entries) - before:
            entries.pop(name, None)


@pytest.fixture(autouse=True)
def _reset_channel_delivery_registry() -> object:
    """Drop any channel-delivery handle a test registers into the process-global registry.

    `channel_delivery` keys one handle per provider in a module-level dict (the writers are channel
    transports reaching core through `GatewayServices`; the readers are both the gateway and the
    dashboard, which is why it is not owned by either object — see #959). So a test that installs a
    fake outlives itself and lands in whatever test shares the worker next.

    Measured while landing that change: three `test_gateway.py` tests went red only in a mix —
    `test_services_initially_none` asserts a fresh orchestrator has NO delivery, and a leaked
    handle from an approval test makes the registry answer one. Cleared rather than
    snapshot-restored, because unlike the provider registries nothing legitimately pre-registers a
    channel at import time: outside a live gateway the correct state is empty.
    """
    from personalclaw.channel_delivery import register

    register(None)
    yield
    register(None)


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


@pytest.fixture(autouse=True)
def _restore_knowledge_provider_registry() -> object:
    """Snapshot + restore the process-global KNOWLEDGE-SOURCE provider registry around every test.

    `knowledge_providers.registry` keeps ONE module-level dict of source providers keyed by name
    (`register_provider`/`unregister_provider` mutate it in place). It is the enrolment set the
    Sources UI and `KnowledgeStore.create_source` read to decide which `watched-*` kinds may be
    offered, and a cross-test hazard of the same class as `_restore_provider_registry` above: an
    entry a test registers outlives it and lands in whatever test shares the worker next.

    Measured, invisible in isolation, deterministic-per-schedule in a mix, and the leak #2720's
    sharding exposed on shard 1: `dashboard.server`'s API-server STARTUP path registers the three
    core source providers (`DirSourceProvider`/`FeedSourceProvider`/`WebSourceProvider` — the
    `watched-dir`/`watched-feed`/`watched-page` kinds) and never unregisters them (a gateway
    registers once for its lifetime, by design). So once a worker has run any test that boots that
    startup path, the registry stays populated, and `test_knowledge_sources_api.py`'s
    `test_a_kind_with_no_enrolled_provider_is_not_offered` — which enrols NOTHING and asserts the
    offered kinds are `[]` — then sees those three and reds. (`test_knowledge_sources_api`'s own
    `registered` fixture already tears down what IT registers; this covers the startup path and any
    other leaker.)

    Snapshot-and-restore the whole dict rather than a name list, for the reason the guards above
    record: a list silently stops covering the next name someone adds. The pre-test state is empty
    today, so this reduces to dropping leaked entries after each test, but snapshotting keeps it
    correct if a legitimate import-time registration is ever added.
    """
    from personalclaw.knowledge_providers import registry as _kp_registry

    before = dict(_kp_registry._providers)
    yield
    _kp_registry._providers.clear()
    _kp_registry._providers.update(before)


@pytest.fixture(autouse=True)
def _restore_pipeline_node_registry() -> object:
    """Snapshot + restore the process-global INGESTION-NODE registry around every test.

    `knowledge.pipeline.registry.NODE_REGISTRY` is one module-level dict keyed by
    `(node_type, backend)`, and `register_node` mutates it in place — the same class of
    cross-test hazard as the registry guards above. What made this one worse is that the
    leak was *known* and worked around three times instead of fixed once: two test modules
    carry comments explaining that "`NODE_REGISTRY` is process-global and another test
    module registers its own `ocr` stub into it", and one of them popped the leaked
    `("ocr", "stub")` / `("vision", "stub")` keys from inside an unrelated test body. That
    cleanup reasoned "each executor test registers its own stubs immediately before use, so
    removing them here cannot affect any other test" — true only for one worker running one
    file in order, which is not how this suite runs: `--splits 4` partitions per TEST, and
    `--dist worksteal` then hands tests to workers greedily, so the registering test and the
    popping test routinely land on different workers and the stubs escape the file entirely.

    Measured on clean `origin/main`, deterministic, two tests, `-n0` — no xdist needed to
    show it once the order is forced:

        pytest tests/test_knowledge_pipeline.py::test_executor_conditional_branch_taken \\
               tests/test_knowledge_searchability.py::test_an_image_only_pdf_persists_a_named_failure_not_done

    The second test asserts an image-only scan cannot ingest to `done`, and it got `done`
    with `content == "O"` and `node_phases.ocr == "done"` — literally the text of
    `_StubNode("ocr", text="O")` registered by the first. The same leak is what made
    `test_knowledge_pipeline::test_runner_records_skip_reason_on_partial` see `done` instead
    of `partial`: its premise is "no model → the ocr node skips", and a stub node declares
    `uses_use_case = None`, so it runs no matter what the model bindings say. Both failures
    showed up on CI shard 2 of PRs whose diffs cannot own them (one has no Python at all),
    which is the tell that the polluter is a third file sharing the shard.

    Snapshot-and-restore the whole dict rather than dropping a list of known keys, for the
    reason the guards above record: a key list silently stops covering the next stub someone
    registers.

    `ensure_nodes_registered()`'s `_REGISTERED` memo is rewound WITH the dict, and that pair
    is the whole subtlety here. Core's backends are not registered at import time — they land
    the first time some test calls `ensure_nodes_registered()`, which then latches the memo.
    Rewinding the dict alone therefore un-registers core's own nodes permanently for that
    worker, because the next `ensure_nodes_registered()` sees the latch and returns early:
    measured, `test_runner_records_skip_reason_on_partial` went to `failed` (nothing ran at
    all) and `test_runner_synthesizes_descriptor_for_textless_image` lost its exif dimensions.
    Restoring both together means the registry and the memo that describes it can never
    disagree — the registry is left exactly as the test found it, never emptied.
    """
    from personalclaw.knowledge import pipeline as _pipeline
    from personalclaw.knowledge.pipeline import registry as _node_registry

    before = dict(_node_registry.NODE_REGISTRY)
    before_registered = _pipeline._REGISTERED
    yield
    _node_registry.NODE_REGISTRY.clear()
    _node_registry.NODE_REGISTRY.update(before)
    _pipeline._REGISTERED = before_registered


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
    # Torch-free-core rail, the one case no per-test transition can attribute: a
    # hazard module already resident before the first test, imported by a plugin or
    # by conftest itself. Checked in EVERY process — each xdist worker carries its
    # own sys.modules, and it is a worker that aborts.
    pre_resident = native_omp_guard.resident(sys.modules)
    if pre_resident:
        raise RuntimeError(native_omp_guard.explain(pre_resident, "session start"))
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
    # Persist before printing: the exitstatus below fails the run WITHOUT failing any
    # test, so the JUnit XML cannot carry the offending paths and STDOUT is the one
    # place they would otherwise live (#3386).
    written = real_home_guard.write_report(session.config.rootpath, report)
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "real-home rail", red=bool(changes))
        reporter.write_line(report)
        if written is not None:
            reporter.write_line(f"real-home rail report written to {written}")
    else:  # pragma: no cover - only when the terminal plugin is disabled
        print(report)
    if changes:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
