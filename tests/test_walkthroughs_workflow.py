"""The walkthroughs' job guards must admit a run that carries NO ``inputs`` at all.

``clean-machine-walkthroughs.yml`` gained a ``schedule:`` trigger (#3353 §3), and a schedule
walks straight into a trap the file's own header forbids. The ``inputs`` context is populated
only for ``workflow_dispatch`` / ``workflow_call``; on a ``schedule`` event ``inputs.which`` is
**null**. The guards as written before the schedule landed were ``inputs.which == 'all' ||
inputs.which == '<job>'``, and both halves of that are false against null — so every scheduled
run would have skipped all three walkthroughs **and reported green**. That is strictly worse
than the hand-dispatch-only state it replaced: silence you cannot distinguish from success,
on exactly the surface whose header says "a skipped step and a passing step look identical on
the board".

**Why this asserts a PROPERTY and not a string.** The guards are load-bearing in two
directions at once, and the cheap fix breaks the other one. Deleting the guards entirely would
satisfy "a scheduled run is admitted" perfectly — and would also make ``which: v1-wheel`` run
all three walkthroughs, silently retiring the input the dispatch form advertises. So this file
evaluates each guard against synthetic event contexts and asserts both relations:

1. with no ``inputs`` context at all (the ``schedule`` shape) every job is admitted — nothing
   is skipped-while-green;
2. under ``workflow_dispatch`` with ``which: v1-wheel`` **only** ``v1-wheel`` is admitted — the
   selector still selects.

Together those pin the fallback shape without pinning its text: any expression that admits
null and still discriminates passes, and both degenerate rewrites fail.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "clean-machine-walkthroughs.yml"

#: The three walkthrough jobs plus the summary job, with the bound each declares.
EXPECTED_JOBS = ("v1-wheel", "v3-container", "v4-selfupdate", "record")


# ---------------------------------------------------------------------------
# A deliberately tiny evaluator for the GitHub expression shapes these guards use.
# ---------------------------------------------------------------------------

_TOKEN = re.compile(
    r"\s*(?:(?P<op>\|\||&&|==|!=|\(|\))|(?P<str>'(?:[^']|'')*')|(?P<word>[A-Za-z_][\w.()\[\]-]*))"
)


def _tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        match = _TOKEN.match(expr, pos)
        if match is None:
            if expr[pos:].strip() == "":
                break
            raise AssertionError(f"cannot tokenize {expr!r} at offset {pos}: {expr[pos:]!r}")
        tokens.append(match.group(0).strip())
        pos = match.end()
    return tokens


class _Parser:
    """Precedence climbing over ``||`` < ``&&`` < ``==``/``!=`` < primary.

    Missing context properties resolve to ``None``, which is what GitHub itself does and is
    the whole behaviour the ``||`` fallbacks under test depend on: a ``schedule`` run has no
    ``inputs`` at all, so ``inputs.which`` must come out falsy rather than raising.
    """

    def __init__(self, tokens: list[str], context: dict[str, Any]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.context = context

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> str:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self) -> Any:
        value = self._or()
        assert self._peek() is None, f"trailing tokens after expression: {self.tokens[self.pos :]}"
        return value

    def _or(self) -> Any:
        value = self._and()
        while self._peek() == "||":
            self._next()
            right = self._and()
            # GitHub's `||` yields the first truthy OPERAND, not a boolean.
            value = value if _truthy(value) else right
        return value

    def _and(self) -> Any:
        value = self._compare()
        while self._peek() == "&&":
            self._next()
            right = self._compare()
            value = right if _truthy(value) else value
        return value

    def _compare(self) -> Any:
        left = self._primary()
        token = self._peek()
        if token in ("==", "!="):
            self._next()
            right = self._primary()
            equal = _loosely_equal(left, right)
            return equal if token == "==" else not equal
        return left

    def _primary(self) -> Any:
        token = self._next()
        if token == "(":
            value = self._or()
            assert self._next() == ")", "unbalanced parenthesis"
            return value
        if token.startswith("'"):
            return token[1:-1].replace("''", "'")
        if token == "always()":
            return True
        if token in ("true", "false"):
            return token == "true"
        return _lookup(self.context, token)


def _lookup(context: dict[str, Any], dotted: str) -> Any:
    current: Any = context
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _truthy(value: Any) -> bool:
    # GitHub treats null, false and the empty string as falsy; every other string is truthy.
    return bool(value)


def _loosely_equal(left: Any, right: Any) -> bool:
    # GitHub coerces null to the empty string in a comparison, so `null == 'all'` is false
    # while `null == ''` is true. Nothing here compares against '', but the coercion is what
    # makes the pre-schedule guards fail on a scheduled run, so it is modelled rather than
    # short-circuited.
    return ("" if left is None else left) == ("" if right is None else right)


def _evaluate(guard: str, context: dict[str, Any]) -> bool:
    """Evaluate a job-level ``if:`` value under *context*.

    A bare ``if:`` with no ``${{ }}`` wrapper is still an expression to GitHub, so the
    delimiters are stripped when present rather than required.
    """
    body = guard.strip()
    match = re.fullmatch(r"\$\{\{(.+)\}\}", body, flags=re.DOTALL)
    if match:
        body = match.group(1)
    return _truthy(_Parser(_tokenize(body), context).parse())


# ---------------------------------------------------------------------------
# Parsing the workflow.
# ---------------------------------------------------------------------------


def _workflow() -> dict[str, Any]:
    loaded = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{WORKFLOW} did not parse as a mapping"
    return loaded


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # `on:` is the YAML 1.1 boolean `True` once parsed, hence the two-key lookup — the same
    # convention as tests/test_ghcr_anonymous_pull.py.
    triggers = workflow.get("on") or workflow.get(True)
    assert isinstance(triggers, dict), "the workflow declares no trigger block"
    return triggers


def _guards() -> dict[str, str]:
    jobs = _workflow()["jobs"]
    return {name: str(jobs[name]["if"]) for name in EXPECTED_JOBS if "if" in jobs[name]}


# ---------------------------------------------------------------------------
# Vacuity floors, first: the relations below are trivially true on a file with no guards.
# ---------------------------------------------------------------------------


def test_the_four_jobs_and_their_guards_were_actually_found() -> None:
    jobs = _workflow()["jobs"]
    assert tuple(jobs) == EXPECTED_JOBS, f"the job set changed: {tuple(jobs)}"
    guards = _guards()
    assert set(guards) == set(EXPECTED_JOBS), (
        "a job lost its `if:` entirely, so every admission relation below would pass "
        f"vacuously on it: guarded={sorted(guards)}"
    )


def test_the_three_walkthrough_guards_still_read_the_which_input() -> None:
    """Anti-cheat: deleting the guards also 'admits a scheduled run'.

    Without this, the null-admission test below would be satisfied by ``if: always()`` on all
    four jobs — which passes while quietly making ``which: v1-wheel`` run everything.
    """
    guards = _guards()
    for job in EXPECTED_JOBS[:3]:
        assert "inputs.which" in guards[job], (
            f"{job}'s guard no longer reads `inputs.which`, so the dispatch selector is dead: "
            f"{guards[job]!r}"
        )


def test_the_evaluator_rejects_the_pre_schedule_guard_shape() -> None:
    """A positive control on the evaluator itself.

    The bug this file rails against was real and measurable; if the evaluator cannot
    reproduce it, its verdict on the fixed guards means nothing.
    """
    broken = "${{ inputs.which == 'all' || inputs.which == 'v1-wheel' }}"
    assert _evaluate(broken, _dispatch_ctx("all")) is True
    assert _evaluate(broken, _schedule_ctx()) is False, (
        "the evaluator thinks the OLD guard admits a scheduled run — it did not, which is "
        "the entire defect, so the assertions below prove nothing"
    )


# ---------------------------------------------------------------------------
# Synthetic contexts: a scheduled run, and a dispatch that names one walkthrough.
# ---------------------------------------------------------------------------


def _schedule_ctx() -> dict[str, Any]:
    # A `schedule` run carries NO `inputs` context. Modelled by its absence, not by a null
    # value inside it, because absence is what GitHub actually presents.
    return {"github": {"event_name": "schedule"}}


def _dispatch_ctx(which: str) -> dict[str, Any]:
    return {"github": {"event_name": "workflow_dispatch"}, "inputs": {"which": which}}


# ---------------------------------------------------------------------------
# The two relations that matter.
# ---------------------------------------------------------------------------


def test_a_scheduled_run_admits_every_job() -> None:
    """#3353 §3's whole point: the weekly run must RUN, not skip-while-green."""
    guards = _guards()
    for job, guard in guards.items():
        assert _evaluate(guard, _schedule_ctx()) is True, (
            f"{job} would SKIP on the scheduled run while the workflow reported green — "
            f"guard {guard!r} does not admit a null `inputs`"
        )


