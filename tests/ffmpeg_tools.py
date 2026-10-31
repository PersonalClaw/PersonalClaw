"""ffmpeg and ffprobe, for the tests that build and read a video (#3918).

No runner image has them, so these tests skipped on every CI job and checked nothing there. The
merge tier's Linux suite now installs them (the ``ffmpeg`` toolchain word), and run-job gives a
job that names the word :data:`REQUIRE` (``tools/ci/jobs.py`` ``REQUIRES``): there a missing
program fails each test that needs it, by name, never a skip.
Anywhere else, a machine without them skips those tests, as before."""

from __future__ import annotations

import functools
import inspect
import os
from collections.abc import Callable
from typing import Any

import pytest

#: Set by run-job for a job whose toolchain names ffmpeg (the ``python-suite`` job): a test that
#: needs it then fails when it is missing, instead of skipping.
REQUIRE = "PERSONALCLAW_REQUIRE_FFMPEG"


def skip_unless_required(why: str) -> None:
    """Skip the running test with *why*, or fail it with *why* where :data:`REQUIRE` is set."""
    if os.environ.get(REQUIRE) == "1":
        pytest.fail(f"{why}, and {REQUIRE}=1 requires it", pytrace=False)
    pytest.skip(why)


def needs_it(present: bool, why: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """The decorator for a test that needs ffmpeg: the test as it is where *present*; a skip with
    *why* where it is missing; and where :data:`REQUIRE` is set, a failure of that test instead,
    with its signature, marks and parametrization kept (``functools.wraps``)."""
    if present:
        return lambda test: test
    if os.environ.get(REQUIRE) != "1":
        return pytest.mark.skipif(True, reason=why)

    def fails_instead(test: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(test):

            @functools.wraps(test)
            async def failing_coroutine(*args: Any, **kwargs: Any) -> None:
                skip_unless_required(why)

            return failing_coroutine

        @functools.wraps(test)
        def failing(*args: Any, **kwargs: Any) -> None:
            skip_unless_required(why)

        return failing

    return fails_instead
