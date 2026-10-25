"""The tier workflows' concurrency groups on a pull request: one key per pull request.

``pr.yml`` and ``merge.yml`` run on ``pull_request`` (and ``merge.yml`` on a push to ``main``, which
keys per commit). On a pull request each group decides two things: a newer push to it must cancel
the run it supersedes, and no pull request's run may cancel another's. They took this over from
``ci.yml`` when the flip (#3915) deleted it.

History, kept because it is why the key has the shape it does. Until #3915 a feature branch also
ran on ``push``, and ci.yml's key had to collapse that run onto the ``pull_request`` run of the
same head. Measured 2026-09-07 (issue #2595): keyed on ``github.ref``, which the two triggers
disagree about (``refs/pull/<n>/merge`` against ``refs/heads/<branch>``), every non-fork PR ran
the whole matrix twice on one head SHA, about 72 jobs in flight for 8 PRs. Keying on the head SHA
instead would have deduped the two triggers and broken cancellation: every commit in its own
group, so three pushes leave three live runs. A pull request's number is one per pull request,
whatever its heads, its branch's name or its fork.

**Why this asserts PROPERTIES and not a string.** A test that pinned the expression as text
would accept the SHA-keyed "fix" as readily as the right key. So this file evaluates each group
against synthetic ``pull_request`` contexts, as GitHub fills them in, and asserts the three
relations that matter:

1. two heads of one pull request → the **same** key (a newer push cancels the superseded run;
   this is the relation a head-SHA or test-merge-SHA key breaks);
2. two pull requests on two branches → **different** keys (a branch must not cancel its
   neighbours);
3. two forks with a branch of the same name → **different** keys (no cross-contributor
   cancellation);

and that a pull request's run cancels in progress where a push's or a dispatch's never does.

``tests/rails/test_ci_contract.py``'s concurrency row holds relations 2 and 3 (different
candidates never share a group) for every workflow, ``pull_request_target`` included, and that
two runs of one head share theirs. It does not hold relation 1: a newer head of one pull request
sharing its key is held here alone. Parsed with a line scan rather than PyYAML, matching the
convention its sibling ``tests/rails/test_ci_tier_enforcement.py`` states.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from rails_repo import REPO

REPO_ROOT = REPO
#: The workflows that run a pull request's checks.
TIER = ("pr.yml", "merge.yml")

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


def _workflow_level(name: str, key: str) -> str:
    """The raw value of *key* in workflow *name*'s top-level ``concurrency:`` block.

    Anchored on the top-level block so a job-level ``concurrency:`` is never picked up.
    """
    lines = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.rstrip() != "concurrency:":
            continue
        for follower in lines[i + 1 : i + 6]:
            m = re.match(rf"\s+{re.escape(key)}:\s*(\S.*?)\s*$", follower)
            if m:
                return m.group(1)
    raise AssertionError(f"no workflow-level `concurrency:` / `{key}:` found in {name}")


def _concurrency_group(name: str) -> str:
    return _workflow_level(name, "group")


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


@pytest.mark.parametrize("name", TIER)
def test_the_group_expression_was_actually_found(name: str) -> None:
    group = _concurrency_group(name)
    assert group, "the concurrency group parsed as empty"
    assert "${{" in group, (
        "the concurrency group contains no expression at all, so every relation asserted "
        f"below would compare two identical constants and pass vacuously: {group!r}"
    )


@pytest.mark.parametrize("name", TIER)
def test_the_evaluator_resolves_the_real_key_to_something(name: str) -> None:
    """If evaluation silently produced ``''``, relation 1 would 'pass'."""
    resolved = _evaluate(_concurrency_group(name), _pull_request_ctx())
    assert "${{" not in resolved, f"unresolved expression left in {resolved!r}"
    assert resolved.strip("-"), f"the key resolved to nothing: {resolved!r}"
    assert "42" in resolved, (
        f"the resolved key {resolved!r} does not name the pull request, so it cannot be telling "
        "pull requests apart"
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


@pytest.mark.parametrize("name", TIER)
def test_a_newer_head_of_one_pull_request_shares_its_key(name: str) -> None:
    """Relation 1 — a newer push cancels the run it supersedes."""
    group = _concurrency_group(name)
    first = _evaluate(group, _pull_request_ctx(head="aaaa111", merge="merge999"))
    second = _evaluate(group, _pull_request_ctx(head="bbbb222", merge="merge888"))
    assert first == second, (
        "two heads of the SAME pull request resolved to DIFFERENT concurrency keys "
        f"({first!r} vs {second!r}), so `cancel-in-progress` can no longer cancel the "
        "superseded run and every push leaves its predecessor burning runners. The key must "
        "not contain a per-commit value such as `github.sha` (the test merge) or "
        "`github.event.pull_request.head.sha`."
    )


@pytest.mark.parametrize("name", TIER)
def test_two_pull_requests_on_two_branches_do_not_share_a_key(name: str) -> None:
    """Relation 2 — a branch must not cancel its neighbours."""
    group = _concurrency_group(name)
    mine = _evaluate(group, _pull_request_ctx(branch="bugfix-mine", number=41))
    yours = _evaluate(group, _pull_request_ctx(branch="bugfix-yours", number=43))
    assert mine != yours, (
        f"two pull requests on different branches resolved to the same key ({mine!r}), so a "
        "push to one would cancel CI for the other."
    )


@pytest.mark.parametrize("name", TIER)
def test_two_forks_with_the_same_branch_name_do_not_share_a_key(name: str) -> None:
    """Relation 3 — no cross-contributor cancellation.

    ``patch-1`` is the name GitHub's own web editor picks, so two unrelated fork PRs carrying
    it is ordinary rather than hypothetical.
    """
    group = _concurrency_group(name)
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
# A pull request's run cancels in progress; a push's and a dispatch's never do.
# ---------------------------------------------------------------------------


def _event_ctx(event: str) -> dict[str, Any]:
    return {"github": {"event_name": event, "sha": "cccc333", "repository": _REPO}}


@pytest.mark.parametrize("name", TIER)
def test_only_a_pull_requests_run_cancels_in_progress(name: str) -> None:
    cancel = _workflow_level(name, "cancel-in-progress")
    assert _evaluate(cancel, _event_ctx("pull_request")) == "True", cancel
    for event in ("push", "workflow_dispatch"):
        assert not _evaluate(cancel, _event_ctx(event)), (
            f"{name} cancels a {event} run in progress, so a run of main or a dispatched commit"
            f" could be cut off: {cancel}"
        )
    pushes = {_evaluate(_concurrency_group(name), _event_ctx("push"))}
    pushes.add(
        _evaluate(
            _concurrency_group(name), {"github": {**_event_ctx("push")["github"], "sha": "dddd444"}}
        )
    )
    assert len(pushes) == 2, f"two pushes share {name}'s group, so one waits on the other: {pushes}"


# ---------------------------------------------------------------------------
# The lint remediation does not key on the event name.
# ---------------------------------------------------------------------------


def _feedback_step_conditions() -> list[tuple[str, str]]:
    """``(workflow, its `if:` expression)`` for each step that uploads the lint feedback
    pr-feedback.yml relays: the stage jobs' upload of what run-job left (#3915)."""
    found: list[tuple[str, str]] = []
    for name in TIER:
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"\s*-\s*name:\s*(\S.*?)\s*$", line)
            if not m or m.group(1) != "Upload the entry's lint feedback":
                continue
            for follower in lines[i + 1 : i + 4]:
                cond = re.match(r"\s+if:\s*(\S.*?)\s*$", follower)
                if cond:
                    found.append((name, cond.group(1)))
                    break
    return found


