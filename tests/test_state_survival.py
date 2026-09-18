"""RET-1 — state a PRIOR RELEASE wrote still loads on HEAD (the "six-month home").

The abandonment driver this exists for is the one with a verified explicit switch-away:
an upgrade that breaks state, where a *downgrade did not undo the damage*. The shape of
that failure is not a crash — it is a store that comes back **empty** while everything
else looks fine, so the user finds out weeks later that their knowledge library, their
memory, or their run history is gone.

Two design decisions carry the whole file, and both exist to make it non-cheatable:

1. **The fixture is written by PersonalClaw 0.1.3's code, not HEAD's.** A fixture built by
   HEAD's writers is trivially in the shape HEAD expects, so it can never fail — it would
   prove that HEAD reads HEAD. ``scripts/generate_six_month_home_fixture.py`` refuses to
   run on any version but the declared prior release, and
   :func:`test_the_fixture_was_written_by_a_prior_release_not_by_head` re-proves the gap
   mechanically from the committed bytes (``config.json``'s own
   ``meta.lastTouchedVersion``), not from a comment.

2. **Every count is compared against a COMMITTED MANIFEST**, never against a number
   re-derived from the fixture at test time. A test that counted the fixture's rows and
   then asserted the count it had just read is self-fulfilling: it passes on a fixture that
   was silently emptied to zero. The manifest is data
   (``tests_fixtures/six-month-home.manifest.json``), regenerated only alongside the
   fixture.

What this file deliberately does NOT accept as evidence — the lazy implementations the
atom names:

* *"the gateway starts"* / *"no exception on boot"* — both pass on a home that was emptied,
  and an empty home renders onboarding on every route, so even a screenshot looks fine.
* an **aggregate** row count — one store dropping to zero while another grew hides in a
  total. Every assertion below is per store, by name, with the store's own count.

What it does not claim: this is not a full gateway boot. It runs the boot-time passes that
could *destroy* state (``MemoryStore.init()`` and ``VectorMemoryStore.init()``, which is
where ``memory.db``'s migrations run — ``GatewayOrchestrator._init_services``,
``gateway.py``; and ``loop_files.reap_orphan_dirs()``, the sweep that deletes a
``loop/<id>/`` dir with no backing row — ``LoopWatchdog``, ``watchdog.py``), then reads
every store through its production reader. A real gateway is deliberately not spawned here:
it costs ~17s and a bound socket on every suite run, for a signal these passes already
carry. The full boot is a two-command check a maintainer can run by hand::

    PERSONALCLAW_HOME=/tmp/six-month PERSONALCLAW_AUTH_MODE=none \\
      personalclaw gateway --seed six-month-home --seed-replace --test-mode
    # then re-run this file's assertions against /tmp/six-month
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from personalclaw import seed as seed_mod

FIXTURE_NAME = "six-month-home"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "src" / "personalclaw" / "tests_fixtures"
_FIXTURE_DIR = _FIXTURES_DIR / FIXTURE_NAME
_MANIFEST_PATH = _FIXTURES_DIR / f"{FIXTURE_NAME}.manifest.json"

# The six stores the atom enumerates by name. ``memory_markdown`` is the seventh entry
# because "the memory store" is ambiguous in this codebase — ``MemoryStore`` is the
# markdown store under ``workspace/memory/`` while ``VectorMemoryStore`` owns
# ``memory.db`` — so both are asserted rather than picking the convenient one.
_REQUIRED_STORES = (
    "knowledge_db",
    "memory_db",
    "memory_markdown",
    "loops",
    "run_history",
    "entity_settings",
    "config_json",
)


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    """The committed expectations. Read as DATA — never recomputed from the fixture."""
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def booted_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed the prior-release fixture into an isolated home and run boot's state passes.

    ``config_dir()`` re-reads ``$PERSONALCLAW_HOME`` on every call, so setting the env var
    points every store at the seeded tree. It is set to a ``tmp_path`` subdirectory, so the
    developer's real ``~/.personalclaw`` is never read, written or created by this file —
    and ``seed()`` refuses the real home unconditionally anyway.

    The passes below are the ones that could EMPTY a store on first boot. Running them
    before any assertion is the difference between "HEAD can read these bytes" and "HEAD
    can read these bytes after doing to them what a real first boot does".
    """
    home = tmp_path / "six_month_home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    seed_mod.seed(FIXTURE_NAME)

    from personalclaw.loop import files as loop_files
    from personalclaw.memory import MemoryStore
    from personalclaw.vector_memory import VectorMemoryStore

    # `MemoryStore.init()` writes the shipped placeholder markdown when a file is absent;
    # guarded by `if not exists()`, and this is the assertion that the guard still holds.
    MemoryStore().init()
    # `VectorMemoryStore.init()` is where `memory.db`'s migrations run. It is THE upgrade
    # path for the semantic store, so it must run before the row counts are read.
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    store.close()
    # The boot sweep that DELETES any `loop/<8hex>/` dir with no backing DB row.
    loop_files.reap_orphan_dirs()
    return home


