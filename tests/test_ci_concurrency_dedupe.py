"""``ci.yml``'s concurrency group must collapse its two triggers onto one key.

Measured 2026-09-07 (issue #2595), from the Actions API rather than the checks tab: every
non-fork PR ran the **entire CI matrix twice, concurrently, on the identical head SHA**.
8 live branches → 16 CI runs → 9 jobs each: ~72 jobs in flight to answer 8 PRs' worth of
questions, against account runner concurrency of 4–6 with ``test`` at ~45 minutes. Half of
all runner capacity was recomputing an answer already in flight, which made this the binding
constraint on merge rate.

The cause was that the group keyed on ``github.ref``, a value the two triggers **disagree
about**: ``refs/pull/<n>/merge`` under ``pull_request`` and ``refs/heads/<branch>`` under
``push``. Different strings → different groups → no dedupe. The guardrail worked *within* a
trigger and could not see *across* the two triggers the same file enables, while its comment
claimed it was preventing wasted runner time.

**Why this asserts a PROPERTY and not a string.** The obvious fix — keying on the head SHA —
also makes the two keys agree, and is wrong: it puts every commit in its own group, so three
pushes leave three live runs instead of cancelling the two superseded ones. That trades the
duplicate-run bug for the very waste the guardrail exists to prevent, and a test that merely
pinned the new expression as text would have happily accepted it. So this file evaluates the
group expression against synthetic event contexts and asserts the four relations that actually
matter:

1. same-repo ``pull_request`` and ``push`` on one branch → the **same** key (the dedupe);
2. two commits on one branch → the **same** key (superseded runs still cancel — this is the
   relation the SHA-keyed "fix" breaks);
3. two different branches → **different** keys (a branch must not cancel its neighbours);
4. two different forks pushing a branch of the same name → **different** keys (no
   cross-contributor cancellation).

Parsed from source with a line scan rather than PyYAML, matching the convention its sibling
``tests/test_ci_tier_enforcement.py`` states: PyYAML is not a declared test dependency.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# A deliberately tiny evaluator for the GitHub expression shapes this key uses.
# ---------------------------------------------------------------------------

_EXPR = re.compile(r"\$\{\{(.+?)\}\}")


def _lookup(context: dict[str, Any], dotted: str) -> Any:
    """Resolve ``a.b.c`` against nested dicts, returning ``''`` for any missing link.

    Empty-for-missing is what GitHub itself does, and it is the behaviour the ``||``
    fallbacks in the key depend on: under ``push`` there is no ``event.pull_request`` at
    all, so ``github.event.pull_request.head.repo.full_name`` must come out falsy rather
    than raising.
    """
    cur: Any = context
    for part in dotted.strip().split("."):
        if not isinstance(cur, dict) or part not in cur:
            return ""
        cur = cur[part]
    return cur


def _evaluate(template: str, context: dict[str, Any]) -> str:
    """Substitute every ``${{ … }}`` in *template*, supporting ``a || b`` chains."""

    def one(match: re.Match[str]) -> str:
        for operand in match.group(1).split("||"):
            value = _lookup(context, operand)
            if value:
                return str(value)
        return ""

    return _EXPR.sub(one, template)


def _concurrency_group() -> str:
    """The raw ``group:`` value from the workflow-level ``concurrency:`` block.

    Anchored on the top-level (two-space-indented) block so a future job-level
    ``concurrency:`` cannot be picked up by mistake.
    """
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() != "concurrency:":
            continue
        for follower in lines[i + 1 : i + 6]:
            m = re.match(r"\s+group:\s*(\S.*?)\s*$", follower)
            if m:
                return m.group(1)
    raise AssertionError("no workflow-level `concurrency:` / `group:` found in ci.yml")


# ---------------------------------------------------------------------------
# Synthetic contexts: the same branch, seen through each trigger.
# ---------------------------------------------------------------------------

_REPO = "PersonalClaw/PersonalClaw"
_BRANCH = "bugfix-some-branch"


def _pull_request_ctx(
    *, branch: str = _BRANCH, head_repo: str = _REPO, sha: str = "aaaa111"
) -> dict[str, Any]:
    return {
        "github": {
            "repository": _REPO,
            "ref": "refs/pull/42/merge",
            "ref_name": "42/merge",
            "head_ref": branch,
            "sha": "merge999",  # the MERGE commit — deliberately not the head sha
            "event_name": "pull_request",
            "event": {
                "pull_request": {
                    "number": 42,
                    "head": {"sha": sha, "repo": {"full_name": head_repo}},
                }
            },
        }
    }


def _push_ctx(*, branch: str = _BRANCH, sha: str = "aaaa111") -> dict[str, Any]:
    return {
        "github": {
            "repository": _REPO,
            "ref": f"refs/heads/{branch}",
            "ref_name": branch,
            "head_ref": "",  # empty on push — this is why `||` is needed
            "sha": sha,
            "event_name": "push",
            "event": {},
        }
    }


# ---------------------------------------------------------------------------
# Vacuity floors, first: every relation below is trivially true on an empty key.
# ---------------------------------------------------------------------------


def test_the_group_expression_was_actually_found() -> None:
    group = _concurrency_group()
    assert group, "the concurrency group parsed as empty"
    assert "${{" in group, (
        "the concurrency group contains no expression at all, so every relation asserted "
        f"below would compare two identical constants and pass vacuously: {group!r}"
    )


def test_the_evaluator_resolves_the_real_key_to_something() -> None:
    """If evaluation silently produced ``''`` for both triggers, relation 1 would 'pass'."""
    group = _concurrency_group()
    for label, ctx in (("pull_request", _pull_request_ctx()), ("push", _push_ctx())):
        resolved = _evaluate(group, ctx)
        assert "${{" not in resolved, f"{label}: unresolved expression left in {resolved!r}"
        assert resolved.strip("-"), f"{label}: the key resolved to nothing: {resolved!r}"
        assert _BRANCH in resolved, (
            f"{label}: the resolved key {resolved!r} does not mention the branch, so it "
            "cannot be distinguishing branches from one another"
        )


def test_the_evaluator_itself_distinguishes_the_two_contexts() -> None:
    """Floor on the harness, not the workflow: `github.ref` MUST differ across the contexts.

    If the two synthetic contexts were accidentally identical, relation 1 would pass no
    matter what the workflow said. This pins the exact field the original defect keyed on.
    """
    assert _evaluate("${{ github.ref }}", _pull_request_ctx()) == "refs/pull/42/merge"
    assert _evaluate("${{ github.ref }}", _push_ctx()) == f"refs/heads/{_BRANCH}"


# ---------------------------------------------------------------------------
# The four relations.
# ---------------------------------------------------------------------------


def test_both_triggers_produce_one_key_for_the_same_branch() -> None:
    """Relation 1 — the dedupe. This is the defect in #2595, stated directly."""
    group = _concurrency_group()
    pr_key = _evaluate(group, _pull_request_ctx())
    push_key = _evaluate(group, _push_ctx())
    assert pr_key == push_key, (
        "ci.yml fires on both `pull_request` and `push`, so a single push to a branch with an "
        "open PR starts TWO runs. They dedupe only if both resolve the same concurrency key, "
        f"and these do not:\n  pull_request → {pr_key!r}\n  push          → {push_key!r}\n"
        "Keying on a value the two events disagree about (`github.ref` is "
        "`refs/pull/<n>/merge` vs `refs/heads/<branch>`) runs the whole matrix twice on one SHA."
    )