def test_a_dispatch_naming_one_walkthrough_admits_only_that_one() -> None:
    guards = _guards()
    for selected in EXPECTED_JOBS[:3]:
        context = _dispatch_ctx(selected)
        admitted = {job for job in EXPECTED_JOBS[:3] if _evaluate(guards[job], context)}
        assert admitted == {selected}, (
            f"dispatching which={selected} admitted {sorted(admitted)}; the selector must "
            "pick exactly one walkthrough"
        )
        assert _evaluate(guards["record"], context) is True, "record must always summarise"


def test_dispatching_all_admits_every_walkthrough() -> None:
    guards = _guards()
    context = _dispatch_ctx("all")
    for job in EXPECTED_JOBS:
        assert _evaluate(guards[job], context) is True, f"which=all did not admit {job}"


# ---------------------------------------------------------------------------
# The schedule and the bounds it makes necessary.
# ---------------------------------------------------------------------------


def test_the_workflow_is_on_a_schedule() -> None:
    schedule = _triggers(_workflow()).get("schedule")
    assert schedule, "the walkthroughs have no `schedule:` — every run is a hand dispatch again"
    crons = [entry["cron"] for entry in schedule]
    assert crons, "`schedule:` declares no cron"
    for cron in crons:
        assert len(cron.split()) == 5, f"not a 5-field cron: {cron!r}"


def test_every_job_is_bounded() -> None:
    """An unattended trigger with an unbounded job is a runner held for six hours."""
    jobs = _workflow()["jobs"]
    unbounded = [name for name in EXPECTED_JOBS if "timeout-minutes" not in jobs[name]]
    assert not unbounded, f"jobs with no `timeout-minutes`: {unbounded}"