def _sqlite_count(db: Path, sql: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


# ── the atom's assertion: per store, by name, against the manifest ──────────────────────


def test_every_store_a_prior_release_wrote_survives_with_the_manifest_s_count_and_record(
    booted_home: Path, manifest: dict[str, Any]
) -> None:
    """The one test the atom asks for: per store, individually by name, (i) a non-zero
    count EQUAL to the manifest's and (ii) a successful typed read of one NAMED record.

    Each block below is deliberately spelled out rather than driven from a table. A loop
    over the manifest would need one generic "read a store" abstraction, and the only
    honest way to read seven stores is through their seven different production readers —
    which is exactly what an upgrade breaks. Naming them is the point.
    """
    home = booted_home
    failures: list[str] = []
    stores = manifest["stores"]

    # ── knowledge.db ───────────────────────────────────────────────────────────────────
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    expected = stores["knowledge_db"]
    knowledge = KnowledgeStore(str(knowledge_db_path(home)))
    count = int(knowledge.db.execute(expected["count_sql"]).fetchone()[0])
    if count != expected["count"]:
        failures.append(f"knowledge.db: {count} items, manifest says {expected['count']}")
    named = expected["named_record"]
    item = knowledge.get_item(named["id"])
    if item is None:
        failures.append(f"knowledge.db: item {named['id']} does not load")
    else:
        if item["title"] != named["title"]:
            failures.append(f"knowledge.db: title is {item['title']!r}, not {named['title']!r}")
        if item["item_type"] != named["item_type"]:
            failures.append(f"knowledge.db: item_type is {item['item_type']!r}")
        # A field the fixture wrote: the note's body. An item row that survived with an
        # empty body is a row the user cannot use.
        if not (item["content"] or "").strip():
            failures.append("knowledge.db: the named item survived with an empty content field")

    # ── memory.db (the semantic store) ─────────────────────────────────────────────────
    from personalclaw.vector_memory import VectorMemoryStore

    expected = stores["memory_db"]
    memory_db = VectorMemoryStore(db_path=home / "memory.db")
    memory_db.init()
    count = int(memory_db.db.execute(expected["count_sql"]).fetchone()[0])
    if count != expected["count"]:
        failures.append(f"memory.db: {count} semantic rows, manifest says {expected['count']}")
    episodic = int(memory_db.db.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0])
    if episodic != expected["episodic_count"]:
        failures.append(
            f"memory.db: {episodic} episodic rows, manifest says {expected['episodic_count']}"
        )
    # Every key by name, not just the count: a migration that renamed one key would keep
    # the count and still lose the record the user asks for.
    for key in expected["keys"]:
        if memory_db.get(key) is None:
            failures.append(f"memory.db: semantic key {key!r} does not load")
    named = expected["named_record"]
    record = memory_db.get(named["key"])
    if record is None:
        failures.append(f"memory.db: named record {named['key']!r} does not load")
    elif record.value != named["value"]:
        failures.append(f"memory.db: {named['key']!r} holds {record.value!r}")
    memory_db.close()

    # ── the markdown memory store ──────────────────────────────────────────────────────
    from personalclaw.memory import _DEFAULT_PREFERENCES, MemoryStore

    expected = stores["memory_markdown"]
    markdown = MemoryStore()
    files = sorted(str(p.relative_to(home)) for p in (home / "workspace" / "memory").rglob("*.md"))
    if files != expected["files"]:
        failures.append(f"workspace/memory: files are {files}, manifest says {expected['files']}")
    prefs = markdown.read_preferences()
    named = expected["named_record"]
    if named["contains"] not in prefs:
        failures.append(
            "workspace/memory: preferences.md no longer carries the line the fixture wrote "
            f"({named['contains']!r})"
        )
    if prefs.strip() == _DEFAULT_PREFERENCES.strip():
        failures.append(
            "workspace/memory: preferences.md was overwritten with the shipped placeholder — "
            "MemoryStore.init()'s `if not exists()` guard no longer holds"
        )
    # `read()` is the combined view the prompt context and consolidator use, and it DROPS a
    # file that still equals its placeholder. A survival check that only stat'ed the file
    # would miss a home whose memory is present but contributes nothing.
    if prefs.strip() not in markdown.read():
        failures.append("workspace/memory: the combined read() does not include preferences.md")

    # ── loops ──────────────────────────────────────────────────────────────────────────
    from personalclaw.loop import store as loop_store

    expected = stores["loops"]
    count = _sqlite_count(home / "loop" / "loops.db", expected["count_sql"])
    if count != expected["count"]:
        failures.append(f"loop/loops.db: {count} rows, manifest says {expected['count']}")
    named = expected["named_record"]
    loop = loop_store.get(named["id"])
    if loop is None:
        failures.append(f"loop/loops.db: loop {named['id']} does not load")
    else:
        if loop.name != named["name"]:
            failures.append(f"loop/loops.db: name is {loop.name!r}")
        if loop.status != named["status"]:
            failures.append(f"loop/loops.db: status is {loop.status!r}, not {named['status']!r}")
        if loop.success_criteria != named["success_criteria"]:
            failures.append("loop/loops.db: success_criteria did not survive")
        # The phased plan is a JSON column, i.e. the field most likely to be lost silently
        # by a schema change — it decodes to [] rather than raising.
        phases = [p.get("phase") for p in loop.plan]
        if phases != named["plan_phases"]:
            failures.append(f"loop/loops.db: plan phases are {phases}, not {named['plan_phases']}")
    if not (home / "loop" / named["id"]).is_dir():
        failures.append(
            f"loop/{named['id']}/ was reaped — its loops.db row is missing, so a real boot "
            "would delete the loop's files too"
        )

    # ── run history ────────────────────────────────────────────────────────────────────
    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    expected = stores["run_history"]
    history = ScheduleRunStore(home)
    _rows, total = asyncio.run(history.list_all(limit=100))
    if total != expected["count"]:
        failures.append(f"cron-history: {total} runs, manifest says {expected['count']}")
    for job_id, per_job in expected["per_job"].items():
        _job_rows, job_total = asyncio.run(history.list_for_job(job_id, limit=100))
        if job_total != per_job:
            failures.append(f"cron-history: job {job_id} has {job_total} runs, manifest {per_job}")
    named = expected["named_record"]
    raw = asyncio.run(history.get_run(named["job_id"], named["run_id"]))
    if raw is None:
        failures.append(f"cron-history: run {named['run_id']} does not load")
    else:
        run = ScheduleRun.from_dict(raw)
        if run.status != named["status"]:
            failures.append(f"cron-history: run status is {run.status!r}, not {named['status']!r}")
        if run.summary != named["summary"]:
            failures.append("cron-history: the run's summary did not survive")
        # The failed run's error string. A "reset to a clean history" would keep the count
        # of successes and lose exactly this.
        if run.error != named["error"]:
            failures.append(f"cron-history: the failed run's error is {run.error!r}")

    # ── entity_settings/*.json ─────────────────────────────────────────────────────────
    from personalclaw.providers.entity_routes import _load_entity_settings

    expected = stores["entity_settings"]
    files = sorted(p.name for p in (home / "entity_settings").glob("*.json"))
    if files != expected["files"]:
        failures.append(f"entity_settings: files are {files}, manifest says {expected['files']}")
    named = expected["named_record"]
    settings = _load_entity_settings(named["entity"])
    for key, value in named["settings"].items():
        if settings.get(key) != value:
            failures.append(
                f"entity_settings/{named['entity']}.json: {key} is {settings.get(key)!r}, "
                f"not the {value!r} the fixture wrote"
            )

    # ── config.json ────────────────────────────────────────────────────────────────────
    from personalclaw.config.loader import AppConfig

    expected = stores["config_json"]
    raw_config = json.loads((home / "config.json").read_text(encoding="utf-8"))
    if len(raw_config) != expected["count"]:
        failures.append(
            f"config.json: {len(raw_config)} top-level keys, manifest says {expected['count']}"
        )
    config = AppConfig.load()
    for key, value in expected["named_record"].items():
        got = getattr(config, key, None)
        if got != value:
            failures.append(f"config.json: {key} loads as {got!r}, not the {value!r} written")

    assert not failures, "state written by a prior release did not survive:\n" + "\n".join(
        f"  - {line}" for line in failures
    )