def test_two_commits_on_one_branch_still_share_a_key() -> None:
    """Relation 2 — superseded runs must still cancel.

    This is the relation a head-SHA-keyed group breaks, which is why it is asserted
    separately from relation 1: a SHA key satisfies the dedupe and silently gives up
    cancelling the run for a commit nobody is waiting on any more.
    """
    group = _concurrency_group()
    first = _evaluate(group, _push_ctx(sha="aaaa111"))
    second = _evaluate(group, _push_ctx(sha="bbbb222"))
    assert first == second, (
        "two commits pushed to the SAME branch resolved to DIFFERENT concurrency keys "
        f"({first!r} vs {second!r}), so `cancel-in-progress` can no longer cancel the "
        "superseded run and every push leaves its predecessor burning runners. The group "
        "key must not contain a per-commit value such as `github.sha` or "
        "`github.event.pull_request.head.sha`."
    )


def test_two_branches_do_not_share_a_key() -> None:
    """Relation 3 — a branch must not cancel its neighbours."""
    group = _concurrency_group()
    mine = _evaluate(group, _push_ctx(branch="bugfix-mine"))
    yours = _evaluate(group, _push_ctx(branch="bugfix-yours"))
    assert mine != yours, (
        f"two different branches resolved to the same concurrency key ({mine!r}), so pushing "
        "one would cancel CI for the other."
    )


