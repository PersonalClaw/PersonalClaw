"""Pinned artifacts + the touched-items feed (WORK-CONTAINERS §6.5 R13 — WF2WOR-7).

The dashboard has NO tile registry — the bento grid and per-user layout persistence were
deliberately retired, and widgets are hard-imported. So a pin is a REFERENCE in a list that one
hard-imported widget renders, not a layout entry. These tests pin that shape, because the tempting
wrong version (a per-tile registry) would rebuild the machinery that was removed.
"""

import pytest

from personalclaw.workflows import pinned


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A real entity_settings dir in an isolated home.

    `PERSONALCLAW_HOME` AND the module-level binding: patching `config_dir` alone still lets a
    module that bound it at import write to the REAL home, which has polluted `~/.personalclaw`
    before. The store here resolves through `providers.entity_routes`, so that is the binding to
    move.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.providers.entity_routes.config_dir", lambda: home)
    return home


class TestPinning:
    def test_a_pin_holds_a_reference_never_a_copy(self) -> None:
        """A denormalized name would go stale on the next rename, and a dashboard card showing a
        title the artifact no longer has is worse than one showing nothing."""
        pinned.pin("weekly-report", run_id="r1")
        [entry] = pinned.list_pins()
        assert entry["slug"] == "weekly-report"
        assert entry["run_id"] == "r1"
        assert entry["pinned_at"]
        # No name, no content, no kind: those live on the artifact.
        assert set(entry) == {"slug", "pinned_at", "run_id"}

    def test_repinning_MOVES_rather_than_duplicating(self) -> None:
        """A list that could hold one slug twice would render two identical cards."""
        pinned.pin("a")
        pinned.pin("b")
        pinned.pin("a")
        assert [p["slug"] for p in pinned.list_pins()] == ["a", "b"]

    def test_pins_are_newest_first(self) -> None:
        pinned.pin("old")
        pinned.pin("new")
        assert [p["slug"] for p in pinned.list_pins()] == ["new", "old"]

    def test_the_cap_evicts_the_OLDEST(self) -> None:
        """The pin the user just created is the one they want; evicting it would make the control
        look broken."""
        for i in range(pinned.MAX_PINS + 3):
            pinned.pin(f"a{i}")
        pins = pinned.list_pins()
        assert len(pins) == pinned.MAX_PINS
        assert pins[0]["slug"] == f"a{pinned.MAX_PINS + 2}"
        assert "a0" not in [p["slug"] for p in pins]

    def test_unpinning_something_unpinned_is_a_noop_not_an_error(self) -> None:
        """A double-click on Unpin should not produce a failure a user has to read."""
        pinned.pin("a")
        assert pinned.unpin("nope") == pinned.list_pins()
        assert pinned.unpin("a") == []

    def test_is_pinned_reflects_the_state_the_control_renders(self) -> None:
        assert pinned.is_pinned("a") is False
        pinned.pin("a")
        assert pinned.is_pinned("a") is True
        pinned.unpin("a")
        assert pinned.is_pinned("a") is False

    def test_an_empty_slug_is_refused_rather_than_stored(self) -> None:
        """A pin with no slug is a row that can never resolve to an artifact — a permanent broken
        card on the dashboard."""
        pinned.pin("")
        pinned.pin("   ")
        assert pinned.list_pins() == []

    def test_a_corrupt_store_reads_as_empty_rather_than_crashing(self) -> None:
        """Fail-OPEN for the STORE: a pin is a bookmark, so the worst case of a bad read is an
        empty widget. Crashing the dashboard over a bookmark would be the real bug."""
        from personalclaw.providers.entity_routes import _entity_settings_path

        path = _entity_settings_path("pinned_artifacts")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert pinned.list_pins() == []

    def test_a_malformed_entry_is_skipped_not_rendered(self) -> None:
        from personalclaw.providers.entity_routes import _save_entity_settings

        _save_entity_settings(
            "pinned_artifacts",
            {"pins": ["a string", {"no_slug": 1}, {"slug": "good", "pinned_at": "t"}]},
        )
        assert [p["slug"] for p in pinned.list_pins()] == ["good"]

    def test_the_store_lives_in_entity_settings_not_config(self) -> None:
        """Entity/user state goes in `entity_settings/*.json`; `config.json` is for config."""
        from personalclaw.providers.entity_routes import _entity_settings_path

        pinned.pin("a")
        assert _entity_settings_path("pinned_artifacts").is_file()


class TestTouchedItemsFeed:
    """The live touched-items feed: what a run PUBLISHED and what was handed INTO it.

    Both sources are already run-scoped, which is why the feed is buildable at all — attribution
    is the hard part, not the union.
    """

    def test_a_run_that_touched_nothing_has_an_empty_feed(self) -> None:
        from personalclaw.workflows import service

        assert service.touched_items("no-such-run") == []

    def test_published_artifacts_and_dropped_files_are_UNIONED(self) -> None:
        from personalclaw.workflows import service, store

        store.append_jsonl(
            "r-touch",
            "publishes.jsonl",
            {
                "ts": "2026-08-11T02:00:00+00:00",
                "slug": "report",
                "artifact": "Weekly report",
                "kind": "markdown",
                "action": "version",
                "change_note": "18% changed",
                "node_id": "write",
            },
        )
        from personalclaw.workflows import filedrop

        filedrop.record_drop(
            "r-touch",
            {
                "filename": "input.csv",
                "size": 10,
                "sha256": "x",
                "mime": "text/csv",
                "accepted_at": "2026-08-11T03:00:00+00:00",
            },
        )
        rows = service.touched_items("r-touch")
        assert {r["kind"] for r in rows} == {"artifact", "file"}
        # Newest-first: a feed is read from the top, and the latest touch is what a watching user
        # is waiting for.
        assert rows[0]["ref"] == "input.csv"

    def test_the_publish_VERB_is_preserved(self) -> None:
        """A converged republish is not a new version. Collapsing them would make an unchanged
        artifact look freshly written."""
        from personalclaw.workflows import service, store

        store.append_jsonl(
            "r-verb",
            "publishes.jsonl",
            {"ts": "2026-08-11T02:00:00+00:00", "slug": "s", "artifact": "A", "action": "noop"},
        )
        [row] = service.touched_items("r-verb")
        assert row["action"] == "noop"

    def test_a_row_with_no_timestamp_sorts_last_rather_than_crashing(self) -> None:
        from personalclaw.workflows import service, store

        for rec in (
            {"ts": "", "slug": "undated", "artifact": "U", "action": "create"},
            {
                "ts": "2026-08-11T02:00:00+00:00",
                "slug": "dated",
                "artifact": "D",
                "action": "create",
            },
        ):
            store.append_jsonl("r-sort", "publishes.jsonl", rec)
        rows = service.touched_items("r-sort")
        assert [r["ref"] for r in rows] == ["dated", "undated"]

    def test_the_feed_rides_the_introspect_payload(self) -> None:
        """It answers "what changed" for THINGS where the timeline answers it for STEPS, and a
        reader needs both together — so it is not a separate fetch."""
        import inspect

        from personalclaw.workflows import service

        assert "touched=touched_items(run_id)" in inspect.getsource(service.introspect)
