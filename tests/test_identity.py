"""TEAM-SHARED-ENTITIES §1 — the owner attribution handle.

The username lands in JSON records (and later shard filenames and sync payloads)
effectively forever, so the normalization rule is strict and pinned here. The other
half of the contract is that it stays *optional*: an install that never sets one
behaves exactly as it does today.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.identity import (
    DEFAULT_USER_NAME,
    USERNAME_MAX_LEN,
    current_username,
    is_valid_username,
    operator_name,
    slugify_username,
)

REPO = Path(__file__).resolve().parents[1]


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


class TestOneSlugSuggester:
    """TSE-1 — the display-name → handle suggestion has exactly ONE implementation.

    The suggester is a FRONTEND concern: it pre-fills a field the user can still edit,
    while the server re-normalizes whatever is finally submitted (``slugify_username`` at
    the PUT boundary and again on load). So the rule lives in TypeScript, where the
    keystroke is, and this module deliberately does not carry a Python twin — the one it
    had was exported, never called, and would have read as the canonical rule to the next
    reader while the shipped suggestion came from somewhere else entirely.

    What CAN drift, silently, is the pair that remains: the TS mirror of the slug rule and
    the cap it copies. These two tests are the rail — the same shape as
    ``test_api_version_one_origin.py``'s constant-parity check.
    """

    TS_MODULE = REPO / "web" / "src" / "app" / "identity.tsx"

    def test_exactly_one_frontend_slug_implementation(self):
        """NEGATIVE CONTROL. A second NFKD folder under ``web/src`` means a second
        convention was minted — which is how this atom's predecessor ended up with a
        Python function and a TypeScript function claiming the same job."""
        folders = sorted(
            p.relative_to(REPO).as_posix()
            for p in (REPO / "web" / "src").rglob("*.ts*")
            if "NFKD" in p.read_text(encoding="utf-8")
        )
        assert folders == [self.TS_MODULE.relative_to(REPO).as_posix()], (
            "the display-name→handle rule must have ONE home "
            f"(web/src/app/identity.tsx); found {folders}"
        )

    def test_the_frontend_cap_still_equals_this_modules(self):
        src = self.TS_MODULE.read_text(encoding="utf-8")
        match = re.search(r"export const USERNAME_MAX_LEN = (\d+)", src)
        assert match, "web/src/app/identity.tsx must export USERNAME_MAX_LEN"
        assert int(match.group(1)) == USERNAME_MAX_LEN, (
            f"the frontend cap ({match.group(1)}) drifted from identity.USERNAME_MAX_LEN "
            f"({USERNAME_MAX_LEN}) — bump both in the same change, or the suggested handle "
            "is silently trimmed by the server after the user accepts it"
        )
        # …and the suggester READS the constant rather than re-hardcoding the number,
        # which is what makes the assertion above cover it.
        assert ".slice(0, USERNAME_MAX_LEN)" in src, (
            "the TS suggester must slice at USERNAME_MAX_LEN, not a literal — a hardcoded "
            "cap is invisible to the parity check above"
        )

    def test_this_module_exports_no_suggester(self):
        """The deleted function, stated as a property so it cannot quietly return.

        A Python suggester has no caller it could serve: every surface that pre-fills the
        field is a React input, and a server-side suggestion would have to be plumbed
        through an endpoint that does not exist.
        """
        import personalclaw.identity as identity_mod

        assert [n for n in vars(identity_mod) if n.startswith("suggest")] == []


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


class TestOperatorName:
    """The ONE answer to "what is the owner's name" — the name a name-mention rule matches.

    `DashboardState.notify` used to answer it from `agent.bot_name`, so the toggle "Escalate on
    name mention — upgrade when the text mentions you by name" fired on the assistant's name.
    """

    @staticmethod
    def _config(tmp_path, monkeypatch, **dashboard):
        import json

        import personalclaw.config.loader as loader

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        doc = {"dashboard": dashboard, "agent": {"bot_name": "Jarvis"}}
        (tmp_path / "config.json").write_text(json.dumps(doc))

    def test_it_is_the_users_name_never_the_assistants(self, tmp_path, monkeypatch):
        self._config(tmp_path, monkeypatch, user_name="  Ada Lovelace  ")
        assert operator_name() == "Ada Lovelace"

    @pytest.mark.parametrize("stored", ["", "   ", DEFAULT_USER_NAME])
    def test_no_name_given_is_empty(self, tmp_path, monkeypatch, stored):
        """Unset, blank, and the placeholder the UI stores for a name nobody gave — none of them
        is a name to match. The assistant's name is set in every case and must not leak in."""
        self._config(tmp_path, monkeypatch, user_name=stored)
        assert operator_name() == ""

    def test_it_never_raises(self, monkeypatch):
        def _boom(cls):
            raise RuntimeError("config exploded")

        monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(_boom))
        assert operator_name() == ""

    def test_the_placeholder_still_equals_the_frontends(self):
        """The rail for the mirrored constant, the same shape as the cap rail above: if the UI
        starts storing a different word for "no name", this module would match it as a name."""
        src = (REPO / "web" / "src" / "app" / "identity.tsx").read_text(encoding="utf-8")
        match = re.search(r"export const DEFAULT_USER_NAME = '([^']*)'", src)
        assert match, "web/src/app/identity.tsx must export DEFAULT_USER_NAME"
        assert match.group(1) == DEFAULT_USER_NAME, (
            f"the frontend placeholder ({match.group(1)!r}) drifted from "
            f"identity.DEFAULT_USER_NAME ({DEFAULT_USER_NAME!r}) — change both together"
        )


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
    async def test_comment_author_agrees_with_task_author_with_no_handle(
        self, tmp_path, monkeypatch
    ):
        """#2847: with no configured handle a comment stamps the SAME author as its
        task — "" — not the retired "user" placeholder. The placeholder made every task
        and its comments disagree until ``dashboard.username`` was set."""
        import personalclaw.config.loader as loader
        import personalclaw.tasks.native as native

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(native, "config_dir", lambda: tmp_path, raising=False)
        monkeypatch.setattr(native, "_current_username", lambda: "")
        provider = native.NativeTaskProvider()
        task = await provider.create_task(title="x")
        comment = await provider.add_comment(task.id, "a note")
        assert comment is not None
        # The invariant: a task and its comments agree in the default no-handle state.
        assert comment.author == task.author == ""
        # An explicit handle still flows through to the comment path unchanged.
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