def test_two_forks_with_the_same_branch_name_do_not_share_a_key() -> None:
    """Relation 4 — no cross-contributor cancellation.

    ``patch-1`` is the name GitHub's own web editor picks, so two unrelated fork PRs
    carrying it is ordinary rather than hypothetical. Keying on the branch name alone would
    make one contributor's push cancel another's CI.
    """
    group = _concurrency_group()
    a = _evaluate(group, _pull_request_ctx(branch="patch-1", head_repo="alice/PersonalClaw"))
    b = _evaluate(group, _pull_request_ctx(branch="patch-1", head_repo="bob/PersonalClaw"))
    assert a != b, (
        f"two different forks pushing a branch named `patch-1` resolved to the same key ({a!r}), "
        "so one stranger's push cancels another stranger's CI run."
    )


# ---------------------------------------------------------------------------
# The regression the dedupe would otherwise cause.
# ---------------------------------------------------------------------------


def _feedback_step_conditions() -> list[tuple[str, str]]:
    """``(step name, its `if:` expression)`` for the steps that feed pr-feedback.yml."""
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    wanted = ("Hand the violations to the feedback workflow", "Upload lint feedback")
    found: list[tuple[str, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"\s*-\s*name:\s*(\S.*?)\s*$", line)
        if not m or m.group(1) not in wanted:
            continue
        for follower in lines[i + 1 : i + 4]:
            cond = re.match(r"\s+if:\s*(\S.*?)\s*$", follower)
            if cond:
                found.append((m.group(1), cond.group(1)))
                break
    return found


def test_the_feedback_step_scan_is_not_vacuous() -> None:
    """Floor: both steps must be found, or the assertion below proves nothing."""
    found = _feedback_step_conditions()
    assert len(found) == 2, (
        "expected to find both feedback steps with an `if:`, found "
        f"{[name for name, _ in found]!r}. pr-feedback.yml depends on the artifact these two "
        "steps produce; if they were renamed, this rail stopped watching them."
    )


def test_the_lint_remediation_does_not_depend_on_which_trigger_survived() -> None:
    """The dedupe is only safe because these steps no longer key on the event name.

    With one shared concurrency group, one of the two runs cancels the other and **which one
    survives is not contractual**. While these steps were gated on
    ``github.event_name == 'pull_request'``, a push-survivor produced a red ``lint`` with the
    remediation posted nowhere at all: no artifact is uploaded, and pr-feedback.yml resolves
    its target from that artifact's ``pr.txt``, so it correctly posts nothing. That trades a
    throughput bug for a silence bug, which is worse — a contributor sees a red check and no
    explanation.
    """
    offenders = [(name, cond) for name, cond in _feedback_step_conditions() if "event_name" in cond]
    assert not offenders, (
        "a lint-feedback step is gated on `github.event_name`, so the remediation is only "
        "produced when the `pull_request` run is the one that survives the shared concurrency "
        "group — and which run survives is not contractual:\n"
        + "\n".join(f"  {name}: if: {cond}" for name, cond in offenders)
    )
