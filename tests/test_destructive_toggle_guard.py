"""RET-6 — no user-visible switch irreversibly deletes state.

Grounds: Open WebUI's four-user / four-day cluster (#29069, #29095, #29153, #29193) in which
toggling a model **off** did not hide it — it *permanently removed* it. Four different users
hit the same defect within four days, which is the signature of a switch that looks like a
view filter and is wired to a delete. The user's mental model of a toggle is "and I can put
it back"; when that is false, the state is gone and no downgrade recovers it.

**The enumeration is machine-derived, and this is the whole design.** The toggle list comes
from :data:`personalclaw.dashboard.handlers.core._EDITABLE_CONFIG` — the *actual object* the
PATCH handler validates against, imported rather than re-typed — filtered to
``{"type": "bool"}``. A hand-written list would be the cheatable version of this atom: it can
only ever contain the toggles someone already suspected, so the one that deletes your models
is exactly the one missing from it. Adding a bool to the allowlist enrols it here
automatically, with no edit to this file; :func:`test_the_enumeration_is_derived_not_declared`
pins that there is no literal list to drift.

**What is asserted is the DOWNSTREAM OBSERVABLE, not the boolean.** For every toggle the sweep
drives ``False`` then ``True`` through the real ``PATCH /api/config/personalclaw`` handler, and
then asserts that nothing a user can see got smaller: every knowledge item, memory key, memory
file, loop, scheduled-run record, ``entity_settings`` file, configured provider, active model
selection and pre-existing file in the home is still there. Asserting the stored boolean came
back is the lazy version the atom names, and it is worthless — it is exactly what Open WebUI's
toggle did correctly while destroying the row behind it.

Deletion is the failure; addition is not. Every comparison is "the before set is still a
subset of the after set", never equality, because a config write legitimately appends to the
state-history root and to the log. A rail demanding byte equality here would be red on arrival
and would be relaxed into uselessness within a week.

**What this structurally CANNOT see** — stated plainly, because the honest boundary of a
machine-derived enumeration is the derivation's own reach:

* **Toggles that are not config booleans.** A per-entity enable/disable that lives in a
  database row or an ``entity_settings`` file rather than in ``_EDITABLE_CONFIG`` — an app's
  enabled flag, a knowledge source's, a trigger's — is not in the derivation and is not swept.
  The derivation source is one write path (the config PATCH allowlist), not every write path.
* **Deletion that happens later.** The sweep flips the toggle and reads the state back. A
  service that acts on the changed value on its next tick, or at the next restart, does its
  deleting after this test has finished. This catches synchronous destruction on the write
  path, which is where Open WebUI's was.
* **Non-bool destructive writes.** An ``enum`` or ``str_list`` field whose value change drops
  state is out of scope: the atom is about toggles, and widening the sweep to 239 entries with
  no notion of a safe value for each would produce a rail nobody could keep green.

The state under test is `RET-1`'s committed prior-release fixture (``six-month-home``), reused
deliberately: it is a home with real contents in every store, so "nothing got smaller" has
something to be smaller *than*. A fresh empty home would make every subset assertion trivially
true — the vacuity trap this atom's sibling was written to close.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import seed as seed_mod

FIXTURE_NAME = "six-month-home"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_HANDLERS = _REPO_ROOT / "src" / "personalclaw" / "dashboard" / "handlers" / "core.py"

#: Home subtrees whose contents legitimately change on any config write (the state-history
#: committer, the log, the runtime secret). Excluded from the file-inventory observable so the
#: rail measures state loss rather than normal churn.
_VOLATILE = ("state-history", "gateway.log", ".local_secret", "logs", "__pycache__")

#: A provider and a model the seeded home is given so "the model is still listed and
#: selectable" has a subject. Named as a probe so it can never be mistaken for real config.
_PROBE_PROVIDER = "ret6-probe-provider"
_PROBE_MODEL = "ret6-probe-model"

#: A fixed timestamp for the seeded graph rows. Constant so nothing in the sweep depends on
#: wall-clock time.
_STAMP = "2026-09-18T00:00:00Z"


# ── the machine derivation ──────────────────────────────────────────────────────────────


def boolean_toggles() -> tuple[str, ...]:
    """Every bool in the config PATCH allowlist, read from the allowlist itself.

    This is the *live object* the write path validates against, not a copy of it. If the two
    could drift the rail would be worthless, so there is deliberately nothing to keep in sync.
    """
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    return tuple(
        sorted(key for key, spec in _EDITABLE_CONFIG.items() if spec.get("type") == "bool")
    )


# ── the downstream observables, each through its production reader ──────────────────────


def _sqlite_ids(db: Path, sql: str) -> list[str]:
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        return sorted(str(row[0]) for row in conn.execute(sql))
    finally:
        conn.close()


def observables(home: Path) -> dict[str, Any]:
    """Everything a user could notice the loss of, keyed by surface.

    Read through the product's own readers rather than by poking the files, so a reader that
    starts filtering rows out counts as a loss even when the row is still on disk — which is
    what "the model is still *listed*" means. That distinction is the reason this returns
    listings rather than counts.
    """
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
    from personalclaw.providers.entity_routes import _load_entity_settings
    from personalclaw.providers.use_cases import load_active_models

    out: dict[str, Any] = {}

    knowledge = KnowledgeStore(str(knowledge_db_path(home)))
    try:
        out["knowledge.items"] = sorted(
            str(r[0]) for r in knowledge.db.execute("SELECT id FROM items")
        )
    finally:
        knowledge.db.close()

    # `semantic_memory` is singular and `episodic_memories` is plural. Both spellings are read
    # from the fixture's committed manifest rather than guessed; the first draft of this file
    # guessed and got an `OperationalError` instead of a survival signal.
    memory_db = home / "memory.db"
    out["memory.semantic_keys"] = _sqlite_ids(memory_db, "SELECT key FROM semantic_memory")
    out["memory.episodic_ids"] = _sqlite_ids(memory_db, "SELECT id FROM episodic_memories")
    # The memory GRAPH, which has its own toggle in the allowlist — so "graph off" deleting
    # the entities it had already extracted is precisely a case this rail must be able to see.
    out["memory.graph_entities"] = _sqlite_ids(memory_db, "SELECT id FROM mem_entities")
    out["memory.graph_links"] = _sqlite_ids(memory_db, "SELECT id FROM mem_links")
    out["memory.markdown"] = sorted(
        str(p.relative_to(home)) for p in (home / "workspace" / "memory").rglob("*.md")
    )
    out["loops"] = _sqlite_ids(home / "loop" / "loops.db", "SELECT id FROM loops")
    out["loop.dirs"] = sorted(p.name for p in (home / "loop").iterdir() if p.is_dir())

    # Scheduled-run records, identified by `job_id:run_id` — the row identity, not a file count.
    # Read at the JSONL layer rather than through `ScheduleRunStore`: that reader is async and
    # this function is called from inside a running event loop, where `asyncio.run` raises. The
    # trade is stated rather than hidden — a reader that started FILTERING runs out would not be
    # caught here, only a run whose record was destroyed.
    history_root = home / "cron-history"
    runs: list[str] = []
    if history_root.is_dir():
        for path in sorted(history_root.glob("*.jsonl")):
            if path.name.startswith("_"):  # `_index.jsonl` is a derived index, not a record
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                runs.append(f"{record.get('job_id')}:{record.get('run_id')}")
    out["run_history"] = sorted(runs)

    settings_root = home / "entity_settings"
    entity_settings: dict[str, Any] = {}
    if settings_root.exists():
        for path in sorted(settings_root.glob("*.json")):
            # The RAW loader return, `None` for an unreadable file included — no `or {}` here.
            # A toggle that leaves a settings file unparseable is precisely a loss this rail
            # must be able to see, and normalising the discard away would hide it.
            entity_settings[path.stem] = _load_entity_settings(path.stem)
    out["entity_settings"] = entity_settings

    # The atom's literal case: the model is still listed and still selectable. `providers` is
    # the inventory a listing is built from; `models.active` is the selection itself. Losing
    # either is the Open WebUI failure, and both live in `config.json` — the same file the
    # toggle writes, which is exactly why a toggle could take them out.
    providers = _read_config(home).get("providers")
    # Normalised to a list unconditionally. A missing key must read as "no providers", not as
    # `None`: the two are the same loss to a user, and a surface whose TYPE changes when the key
    # is dropped skips the comparison instead of failing it (`set(None)` raises, and a raising
    # comparison is not a red — it is a broken rail).
    out["providers"] = (
        sorted(str(p.get("name", "")) for p in providers if isinstance(p, dict))
        if isinstance(providers, list)
        else []
    )
    out["models.active"] = {uc: list(ids) for uc, ids in sorted(load_active_models().items())}

    out["files"] = sorted(
        str(p.relative_to(home))
        for p in home.rglob("*")
        if p.is_file() and not any(v in p.parts or v == p.name for v in _VOLATILE)
    )
    return out


def _read_config(home: Path) -> dict[str, Any]:
    path = home / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _key_paths(node: Any, prefix: str = "") -> set[str]:
    """Every dotted key path in a config document, so a vanished SUBTREE is visible."""
    paths: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{prefix}.{key}" if prefix else str(key)
            paths.add(here)
            paths |= _key_paths(value, here)
    return paths


# ── the assertion, callable with an arbitrary flip so the teeth test can inject a
#    destructive one and run the REAL rail rather than a copy of it ─────────────────────


def check_toggle_is_reversible(home: Path, key: str, flip: Callable[[str, bool], None]) -> None:
    """RED if driving *key* False-then-True loses anything observable.

    ``flip`` writes the value; the caller supplies it so this function can be driven over the
    real HTTP handler in the sweep and over a deliberately destructive writer in the teeth
    test, with no second implementation of the comparison.
    """
    before = observables(home)
    before_keys = _key_paths(_read_config(home))

    flip(key, False)
    flip(key, True)

    assert_nothing_lost(key, before, observables(home), before_keys, _key_paths(_read_config(home)))


# ── the sweep ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def seeded_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """RET-1's prior-release fixture in an isolated home.

    `config_dir()` re-reads `$PERSONALCLAW_HOME` on every call, so this points every store and
    the PATCH handler's `config_path()` at the seeded tree. The developer's real
    `~/.personalclaw` is never read or written — and `seed()` refuses the real home outright.
    """
    home = tmp_path / "toggle_home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    seed_mod.seed(FIXTURE_NAME)

    # The fixture ships no `providers` and no active-model selection, so the two observables
    # that carry the atom's literal case ("the model is still listed and selectable") would be
    # EMPTY — and a subset assertion over two empty lists passes for a home whose models were
    # all deleted. Both are therefore populated here, through the product's own writer, so the
    # sweep has a model to lose. This is the vacuity hole the first draft of this file shipped
    # with: the whole 80-toggle sweep was green while those two surfaces proved nothing.
    from personalclaw.providers.use_cases import save_active_models

    config_file = home / "config.json"
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["providers"] = [
        {"name": _PROBE_PROVIDER, "type": "openai_compatible", "base_url": "http://127.0.0.1:1/v1"}
    ]
    config_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    save_active_models({"chat": [f"{_PROBE_PROVIDER}:{_PROBE_MODEL}"]})

    # Same reason, for the memory GRAPH: the fixture's `mem_entities`/`mem_links` are empty, and
    # `memory.graph_enabled` is one of the swept toggles — so without a row here, "graph off
    # deleted the entities it had extracted" is exactly the case the rail could not see.
    conn = sqlite3.connect(str(home / "memory.db"))
    try:
        conn.execute(
            "INSERT INTO mem_entities (id, name, entity_type, aliases, source, created_at, "
            "updated_at, is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            ("ret6-entity", "Probe Entity", "person", "[]", "test", _STAMP, _STAMP),
        )
        # `mem_links.id` is an INTEGER PRIMARY KEY (a rowid alias), so it is omitted and
        # allocated by SQLite. `mem_entities.id` is TEXT and is supplied.
        conn.execute(
            "INSERT INTO mem_links (from_kind, from_ref, to_entity, to_ref, link_type, "
            "provenance, confidence, context, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("semantic", "ret6", "ret6-entity", "", "mentions", "test", 1.0, "", _STAMP),
        )
        conn.commit()
    finally:
        conn.close()
    return home


def _app() -> web.Application:
    from personalclaw.dashboard.handlers import api_personalclaw_config_patch

    app = web.Application()
    app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
    return app


@pytest.mark.asyncio
async def test_no_boolean_toggle_irreversibly_deletes_state(seeded_home: Path) -> None:
    """The atom. Every machine-derived toggle, off and back on, against a populated home."""
    toggles = boolean_toggles()
    assert len(toggles) >= 50, (
        f"the derivation found only {len(toggles)} bool toggles in _EDITABLE_CONFIG — it is "
        "reading the wrong thing, and a sweep over an almost-empty list proves nothing"
    )

    async with TestClient(TestServer(_app())) as client:
        written: list[tuple[str, bool]] = []

        async def _patch(key: str, value: bool) -> None:
            # As the OWNER who has consented: a toggle whose ON (or OFF) loosens a security
            # setting needs `confirm: true` (config/edit_spec.py). What this sweep measures is
            # whether a toggle destroys state, which is the same question with or without it.
            resp = await client.patch(
                "/api/config/personalclaw", json={"path": key, "value": value, "confirm": True}
            )
            assert resp.status == 200, (
                f"PATCH {key}={value} returned {resp.status}: {await resp.text()}. Every entry "
                "in the allowlist must be writable through the path that declares it."
            )
            # The vacuity floor: prove the write LANDED before asserting anything survived it.
            # A handler that silently no-ops would make every survival assertion below pass
            # for the reason that nothing happened.
            stored = _read_config(seeded_home)
            cursor: Any = stored
            for segment in key.split("."):
                cursor = cursor[segment]
            assert cursor is value, f"{key} reads back as {cursor!r} after writing {value!r}"
            written.append((key, value))

        # The baseline is captured ONCE, before any toggle is touched, and every toggle is
        # compared against it. That is what "restores the PRIOR downstream observable" means
        # literally, and it makes the sweep cumulative: state lost by toggle 12 stays lost, so
        # it is still reported if toggle 13 happens to look clean. Re-reading the baseline
        # between toggles would let a destructive toggle launder its own damage into the next
        # comparison's starting point.
        baseline = observables(seeded_home)
        baseline_keys = _key_paths(_read_config(seeded_home))
        _assert_the_baseline_can_lose_something(baseline)

        for key in toggles:
            # The same three steps `check_toggle_is_reversible` performs, spelled out here
            # because the writes are awaited: drive OFF, drive ON, compare. The comparison is
            # the shared `assert_nothing_lost`, so the sweep and the teeth tests can never
            # diverge on what counts as a loss.
            await _patch(key, False)
            await _patch(key, True)
            assert_nothing_lost(
                key,
                baseline,
                observables(seeded_home),
                baseline_keys,
                _key_paths(_read_config(seeded_home)),
            )

        assert len(written) == 2 * len(
            toggles
        ), f"expected {2 * len(toggles)} landed writes, recorded {len(written)}"


def _assert_the_baseline_can_lose_something(baseline: dict[str, Any]) -> None:
    """Every observable surface is non-empty before the sweep starts.

    A subset assertion over an empty collection is always true, so an empty surface is a rail
    that reports nothing while looking green — three surfaces of this file were exactly that
    in its first draft. Enumerated here so adding an observable and forgetting to populate it
    reds immediately rather than silently widening the blind spot.
    """
    empty = sorted(name for name, value in baseline.items() if not value)
    assert not empty, (
        f"these observable surfaces are EMPTY in the seeded home: {empty}. A 'nothing was "
        "lost' assertion over an empty collection is vacuously true, so each of these proves "
        "nothing until the fixture gives it something to lose. Populate it in `seeded_home` "
        "or remove the surface — do not leave it enumerated and empty."
    )


def assert_nothing_lost(
    key: str,
    before: dict[str, Any],
    after: dict[str, Any],
    before_keys: set[str],
    after_keys: set[str],
) -> None:
    """The ONE comparison, shared by the sweep and by every teeth test.

    Deletion is the failure; addition is not. Lists are compared as "the before set is still a
    subset", never for equality, because a config write legitimately appends to the
    state-history root. Dicts are compared entry by entry so a single changed setting is named
    rather than hidden in a whole-object diff, and the config key-path set is compared
    separately so a vanished SUBTREE is caught even where the surface reader substitutes a
    default for the missing key.
    """
    failures: list[str] = []
    for surface, prior in before.items():
        now = after[surface]
        if isinstance(prior, list):
            lost = sorted(set(prior) - set(now))
            if lost:
                failures.append(f"{surface}: lost {lost}")
        elif isinstance(prior, dict):
            for name, value in prior.items():
                if name not in now:
                    failures.append(f"{surface}: entry {name!r} disappeared")
                elif now[name] != value:
                    failures.append(f"{surface}[{name}]: was {value!r}, now {now[name]!r}")
        elif prior != now:
            failures.append(f"{surface}: was {prior!r}, now {now!r}")
    lost_keys = sorted(k for k in before_keys - after_keys if k != key)
    if lost_keys:
        failures.append(f"config.json: these key paths disappeared: {lost_keys}")
    assert not failures, (
        f"toggling {key!r} off and back on did not restore what a user can see:\n"
        + "\n".join(f"  {f}" for f in failures)
        + "\n\nA toggle a user can flip must be reversible. Anything listed above is state the "
        "user cannot get back by flipping the switch again — the defect that cost Open WebUI "
        "four users in four days (#29069, #29095, #29153, #29193), where toggling a model OFF "
        "removed it permanently instead of hiding it. If this switch is genuinely meant to "
        "destroy state, it is not a toggle: it needs a confirmation and a named destructive "
        "action, not a checkbox."
    )


# ── the derivation is real, not a list someone typed ────────────────────────────────────


def test_the_enumeration_is_derived_not_declared() -> None:
    """No literal toggle list may exist in this file. A hand-written list FAILS the atom.

    Enforced against this module's own source, because the failure mode is not "someone
    disagrees with the design" — it is a future edit pinning the list to make a red go away,
    which would silently return the rail to only-the-toggles-we-thought-of.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    derived = boolean_toggles()
    # Every toggle name appearing as a string literal here would be a hand-listed entry. The
    # module docstring names none of them, deliberately.
    literals = sorted(name for name in derived if f'"{name}"' in source or f"'{name}'" in source)
    assert not literals, (
        f"these toggle names are hard-coded in this file: {literals}. The enumeration must "
        "come from _EDITABLE_CONFIG so a newly added toggle is swept without editing this "
        "test — a hand-written list can only contain the toggles someone already thought of."
    )
    assert "_EDITABLE_CONFIG" in source, "the derivation source is no longer referenced"


