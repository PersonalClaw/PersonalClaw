"""Tests for the ``demo-home`` seed fixture (DL-4).

``tests/test_seed.py`` covers the seeding *mechanism* (rails, audit, CLI wiring)
against the ``empty`` fixture. This file covers the ``demo-home`` fixture's
*content*: that the hand-authored records actually load through the production
stores, and that every file in the fixture tree is declared as package data so
the wheel carries it.

Two failure modes drive the shape of these tests:

1. **Silent record loss.** Both entity readers swallow every exception
   (``hierarchy._read_project``, ``native._read_task`` are
   ``except Exception: return None``), so a trailing comma in a fixture file
   makes that record vanish with no error and no log line. Asserting "the file
   exists" would still pass. Every assertion below therefore goes through the
   real store and asserts on the loaded record.

2. **Silent packaging loss.** The package-data glob was ``tests_fixtures/*/*``
   — exactly one level deep — so every file the fixture keeps in a subdirectory
   (``projects/<id>/project.json``, ``workspace/memory/history/*.md``) was
   excluded from the wheel. A source checkout looked perfect and only a real
   ``pip install`` would have shown the fixture half-missing.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from personalclaw import seed as seed_mod

FIXTURE_NAME = "demo-home"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "src" / "personalclaw" / "tests_fixtures"
_DEMO_DIR = _FIXTURES_DIR / FIXTURE_NAME


@pytest.fixture
def seeded_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed ``demo-home`` into an isolated ``$PERSONALCLAW_HOME`` and return it.

    ``config_dir()`` re-reads ``$PERSONALCLAW_HOME`` on every call, so setting
    the env var is enough to point every store at the seeded tree — no
    per-module ``config_dir`` patching needed.
    """
    home = tmp_path / "demo_home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    seed_mod.seed(FIXTURE_NAME)
    return home


def _load_tasks() -> list:
    """The native provider's ``list_tasks`` is async and returns ``(tasks, total)``."""
    from personalclaw.tasks.native import create_provider

    tasks, _total = asyncio.run(create_provider().list_tasks(limit=200))
    return tasks


# ── the fixture is reachable as a fixture at all ────────────────────────────


def test_demo_home_is_listed_as_an_available_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--seed demo-home`` resolves, and an unknown name advertises it.

    The "available fixtures" list is how a user discovers the demo without
    reading the package tree, so the name has to appear there.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "h"))
    with pytest.raises(seed_mod.SeedError) as excinfo:
        seed_mod.seed("nope-not-a-fixture")
    assert FIXTURE_NAME in str(excinfo.value)


def test_demo_home_seeds_the_fixture_marker(seeded_home: Path) -> None:
    """The marker every fixture carries, at the same schema version as ``empty``."""
    marker = seeded_home / "fixture.yaml"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == (
        (_FIXTURES_DIR / "empty" / "fixture.yaml").read_text(encoding="utf-8")
    )


# ── the records LOAD (not merely exist) ────────────────────────────────────


def test_the_demo_projects_load_through_the_production_store(seeded_home: Path) -> None:
    """Projects come back from ``HierarchyStore``, ids intact, builtins not re-minted.

    ``ensure_defaults()`` injects ``Personal`` and ``Repeatable`` with *random*
    ids on the first ``GET /api/projects`` when they are absent. The fixture
    ships both so a re-seeded demo home keeps the same ids across captures —
    if that ever regresses, this test sees six projects instead of four.
    """
    from personalclaw.tasks.hierarchy import HierarchyStore

    projects = HierarchyStore().list_projects()
    by_name = {p.name: p for p in projects}

    assert sorted(by_name) == ["Home Server", "Personal", "Reading Pipeline", "Repeatable"], (
        "expected exactly the fixture's four projects — extra entries mean "
        "ensure_defaults() re-minted a builtin the fixture was supposed to pin"
    )
    assert by_name["Personal"].is_builtin
    assert by_name["Repeatable"].is_builtin
    assert not by_name["Reading Pipeline"].is_builtin

    # The demo's whole point is prose worth screenshotting, so the two authored
    # projects must actually carry a brief.
    assert len(by_name["Reading Pipeline"].brief) > 60
    assert len(by_name["Home Server"].brief) > 60


