"""``ci.yml``'s concurrency group, off ``main``: one key per pull request's branch.

Off ``main``, ``ci.yml`` runs on ``pull_request`` only (#3915: the push trigger admits ``main``
alone). Its group decides two things there: a newer push to a pull request's branch must cancel
the run it supersedes, and no pull request's run may cancel another's.

History, kept because it is why the key has the shape it does. Until #3915 a feature branch also
ran on ``push``, and the key had to collapse that run onto the ``pull_request`` run of the same
head. Measured 2026-09-07 (issue #2595): keyed on ``github.ref``, which the two triggers disagree
about (``refs/pull/<n>/merge`` against ``refs/heads/<branch>``), every non-fork PR ran the whole
matrix twice on one head SHA, about 72 jobs in flight for 8 PRs. Keying on the head SHA instead
would have deduped the two triggers and broken cancellation: every commit in its own group, so
three pushes leave three live runs.

**Why this asserts PROPERTIES and not a string.** A test that pinned the expression as text
would accept the SHA-keyed "fix" as readily as the right key. So this file evaluates the group
against synthetic ``pull_request`` contexts, as GitHub fills them in, and asserts the three
relations that matter off ``main``:

1. two heads of one pull request → the **same** key (a newer push cancels the superseded run;
   this is the relation a head-SHA or test-merge-SHA key breaks);
2. two pull requests on two branches → **different** keys (a branch must not cancel its
   neighbours);
3. two forks with a branch of the same name → **different** keys (no cross-contributor
   cancellation).

``tests/rails/test_ci_contract.py``'s concurrency row holds relations 2 and 3 (different
candidates never share a group) for every workflow, ``pull_request_target`` included, and that
two runs of one head share theirs. It does not hold relation 1: a newer head of one pull request
sharing its key is held here alone. Parsed with a line scan rather than PyYAML, matching the
convention its sibling ``tests/rails/test_ci_tier_enforcement.py`` states.
"""

from __future__ import annotations

import re
from typing import Any

from rails_repo import REPO

REPO_ROOT = REPO
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# ---------------------------------------------------------------------------
# A deliberately tiny evaluator for the GitHub expression shapes this key uses.
# ---------------------------------------------------------------------------

_EXPR = re.compile(r"\$\{\{(.+?)\}\}")


def _lookup(context: dict[str, Any], dotted: str) -> Any:
    """Resolve ``a.b.c`` against nested dicts, returning ``''`` for any missing link, as GitHub
    does: the ``||`` fallbacks in the key depend on a missing property being falsy."""
    cur: Any = context
    for part in dotted.strip().split("."):
        if not isinstance(cur, dict) or part not in cur:
            return ""
        cur = cur[part]
    return cur


def _operand(context: dict[str, Any], text: str) -> Any:
    """A property, or a property compared with a quoted literal (``a == 'b'``)."""
    m = re.fullmatch(r"\s*([\w.-]+)\s*(==|!=)\s*'([^']*)'\s*", text)
    if not m:
        return _lookup(context, text)
    same = str(_lookup(context, m.group(1))) == m.group(3)
    return same if m.group(2) == "==" else not same


def _evaluate(template: str, context: dict[str, Any]) -> str:
    """Substitute every ``${{ … }}`` in *template*: ``a && b || c`` chains, GitHub's way (the
    first truthy alternative, and within one the first falsy term or the last)."""

    def one(match: re.Match[str]) -> str:
        value: Any = ""
        for alternative in match.group(1).split("||"):
            for term in alternative.split("&&"):
                value = _operand(context, term)
                if not value:
                    break
            if value:
                return str(value)
        return str(value) if value else ""

    return _EXPR.sub(one, template)


