"""Commit-delta state for the Self-QA watcher (SELF-VERIFICATION §3.1, SV-11).

The Wave-2 companion shipped this logic inside a sandboxed cron script
(``crons/selfqa_commit_watch.py``) because no vcs trigger existed yet. AUTOMATION-SUBSTRATE's
``vcs`` preset now does (:func:`personalclaw.triggers.file_watch.vcs_patterns` watches
``.git/refs/heads/*`` + ``.git/HEAD``), so the interim script retires and the delta logic
lives HERE — in-process, with real package access, freed of the sandbox constraints the
script's own docstring catalogued (stub ``personalclaw`` module, allowlisted env, config
smuggled through a file beside the script).

What a fire means is unchanged from the script, clause for clause:

- **First sight of a repo records HEAD and stays quiet** — enabling the companion must not
  fire a run against whatever commit happened to be checked out.
- **State advances BEFORE the run starts** — a run that fails downstream must not re-fire
  the same commits on the next change; a watcher that retries a failing run on every fire
  is how a token budget disappears overnight.
- **An unknown ``last_sha`` (rebase, force-push, fresh clone) reports just HEAD** — the
  honest answer: one commit is provably new and nothing before it is provable.
- **At most :data:`MAX_COMMITS_PER_FIRE` SHAs per fire, newest kept** — a watcher that was
  off for a week should not open a hundred-commit run; the state still advances past the
  backlog so it cannot re-fire.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The bundled template a fire starts.
TEMPLATE = "self-qa"

#: Most SHAs to hand one run (the script's cap, unchanged).
MAX_COMMITS_PER_FIRE = 20

_GIT_TIMEOUT = 30


def state_path() -> Path:
    """Where the last-seen SHA lives — in the selfqa data dir, no longer beside a script.

    The crons-dir placement existed only because the sandboxed script could read nothing
    else; an in-process module keeps companion state with the companion.
    """
    from personalclaw.config.loader import config_dir

    return config_dir() / "selfqa" / "commit_watch.state.json"


def _git(repo: Path, *args: str) -> str:
    """One read-only git command. Returns stdout stripped, or "" on any failure."""
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, read-only git
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def read_state() -> dict:
    """The recorded state, or {} when absent or unreadable.

    A corrupt state file degrades to "no state": the next fire records HEAD and stays
    quiet rather than replaying the entire history as new commits.
    """
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(repo: str, head: str) -> None:
    """Record `head` as seen, through a temp file so a killed process cannot truncate it."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"repo": repo, "last_sha": head}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def new_commits(repo: Path, last_sha: str, head: str) -> list[str]:
    """The SHAs between `last_sha` and `head`, oldest first, capped at the newest 20."""
    if not last_sha:
        return [head]
    out = _git(repo, "rev-list", "--reverse", f"{last_sha}..{head}")
    if not out:
        return [head]
    shas = [line.strip() for line in out.splitlines() if line.strip()]
    if not shas:
        return [head]
    return shas[-MAX_COMMITS_PER_FIRE:]


@dataclass(frozen=True)
class WatchFire:
    """What one vcs-trigger fire resolved to."""

    #: Inputs for the `self-qa` template ({"repo", "commits"}), or None when quiet.
    inputs: dict | None
    #: Dedupe key on the head SHA — two fires seeing the same frontier produce ONE run.
    idempotency_key: str
    #: Why nothing fired, when `inputs` is None. Never empty in that case: a silent
    #: skip and a broken watcher must stay distinguishable (the two-weight discipline).
    quiet_reason: str = ""


def check(repo_raw: str) -> WatchFire:
    """Resolve one fire against the watched repo. Pure state-and-git; starts nothing.

    The provider that calls this owns starting the run; splitting it this way keeps the
    delta logic testable without an engine and mirrors the retired script's `check()`.
    """
    repo_raw = (repo_raw or "").strip()
    if not repo_raw:
        return WatchFire(None, "", quiet_reason="no watched repo configured")

    repo = Path(repo_raw).expanduser()
    head = _git(repo, "rev-parse", "HEAD")
    if not head:
        # Not a readable repo. Quiet rather than a nag: a watcher pointed at a moved path
        # must not deliver an error on every branch-ref change forever.
        return WatchFire(None, "", quiet_reason=f"not a readable git repo: {repo}")

    state = read_state()
    last_sha = str(state.get("last_sha", "") or "")
    same_repo = str(state.get("repo", "") or "") == str(repo)

    if same_repo and last_sha == head:
        return WatchFire(None, "", quiet_reason="no new commits")

    shas = new_commits(repo, last_sha if same_repo else "", head)
    write_state(str(repo), head)

    if not same_repo and not last_sha:
        return WatchFire(None, "", quiet_reason="first sight — recorded HEAD, not reporting")

    return WatchFire(
        inputs={"repo": str(repo), "commits": shas},
        idempotency_key=f"selfqa-{head}",
    )
