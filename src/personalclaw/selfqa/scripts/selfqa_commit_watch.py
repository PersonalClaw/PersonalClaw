"""Self-QA commit watcher — a zero-token cron script (SELF-VERIFICATION §3.1).

Installed to ``~/.personalclaw/crons/selfqa_commit_watch.py`` and run by an interval trigger
through the ``run-script`` action. Each tick it reads the watched repo's HEAD, compares it
against the last SHA it recorded, and either raises ``Skip`` (nothing new — silent, zero cost) or
starts the `self-qa` run itself via ``ctx.call_tool("workflow_start", …)`` and raises ``Report``
naming what it started.

**The script starts the run; the trigger's action does not.** The plan sketched a `run-workflow`
action consuming the watcher's report, which would need the reported SHA list to flow from a
script's `Report` into a second action's inputs — a hand-off that does not exist. `call_tool`
does: it routes through the Tool entity, so one `run-script` job is the whole trigger and the
`Report` is a record of what happened rather than an instruction to something downstream. See
:func:`check`.

**This is an interim seam, stated honestly.** There is no `vcs` trigger kind today; the approved
one (AUTOMATION-SUBSTRATE AUTO-R12: a `file` trigger watching `.git/refs/heads/*` with
content-hash dedup) is a later wave. When it lands, this script retires and the same template
binds to the real trigger. The template is the durable half; the trigger is a swap.

Two constraints shape the imports below, and both are load-bearing:

- **Only ``personalclaw.schedule_script`` may be imported.** The in-sandbox launcher installs a
  *stub* ``personalclaw`` module into ``sys.modules`` when the real package is not on the child's
  path. A ``from personalclaw.config.loader import config_dir`` would therefore fail inside the
  sandbox even though it works in a test. The state path is derived from ``__file__`` instead —
  which is the crons dir by definition, since that is the only directory a script job may load
  from.
- **Everything else is stdlib.** The child environment is allowlisted and there is no guarantee
  of anything else being importable.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from personalclaw.schedule_script import Report, Skip

#: Where the last-seen SHA lives — beside this script, in the crons dir.
STATE_FILE = Path(__file__).with_name("selfqa_commit_watch.state.json")

#: The watched repo, written here by `selfqa.install.reconcile()` from `agent.self_qa.watched_repo`.
#:
#: 🪤 THIS FILE IS THE ONLY WORKING CHANNEL, and the two obvious alternatives both fail silently.
#: An env var is dropped: `sandbox.build_child_env` builds the child environment from an allowlist
#: rather than from a copy of the parent's, so a bespoke `PERSONALCLAW_SELFQA_*` name never arrives
#: unless the operator adds it to `sandbox.env_passthrough`. The job message is empty: a trigger
#: fire constructs `ActionContext(event=…, context="", …)`, so `run-script` hands the launcher
#: `job_message=""` and `ctx.message` is blank however the job was created. Measured against
#: `gateway.py`'s fire path, not assumed.
#:
#: A file beside the script is also the only source the sandbox can read at all: the launcher
#: installs a STUB `personalclaw` package into `sys.modules` when the real one is not on the
#: child's path, so `from personalclaw.config.loader import config_dir` would fail here. The path
#: is derived from `__file__`, which is the crons dir by definition.
CONFIG_FILE = Path(__file__).with_name("selfqa_commit_watch.config.json")

#: A developer running the script by hand can override the file. Not the production path — see
#: above.
REPO_ENV = "PERSONALCLAW_SELFQA_WATCHED_REPO"

#: The bundled template this watcher fires.
TEMPLATE = "self-qa"

#: Most SHAs to hand one run. A watcher that was off for a week should not open a hundred-commit
#: run; the newest ones are the ones worth checking, and the state file still advances past all
#: of them so the backlog does not re-fire next tick.
MAX_COMMITS_PER_FIRE = 20

_GIT_TIMEOUT = 30


def _git(repo: Path, *args: str) -> str:
    """Run one read-only git command. Returns stdout stripped, or "" on failure."""
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


def read_repo() -> str:
    """The configured repo path: the config file, else the env override, else "".

    An unreadable or absent file is "" rather than an error — the watcher then skips silently,
    which is the correct behaviour for a companion that is enabled but not yet pointed anywhere.
    """
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            configured = str(data.get("repo", "") or "").strip()
            if configured:
                return configured
    except (OSError, ValueError):
        pass
    return os.environ.get(REPO_ENV, "").strip()


def read_state() -> dict:
    """The recorded state, or an empty dict when there is none or it is unreadable.

    A corrupt state file degrades to "no state", which makes the next tick record HEAD and skip
    rather than replaying the entire history as new commits.
    """
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(repo: str, head: str) -> None:
    """Record `head` as seen. Written through a temp file so a killed tick cannot truncate it."""
    payload = {"repo": repo, "last_sha": head}
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def new_commits(repo: Path, last_sha: str, head: str) -> list[str]:
    """The SHAs between `last_sha` and `head`, oldest first, capped.

    When `last_sha` is unknown to the repo — a rebase, a force-push, a fresh clone — the range
    query fails and this returns just `head`. That is the honest answer: the watcher knows one
    commit is new and cannot prove anything about the ones before it, and reporting the whole
    history instead would bury the real change.
    """
    if not last_sha:
        return [head]
    out = _git(repo, "rev-list", "--reverse", f"{last_sha}..{head}")
    if not out:
        return [head]
    shas = [line.strip() for line in out.splitlines() if line.strip()]
    if not shas:
        return [head]
    return shas[-MAX_COMMITS_PER_FIRE:]


def check(ctx) -> None:
    """The cron entry point (`selfqa_commit_watch.py:check`).

    Starts the `self-qa` run through ``ctx.call_tool`` and raises ``Report`` naming what it
    started, or ``Skip`` when there is nothing new. Starting the run from here rather than
    delivering the SHA list and hoping something picks it up is what makes this one job the whole
    trigger: `call_tool` routes through the Tool entity (`POST /api/tools/invoke`), so the script
    reaches `workflow_start` with the same authority the agent has and nothing else needs wiring.

    The state file advances *before* the run starts, so a run that fails downstream does not
    re-fire the same commits on the next tick — a watcher that retries a failing run every
    interval is how a token budget disappears overnight.
    """
    repo_raw = read_repo() or (getattr(ctx, "message", "") or "").strip()
    if not repo_raw:
        raise Skip()

    repo = Path(repo_raw).expanduser()
    head = _git(repo, "rev-parse", "HEAD")
    if not head:
        # Not a readable repo. Silent rather than a nag: a watcher pointed at a path the user
        # moved should not deliver a message every interval forever.
        raise Skip()

    state = read_state()
    last_sha = str(state.get("last_sha", "") or "")
    same_repo = str(state.get("repo", "") or "") == str(repo)

    if same_repo and last_sha == head:
        raise Skip()

    shas = new_commits(repo, last_sha if same_repo else "", head)
    write_state(str(repo), head)

    if not same_repo and not last_sha:
        # First sight of this repo: record HEAD, do not report. Otherwise enabling the companion
        # fires a run against whatever commit happened to be checked out, which is noise.
        raise Skip()

    payload = {"repo": str(repo), "commits": shas}

    # `idempotency_key` on the head SHA, so two ticks that somehow see the same frontier — a slow
    # run overlapping the next interval, a retried tick — produce ONE run rather than two racing
    # browser sessions against the same commit.
    ctx.call_tool(
        "workflow_start",
        {
            "name": TEMPLATE,
            "inputs": payload,
            "mode": "background",
            "idempotency_key": f"selfqa-{head}",
        },
    )

    # The tool's return value is deliberately NOT embedded here. It is an opaque provider-shaped
    # dict, and `json.dumps` on something unserializable inside it would turn a successful fire
    # into a script error — the run would already have started, and the watcher would report a
    # failure for it.
    raise Report(json.dumps(payload))
