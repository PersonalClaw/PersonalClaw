"""TEAM-SHARED-ENTITIES §1 — the owner attribution handle.

The username lands in JSON records (and later shard filenames and sync payloads)
effectively forever, so the normalization rule is strict and pinned here. The other
half of the contract is that it stays *optional*: an install that never sets one
behaves exactly as it does today.
"""

from __future__ import annotations

import pytest

from personalclaw.identity import (
    USERNAME_MAX_LEN,
    current_username,
    is_valid_username,
    slugify_username,
    suggest_username,
)


class TestSlugify:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Keyur Golani", "keyur-golani"),
            ("keyur", "keyur"),
            ("  Trailing  Spaces  ", "trailing-spaces"),
            ("Ann-Marie O'Neil", "ann-marie-o-neil"),
            ("a@b.com", "a-b-com"),
            ("UPPER_CASE-ok", "upper_case-ok"),
            ("multiple   ---   separators", "multiple-separators"),
            ("--leading-and-trailing--", "leading-and-trailing"),
            ("digits123", "digits123"),
        ],
    )
    def test_canonical_forms(self, raw, expected):
        assert slugify_username(raw) == expected

    def test_accents_fold_to_base_letters(self):
        """Folding beats deleting: José should be jose, not jos."""
        assert slugify_username("José") == "jose"
        assert slugify_username("Ünicode Café") == "unicode-cafe"

    def test_non_decomposable_letters_become_separators(self):
        """Æ and ø are distinct letters, not accented forms — NFKD cannot fold
        them, so they normalize to a separator rather than a wrong guess."""
        assert slugify_username("Ærø") == "r"

    def test_length_is_capped_without_a_trailing_separator(self):
        raw = "a" * 20 + " " + "b" * 40
        out = slugify_username(raw)
        assert len(out) <= USERNAME_MAX_LEN
        assert not out.endswith(("-", "_"))

    @pytest.mark.parametrize("raw", ["", "   ", "---", "!!!", None])
    def test_unusable_input_yields_empty_not_a_fabricated_name(self, raw):
        """Empty is a VALID state (no attribution). Never invent `user-1`."""
        assert slugify_username(raw) == ""

    def test_idempotent(self):
        once = slugify_username("Keyur Golani")
        assert slugify_username(once) == once

    def test_is_valid_username_recognizes_canonical_form(self):
        assert is_valid_username("keyur-golani")
        assert not is_valid_username("Keyur Golani")
        assert is_valid_username("")  # empty is canonical

    def test_suggest_from_display_name(self):
        assert suggest_username("Keyur Golani") == "keyur-golani"


class TestConfigRoundTrip:
    def test_username_normalizes_on_load(self, tmp_path, monkeypatch):
        """A hand-edited config.json can't smuggle a non-canonical handle into
        records — load normalizes too, not just the write boundary."""
        import json

        import personalclaw.config.loader as loader

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        (tmp_path / "config.json").write_text(
            json.dumps({"dashboard": {"username": "  Hand Edited  "}})
        )
        cfg = loader.AppConfig.load()
        assert cfg.dashboard.username == "hand-edited"

    def test_absent_username_defaults_to_empty(self, tmp_path, monkeypatch):
        import json

        import personalclaw.config.loader as loader

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        (tmp_path / "config.json").write_text(json.dumps({"dashboard": {}}))
        assert loader.AppConfig.load().dashboard.username == ""

    def test_current_username_never_raises(self, monkeypatch):
        """Attribution decorates a write; a config fault must not fail the write."""

        def _boom(cls):
            raise RuntimeError("config exploded")

        monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(_boom))
        assert current_username() == ""


