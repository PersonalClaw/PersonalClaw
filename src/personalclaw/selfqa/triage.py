"""Per-commit user-impact triage — step zero of the Self-QA loop.

One question: **could a user notice this commit?** Three answers, and the answer decides
whether the companion spends a scenario on it:

- ``user`` — shipped behaviour changed. Worth one deep as-a-user scenario.
- ``test`` — assertion maintenance only. Ledger-only skip.
- ``none`` — no runtime surface at all (docs, CI config). Ledger-only skip.

The classification is **deterministic over the commit's changed paths**, which is a deliberate
narrowing of the plan's `infer` node. Two reasons. A path classifier costs nothing per commit,
so the watcher can run on a tight interval without a token budget. And it is falsifiable: a
test can commit one file and assert the exact verdict and rationale, where a prompt can only be
asserted as "some string came back". Judgment is still needed to decide *what to do* with an
impactful commit — that stays in the template's `scenario-gen` prompt, which is where the
deep-as-a-user method lives.

The rationale is one line, always populated, and written into the ledger for skips. It is the
answer to "why did nothing run?", so an empty rationale is a defect, not a tidy default.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: A commit ref this module will hand to git. Hex only, so nothing option-shaped gets through.
#:
#: The `commits` list reaches here from the template's `{{inputs.commits}}`, and a run can be
#: started by an agent calling `workflow_start` — so these strings are model-reachable even though
#: the watcher itself only ever supplies git's own `rev-list` output. The argv is fixed and there
#: is no shell, so the exposure is option injection rather than command injection: a `sha` of
#: `--output=<path>` is a real `git show` diff option and would write git's output to a file of
#: the caller's choosing. Validating the shape closes that, and is the same discipline
#: `durability/state_history.py` applies to the one caller-supplied value it lets reach git.
_SHA_RE = re.compile(r"[0-9a-fA-F]{4,64}\Z")

#: Shipped behaviour changed — worth a scenario.
IMPACT_USER = "user"
#: Test/assertion maintenance only — ledger-only skip.
IMPACT_TEST = "test"
#: No runtime surface (docs, CI, metadata) — ledger-only skip.
IMPACT_NONE = "none"

#: The two verdicts that must produce a ledger record and nothing else. A caller that
#: hardcodes `== "test"` misses `none` and files a scenario for a README edit.
SKIPPED_IMPACTS = frozenset({IMPACT_TEST, IMPACT_NONE})

_GIT_TIMEOUT = 30

# Directory prefixes whose contents are tests. Checked as path segments, so `src/tests_fixtures`
# does not match `tests/` and a vendored `node_modules/foo/test/` does.
_TEST_DIRS = ("tests", "test", "__tests__", "e2e")

# Filename shapes that are tests wherever they live.
_TEST_SUFFIXES = (".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.tsx", ".spec.js")
_TEST_NAMES = ("conftest.py",)

# Paths with no runtime surface. `.md` anywhere counts: a docstring-only change lands in a .py
# file and is correctly classified `user`, because a docstring can carry a prompt.
_DOC_SUFFIXES = (".md", ".rst", ".txt")
_DOC_DIRS = ("docs", ".github", "harness")
_DOC_NAMES = ("LICENSE", "NOTICE", "CODEOWNERS", ".gitignore", "CHANGELOG.md")


def _is_test_path(path: str) -> bool:
    """True when `path` is a test file — by directory segment or by filename shape."""
    parts = Path(path).parts
    if any(seg in _TEST_DIRS for seg in parts):
        return True
    name = parts[-1] if parts else path
    if name in _TEST_NAMES:
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return any(name.endswith(sfx) for sfx in _TEST_SUFFIXES)


def _is_doc_path(path: str) -> bool:
    """True when `path` carries no runtime surface — docs, CI config, repo metadata."""
    parts = Path(path).parts
    if parts and parts[0] in _DOC_DIRS:
        return True
    name = parts[-1] if parts else path
    if name in _DOC_NAMES:
        return True
    return any(name.endswith(sfx) for sfx in _DOC_SUFFIXES)


def classify_paths(paths: list[str]) -> tuple[str, str]:
    """Classify a commit's changed paths into ``(impact, one-line rationale)``.

    The order matters and is not arbitrary. A commit touching *any* non-test, non-doc path is
    `user`, because one shipped line is enough for a user to notice. Only when every path is a
    test does it become `test`; only when every path is a doc does it become `none`. A mixed
    test+doc commit is `none`-adjacent but still ships nothing, so it skips as `test` — the
    rationale says which, so the ledger row is never ambiguous about what was seen.

    An empty path list is `none`, not `user`: an empty commit has no surface to check. This is
    the one case where "nothing changed" and "we could not tell" must not be conflated, so the
    rationale says so explicitly.
    """
    if not paths:
        return IMPACT_NONE, "no changed paths — nothing to exercise"

    tests = [p for p in paths if _is_test_path(p)]
    docs = [p for p in paths if not _is_test_path(p) and _is_doc_path(p)]
    shipped = [p for p in paths if not _is_test_path(p) and not _is_doc_path(p)]

    if shipped:
        head = ", ".join(sorted(shipped)[:3])
        more = f" (+{len(shipped) - 3} more)" if len(shipped) > 3 else ""
        return IMPACT_USER, f"shipped code changed: {head}{more}"

    if tests and docs:
        return (
            IMPACT_TEST,
            f"assertion maintenance only — {len(tests)} test file(s) "
            f"and {len(docs)} doc file(s), no shipped code",
        )
    if tests:
        return (
            IMPACT_TEST,
            f"assertion maintenance only — {len(tests)} test file(s), no shipped code",
        )
    return IMPACT_NONE, f"no runtime surface — {len(docs)} doc/CI file(s) only"


@dataclass(frozen=True)
class CommitTriage:
    """One commit's verdict. `rationale` is never empty — it is the ledger's answer to "why?"."""

    sha: str
    impact: str
    rationale: str
    subject: str = ""
    paths: tuple[str, ...] = field(default=())

    @property
    def skipped(self) -> bool:
        """True when this commit gets a ledger record and no scenario."""
        return self.impact in SKIPPED_IMPACTS

    def to_dict(self) -> dict[str, object]:
        return {
            "sha": self.sha,
            "impact": self.impact,
            "rationale": self.rationale,
            "subject": self.subject,
            "paths": list(self.paths),
        }


def _git(repo: Path, *args: str) -> str:
    """Run one read-only git command in `repo`. Returns stdout, or "" on any failure.

    Read-only by construction: every caller passes an inspection subcommand. A failure is
    logged and degrades to an empty result rather than raising, because a commit the watcher
    cannot read must still produce a verdict — `classify_paths([])` gives it one.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, read-only git
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("selfqa triage: git %s failed in %s: %s", args[0] if args else "", repo, exc)
        return ""
    if proc.returncode != 0:
        logger.warning(
            "selfqa triage: git %s exited %d: %s", args, proc.returncode, proc.stderr[:200]
        )
        return ""
    return proc.stdout


