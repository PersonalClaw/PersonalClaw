"""SDK: running git from an app's provider, the way core runs its own.

An app's provider runs inside the gateway, so a git it starts runs as the gateway: often in a
repository an agent's shell can write (the owner's clone, a notebook, a sync clone), and with the
gateway's environment, which holds every secret saved in PersonalClaw, unless it is given another.
An agent can write a repository's ``.git`` as easily as its files, so a hook, a file-system monitor
or an ssh command set there would run the next time the app asked git anything.

- ``git_argv(args)`` is the argv for ``git <args>`` with the settings that stop the repository's
  own configuration from running a program, on git's command line and after the app's own leading
  options. A command that talks to a remote signs in with the owner's own ssh command and
  credential helpers; a remote at a local path is refused.
- ``git_env(remote=False)`` is its environment: the child allowlist, never the gateway's own, and
  git never waits on a password prompt. ``remote=True`` (for a command ``talks_to_remote``) adds
  the owner's SSH agent, which git over ssh signs in through.
- ``remote_refusal(url)`` says why a remote URL is refused (a local path, ``ext::``, ``git://``)
  and what to use instead, ``""`` when git may reach it: for a URL the owner typed, checked before
  git runs. ``transport_refusal(stderr)`` says the same for a git that was refused anyway.
- ``git_argv`` refuses a git older than 2.12, which ignores some of those settings: it raises
  ``GitTooOld``, an ``OSError`` (as a git that is not installed is) whose message names the
  version needed, the one found and what to do. Catch it where a missing git is caught, and say
  its message. ``git_problem()`` is that message before anything runs (or that there is no git
  on ``PATH``), ``""`` when git can run: for an app's doctor or setup step.

    from personalclaw.sdk.git import git_argv, git_env, talks_to_remote

    args = ["-C", repo, "pull", "--ff-only", "origin", "main"]
    subprocess.run(git_argv(args), env=git_env(remote=talks_to_remote(args)), ...)
"""

from __future__ import annotations

from personalclaw.net.git import (  # noqa: F401
    GitTooOld,
    git_argv,
    git_problem,
    remote_refusal,
    talks_to_remote,
    transport_refusal,
)


def git_env(*, remote: bool = False) -> dict[str, str]:
    """The environment for a git an app's provider starts, as ``env=`` to its spawn.

    The child allowlist (``PATH``, the home, locale, proxy and certificate settings, git's own
    ``GIT_SSL_CAINFO``/``GIT_SSL_CAPATH``, and what the owner passed through by name), never a copy
    of the gateway's environment. *remote* adds the owner's SSH agent (``SSH_AUTH_SOCK``), for a
    command that talks to a remote and only for one."""
    from personalclaw.net import git as core_git

    return core_git.git_env(site="app-git", remote=remote)


__all__ = [
    "GitTooOld",
    "git_argv",
    "git_env",
    "git_problem",
    "remote_refusal",
    "talks_to_remote",
    "transport_refusal",
]
