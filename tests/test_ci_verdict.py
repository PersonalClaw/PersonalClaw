"""``tools/ci/verdict.py``: one verdict over a plan's jobs, and every way it reads red.

The verdict is green only when the plan is not empty and every planned job has a result, every
result for it on every attempt is ``success``, and every result names the candidate's head and
the candidate's partner revisions. Each red row below plants one way that fails and requires the
verdict to say so, under that job's key, with that reason; the green controls show the same
function passes a plan that did all its work, so a verdict that reads everything as red cannot
pass this module either. Each refusal row names the reason it must be refused for, so one check
cannot stand in for another.

Security-marked: this is a fail-closed boundary (rule 2 in ``docs/maintainers/testing.md``).
Pure and in memory, apart from the CLI's files in ``tmp_path`` and one fresh interpreter that
runs the script the way a trusted job does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from table_rows import row

from tools.ci import __main__ as ci
from tools.ci import verdict
from tools.ci.verdict import Identity, JobResult, Refused, decide, from_needs

pytestmark = pytest.mark.security

HEAD = "a" * 40
OLD_HEAD = "b" * 40
PARTNER = "c" * 40
OLD_PARTNER = "d" * 40
OTHER = "e" * 40

CANDIDATE = Identity(HEAD, {"apps": PARTNER})

#: Deeper than any interpreter's recursion limit, so a JSON reader cannot follow it.
DEEP = 100_000


def ok(key: str, attempt: int = 1) -> JobResult:
    return JobResult(key, "success", CANDIDATE, attempt)


def as_(
    key: str, result: str, identity: Identity | None = CANDIDATE, attempt: int = 1
) -> JobResult:
    return JobResult(key, result, identity, attempt)


@pytest.fixture(autouse=True)
def _no_step_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inside a GitHub job this variable names the real run's summary; no test may write to it.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


# ── decide: every red ─────────────────────────────────────────────────────────────────────────

#: (name, planned, results, the key the reason names, the reason's kind)
RED_ROWS: list[tuple[str, list[str], list[JobResult], str, str]] = [
    ("a missing result", ["lint", "test"], [ok("lint")], "test", "missing"),
    (
        "a cancelled job",
        ["lint", "test"],
        [ok("lint"), as_("test", "cancelled")],
        "test",
        "cancelled",
    ),
    (
        "a skipped planned job",
        ["lint", "test"],
        [ok("lint"), as_("test", "skipped")],
        "test",
        "skipped",
    ),
    ("a failed job", ["lint", "test"], [ok("lint"), as_("test", "failure")], "test", "failure"),
    (
        "a result for an older head",
        ["lint", "test"],
        [ok("lint"), as_("test", "success", Identity(OLD_HEAD, {"apps": PARTNER}))],
        "test",
        "stale head",
    ),
    (
        "a result for an older partner head",
        ["lint", "test"],
        [ok("lint"), as_("test", "success", Identity(HEAD, {"apps": OLD_PARTNER}))],
        "test",
        "stale partner",
    ),
    (
        "a result that names no partner revision",
        ["lint", "test"],
        [ok("lint"), as_("test", "success", Identity(HEAD, {}))],
        "test",
        "stale partner",
    ),
    (
        "a result that names a partner the candidate has not",
        ["lint", "test"],
        [ok("lint"), as_("test", "success", Identity(HEAD, {"apps": PARTNER, "web": OTHER}))],
        "test",
        "stale partner",
    ),
    (
        "a result with no identity",
        ["lint", "test"],
        [ok("lint"), as_("test", "success", None)],
        "test",
        "no identity",
    ),
    (
        "an unknown result word",
        ["lint", "test"],
        [ok("lint"), as_("test", "neutral")],
        "test",
        "unknown result",
    ),
    (
        "a result word in another case",
        ["lint", "test"],
        [ok("lint"), as_("test", "SUCCESS")],
        "test",
        "unknown result",
    ),
    ("an empty plan", [], [ok("lint")], "", "empty plan"),
    (
        "a red first attempt followed by a green rerun",
        ["lint", "test"],
        [ok("lint"), as_("test", "failure", attempt=1), ok("test", attempt=2)],
        "test",
        "red on an earlier attempt",
    ),
    (
        "an unplanned job that failed",
        ["lint"],
        [ok("lint"), as_("deploy", "failure")],
        "deploy",
        "unplanned failure",
    ),
    (
        "an unplanned job that was cancelled",
        ["lint"],
        [ok("lint"), as_("deploy", "cancelled")],
        "deploy",
        "unplanned cancelled",
    ),
    (
        "an unplanned job with an unknown result word",
        ["lint"],
        [ok("lint"), as_("deploy", "neutral")],
        "deploy",
        "unplanned unknown result",
    ),
    (
        "an unplanned job that failed before a green rerun",
        ["lint"],
        [ok("lint"), as_("deploy", "failure", attempt=1), as_("deploy", "success", attempt=2)],
        "deploy",
        "unplanned failure",
    ),
]


def test_every_way_a_plan_falls_short_reads_red(subtests: object) -> None:
    assert len(RED_ROWS) == 17, "a red row was dropped from the table"
    for name, planned, results, key, kind in RED_ROWS:
        with row(subtests, case=name):
            got = decide(planned, results, CANDIDATE)
            assert not got.green, f"{name}: read green"
            assert [(r.key, r.kind) for r in got.reasons] == [(key, kind)], f"{name}: {got.reasons}"


# ── decide: the green controls ────────────────────────────────────────────────────────────────

#: (name, candidate, planned, results)
GREEN_ROWS: list[tuple[str, Identity, list[str], list[JobResult]]] = [
    (
        "every planned job green on the candidate",
        CANDIDATE,
        ["lint", "test"],
        [ok("lint"), ok("test")],
    ),
    (
        "an unplanned job that was skipped",
        CANDIDATE,
        ["lint", "test"],
        [ok("lint"), ok("test"), as_("deploy", "skipped")],
    ),
    (
        "a candidate and results with no partner",
        Identity(HEAD, {}),
        ["lint"],
        [JobResult("lint", "success", Identity(HEAD, {}), 1)],
    ),
]


def test_a_plan_that_did_all_its_work_reads_green(subtests: object) -> None:
    assert len(GREEN_ROWS) == 3, "a green control was dropped from the table"
    for name, candidate, planned, results in GREEN_ROWS:
        with row(subtests, case=name):
            got = decide(planned, results, candidate)
            assert got.green, f"{name}: read red: {got.reasons}"
            assert got.reasons == (), f"{name}: green with reasons {got.reasons}"


# ── decide: a record's own partners ───────────────────────────────────────────────────────────
#
# A run-job record names only the partners its own job read (``own_partners``): apps-contract's
# names the apps checkout, every other job's names none. So the verdict is given each planned
# key's partners (``key_partners``, from the plan: the job's catalogue entry's revision inputs),
# and a record must name exactly those, each at the candidate's revision. Given none, a record is
# held to the candidate's partners exactly, as a ``needs`` result always is (the rows above).

#: The merge tier's partners by key: apps-contract reads the apps checkout, the others none.
KEY_PARTNERS = {"static": [], "apps-contract": ["apps"], "rails (1/2)": []}


def own(key: str, partners: dict[str, str], head: str = HEAD) -> JobResult:
    return JobResult(key, "success", Identity(head, partners), 1, own_partners=True)


def merge_tier(changed: dict[str, dict[str, str]] | None = None) -> list[JobResult]:
    """The merge tier's records, each naming its job's partners, with ``changed``'s replaced."""
    named = {"static": {}, "apps-contract": {"apps": PARTNER}, "rails (1/2)": {}, **(changed or {})}
    return [own(key, partners) for key, partners in named.items()]