def triage_commit(repo: Path | str, sha: str) -> CommitTriage:
    """Triage one commit in `repo` from its changed paths and subject.

    A ref that is not plain hex never reaches git (see :data:`_SHA_RE`). It still gets a verdict —
    `none`, with a rationale naming the refusal — because "one verdict per sha, always" is what
    makes a missing row mean "the companion never ran", and dropping the commit silently here
    would put a hole in exactly that guarantee.
    """
    if not _SHA_RE.match(sha or ""):
        logger.warning("selfqa triage: refusing a non-hex commit ref %r", sha)
        return CommitTriage(
            sha=str(sha),
            impact=IMPACT_NONE,
            rationale="refused: the commit ref is not a hex sha, so it was never resolved",
        )

    root = Path(repo)
    raw_paths = _git(root, "show", "--name-only", "--pretty=format:", sha)
    paths = [line.strip() for line in raw_paths.splitlines() if line.strip()]
    subject = _git(root, "show", "-s", "--pretty=format:%s", sha).strip()
    impact, rationale = classify_paths(paths)
    return CommitTriage(
        sha=sha,
        impact=impact,
        rationale=rationale,
        subject=subject,
        paths=tuple(paths),
    )


def triage_commits(repo: Path | str, shas: list[str]) -> list[CommitTriage]:
    """Triage each commit, in the order given. One verdict per sha, always."""
    return [triage_commit(repo, sha) for sha in shas]