def test_the_derivation_tracks_the_write_path_it_claims_to() -> None:
    """The allowlist this reads is the one the PATCH handler validates against.

    Proven by parsing the handler's source for the literal and comparing key-for-key with the
    imported object, so a second copy of the dict appearing somewhere else cannot quietly
    become the thing this rail sweeps.
    """
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    source = _CORE_HANDLERS.read_text(encoding="utf-8")
    assert "_EDITABLE_CONFIG: dict[str, dict] = {" in source, (
        "the _EDITABLE_CONFIG literal is no longer declared in "
        f"{_CORE_HANDLERS.relative_to(_REPO_ROOT)} — find where the PATCH path's allowlist "
        "moved to and repoint this rail, because an allowlist nothing sweeps is the whole "
        "defect this file exists to prevent"
    )
    missing = sorted(key for key in boolean_toggles() if f'"{key}"' not in source)
    assert not missing, (
        f"these bool toggles are in the imported allowlist but not in its declaring source: "
        f"{missing} — the object and the literal have diverged"
    )
    assert len(_EDITABLE_CONFIG) > len(
        boolean_toggles()
    ), "every allowlist entry is a bool, which means the type filter is not filtering"


# ── the negative case: a destructive toggle is caught ───────────────────────────────────


def test_the_rail_reds_on_a_toggle_that_deletes_the_row(seeded_home: Path) -> None:
    """Open WebUI's exact defect, injected: flipping OFF removes the row for good.

    The destructive writer deletes knowledge items on the way OFF and writes the value on the
    way back ON — a toggle whose stored boolean round-trips perfectly while the state behind it
    is gone. That is the lazy implementation the atom names, and the assertion below is the
    proof this rail is not it.
    """
    from personalclaw.knowledge.store import knowledge_db_path

    baseline = observables(seeded_home)
    assert baseline["knowledge.items"], (
        "the seeded fixture has no knowledge items, so 'the row was deleted' has nothing to "
        "delete and this negative case would pass vacuously"
    )

    def destructive_flip(key: str, value: bool) -> None:
        _write_toggle(seeded_home, key, value)
        if value is False:
            conn = sqlite3.connect(str(knowledge_db_path(seeded_home)))
            try:
                conn.execute("DELETE FROM items")
                conn.commit()
            finally:
                conn.close()

    with pytest.raises(AssertionError, match=r"knowledge\.items: lost"):
        check_toggle_is_reversible(seeded_home, boolean_toggles()[0], destructive_flip)