def _stage_jobs(name: str) -> int:
    """How many of the workflow's jobs run a stage of the plan: a matrix the plan job gives."""
    text = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    return len(re.findall(r"^      matrix: \$\{\{ fromJSON\(needs\.changes\.outputs\.", text, re.M))


def test_the_feedback_step_scan_is_not_vacuous() -> None:
    """Floor: each tier workflow's every stage job uploads it, or the check below proves
    nothing."""
    found = [n for n, _ in _feedback_step_conditions()]
    for name in TIER:
        assert _stage_jobs(name) and found.count(name) == _stage_jobs(name), (
            f"expected the lint-feedback upload in each of {name}'s stage jobs, found {found!r}."
            " pr-feedback.yml depends on the artifact it uploads; if the step was renamed, this"
            " rail stopped watching it."
        )


def test_the_lint_remediation_does_not_depend_on_the_event_name() -> None:
    """While ci.yml's feedback steps were gated on ``github.event_name == 'pull_request'``, a
    surviving push run produced a red ``lint`` with the remediation posted nowhere (#2595). The
    upload still must not key on the event: run-job leaves the remediation only for a pull
    request's run, from the run's own event, and a trigger change must not bring that silence
    back."""
    offenders = [(name, cond) for name, cond in _feedback_step_conditions() if "event_name" in cond]
    assert not offenders, (
        "a lint-feedback upload is gated on `github.event_name`, so whether the remediation is "
        "uploaded depends on which trigger started the run:\n"
        + "\n".join(f"  {name}: if: {cond}" for name, cond in offenders)
    )
