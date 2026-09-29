"""No test's git signs in with a credential helper of the machine's own.

A test's git reads the machine's git configuration, and on a Mac the file Apple's git bundles names
``osxkeychain``: git hands every credential it signs in with to the owner's real keychain, and asks
the keychain for the ones it needs. In the apps suite a test's own ``git clone`` of a URL with a
token in it told that helper to keep the token, and it waited on the owner's keychain for ten
minutes. This suite runs on developers' machines all day, and nothing here kept a test's git, or
core's git under a test, off that configuration either: core's git signs in as the owner would, with
the helpers the owner's configuration names.

So, from before anything is collected, the suite's git runs with no system configuration, a global
file of each test's own in place of ``~/.gitconfig`` (``GIT_CONFIG_GLOBAL``: a fixture that needs a
global setting, an ssh stand-in, writes it there), and an empty ``credential.helper`` on its command
line (``personalclaw.sdk.testing.neutral_git_env``). Every git a test starts is checked before it
starts, and one that could still sign in with a helper of the machine's own is refused and charged
(``personalclaw.sdk.testing.GitGuard``, which the apps suite installs too); ``conftest`` fails the
test that started it, by name. And core's own git is asked, here, which helpers it would sign a test
in with: a core that would still name one of the machine's stops the run before a test can.

What it cannot see: a git a shell starts from a command line, and a git another program starts.
Both inherit the environment above unless whatever starts them hands them one of their own.
"""

from __future__ import annotations

import itertools
import os
import shutil
import tempfile
from pathlib import Path

from personalclaw.sdk.testing import neutral_git_env, refuse_git_helpers

#: The home of whoever runs the tests, read before any test can point ``HOME`` elsewhere: its
#: ``~/.gitconfig`` is theirs.
REAL_HOME = os.path.realpath(os.path.expanduser("~"))

#: Where each test's own global git file is, one per test, made by git on the first write.
BASE = Path(tempfile.mkdtemp(prefix="pclaw-tests-git-"))

_serial = itertools.count()

os.environ.update(neutral_git_env(BASE / "collect.gitconfig"))

#: The suite's guard, installed as this module is imported, which ``conftest`` does before
#: anything is collected: through ``refuse_git_helpers``, the door the apps suite uses too.
GUARD = refuse_git_helpers(
    real_home=REAL_HOME, own=[str(BASE), str(Path(__file__).resolve().parents[1])]
)

#: A remote nothing reaches: no git in :func:`cores_git_refusal` starts for it.
_NOWHERE = "https://example.invalid/state.git"


def own_global_config() -> str:
    """A global git file for the next test, not made until git writes it."""
    return str(BASE / f"t{next(_serial)}.gitconfig")


def cores_git_refusal() -> str:
    """Why core's git would sign a test in with a helper of the machine's own; ``""`` when it
    wouldn't. Asked with a home of its own: it runs before any test, where nothing else keeps
    core off the developer's real home (``git_env`` reads the passthrough settings there)."""
    from personalclaw.net.git import git_argv, git_env

    chosen = os.environ.get("PERSONALCLAW_HOME")
    os.environ["PERSONALCLAW_HOME"] = str(BASE / "home")
    try:
        return GUARD.refusal(
            git_argv(["ls-remote", _NOWHERE]), git_env(site="git-helper-guard", remote=True)
        )
    finally:
        if chosen is None:
            os.environ.pop("PERSONALCLAW_HOME", None)
        else:
            os.environ["PERSONALCLAW_HOME"] = chosen


def failure(refused: list[str]) -> str:
    """The failure a test that started a git that could reach the machine's helper is given."""
    return (
        "this test started a git that could sign in with the machine's own credential helper, and "
        "it was refused: " + "; ".join(refused) + ". Run it with the environment the tests "
        "inherit, and put a setting of the test's own in the file GIT_CONFIG_GLOBAL names "
        "(tests/git_helper_guard.py)."
    )


def close() -> None:
    """Take the guard out, and the tests' git files with it."""
    GUARD.undo()
    shutil.rmtree(BASE, ignore_errors=True)


_WHY = cores_git_refusal()
if _WHY:
    close()  # the run stops here, before pytest_unconfigure could take the guard out
    raise RuntimeError(
        f"core's git would sign a test in with the machine's own credential helper: it {_WHY}. "
        "personalclaw.net.git.git_env must keep GIT_CONFIG_NOSYSTEM and GIT_CONFIG_GLOBAL "
        "(tests/git_helper_guard.py)."
    )