# ── the rails that keep the test above from being vacuous ──────────────────────────────


def test_the_fixture_is_in_a_prior_release_s_shape_not_in_head_s(manifest: dict[str, Any]) -> None:
    """The premise of the whole file, asserted from the committed bytes.

    If someone regenerates the fixture with HEAD's writers, every assertion above keeps
    passing and stops meaning anything — HEAD reading its own output is not survival.

    ``personalclaw.__version__`` cannot be the discriminator here, and that is a measured
    fact rather than a preference: ``pyproject.toml`` still declares ``0.1.3`` more than
    1900 commits past the ``v0.1.3`` tag, because the next release has not been cut. HEAD
    and the prior release report the *same* version string, so a version comparison would
    red on an honest fixture and stay green on a dishonest one.

    What does discriminate is the SHAPE. ``config.json`` is the store where the gap is
    legible without any schema archaeology: HEAD's ``AppConfig.to_dict()`` no longer emits
    the top-level keys the fixture carries (``auto_update``, ``inbound`` were retired) and
    emits a large set of blocks the fixture has never heard of. Both directions are asserted,
    so the rail cannot be satisfied by a fixture that merely *lags* in one of them.
    """
    from personalclaw.config.loader import AppConfig

    head_keys = set(AppConfig().to_dict())
    fixture_keys = set(manifest["stores"]["config_json"]["top_level_keys"])
    assert fixture_keys, "the manifest records no top-level config keys — nothing to compare"

    retired = sorted(fixture_keys - head_keys)
    added = sorted(head_keys - fixture_keys)
    assert retired or added, (
        "the fixture's config.json has the same top-level key set as HEAD's own writer "
        f"({len(head_keys)} keys) — it was regenerated against HEAD and proves nothing "
        "about surviving an upgrade"
    )
    assert added, (
        "HEAD's writer emits no key the fixture lacks, so the fixture is not behind HEAD "
        f"at all (retired-only gap: {retired})"
    )

    # The manifest and the fixture must come from the same build. ``lastTouchedVersion`` is
    # written only by ``AppConfig.save()`` — the writing code's own record of itself.
    stamped = json.loads((_FIXTURE_DIR / "config.json").read_text(encoding="utf-8"))["meta"][
        "lastTouchedVersion"
    ]
    assert stamped == manifest["written_by_release"], (
        f"config.json was last written by {stamped!r} but the manifest declares "
        f"{manifest['written_by_release']!r} — fixture and manifest came from different builds"
    )


