"""``ffmpeg_tools``: a test that needs ffmpeg skips without it, except where the run requires it,
where it fails, by name, never a skip (#3918)."""

from __future__ import annotations

import inspect

import ffmpeg_tools
import pytest


def a_test(tmp_path):
    return "ran"


def test_present_the_test_is_left_as_it_is(monkeypatch):
    monkeypatch.setenv(ffmpeg_tools.REQUIRE, "1")
    assert ffmpeg_tools.needs_it(True, "why")(a_test) is a_test


def test_missing_and_not_required_it_is_a_skip(monkeypatch):
    monkeypatch.delenv(ffmpeg_tools.REQUIRE, raising=False)
    assert ffmpeg_tools.needs_it(False, "why").args == (True,)
    with pytest.raises(pytest.skip.Exception, match="why"):
        ffmpeg_tools.skip_unless_required("why")


def test_missing_and_required_each_test_fails_with_the_cause(monkeypatch):
    monkeypatch.setenv(ffmpeg_tools.REQUIRE, "1")
    required = ffmpeg_tools.needs_it(False, "no ffmpeg here")(a_test)
    assert required.__name__ == "a_test"
    assert list(inspect.signature(required).parameters) == ["tmp_path"]
    with pytest.raises(
        pytest.fail.Exception, match=f"no ffmpeg here, and {ffmpeg_tools.REQUIRE}=1"
    ):
        required(None)
    with pytest.raises(pytest.fail.Exception, match="why"):
        ffmpeg_tools.skip_unless_required("why")