class TestTaskAttribution:
    """`Task.author` did not exist before this change; `assignee` did."""

    @pytest.mark.asyncio
    async def test_created_task_carries_the_owner_handle(self, tmp_path, monkeypatch):
        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        monkeypatch.setattr(native, "_current_username", lambda: "keyur-golani")
        task = await native.NativeTaskProvider().create_task(title="Ship it")
        assert task.author == "keyur-golani"
        assert task.assignee == ""  # author (who wrote it) != assignee (who does it)

    @pytest.mark.asyncio
    async def test_explicit_author_wins_over_the_owner_handle(self, tmp_path, monkeypatch):
        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        monkeypatch.setattr(native, "_current_username", lambda: "owner")
        task = await native.NativeTaskProvider().create_task(title="x", author="someone-else")
        assert task.author == "someone-else"

    @pytest.mark.asyncio
    async def test_no_handle_means_no_attribution(self, tmp_path, monkeypatch):
        """Today's behavior, unchanged, for an install that never sets a username."""
        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        monkeypatch.setattr(native, "_current_username", lambda: "")
        task = await native.NativeTaskProvider().create_task(title="x")
        assert task.author == ""

    @pytest.mark.asyncio
    async def test_preexisting_task_json_reads_back_without_author(self, tmp_path, monkeypatch):
        """The additive contract: a task file written before this field existed
        loads cleanly with author == "" rather than raising."""
        import json

        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        provider = native.NativeTaskProvider()
        tasks_dir = provider._ensure_dir()
        (tasks_dir / "t-legacy.json").write_text(
            json.dumps({"id": "t-legacy", "title": "From before", "status": "open"})
        )
        task = await provider.get_task("t-legacy")
        assert task is not None
        assert task.author == ""
        assert task.title == "From before"

    @pytest.mark.asyncio
    async def test_comment_author_falls_back_to_the_historical_placeholder(
        self, tmp_path, monkeypatch
    ):
        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        provider = native.NativeTaskProvider()
        task = await provider.create_task(title="x")
        monkeypatch.setattr(native, "_current_username", lambda: "")
        comment = await provider.add_comment(task.id, "a note")
        assert comment is not None and comment.author == "user"
        monkeypatch.setattr(native, "_current_username", lambda: "keyur-golani")
        second = await provider.add_comment(task.id, "another")
        assert second is not None and second.author == "keyur-golani"


class TestRenameSemantics:
    @pytest.mark.asyncio
    async def test_rename_affects_future_writes_only(self, tmp_path, monkeypatch):
        """Rewriting history to match a new handle would falsify the very record
        attribution exists to preserve."""
        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        provider = native.NativeTaskProvider()
        monkeypatch.setattr(native, "_current_username", lambda: "old-name")
        first = await provider.create_task(title="before rename")
        monkeypatch.setattr(native, "_current_username", lambda: "new-name")
        second = await provider.create_task(title="after rename")
        reloaded = await provider.get_task(first.id)
        assert reloaded is not None
        assert reloaded.author == "old-name"  # untouched
        assert second.author == "new-name"


class TestDashboardConfigEndpoint:
    """REGRESSION: the PUT handler carries its own field allowlist, separate from
    the dataclass. Adding the field in four places still left `username` rejected
    as an unknown field — caught only by driving the real endpoint."""

    def test_username_is_in_the_put_allowlist(self):
        from pathlib import Path

        import personalclaw.dashboard.handlers.files as files_mod

        src = Path(files_mod.__file__).read_text(encoding="utf-8")
        allowlist_start = src.index("_allowed = {")
        allowlist_end = src.index("unknown = set(body.keys())", allowlist_start)
        assert '"username"' in src[allowlist_start:allowlist_end], (
            "dashboard.username must be in the PUT /api/dashboard/config allowlist, "
            "or the endpoint 400s with 'Unknown fields'"
        )

    def test_username_is_returned_by_the_get(self):
        from pathlib import Path

        import personalclaw.dashboard.handlers.files as files_mod

        src = Path(files_mod.__file__).read_text(encoding="utf-8")
        assert '"username": cfg.dashboard.username' in src