def test_head_reads_the_fixture_s_retired_config_key_as_a_legacy_value(
    booted_home: Path, manifest: dict[str, Any]
) -> None:
    """The upgrade path, not just the shape gap: HEAD *acts on* the older key.

    The fixture carries ``auto_update: true`` — a top-level bool HEAD retired in favour of
    the ``updates`` block. HEAD's loader backfills ``updates.auto`` by reading that raw
    legacy key, so a home written before the retirement keeps riding staged updates rather
    than silently reverting to notify-only. This is the difference the atom's "a field the
    fixture wrote is absent after boot" clause turns on: a retired field is allowed to move,
    but it is not allowed to evaporate.

    It is also a second, independent proof that the fixture is not HEAD's output — HEAD
    writes no ``auto_update`` at all, so the backfill would have nothing to read.
    """
    from personalclaw.config.loader import AppConfig

    raw = json.loads((booted_home / "config.json").read_text(encoding="utf-8"))
    assert raw.get("auto_update") is True, (
        "the fixture no longer carries the retired top-level auto_update key, so this "
        "assertion has nothing to prove — regenerate against the prior release"
    )
    assert "auto_update" not in AppConfig().to_dict(), (
        "HEAD's writer emits auto_update again, so its presence in the fixture is no longer "
        "evidence of an older writer"
    )
    assert AppConfig.load().updates.auto == "staged", (
        "HEAD loaded a home whose legacy auto_update=true into updates.auto != 'staged' — "
        "the user's opt-in was dropped by the upgrade, not migrated"
    )