#: (name, results, the red key, a phrase of its detail): each red as ``stale partner``.
OWN_PARTNER_RED_ROWS: list[tuple[str, list[JobResult], str, str]] = [
    (
        "apps-contract's record naming no partner",
        merge_tier({"apps-contract": {}}),
        "apps-contract",
        "names no apps, which its job reads",
    ),
    (
        "apps-contract's record naming a partner its job does not read",
        merge_tier({"apps-contract": {"apps": PARTNER, "web": OTHER}}),
        "apps-contract",
        f"ran with partner web={OTHER}, which its job does not read",
    ),
    (
        "a record of a job that reads no partner naming one",
        merge_tier({"static": {"apps": PARTNER}}),
        "static",
        f"ran with partner apps={PARTNER}, which its job does not read",
    ),
    (
        "apps-contract's record of an older partner head",
        merge_tier({"apps-contract": {"apps": OLD_PARTNER}}),
        "apps-contract",
        f"ran with partner apps={OLD_PARTNER}, not apps={PARTNER}",
    ),
    (
        "apps-contract's record naming the candidate's revision under another partner's name",
        merge_tier({"apps-contract": {"web": PARTNER}}),
        "apps-contract",
        f"ran with partner web={PARTNER}, which its job does not read",
    ),
]


def test_a_record_names_exactly_its_jobs_partners_at_the_candidates_revisions(
    subtests: object,
) -> None:
    assert len(OWN_PARTNER_RED_ROWS) == 5, "a red row was dropped from the table"
    for name, results, key, why in OWN_PARTNER_RED_ROWS:
        with row(subtests, case=name):
            got = decide(list(KEY_PARTNERS), results, CANDIDATE, key_partners=KEY_PARTNERS)
            assert [(r.key, r.kind) for r in got.reasons] == [(key, "stale partner")], name
            assert why in got.reasons[0].detail, got.reasons[0].detail