def _concurrency_group() -> str:
    """The raw ``group:`` value from the workflow-level ``concurrency:`` block.

    Anchored on the top-level block so a job-level ``concurrency:`` is never picked up.
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
# Synthetic `pull_request` contexts, as GitHub fills them in.
# ---------------------------------------------------------------------------

_REPO = "PersonalClaw/PersonalClaw"
_BRANCH = "bugfix-some-branch"


def _pull_request_ctx(
    *,
    branch: str = _BRANCH,
    head_repo: str = _REPO,
    number: int = 42,
    head: str = "aaaa111",
    merge: str = "merge999",
) -> dict[str, Any]:
    return {
        "github": {
            "repository": _REPO,
            "ref": f"refs/pull/{number}/merge",
            "ref_name": f"{number}/merge",
            "head_ref": branch,
            # The test-merge commit: a new one for every head, and never the head itself.
            "sha": merge,
            "event_name": "pull_request",
            "event": {
                "pull_request": {
                    "number": number,
                    "head": {"sha": head, "repo": {"full_name": head_repo}},
                }
            },
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
    """If evaluation silently produced ``''``, relation 1 would 'pass'."""
    resolved = _evaluate(_concurrency_group(), _pull_request_ctx())
    assert "${{" not in resolved, f"unresolved expression left in {resolved!r}"
    assert resolved.strip("-"), f"the key resolved to nothing: {resolved!r}"
    assert _BRANCH in resolved, (
        f"the resolved key {resolved!r} does not mention the branch, so it cannot be telling "
        "branches apart"
    )


def test_the_contexts_differ_in_the_fields_a_wrong_key_would_read() -> None:
    """Floor on the harness: two heads of one pull request differ in the head SHA and the
    test-merge SHA, so relation 1 really checks the key does not read either."""
    one, two = _pull_request_ctx(), _pull_request_ctx(head="bbbb222", merge="merge888")
    for field in ("${{ github.sha }}", "${{ github.event.pull_request.head.sha }}"):
        assert _evaluate(field, one) != _evaluate(field, two), field


# ---------------------------------------------------------------------------
# The three relations.
# ---------------------------------------------------------------------------


def test_a_newer_head_of_one_pull_request_shares_its_key() -> None:
    """Relation 1 — a newer push cancels the run it supersedes."""
    group = _concurrency_group()
    first = _evaluate(group, _pull_request_ctx(head="aaaa111", merge="merge999"))
    second = _evaluate(group, _pull_request_ctx(head="bbbb222", merge="merge888"))
    assert first == second, (
        "two heads of the SAME pull request resolved to DIFFERENT concurrency keys "
        f"({first!r} vs {second!r}), so `cancel-in-progress` can no longer cancel the "
        "superseded run and every push leaves its predecessor burning runners. The key must "
        "not contain a per-commit value such as `github.sha` (the test merge) or "
        "`github.event.pull_request.head.sha`."
    )


def test_two_pull_requests_on_two_branches_do_not_share_a_key() -> None:
    """Relation 2 — a branch must not cancel its neighbours."""
    group = _concurrency_group()
    mine = _evaluate(group, _pull_request_ctx(branch="bugfix-mine", number=41))
    yours = _evaluate(group, _pull_request_ctx(branch="bugfix-yours", number=43))
    assert mine != yours, (
        f"two pull requests on different branches resolved to the same key ({mine!r}), so a "
        "push to one would cancel CI for the other."
    )


def test_two_forks_with_the_same_branch_name_do_not_share_a_key() -> None:
    """Relation 3 — no cross-contributor cancellation.

    ``patch-1`` is the name GitHub's own web editor picks, so two unrelated fork PRs carrying
    it is ordinary rather than hypothetical.
    """
    group = _concurrency_group()
    a = _evaluate(
        group, _pull_request_ctx(branch="patch-1", head_repo="alice/PersonalClaw", number=50)
    )
    b = _evaluate(
        group, _pull_request_ctx(branch="patch-1", head_repo="bob/PersonalClaw", number=51)
    )
    assert a != b, (
        f"two forks pushing a branch named `patch-1` resolved to the same key ({a!r}), so one "
        "stranger's push cancels another stranger's CI run."
    )


# ---------------------------------------------------------------------------
# The lint remediation does not key on the event name.
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


def test_the_lint_remediation_does_not_depend_on_the_event_name() -> None:
    """While the feedback steps were gated on ``github.event_name == 'pull_request'``, a
    surviving push run produced a red ``lint`` with the remediation posted nowhere (#2595).
    Since #3915 there is no feature-branch push run, but the steps still must not key on the
    event: a trigger change must not bring that silence back."""
    offenders = [(name, cond) for name, cond in _feedback_step_conditions() if "event_name" in cond]
    assert not offenders, (
        "a lint-feedback step is gated on `github.event_name`, so whether the remediation is "
        "produced depends on which trigger started the run:\n"
        + "\n".join(f"  {name}: if: {cond}" for name, cond in offenders)
    )