def test_the_rail_reds_on_a_toggle_that_unlists_the_model(seeded_home: Path) -> None:
    """The atom's literal case: the model stops being listed and selectable.

    Distinct from the row-deletion arm above in two ways that matter. Nothing is deleted from a
    database — a `providers` entry is dropped from `config.json` — so a rail watching only DB
    rows would call this reversible. And the model's disappearance is not written by the
    destructive code at all: `load_active_models()` PRUNES refs whose provider is no longer
    configured (`_prune_removed_providers`, `providers/use_cases.py`), which is correct
    behaviour for a removed provider and catastrophic as the side effect of a checkbox. The
    selection file on disk is untouched throughout; the model is simply no longer listed.

    That is the closest thing in this codebase to Open WebUI's defect, and it is reachable
    today by any toggle handler that decides to tidy up `providers` on its way off.
    """
    baseline = observables(seeded_home)
    assert baseline["models.active"].get("chat"), "the probe model is not selected to begin with"

    def destructive_flip(key: str, value: bool) -> None:
        raw = _read_config(seeded_home)
        if value is False:
            raw.pop("providers", None)
        _set_path(raw, key, value)
        (seeded_home / "config.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")

    with pytest.raises(AssertionError, match=r"models\.active|providers"):
        check_toggle_is_reversible(seeded_home, boolean_toggles()[0], destructive_flip)


def test_the_rail_is_green_on_a_faithful_toggle(seeded_home: Path) -> None:
    """The control arm. Without it every red above could be caused by the harness.

    A writer that only writes the value leaves every observable intact, so the rail passes —
    which is what makes the two failures above attributable to the destruction and not to the
    fixture, the readers, or the comparison.
    """

    def faithful_flip(key: str, value: bool) -> None:
        _write_toggle(seeded_home, key, value)

    check_toggle_is_reversible(seeded_home, boolean_toggles()[0], faithful_flip)


def _set_path(raw: dict[str, Any], key: str, value: bool) -> None:
    cursor: Any = raw
    parts = key.split(".")
    for segment in parts[:-1]:
        cursor = cursor.setdefault(segment, {})
    cursor[parts[-1]] = value


def _write_toggle(home: Path, key: str, value: bool) -> None:
    raw = _read_config(home)
    _set_path(raw, key, value)
    (home / "config.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