def test_records_that_name_their_own_partners_or_none_read_green() -> None:
    got = decide(list(KEY_PARTNERS), merge_tier(), CANDIDATE, key_partners=KEY_PARTNERS)
    assert got.green, got.reasons


def test_a_partner_its_job_reads_that_the_candidate_does_not_name_reads_red() -> None:
    got = decide(list(KEY_PARTNERS), merge_tier(), Identity(HEAD, {}), key_partners=KEY_PARTNERS)
    assert [(r.key, r.kind) for r in got.reasons] == [("apps-contract", "stale partner")]
    assert "its job reads apps, which the candidate (none) does not name" in got.reasons[0].detail


def test_with_no_partners_by_key_a_record_is_held_to_the_candidates_partners_exactly() -> None:
    # The fail-closed reading: a record naming none of the candidate's partners is stale.
    got = decide(["static"], [own("static", {})], CANDIDATE)
    assert [(r.key, r.kind) for r in got.reasons] == [("static", "stale partner")]
    assert decide(["static"], [own("static", {"apps": PARTNER})], CANDIDATE).green


def test_a_record_naming_no_partner_still_needs_the_candidates_head() -> None:
    got = decide(
        ["static"], [own("static", {}, head=OLD_HEAD)], CANDIDATE, key_partners={"static": []}
    )
    assert [(r.key, r.kind) for r in got.reasons] == [("static", "stale head")]


def test_a_needs_result_naming_no_partner_is_still_stale() -> None:
    # The run's identity, not a job's: it must name the candidate's partners exactly, whatever
    # the partners by key say.
    result = JobResult("static", "success", Identity(HEAD, {}), 1)
    got = decide(["static"], [result], CANDIDATE, key_partners={"static": []})
    assert [(r.key, r.kind) for r in got.reasons] == [("static", "stale partner")]


def test_decide_refuses_partners_by_key_that_are_not_the_plans(subtests: object) -> None:
    rows: list[tuple[str, object, str]] = [
        ("a planned key with no entry", {"static": []}, "name no partners for apps-contract"),
        (
            "an entry for a key outside the plan",
            {"static": [], "apps-contract": ["apps"], "lint": []},
            "name partners for lint, which is not planned",
        ),
        ("partners that are not a list", {"static": [], "apps-contract": "apps"}, "a list of"),
        ("a partner named twice", {"static": [], "apps-contract": ["apps", "apps"]}, "twice"),
        ("an empty partner name", {"static": [], "apps-contract": [""]}, "a list of"),
        ("not a mapping", ["static", "apps-contract"], "a mapping of"),
    ]
    assert len(rows) == 6, "a refusal was dropped from the table"
    for name, key_partners, why in rows:
        with row(subtests, case=name):
            with pytest.raises(Refused, match=why):
                planned = ["static", "apps-contract"]
                decide(planned, [], CANDIDATE, key_partners=key_partners)  # type: ignore[arg-type]


def test_a_sha256_repository_reads_green() -> None:
    # Built here, not in a table, so an identity that refuses SHA-256 fails this control alone.
    head, partner = "a" * 64, "c" * 64
    candidate = Identity(head, {"apps": partner})
    got = decide(
        ["lint"], [JobResult("lint", "success", Identity(head, {"apps": partner}), 1)], candidate
    )
    assert got.green, got.reasons


def test_every_job_that_falls_short_gets_its_own_reason() -> None:
    got = decide(
        ["lint", "test", "web"],
        [as_("lint", "cancelled"), ok("test"), as_("web", "failure")],
        CANDIDATE,
    )
    assert not got.green
    assert [(r.key, r.kind) for r in got.reasons] == [("lint", "cancelled"), ("web", "failure")]