def test_the_demo_task_lists_load_and_point_at_their_projects(seeded_home: Path) -> None:
    """Every authored list resolves to a project that exists in the fixture."""
    from personalclaw.tasks.hierarchy import HierarchyStore

    store = HierarchyStore()
    project_ids = {p.id for p in store.list_projects()}
    lists = [tl for pid in project_ids for tl in store.list_task_lists(pid)]

    assert sorted(tl.name for tl in lists) == ["Maintenance", "Reading queue", "This week"]
    for tl in lists:
        assert tl.project_id in project_ids, f"{tl.name} points at a missing project"


def test_the_demo_tasks_load_and_cover_the_status_range(seeded_home: Path) -> None:
    """All ten tasks load, and they span the statuses a screenshot needs.

    A demo home where every task is ``open`` shows one column of one colour.
    The point of the fixture is that the board looks *used*, so the spread is
    part of the contract, not a detail of the data.
    """
    tasks = _load_tasks()
    assert len(tasks) == 10, "a task that failed to parse is silently dropped by _read_task"

    statuses = {t.status.value for t in tasks}
    assert {"open", "in_progress", "done", "blocked", "cancelled"} <= statuses

    priorities = {t.priority.value for t in tasks}
    assert len(priorities) >= 4, f"priorities look flat: {sorted(priorities)}"

    # Substance, not stubs: several tasks carry real exit criteria / plans /
    # notes, which is what the task detail pane renders.
    assert sum(1 for t in tasks if t.exit_criteria) >= 3
    assert sum(1 for t in tasks if t.action_plan) >= 2
    assert sum(1 for t in tasks if t.notes) >= 3


def test_the_demo_dependency_actually_blocks(seeded_home: Path) -> None:
    """The blocked task names what it waits on, through the real reconciler.

    Authoring ``status: blocked`` is free; a *derived* block reason is not. This
    asserts the dependency edge resolves to a real title, which is what makes
    the blocked row legible in a screenshot.
    """
    tasks = {t.id: t for t in _load_tasks()}
    blocked = tasks["t-3c6e9024"]
    assert blocked.dependencies, "the blocked task lost its dependency edge"
    upstream_id = blocked.dependencies[0].depends_on_task_id
    assert upstream_id in tasks, "dangling dependency — reads as not-blocking"
    assert tasks[upstream_id].status.value == "in_progress"