def test_the_manifest_covers_every_store_the_atom_names(manifest: dict[str, Any]) -> None:
    """A manifest missing a store makes that store's loss invisible.

    The survival test iterates the manifest's own entries, so dropping ``run_history`` from
    the manifest would silently remove the assertion instead of failing it. This pins the
    required set from the outside.
    """
    assert set(manifest["stores"]) == set(_REQUIRED_STORES), (
        "the manifest's stores drifted from the set RET-1 enumerates: "
        f"{sorted(set(_REQUIRED_STORES) ^ set(manifest['stores']))}"
    )


def test_the_manifest_declares_a_non_zero_count_and_a_named_record_per_store(
    manifest: dict[str, Any],
) -> None:
    """The vacuity floor.

    ``count == manifest count`` is satisfied by ``0 == 0``. A manifest of zeros would make
    the survival test pass against a completely empty home — the exact failure it exists to
    catch — so the manifest itself has to be non-trivial, and every store has to name a
    record for the typed read.
    """
    for name, entry in manifest["stores"].items():
        assert entry["count"] > 0, f"{name}: manifest declares a zero count"
        assert entry.get("named_record"), f"{name}: manifest names no record to read back"
        assert entry.get("path"), f"{name}: manifest declares no on-disk path"


def test_every_manifest_path_exists_in_the_committed_fixture(manifest: dict[str, Any]) -> None:
    """A manifest entry pointing at a path the fixture does not carry is a store the
    survival test would read as empty-but-expected rather than missing."""
    for name, entry in manifest["stores"].items():
        target = _FIXTURE_DIR / entry["path"]
        assert target.exists(), f"{name}: the fixture has no {entry['path']}"


def test_the_committed_fixture_ships_no_sqlite_sidecar_and_no_machine_path() -> None:
    """Read off the repo tree, not a seeded home — this is about what SHIPS.

    Every store here opens with ``PRAGMA journal_mode=WAL``, so writes sit in a ``-wal``
    sidecar until checkpointed. Committing the bare ``.db`` without a checkpoint ships a
    fixture that boots EMPTY, which would make the survival test pass against nothing;
    committing the sidecar ships a file that is not state. A leaked absolute path would
    resolve on exactly one machine.
    """
    dbs = sorted(p.name for p in _FIXTURE_DIR.rglob("*.db"))
    assert dbs == ["knowledge.db", "loops.db", "memory.db"], f"unexpected db set: {dbs}"

    strays = sorted(
        p.name
        for p in _FIXTURE_DIR.rglob("*")
        if p.name.endswith(("-wal", "-shm", ".db-journal", ".lock"))
    )
    assert not strays, f"these must not ship: {strays}"

    for path in sorted(p for p in _FIXTURE_DIR.rglob("*") if p.is_file()):
        blob = path.read_bytes()
        for needle in (b"/Users/", b"/home/", b"/private/tmp", b"/var/folders"):
            assert needle not in blob, (
                f"{path.relative_to(_FIXTURE_DIR)} embeds the absolute path "
                f"{needle.decode()!r} from the machine that generated it"
            )


def test_the_fixture_ships_the_same_marker_as_every_other_fixture() -> None:
    """One seeding mechanism, not a parallel path: ``--seed six-month-home`` is the same
    ``shutil.copytree`` every other fixture rides, and the marker is what says so."""
    assert (_FIXTURE_DIR / "fixture.yaml").read_text(encoding="utf-8") == (
        (_FIXTURES_DIR / "empty" / "fixture.yaml").read_text(encoding="utf-8")
    )


def test_the_fixture_is_listed_as_an_available_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixture nobody can discover is a fixture nobody regenerates."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "h"))
    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("nope-not-a-fixture")
    assert FIXTURE_NAME in str(excinfo.value)


def test_the_manifest_is_not_seeded_into_the_home(booted_home: Path) -> None:
    """The manifest is committed BESIDE the fixture, not inside it, on purpose.

    A ``manifest.json`` at a home's root is a path no ``StateEntry`` claims, so
    ``audit_home()`` would report every seeded home as carrying an unclaimed path and turn
    the doctor's health strip coral — a fixture that makes the product look broken.
    """
    assert not (booted_home / _MANIFEST_PATH.name).exists()
    assert not (booted_home / "manifest.json").exists()
    assert _MANIFEST_PATH.is_file(), "the manifest must still be committed somewhere"