def test_a_key_planned_twice_is_judged_once() -> None:
    got = decide(["lint", "lint"], [as_("lint", "failure")], CANDIDATE)
    assert [(r.key, r.kind) for r in got.reasons] == [("lint", "failure")]


def test_decide_refuses_a_planned_key_that_is_not_a_name(subtests: object) -> None:
    rows: list[tuple[str, list[object]]] = [
        ("an empty key", [""]),
        ("a key that is not a string", [1]),
    ]
    assert len(rows) == 2, "a refusal was dropped from the table"
    for name, planned in rows:
        with row(subtests, case=name):
            with pytest.raises(Refused, match="a planned job key must be a non-empty string"):
                decide(planned, [ok("lint")], CANDIDATE)  # type: ignore[arg-type]


# ── identities ────────────────────────────────────────────────────────────────────────────────

#: The reasons an identity is refused.
BAD_HEAD = "head_sha must be a full object id"
BAD_PARTNER = "partner apps must be a full object id"
UNNAMED = "a partner revision must be named"

#: (name, head, partners, how the refusal begins): neither a head nor a partner may be anything
#: but a whole object id.
BAD_IDENTITIES: list[tuple[str, object, dict[object, object], str]] = [
    ("a short head", "abc123", {}, BAD_HEAD),
    ("an empty head", "", {}, BAD_HEAD),
    ("a head in upper case", "A" * 40, {}, BAD_HEAD),
    ("a head abbreviated to 7 characters", "a" * 7, {}, BAD_HEAD),
    ("a head one short of SHA-1", "a" * 39, {}, BAD_HEAD),
    ("a head one past SHA-1", "a" * 41, {}, BAD_HEAD),
    ("a head one short of SHA-256", "a" * 63, {}, BAD_HEAD),
    ("a head one past SHA-256", "a" * 65, {}, BAD_HEAD),
    ("a head with a trailing newline", HEAD + "\n", {}, BAD_HEAD),
    ("a head that is bytes", HEAD.encode(), {}, BAD_HEAD),
    ("a partner revision that is not an object id", HEAD, {"apps": "main"}, BAD_PARTNER),
    ("a partner abbreviated to 7 characters", HEAD, {"apps": "c" * 7}, BAD_PARTNER),
    ("a partner one short of SHA-1", HEAD, {"apps": "c" * 39}, BAD_PARTNER),
    ("a partner one past SHA-1", HEAD, {"apps": "c" * 41}, BAD_PARTNER),
    ("a partner one short of SHA-256", HEAD, {"apps": "c" * 63}, BAD_PARTNER),
    ("a partner one past SHA-256", HEAD, {"apps": "c" * 65}, BAD_PARTNER),
    ("a partner with a trailing newline", HEAD, {"apps": PARTNER + "\n"}, BAD_PARTNER),
    ("a partner revision that is bytes", HEAD, {"apps": PARTNER.encode()}, BAD_PARTNER),
    ("a partner with no name", HEAD, {"": PARTNER}, UNNAMED),
    ("a partner name that is not a string", HEAD, {1: PARTNER}, UNNAMED),
]


def test_an_identity_names_full_object_ids(subtests: object) -> None:
    assert len(BAD_IDENTITIES) == 20, "a refusal was dropped from the table"
    for name, head, partners, why in BAD_IDENTITIES:
        with row(subtests, case=name):
            with pytest.raises(Refused) as refused:
                Identity(head, partners)  # type: ignore[arg-type]
            assert str(refused.value).startswith(why), f"{name}: {refused.value}"


def test_an_identity_keeps_its_own_copy_of_the_partners() -> None:
    partners = {"apps": PARTNER}
    candidate = Identity(HEAD, partners)
    result = JobResult("lint", "success", Identity(HEAD, {"apps": PARTNER}), 1)
    # The caller's dict, changed after the identity was built, changes nothing it judges.
    partners["apps"] = OLD_PARTNER
    partners["web"] = "main"
    assert candidate.external_revisions == {"apps": PARTNER}
    assert decide(["lint"], [result], candidate).green


# ── from_needs ────────────────────────────────────────────────────────────────────────────────

#: GitHub's ``toJSON(needs)``: one object per needed job, its ``result`` and its ``outputs``.
NEEDS = {
    "test": {"result": "failure", "outputs": {"shard": "1"}},
    "lint": {"result": "success", "outputs": {}},
}


