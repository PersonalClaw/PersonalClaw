"""Drift guard for the committed config-schema baseline (PLATFORM-HARDENING-FLOORS SH3.1).

``config-baseline.json`` is GENERATED from the ``AppConfig`` dataclass hierarchy and
its ``_meta`` metadata by ``scripts/generate_config_baseline.py`` — the same source of
truth ``test_config_roundtrip.py`` walks. This suite is what keeps the committed copy
honest: it regenerates in-memory and byte-compares, so a schema change not regenerated
(a renamed key, an added field, a dropped ``_meta``) reddens CI. Regenerate with
``python scripts/generate_config_baseline.py``.

The round-trip test proves each field SURVIVES a save/load cycle; this baseline proves
the field SET itself has not drifted — the two are complementary.
"""

from __future__ import annotations

import json

from scripts.generate_config_baseline import baseline_path, build_baseline


def test_committed_baseline_matches_a_fresh_render():
    """The committed baseline byte-matches a fresh render.

    This is the whole point: renaming a config field without regenerating, or
    adding one, changes the fresh render — so the committed file no longer matches
    and CI reds, naming the drift.
    """
    fresh = build_baseline()
    path = baseline_path()
    assert path.is_file(), (
        "config-baseline.json is missing — generate it with "
        "`python scripts/generate_config_baseline.py`"
    )
    committed = path.read_text(encoding="utf-8")
    assert committed == fresh, (
        "config-baseline.json is stale — a config field was renamed, added, or "
        "removed without regenerating. Run `python scripts/generate_config_baseline.py`."
    )


def test_render_is_deterministic():
    """Generating twice yields byte-identical output — no set-ordering, no timestamps."""
    assert build_baseline() == build_baseline()


def test_baseline_is_flat_sorted_and_well_shaped():
    """Every entry is a flat leaf with the four declared keys, sorted by path."""
    entries = json.loads(build_baseline())
    assert entries, "baseline is empty"
    paths = [e["path"] for e in entries]
    assert paths == sorted(paths), "entries are not sorted by path"
    assert len(paths) == len(set(paths)), "duplicate paths in baseline"
    for e in entries:
        assert set(e) == {"path", "type", "default", "sensitive"}, e
        assert isinstance(e["path"], str) and e["path"]
        assert isinstance(e["type"], str) and e["type"]
        assert isinstance(e["sensitive"], bool)


def test_renaming_a_field_is_caught():
    """A renamed path in the committed file no longer matches the fresh render.

    Simulates done_when case (i): a field renamed without regenerating. We mutate a
    COPY of the fresh render (never the committed file) and confirm the byte-compare
    the drift test relies on would fail.
    """
    entries = json.loads(build_baseline())
    assert entries[0]["path"] != "renamed.field"
    entries[0]["path"] = "renamed.field"
    mutated = json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert mutated != build_baseline()


def test_adding_a_field_is_caught():
    """Adding an entry changes the render — done_when case (ii)."""
    entries = json.loads(build_baseline())
    entries.append({"path": "zzz.new_field", "type": "str", "default": "", "sensitive": False})
    mutated = json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert mutated != build_baseline()


def test_baseline_carries_no_secret_values():
    """No committed default is a live-looking credential.

    Defaults are declaration-time, so this is inherently safe; the assertion makes
    the guarantee explicit. The ``sensitive`` flag records WHICH fields are sensitive
    for future use — it never emits a real secret VALUE.
    """
    entries = json.loads(build_baseline())
    for e in entries:
        default = e["default"]
        if isinstance(default, str):
            # No default should look like a populated token/key/password.
            assert not (
                len(default) > 20 and default.isalnum() and default.islower()
            ), f"{e['path']} default looks like a credential: {default!r}"
