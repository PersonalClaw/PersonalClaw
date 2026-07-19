"""Tests for the lesson store module."""

from pathlib import Path
from unittest.mock import patch

from personalclaw.learn import _DEFAULT_DIR, Lesson, LessonStore


def _make_lesson(rule: str, category: str = "knowledge", negative: str | None = None) -> Lesson:
    return Lesson(ts="2026-01-01T00:00:00Z", rule=rule, category=category, negative=negative)


class TestLessonStore:
    def test_save_and_load(self, tmp_path: Path) -> None:
        store = LessonStore(base_dir=tmp_path)
        store.save(_make_lesson("Use uv instead of pip", "tool", "Never use pip"))

        loaded = store.load_all()
        assert len(loaded) == 1
        assert "uv" in loaded[0].rule
        assert loaded[0].category == "tool"
        assert loaded[0].negative == "Never use pip"

    def test_remove_matching(self, tmp_path: Path) -> None:
        store = LessonStore(base_dir=tmp_path)
        store.save(_make_lesson("Use uv"))
        store.save(_make_lesson("Use make build"))
        assert store.remove("uv")
        assert len(store.load_all()) == 1
        assert "make build" in store.load_all()[0].rule

    def test_remove_no_match(self, tmp_path: Path) -> None:
        store = LessonStore(base_dir=tmp_path)
        store.save(_make_lesson("Use uv"))
        assert not store.remove("nonexistent")
        assert len(store.load_all()) == 1

    def test_get_context_empty(self, tmp_path: Path) -> None:
        store = LessonStore(base_dir=tmp_path)
        assert store.get_context() == ""

    def test_get_context_with_lessons(self, tmp_path: Path) -> None:
        store = LessonStore(base_dir=tmp_path)
        store.save(_make_lesson("Always use uv", negative="Never use pip"))

        ctx = store.get_context()
        assert "uv" in ctx
        assert "pip" in ctx
        assert "Learned corrections" in ctx

    def test_load_corrupted_line(self, tmp_path: Path) -> None:
        path = tmp_path / "lessons.jsonl"
        path.write_text("not json\n")
        store = LessonStore(base_dir=tmp_path)
        assert store.load_all() == []

    def test_multiple_saves(self, tmp_path: Path) -> None:
        store = LessonStore(base_dir=tmp_path)
        store.save(_make_lesson("Rule one"))
        store.save(_make_lesson("Rule two"))
        store.save(_make_lesson("Rule three"))
        assert len(store.load_all()) == 3


class TestLessonStoreSecurity:
    """Tests for sensitive path rejection and SEL audit in LessonStore."""

    def test_sensitive_base_dir_falls_back_to_default(self, tmp_path: Path) -> None:
        sensitive = tmp_path / ".ssh"
        sensitive.mkdir()
        with patch("personalclaw.security.is_sensitive_path", return_value=True):
            store = LessonStore(base_dir=sensitive)
        assert store._dir == _DEFAULT_DIR

    def test_sensitive_base_dir_emits_sel_audit(self, tmp_path: Path) -> None:
        sensitive = tmp_path / ".aws"
        sensitive.mkdir()
        with (
            patch("personalclaw.security.is_sensitive_path", return_value=True),
            patch("personalclaw.sel.SecurityEventLog.log_tool_invocation") as mock_log,
        ):
            LessonStore(base_dir=sensitive)
        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args[1]
        assert call_kwargs["outcome"] == "rejected"
        assert str(sensitive) in call_kwargs["resources"]

    def test_sel_failure_does_not_bypass_fallback(self, tmp_path: Path) -> None:
        sensitive = tmp_path / ".secret"
        sensitive.mkdir()
        with (
            patch("personalclaw.security.is_sensitive_path", return_value=True),
            patch(
                "personalclaw.sel.SecurityEventLog.log_tool_invocation",
                side_effect=RuntimeError("SEL broken"),
            ),
        ):
            store = LessonStore(base_dir=sensitive)
        assert store._dir == _DEFAULT_DIR

    def test_sensitive_config_dir_falls_back_to_default(self, tmp_path: Path) -> None:
        sensitive = tmp_path / ".personalclaw-sensitive"
        sensitive.mkdir()
        with (
            patch("personalclaw.learn._config_dir", return_value=sensitive),
            patch("personalclaw.security.is_sensitive_path", return_value=True),
        ):
            store = LessonStore()
        assert store._dir == _DEFAULT_DIR

    def test_config_dir_exception_falls_back_to_default(self) -> None:
        with patch("personalclaw.learn._config_dir", side_effect=OSError("broken loader")):
            store = LessonStore()
        assert store._dir == _DEFAULT_DIR

    def test_config_dir_none_falls_back_to_default(self) -> None:
        with patch("personalclaw.learn._config_dir", None):
            store = LessonStore()
        assert store._dir == _DEFAULT_DIR

    def test_non_sensitive_base_dir_used_directly(self, tmp_path: Path) -> None:
        with patch("personalclaw.security.is_sensitive_path", return_value=False):
            store = LessonStore(base_dir=tmp_path)
        assert store._dir == tmp_path