def test_from_needs_reads_githubs_shape_and_stamps_the_runs_identity() -> None:
    got = from_needs(json.dumps(NEEDS), CANDIDATE)
    assert got == [
        JobResult("lint", "success", CANDIDATE, 1),
        JobResult("test", "failure", CANDIDATE, 1),
    ]
    assert [r.attempt for r in from_needs(json.dumps(NEEDS), CANDIDATE, attempt=2)] == [2, 2]
    assert from_needs("{}", CANDIDATE) == []


_JOB = '{"result": "success", "outputs": {}}'

#: (name, the text, how the refusal begins)
NEEDS_REFUSED: list[tuple[str, str, str]] = [
    ("not JSON", "{", "needs is not JSON"),
    ("a list", "[]", "needs must be an object of jobs"),
    ("a string", '"lint"', "needs must be an object of jobs"),
    ("a job that is not an object", '{"lint": "success"}', "needs.lint must be an object"),
    (
        "a job with no result",
        '{"lint": {"outputs": {}}}',
        "needs.lint must hold exactly result and outputs",
    ),
    (
        "a job with no outputs",
        '{"lint": {"result": "success"}}',
        "needs.lint must hold exactly result and outputs",
    ),
    (
        "a job with a field GitHub does not write",
        '{"lint": {"result": "success", "outputs": {}, "conclusion": "success"}}',
        "needs.lint must hold exactly result and outputs",
    ),
    (
        "a result that is not a string",
        '{"lint": {"result": 1, "outputs": {}}}',
        "needs.lint has no result string",
    ),
    (
        "outputs that are not an object",
        '{"lint": {"result": "success", "outputs": []}}',
        "needs.lint.outputs must be an object",
    ),
    ("an empty job id", '{"": ' + _JOB + "}", "needs names a job with an empty id"),
    (
        "a job id given twice",
        '{"lint": {"result": "failure", "outputs": {}}, "lint": ' + _JOB + "}",
        "needs repeats the key 'lint'",
    ),
    (
        "an output name given twice",
        '{"lint": {"result": "success", "outputs": {"shard": "1", "shard": "2"}}}',
        "needs repeats the key 'shard'",
    ),
    ("needs nested too deeply to read", "[" * DEEP + "]" * DEEP, "needs is nested too deeply"),
]


def test_from_needs_refuses_anything_else(subtests: object) -> None:
    assert len(NEEDS_REFUSED) == 13, "a refusal was dropped from the table"
    for name, text, why in NEEDS_REFUSED:
        with row(subtests, case=name):
            with pytest.raises(Refused) as refused:
                from_needs(text, CANDIDATE)
            assert str(refused.value).startswith(why), f"{name}: {refused.value}"


# ── the command ───────────────────────────────────────────────────────────────────────────────


def _inputs(tmp_path: Path, plan: object, needs: object) -> tuple[str, str]:
    p, n = tmp_path / "plan.json", tmp_path / "needs.json"
    p.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
    n.write_text(needs if isinstance(needs, str) else json.dumps(needs), encoding="utf-8")
    return str(p), str(n)


def _args(
    plan: str,
    needs: str,
    *,
    head: str = HEAD,
    run_head: str = HEAD,
    run_partner: str = PARTNER,
) -> list[str]:
    return [
        "--plan", plan, "--needs", needs,
        "--head", head, "--partner", f"apps={PARTNER}",
        "--run-head", run_head, "--run-partner", f"apps={run_partner}",
    ]  # fmt: skip


GREEN_NEEDS = {
    "lint": {"result": "success", "outputs": {}},
    "test": {"result": "success", "outputs": {}},
}


def _run(args: list[str]) -> int:
    """``python -m tools.ci verdict``; a SystemExit escaping the dispatcher fails the row."""
    try:
        return ci.main(["verdict", *args])
    except SystemExit as exc:
        pytest.fail(f"SystemExit({exc.code}) escaped the dispatcher")


def test_the_command_exits_0_on_green_and_1_on_red(tmp_path: Path) -> None:
    plan, needs = _inputs(tmp_path, ["lint", "test"], GREEN_NEEDS)
    assert _run(_args(plan, needs)) == 0
    # The run's own head is not the candidate's: every result it stamped is stale.
    assert _run(_args(plan, needs, run_head=OLD_HEAD)) == 1
    # Nor is a run that used an older head of a partner repository.
    assert _run(_args(plan, needs, run_partner=OLD_PARTNER)) == 1
    plan, needs = _inputs(tmp_path, ["lint", "test"], NEEDS)
    assert _run(_args(plan, needs)) == 1
    plan, needs = _inputs(tmp_path, [], GREEN_NEEDS)
    assert _run(_args(plan, needs)) == 1