def test_every_entity_filename_matches_its_id(seeded_home: Path) -> None:
    """Filename stem (and project dir name) must equal the record's ``id``.

    Readers key records by the ``id`` *inside* the file while getters build the
    path *from* the id. When they disagree the list view shows the row but
    ``GET /api/tasks/{id}`` 404s and the next write creates a second file — a
    split-brain record with no error anywhere.
    """
    for path in sorted((seeded_home / "tasks").glob("t-*.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.stem

    for path in sorted((seeded_home / "tasks" / "task_lists").glob("*.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.stem

    for path in sorted((seeded_home / "projects").glob("*/project.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.parent.name


def test_the_demo_memory_loads_and_is_not_the_default_placeholder(seeded_home: Path) -> None:
    """Memory markdown reads back through ``MemoryStore`` with authored content.

    ``MemoryStore.read()`` — the combined view the consolidator and prompt
    context use — *drops* preferences/projects when they still equal the shipped
    placeholder, so a fixture that left the defaults in place would contribute
    nothing to a prompt: visibly present, functionally inert. This pins the
    opposite by asserting the combined read is non-empty and carries both files.
    """
    from personalclaw.memory import _DEFAULT_PREFERENCES, _DEFAULT_PROJECTS, MemoryStore

    store = MemoryStore()
    prefs = store.read_preferences()
    projects = store.read_projects()

    assert prefs.strip() != _DEFAULT_PREFERENCES.strip()
    assert projects.strip() != _DEFAULT_PROJECTS.strip()

    combined = store.read()
    assert prefs.strip() in combined
    assert projects.strip() in combined

    # The two authored projects are the ones the task board shows, so memory
    # and tasks describe the same world rather than two unrelated demos.
    assert "Reading Pipeline" in projects
    assert "Home Server" in projects

    history = sorted((seeded_home / "workspace" / "memory" / "history").glob("*.md"))
    assert len(history) >= 2, "daily history is what makes memory look accumulated"
    for entry in history:
        text = entry.read_text(encoding="utf-8")
        assert text.startswith(f"# {entry.stem}\n"), "history files carry a dated H1"
        assert "####" in text, "history entries are timestamped sections"


def test_init_does_not_overwrite_the_authored_memory(seeded_home: Path) -> None:
    """``MemoryStore.init()`` runs on gateway boot; it must not clobber the fixture.

    ``init()`` is guarded by ``if not exists()``. If that guard were ever
    dropped to an unconditional write, the demo home would boot with empty
    memory and the failure would look like the fixture was never authored.
    """
    from personalclaw.memory import MemoryStore

    store = MemoryStore()
    before = store.read_preferences()
    store.init()
    assert store.read_preferences() == before


# ── the SQLite-backed surfaces: knowledge + the one loop ───────────────────
#
# These two are the reason the fixture carries binary ``.db`` files at all.
# ``--seed`` is a bare ``shutil.copytree`` with no hydration hook, and neither
# store has any file-based ingest a boot would pick up:
#
#   * knowledge lives ONLY in ``workspace/knowledge/knowledge.db``, whose schema
#     includes an FTS5 virtual table — markdown under ``workspace/knowledge/``
#     would never be read;
#   * a loop's row lives ONLY in ``loop/loops.db``, and the boot-time
#     ``reap_orphan_dirs()`` sweep DELETES any ``loop/<8hex>/`` dir with no
#     backing row, so a text-only loop fixture is wiped on first boot.
#
# ``scripts/generate_demo_home_fixture.py`` regenerates both by driving the real
# writers. The tests below are what make a schema change that invalidates them
# fail loudly instead of shipping a demo home that boots empty.


def _knowledge_rows(home: Path) -> list:
    """Read the items the way ``GET /api/knowledge/items`` does.

    The handler runs SQL straight off ``store.db`` rather than through a list
    helper, so this mirrors the real read path.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    store = KnowledgeStore(str(knowledge_db_path(home)))
    return store.db.execute(
        "SELECT id, title, item_type, content, url, file_path FROM items ORDER BY title"
    ).fetchall()


def test_the_demo_knowledge_docs_load_through_the_production_store(seeded_home: Path) -> None:
    """The seeded knowledge items come back from ``KnowledgeStore``, with prose.

    ``KnowledgeStore`` refuses to open at all without FTS5, so merely getting rows
    back here also proves the shipped ``.db`` opens on this interpreter.
    """
    rows = _knowledge_rows(seeded_home)

    assert len(rows) == 5, (
        f"expected the fixture's five knowledge docs, got {len(rows)} — regenerate "
        "with scripts/generate_demo_home_fixture.py"
    )
    titles = [r["title"] for r in rows]
    assert all(titles), "a seeded knowledge doc has no title"
    # The demo's whole point is prose worth screenshotting.
    for row in rows:
        assert (
            len(row["content"] or "") > 120
        ), f"knowledge doc {row['title']!r} has no body worth showing in a capture"
    assert {r["item_type"] for r in rows} == {
        "note",
        "bookmark",
    }, "the demo should show more than one knowledge item type"


def test_the_demo_knowledge_docs_are_findable_through_fts(seeded_home: Path) -> None:
    """Search must work, not just listing.

    ``items_fts`` is an **external-content FTS5 table with no triggers**, so its
    index is only populated by the real writer. A hand-built or hand-patched
    ``knowledge.db`` would list fine here and return nothing for every search —
    a demo where the search box looks broken. This is that rail.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    store = KnowledgeStore(str(knowledge_db_path(seeded_home)))
    hits = store.search_items_fts("digest", limit=10)
    assert hits, (
        "FTS returned nothing for a term the fixture definitely contains — the "
        "shipped knowledge.db has an unpopulated items_fts index"
    )


def test_no_seeded_knowledge_doc_carries_an_absolute_path(seeded_home: Path) -> None:
    """A file-backed item stores an ABSOLUTE ``file_path``.

    Baked into package data, that path points at whatever machine generated the
    fixture, so the item 404s in a user's home. The demo items are text/url only;
    this pins that.
    """
    for row in _knowledge_rows(seeded_home):
        assert not row["file_path"], (
            f"knowledge doc {row['title']!r} has file_path={row['file_path']!r} — a "
            "baked-in absolute path will not resolve in a user's home"
        )


def test_the_demo_loop_loads_through_the_production_store(seeded_home: Path) -> None:
    """Exactly one loop, fully parsed, scoped to a project that exists."""
    from personalclaw.loop import store as loop_store
    from personalclaw.tasks.hierarchy import HierarchyStore

    loops = loop_store.list_all()
    assert len(loops) == 1, f"expected the fixture's single loop, got {len(loops)}"
    loop = loops[0]

    assert loop.name and loop.task and loop.summary, "the demo loop reads as a blank row"
    assert (
        len(loop.plan) == 3
    ), f"the demo loop should carry its authored 3-phase plan, got {len(loop.plan)}"
    # A phase with no exit criteria renders as an empty checklist in the cockpit.
    for phase in loop.plan:
        assert phase.get("exit_criteria"), f"loop phase {phase.get('phase')!r} has no exit criteria"
    assert loop.total_cycles > 0, "a completed loop with zero cycles reads as never run"

    project_ids = {p.id for p in HierarchyStore().list_projects()}
    assert loop.project_id in project_ids, (
        f"the demo loop points at project {loop.project_id!r}, which is not in the "
        "fixture — the Loops surface would show an orphan"
    )


def test_the_seeded_loop_is_terminal_so_boot_does_not_spend_model_calls(
    seeded_home: Path,
) -> None:
    """A seeded ``running``/``planning`` loop is re-armed at gateway boot.

    That would spend real model calls on the machine of whoever ran
    ``--seed demo-home`` just to look at a demo. Only the two documented terminal
    states are safe to ship.
    """
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import LoopStatus

    terminal = {LoopStatus.COMPLETE.value, LoopStatus.STOPPED.value}
    for loop in loop_store.list_all():
        assert loop.status in terminal, (
            f"seeded loop {loop.id} ships in status {loop.status!r}; the gateway "
            f"re-arms anything outside {sorted(terminal)} on boot"
        )


def test_the_seeded_loop_dir_survives_the_boot_time_orphan_reap(seeded_home: Path) -> None:
    """``reap_orphan_dirs()`` runs once at boot and deletes any ``loop/<8hex>/``
    directory with no backing DB row.

    A text-only loop fixture is therefore silently wiped the first time the
    gateway starts. This asserts the shipped dir is backed by a real row, which
    is the whole reason ``loops.db`` is in the fixture.
    """
    from personalclaw.loop import store as loop_store

    loop_id = loop_store.list_all()[0].id
    loop_dir = seeded_home / "loop" / loop_id
    assert loop_dir.is_dir(), "the fixture's loop dir did not survive seeding"

    reaped = loop_store.reap_orphan_dirs()
    assert reaped == 0, f"the boot-time reap deleted {reaped} seeded loop dir(s)"
    assert loop_dir.is_dir(), (
        "the seeded loop dir was reaped — its loops.db row is missing, so a real "
        "boot would wipe it too"
    )


def test_the_committed_fixture_dbs_are_self_contained() -> None:
    """Read off the repo tree, not a seeded home — this is about what SHIPS.

    Both stores open with ``PRAGMA journal_mode=WAL``, so writes sit in a ``-wal``
    sidecar until checkpointed. Committing the bare ``.db`` without a checkpoint
    ships a fixture that boots EMPTY, and committing the sidecar ships a file that
    is not state. Also pins that no generation-machine path leaked into the bytes.
    """
    dbs = sorted(_DEMO_DIR.rglob("*.db"))
    assert len(dbs) == 2, f"expected knowledge.db + loops.db in the fixture, found {dbs}"

    strays = sorted(
        p.name for p in _DEMO_DIR.rglob("*") if p.name.endswith(("-wal", "-shm", ".db-journal"))
    )
    assert not strays, f"SQLite sidecars must not ship: {strays}"

    for db in dbs:
        blob = db.read_bytes()
        for needle in (b"/Users/", b"/home/", b"/private/tmp", b"/var/folders"):
            assert needle not in blob, (
                f"{db.name} embeds the absolute path {needle.decode()!r} from the "
                "machine that generated it — it will not resolve in a user's home"
            )


def test_every_demo_surface_is_non_empty(seeded_home: Path) -> None:
    """The vacuity floor for the whole fixture.

    ``done_when`` is "boots a demo-ready dashboard". Each surface above is
    asserted on its own, but a future refactor that quietly empties one store
    would leave the others green and still ship a half-blank demo. This pins a
    count per surface in one place, so "demo-ready" cannot degrade silently.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
    from personalclaw.loop import store as loop_store
    from personalclaw.tasks.hierarchy import HierarchyStore

    hierarchy = HierarchyStore()
    counts = {
        "projects": len(hierarchy.list_projects()),
        "task_lists": len(hierarchy.list_task_lists()),
        "tasks": len(_load_tasks()),
        "knowledge": KnowledgeStore(str(knowledge_db_path(seeded_home))).get_stats()["items"],
        "loops": len(loop_store.list_all()),
        "memory_files": len(list((seeded_home / "workspace" / "memory").rglob("*.md"))),
    }
    empty = sorted(name for name, n in counts.items() if not n)
    assert not empty, f"these demo surfaces are empty: {empty} (counts={counts})"

    # Floors, not just non-zero: one token row per surface is not a demo.
    assert counts["projects"] >= 4, counts
    assert counts["tasks"] >= 10, counts
    assert counts["task_lists"] >= 3, counts
    assert counts["knowledge"] >= 5, counts
    assert counts["loops"] == 1, counts
    assert counts["memory_files"] >= 4, counts


# ── packaging: the wheel must carry the whole tree ──────────────────────────


def _package_data_globs() -> list[str]:
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"\[tool\.setuptools\.package-data\](.*?)\n\[", text, re.S)
    assert block, "could not find the [tool.setuptools.package-data] block"
    return re.findall(r'"([^"]+)"', block.group(1))


def test_every_fixture_file_is_covered_by_a_package_data_glob() -> None:
    """Only observable from ``pyproject.toml``: an editable install looks perfect
    while the WHEEL ships a half-empty fixture.

    The glob used to be ``tests_fixtures/*/*`` — one level deep, which covered
    ``empty/fixture.yaml`` and nothing else. Every nested file the demo fixture
    needs (``projects/<id>/project.json``,
    ``workspace/memory/history/<date>.md``) was silently dropped from the wheel,
    so ``pip install personalclaw && personalclaw gateway --seed demo-home``
    would have produced a home with no projects and no memory.

    This asserts *coverage of the real tree* rather than the presence of a
    string, so it keeps holding as the fixture grows deeper.
    """
    globs = [g for g in _package_data_globs() if g.startswith("tests_fixtures/")]
    assert globs, "no tests_fixtures glob in the package-data block"

    pkg_root = _FIXTURES_DIR.parent
    matched: set[Path] = set()
    for pattern in globs:
        matched.update(p for p in pkg_root.glob(pattern) if p.is_file())

    present = {p for p in _FIXTURES_DIR.rglob("*") if p.is_file()}
    assert present, "no fixture files on disk — this test would pass vacuously"

    missing = sorted(str(p.relative_to(pkg_root)) for p in present - matched)
    assert not missing, (
        "these fixture files match no package-data glob and would be missing "
        f"from the wheel: {missing}"
    )


def test_the_demo_fixture_is_nested_deeper_than_one_level() -> None:
    """The vacuity floor for the test above.

    A coverage check over a flat tree is satisfied by the old one-level glob, so
    it would pass while the packaging bug it exists to catch is wide open. This
    pins that the fixture really does keep files below ``<fixture>/<file>`` —
    i.e. that there is something for the recursive glob to be load-bearing for.
    """
    depths = [len(p.relative_to(_DEMO_DIR).parts) for p in _DEMO_DIR.rglob("*") if p.is_file()]
    assert depths, f"{FIXTURE_NAME} has no files"
    assert max(depths) >= 3, (
        "the demo fixture is flat — the recursive package-data glob is no longer "
        "load-bearing and the coverage test above has nothing to prove"
    )
