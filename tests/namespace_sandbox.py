"""The Linux namespace sandbox, for the tests that need it to run (#3918).

GitHub's Ubuntu 24.04 runners, like any Ubuntu 24.04 machine, set
``kernel.apparmor_restrict_unprivileged_userns=1``, which withholds the user and mount namespaces
the sandbox's launcher needs. A test that needs them skipped there, on every suite shard, so the
sandbox's own fence tests ran on no CI job at all. The ``namespace-sandbox`` job lifts the
restriction on its throwaway runner (``tools/ci/allow_userns.sh``) and runs them with
:data:`REQUIRE` set: on Linux, a withheld sandbox then fails each of them, never a skip."""

from __future__ import annotations

import functools
import inspect
import os
import sys
from collections.abc import Callable
from typing import Any

import pytest

#: Set where a run has made the namespaces available (the ``namespace-sandbox`` job): on Linux a
#: test that needs them then errors when they are withheld, instead of skipping.
REQUIRE = "PERSONALCLAW_REQUIRE_NAMESPACE_SANDBOX"
REASON = "needs the Linux namespace sandbox (user + mount namespaces)"


def works() -> bool:
    """Whether this host gives the sandbox its user and mount namespaces (Linux only)."""
    if sys.platform != "linux":
        return False
    from personalclaw.sandbox import _probe_unshare

    answer = _probe_unshare()
    if answer is None:  # never a skip: an unanswered check would hide the test
        raise RuntimeError("the sandbox's namespace check did not answer")
    return answer


def needs_it() -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """The decorator for a test that needs the namespace sandbox: the test as it is where the host
    gives the namespaces; a skip where it withholds them; and, where :data:`REQUIRE` is set on
    Linux, a failure of that test, by name, naming the cause, never a skip."""
    if works():
        return lambda test: test
    if sys.platform == "linux" and os.environ.get(REQUIRE) == "1":
        return _fails_instead
    return pytest.mark.skipif(True, reason=REASON)


WITHHELD = (
    f"{REQUIRE}=1, and this host withholds the namespace sandbox: lift "
    "kernel.apparmor_restrict_unprivileged_userns (tools/ci/allow_userns.sh) or give the sandbox "
    "an AppArmor profile that allows user namespaces"
)


def _fails_instead(test: Callable[..., Any]) -> Callable[..., Any]:
    """*test*, failing with :data:`WITHHELD` before its body runs, with its signature, marks and
    parametrization kept (``functools.wraps``)."""
    if inspect.iscoroutinefunction(test):

        @functools.wraps(test)
        async def failing_coroutine(*args: Any, **kwargs: Any) -> None:
            pytest.fail(WITHHELD, pytrace=False)

        return failing_coroutine

    @functools.wraps(test)
    def failing(*args: Any, **kwargs: Any) -> None:
        pytest.fail(WITHHELD, pytrace=False)

    return failing