def test_the_command_exits_2_on_input_it_cannot_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], subtests: object
) -> None:
    plan, needs = _inputs(tmp_path, ["lint"], GREEN_NEEDS)

    def file(name: str, data: bytes) -> str:
        path = tmp_path / name
        path.write_bytes(data)
        return str(path)

    bad_plan = file("bad-plan.json", b'{"lint": true}')
    empty_id_plan = file("empty-id-plan.json", b'["lint", ""]')
    deep_plan = file("deep-plan.json", b"[" * DEEP + b"]" * DEEP)
    bad_needs = file("bad-needs.json", b"{")
    not_utf8_needs = file(
        "latin-needs.json", b'{"lint": {"result": "success", "outputs": {"\xff": "1"}}}'
    )
    deep_needs = file("deep-needs.json", b"[" * DEEP + b"]" * DEEP)
    absent = str(tmp_path / "absent.json")
    green = _args(plan, needs)
    # `green` alone reads green, so each row's exit 2 comes from what the row changes.
    rows: list[tuple[str, list[str], str]] = [
        ("a plan file that does not exist", _args(absent, needs), "cannot read the plan"),
        ("a plan that is not a list of job ids", _args(bad_plan, needs), "must be a JSON list"),
        ("a plan with an empty job id", _args(empty_id_plan, needs), "must be a JSON list"),
        ("a plan nested too deeply to read", _args(deep_plan, needs), "nested too deeply"),
        ("a needs file that does not exist", _args(plan, absent), f"cannot read {absent}"),
        ("needs that are not JSON", _args(plan, bad_needs), "needs is not JSON"),
        ("needs that are not UTF-8", _args(plan, not_utf8_needs), "codec can't decode"),
        ("needs nested too deeply to read", _args(plan, deep_needs), "nested too deeply"),
        (
            "a candidate head that is not an object id",
            _args(plan, needs, head="main"),
            "head_sha must be a full object id",
        ),
        ("a partner with no revision", [*green, "--partner", "web"], "a partner is NAME=SHA"),
        (
            "a partner named twice",
            [*green, "--partner", f"apps={OLD_PARTNER}"],
            "partner apps is named twice",
        ),
        (
            "a run partner named twice",
            [*green, "--run-partner", f"apps={OLD_PARTNER}"],
            "partner apps is named twice",
        ),
        ("a head given twice", [*green, "--head", OLD_HEAD], "argument --head: given twice"),
        (
            "a run head given twice",
            [*green, "--run-head", OLD_HEAD],
            "argument --run-head: given twice",
        ),
        ("a plan given twice", [*green, "--plan", plan], "argument --plan: given twice"),
        ("needs given twice", [*green, "--needs", needs], "argument --needs: given twice"),
        ("no plan at all", green[2:], "required: --plan"),
        ("an abbreviated option", ["--pla", *green[1:]], "required: --plan"),
        ("-h among the arguments", [*green, "-h"], "unrecognized arguments: -h"),
        ("--help among the arguments", [*green, "--help"], "unrecognized arguments: --help"),
        ("--help alone", ["--help"], "the following arguments are required"),
    ]
    assert len(rows) == 21, "a refusal was dropped from the table"
    for name, args, why in rows:
        with row(subtests, case=name):
            capsys.readouterr()
            code = _run(args)
            err = capsys.readouterr().err
            assert code == 2, f"{name}: exit {code}\n{err}"
            assert why in err, f"{name}: refused for another reason\n{err}"


def test_the_command_writes_its_reasons_to_the_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    plan, needs = _inputs(tmp_path, ["lint", "test", "web"], NEEDS)
    assert _run(_args(plan, needs)) == 1
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier step\n"), "the summary was overwritten, not appended to"
    assert "red" in text and "`test`: failure" in text and "`web`: missing" in text, text


def test_the_command_writes_why_it_could_not_read_to_the_step_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _, needs = _inputs(tmp_path, ["lint"], GREEN_NEEDS)
    absent = str(tmp_path / "absent.json")
    assert _run(_args(absent, needs)) == 2
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("earlier step\n"), "the summary was overwritten, not appended to"
    assert (
        "### Verdict: unreadable input" in text and f"cannot read the plan {absent}" in text
    ), text


def test_a_reason_naming_a_key_that_is_not_text_still_reaches_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # JSON can spell a lone surrogate, which no UTF-8 output can hold as it is.
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    plan, needs = _inputs(tmp_path, '["lint", "\\ud800"]', GREEN_NEEDS)
    assert _run(_args(plan, needs)) == 1
    assert "`\\ud800`: missing" in summary.read_text(encoding="utf-8")


# ── the main-push reading: superseded, still red ──────────────────────────────────────────────
#
# On a push to main, a suite shard that GitHub's concurrency group replaced with a newer push's
# waiting shard is cancelled. ``tools/ci/superseded.py`` proves that from the API; this reading
# only names it. It is never green, and ``decide`` (every candidate's reading) never says it.

NEWER = "f" * 40
PUSHED = Identity(HEAD, {})


def pushed(key: str, result: str = "success") -> JobResult:
    return JobResult(key, result, PUSHED, 1)


#: (name, planned, results, proven superseded, the reasons, superseded_by)
MAIN_PUSH_ROWS: list[
    tuple[str, list[str], list[JobResult], dict[str, str], list[tuple[str, str]], str]
] = [
    (
        "a cancelled shard proven superseded",
        ["s1", "s2"],
        [pushed("s1"), pushed("s2", "cancelled")],
        {"s2": NEWER},
        [("s2", "superseded")],
        NEWER,
    ),
    (
        "a cancelled shard with no proof",
        ["s1", "s2"],
        [pushed("s1"), pushed("s2", "cancelled")],
        {},
        [("s2", "cancelled")],
        "",
    ),
    (
        "a failed shard named as superseded",
        ["s1", "s2"],
        [pushed("s1"), pushed("s2", "failure")],
        {"s2": NEWER},
        [("s2", "failure")],
        "",
    ),
    (
        "one superseded shard beside a failed one",
        ["s1", "s2"],
        [pushed("s1", "failure"), pushed("s2", "cancelled")],
        {"s2": NEWER},
        [("s1", "failure"), ("s2", "superseded")],
        "",
    ),
    (
        "two shards superseded by different commits",
        ["s1", "s2"],
        [pushed("s1", "cancelled"), pushed("s2", "cancelled")],
        {"s1": NEWER, "s2": OTHER},
        [("s1", "superseded"), ("s2", "superseded")],
        "",
    ),
    (
        "a cancelled shard proven superseded, missing another",
        ["s1", "s2"],
        [pushed("s2", "cancelled")],
        {"s2": NEWER},
        [("s1", "missing"), ("s2", "superseded")],
        "",
    ),
    (
        "a proven shard that never started, so left no record",
        ["s1", "s2"],
        [pushed("s1")],
        {"s2": NEWER},
        [("s2", "superseded")],
        NEWER,
    ),
    (
        "its stage, cancelled in needs, when every planned fault is superseded by one commit",
        ["s1", "s2"],
        [pushed("s1"), pushed("test", "cancelled")],
        {"s2": NEWER},
        [("s2", "superseded"), ("test", "superseded")],
        NEWER,
    ),
    (
        "a failed job beside its stage stays failed, and the stage reads superseded",
        ["s1", "s2"],
        [pushed("s1"), pushed("test", "cancelled"), pushed("tree-check", "failure")],
        {"s2": NEWER},
        [("s2", "superseded"), ("test", "superseded"), ("tree-check", "unplanned failure")],
        "",
    ),
    (
        "its stage stays cancelled beside a missing key no proof names",
        ["s1", "s2"],
        [pushed("test", "cancelled")],
        {"s2": NEWER},
        [("s1", "missing"), ("s2", "superseded"), ("test", "unplanned cancelled")],
        "",
    ),
    (
        "its stage stays cancelled when two commits replaced the legs",
        ["s1", "s2"],
        [pushed("test", "cancelled")],
        {"s1": NEWER, "s2": OTHER},
        [("s1", "superseded"), ("s2", "superseded"), ("test", "unplanned cancelled")],
        "",
    ),
]


MAIN = {"event": "push", "ref": "refs/heads/main"}


def test_a_superseded_shard_on_main_reads_red_under_its_own_name(subtests: object) -> None:
    assert len(MAIN_PUSH_ROWS) == 11, "a row was dropped from the table"
    for name, planned, results, proven, reasons, by in MAIN_PUSH_ROWS:
        with row(subtests, case=name):
            got = verdict.decide_main_push(planned, results, PUSHED, proven, **MAIN)
            assert not got.green, f"{name}: read green"
            assert [(r.key, r.kind) for r in got.reasons] == reasons, f"{name}: {got.reasons}"
            assert got.superseded_by == by, f"{name}: {got.superseded_by!r}"


def test_a_green_push_is_green_whatever_the_proof_names() -> None:
    got = verdict.decide_main_push(["s1"], [pushed("s1")], PUSHED, {"s1": NEWER}, **MAIN)
    assert got.green and got.reasons == () and got.superseded_by == ""


def test_the_main_push_reading_refuses_a_proof_it_cannot_trust(subtests: object) -> None:
    rows: list[tuple[str, dict[object, object], str]] = [
        ("a newer commit that is the pushed one", {"s1": HEAD}, "is the pushed commit"),
        ("a newer commit that is not an object id", {"s1": "abc123"}, "full object id"),
        ("a newer commit with a trailing newline", {"s1": NEWER + "\n"}, "full object id"),
        ("a newer commit of hex but 39 characters", {"s1": NEWER[:39]}, "full object id"),
        ("a newer commit in uppercase hex", {"s1": NEWER.upper()}, "full object id"),
        ("an empty key", {"": NEWER}, "non-empty"),
    ]
    assert len(rows) == 6, "a refusal was dropped from the table"
    for name, proven, why in rows:
        with row(subtests, case=name):
            with pytest.raises(Refused, match=why):
                verdict.decide_main_push(
                    ["s1"],
                    [pushed("s1", "cancelled")],
                    PUSHED,
                    proven,  # type: ignore[arg-type]
                    **MAIN,
                )


#: (name, event, ref): only a push to main may read a shard as superseded.
NOT_MAIN_PUSHES: list[tuple[str, str, str]] = [
    ("a pull request", "pull_request", "refs/pull/41/merge"),
    ("a pull_request_target run on main's ref", "pull_request_target", "refs/heads/main"),
    ("a push to a feature branch", "push", "refs/heads/feature-a"),
    ("a tag named main", "push", "refs/tags/main"),
    ("a scheduled run on main", "schedule", "refs/heads/main"),
]


def test_the_main_push_reading_refuses_any_other_run(subtests: object) -> None:
    assert len(NOT_MAIN_PUSHES) == 5, "a row was dropped from the table"
    for name, event, ref in NOT_MAIN_PUSHES:
        with row(subtests, case=name):
            with pytest.raises(Refused, match="only a push to refs/heads/main"):
                verdict.decide_main_push(
                    ["s1"], [pushed("s1", "cancelled")], PUSHED, {"s1": NEWER}, event=event, ref=ref
                )


def test_a_candidates_cancelled_job_is_never_superseded(subtests: object) -> None:
    # `decide` is every candidate's reading: it takes no proof, so nothing can argue a pull
    # request's cancelled job into the word, and no red row produces it.
    import inspect

    assert "superseded" not in inspect.signature(decide).parameters
    for name, planned, results, _key, _kind in RED_ROWS:
        with row(subtests, case=name):
            got = decide(planned, results, CANDIDATE)
            assert all(r.kind != "superseded" for r in got.reasons), f"{name}: {got.reasons}"
            assert got.superseded_by == "", f"{name}: {got.superseded_by!r}"
    got = decide(["test"], [as_("test", "cancelled")], CANDIDATE)
    assert [(r.key, r.kind) for r in got.reasons] == [("test", "cancelled")]


def test_a_trusted_job_can_run_the_script_on_its_own(tmp_path: Path, subtests: object) -> None:
    # `python3 -I tools/ci/verdict.py`: isolated mode, no package on the path, so the script must
    # import nothing but the standard library.
    script = verdict.__file__
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
    green_plan, green_needs = _inputs(tmp_path, ["lint", "test"], GREEN_NEEDS)
    red_dir = tmp_path / "red"
    red_dir.mkdir()
    red_plan, red_needs = _inputs(red_dir, ["lint", "test"], NEEDS)
    rows = [
        ("green", _args(green_plan, green_needs), 0),
        ("red", _args(red_plan, red_needs), 1),
        ("unreadable", _args(str(tmp_path / "absent.json"), green_needs), 2),
        ("a help flag on green input", [*_args(green_plan, green_needs), "--help"], 2),
    ]
    assert len(rows) == 4
    for name, args, code in rows:
        with row(subtests, case=name):
            done = subprocess.run(
                [sys.executable, "-I", script, *args],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert done.returncode == code, f"{name}: {done.returncode}\n{done.stdout}{done.stderr}"
