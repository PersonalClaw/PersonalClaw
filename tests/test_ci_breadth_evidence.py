"""``tools/ci/breadth_evidence.py``: the breadth record, its digest, reuse and the leg issues."""

from __future__ import annotations

import subprocess
import urllib.parse
from pathlib import Path

import pytest

from tools.ci import breadth_evidence as be

INPUTS = {
    "tree": "d" * 40,
    "image": "ubuntu24",
    "image_version": "20261012.1.0",
    "arch": "X64",
    "python": "3.13.16",
    "uv": "0.13.1",
    "node": "24.21.0",
    "external": {},
}


def _record(**legs: dict) -> dict:
    return {"version": be.VERSION, "run_id": 1, "legs": legs}


def _leg(result: str = be.GREEN, **inputs) -> dict:
    return {"inputs": {**INPUTS, **inputs}, "result": result, "started": "t0", "ended": "t1"}


def test_a_record_is_read_back_only_under_its_own_digest() -> None:
    record = _record(**{"ubuntu-3.13": _leg()})
    data = be.canonical(record)
    named = be.digest(data)
    assert be.verify(data, named) == record
    assert be.artifact_name(named) == f"breadth-evidence-{named}"
    with pytest.raises(be.Refused):
        be.verify(data, "0" * 64)
    with pytest.raises(be.Refused, match="does not hash"):
        be.verify(data.replace(b"success", b"failure"), named)


def test_the_byte_form_is_canonical() -> None:
    one = {"legs": {"a": _leg()}, "version": 1}
    other = {"version": 1, "legs": {"a": dict(reversed(list(_leg().items())))}}
    assert be.canonical(one) == be.canonical(other)


@pytest.mark.parametrize(
    "bad",
    [b"not json", b"[]", b'{"version": 2, "legs": {"a": {}}}', b'{"version": 1, "legs": {}}'],
    ids=["not json", "not an object", "another version", "no legs"],
)
def test_a_record_of_the_wrong_shape_is_refused_even_under_its_digest(bad: bytes) -> None:
    with pytest.raises(be.Refused):
        be.verify(bad, be.digest(bad))


@pytest.mark.parametrize(
    "changed",
    [
        {"tree": "e" * 40},
        {"python": "3.13.17"},
        {"uv": "0.13.2"},
        {"node": "24.22.0"},
        {"image_version": "20261019.1.0"},
        {"image": "ubuntu26"},
        {"arch": "ARM64"},
        {"external": {"installer_sha256": "f" * 64}},
    ],
    ids=lambda changed: next(iter(changed)),
)
def test_any_changed_input_runs_the_leg_again(changed: dict) -> None:
    assert be.reusable("ubuntu-3.13", _leg(), INPUTS)
    assert not be.reusable("ubuntu-3.13", _leg(), {**INPUTS, **changed})


def test_a_missing_input_is_a_difference_not_a_match() -> None:
    partial = {k: v for k, v in INPUTS.items() if k != "node"}
    assert not be.reusable("ubuntu-3.13", _leg(), partial)
    then = _leg()
    del then["inputs"]["node"]
    assert not be.reusable("ubuntu-3.13", then, partial)


def test_a_leg_that_may_not_be_reused_always_runs() -> None:
    for leg_id in be.NON_REUSABLE:
        assert not be.reusable(leg_id, _leg(), INPUTS), leg_id


#: breadth.yml's suite legs: each runs the suite, so the security-marked modules.
SUITE_LEGS = ("ubuntu-24.04-3.13", "macos-26-3.12", "ubuntu-24.04-arm-3.13", "coverage")


@pytest.mark.parametrize("leg_id", SUITE_LEGS)
def test_a_suite_leg_never_carries_a_result_whatever_matches(leg_id: str) -> None:
    """A security result is never carried from an earlier night: the suite runs the security-
    marked modules, so each suite leg runs every night, red or green, on identical inputs."""
    assert leg_id in be.NON_REUSABLE and leg_id in be.SHARDS
    for result in (be.GREEN, be.RED):
        found = {f"{leg_id}/1": {"entry": _leg(result), "run_id": 1, "digest": "a" * 64}}
        assert be.decide(f"{leg_id}/1", INPUTS, found) is None, (leg_id, result)


#: The legs that may carry an earlier result: the rails on macOS, and the whole-suite
#: collection, which only collects and so runs no security-marked test.
CARRYING_LEGS = frozenset({"rails-macos-26-3.12", "collection-parses"})


def test_the_rails_on_macos_and_the_collection_are_the_legs_that_carry() -> None:
    assert set(be.SHARDS) - be.NON_REUSABLE == CARRYING_LEGS
    for leg_id in sorted(CARRYING_LEGS):
        found = {f"{leg_id}/1": {"entry": _leg(), "run_id": 1, "digest": "a" * 64}}
        assert be.decide(f"{leg_id}/1", INPUTS, found) is not None, leg_id


def test_a_red_on_identical_inputs_is_carried_forward_red_and_names_its_record() -> None:
    red = _leg(be.RED)
    assert be.reusable("macos-3.12", red, INPUTS)
    leg = be.carried(red, run_id=41, record_digest="a" * 64)
    assert leg["result"] == be.RED
    assert leg["reused_from"] == {"run_id": 41, "digest": "a" * 64}


def test_a_carried_result_is_not_carried_again() -> None:
    """Reuse follows to the run that executed the leg, never a chain of carries."""
    once = be.carried(_leg(), run_id=41, record_digest="a" * 64)
    assert not be.reusable("macos-3.12", once, INPUTS)


def _every_shard(**overrides: dict) -> dict:
    """A record with a green, executed entry for every shard breadth.yml runs."""
    legs = {key: _leg() for key in be.expected_keys()}
    legs.update(overrides)
    return _record(**legs)


def test_a_run_whose_legs_were_reused_does_not_satisfy_a_release_alone() -> None:
    assert be.satisfies_release_alone(_every_shard())
    carried = be.carried(_leg(), 41, "a" * 64)
    assert not be.satisfies_release_alone(_every_shard(**{"macos-26-3.12/1": carried}))
    assert not be.satisfies_release_alone(_every_shard(**{"audit/1": _leg(be.RED)}))


def test_a_record_that_lacks_a_shard_does_not_satisfy_a_release_alone() -> None:
    whole = _every_shard()
    del whole["legs"]["macos-26-3.12/3"]
    assert not be.satisfies_release_alone(whole)
    only_three = _record(**{"security/1": _leg(), "audit/1": _leg(), "install-smoke/1": _leg()})
    assert not be.satisfies_release_alone(only_three)


def test_a_shard_that_wrote_no_entry_is_recorded_red_and_never_carried() -> None:
    entries = {k: _leg() for k in be.expected_keys() if k != "macos-26-3.12/3"}
    filled = be.fill_missing(entries)
    assert filled["macos-26-3.12/3"]["result"] == be.RED
    assert filled["macos-26-3.12/3"]["missing"] is True
    _, actions = be.settle_record(filled, {}, run_id=7, tree="d" * 40)
    macos = next(a for a in actions if a.leg == "macos-26-3.12")
    assert macos.action == "open" and macos.red == ("macos-26-3.12/3",)
    earlier = {
        "macos-26-3.12/3": {"entry": filled["macos-26-3.12/3"], "run_id": 7, "digest": "a" * 64}
    }
    assert be.decide("macos-26-3.12/3", INPUTS, earlier) is None, "empty inputs never match"


def test_a_missing_red_shard_does_not_close_the_open_issue() -> None:
    """The night after a red, the red shard writes nothing and the rest are green on new inputs."""
    red_inputs = frozenset({be.inputs_key({"macos-26-3.12/3": INPUTS})})
    tonight = {k: _leg(tree="e" * 40) for k in be.expected_keys() if k != "macos-26-3.12/3"}
    _, actions = be.settle_record(
        be.fill_missing(tonight), {"macos-26-3.12": red_inputs}, run_id=8, tree="e" * 40
    )
    assert next(a for a in actions if a.leg == "macos-26-3.12").action == "update"


def test_a_leg_whose_issue_cannot_be_read_keeps_its_greens_and_touches_no_issue() -> None:
    entries = {"macos-26-3.12/1": _leg(), "macos-26-3.12/2": _leg()}
    record, actions = be.settle_record(
        entries, {}, run_id=9, tree="d" * 40, unsettled=frozenset({"macos-26-3.12"})
    )
    assert {e["result"] for e in record["legs"].values()} == {be.GREEN}, "a lookup reds no green"
    assert all(e["unsettled"] is True for e in record["legs"].values())
    assert not any(be.measured(e) for e in record["legs"].values()), "so none is carried"
    assert actions[0].action == "none" and actions[0].red == ()


def test_a_red_then_a_green_on_changed_inputs_closes_the_issue() -> None:
    first = be.settle(be.RED, INPUTS, None)
    assert first == be.Settled(be.RED, "open")
    fixed = be.settle(be.GREEN, {**INPUTS, "tree": "e" * 40}, frozenset({be.inputs_key(INPUTS)}))
    assert fixed == be.Settled(be.GREEN, "close")


def test_a_red_then_a_green_on_the_same_inputs_is_a_flake_that_stays_red() -> None:
    reds = frozenset({be.inputs_key(INPUTS)})
    assert be.settle(be.RED, INPUTS, reds) == be.Settled(be.RED, "update")
    flake = be.settle(be.GREEN, INPUTS, reds)
    assert flake == be.Settled(be.RED, "flake"), "the record keeps the leg red, the issue open"


def test_a_green_with_no_open_issue_does_nothing_and_the_title_is_exact() -> None:
    assert be.settle(be.GREEN, INPUTS, None) == be.Settled(be.GREEN, "none")
    assert be.issue_title("macos-26-3.12") == "breadth: macos-26-3.12 is red"
    with pytest.raises(ValueError):
        be.settle("skipped", INPUTS, None)


# ── what breadth.yml runs: the earlier record, a shard's decision, the record and the issues ──


class _Source:
    """Completed runs on main, newest first, each with its artifacts."""

    def __init__(self, runs: dict[int, dict[str, bytes]], *, deleted: tuple[int, ...] = ()) -> None:
        self.by_run, self.deleted = runs, set(deleted)

    def runs(self) -> list[int]:
        return sorted(self.by_run, reverse=True)

    def artifacts(self, run_id: int) -> list[str]:
        """A run the API no longer has refuses (a 404); a run it has lists what it holds."""
        if run_id in self.deleted:
            raise subprocess.CalledProcessError(1, ["gh", "api", f"runs/{run_id}/artifacts"])
        return list(self.by_run.get(run_id, {}))

    def record(self, run_id: int, name: str) -> bytes:
        return self.by_run[run_id][name]


def _stored(record: dict) -> tuple[str, bytes]:
    data = be.canonical(record)
    return be.artifact_name(be.digest(data)), data


def test_the_earlier_results_follow_a_carried_leg_to_the_run_that_executed_it() -> None:
    ran = _record(**{"rails-macos-26-3.12/1": _leg(be.RED)})
    name, data = _stored(ran)
    first_digest = name[len(be.PREFIX) :]
    carried_record = _record(
        **{"rails-macos-26-3.12/1": be.carried(_leg(be.RED), 10, first_digest)}
    )
    later_name, later_data = _stored(carried_record)
    found, said = be.previous(_Source({10: {name: data}, 11: {later_name: later_data}}))
    assert found["rails-macos-26-3.12/1"]["run_id"] == 10, "the origin, not the run that carried it"
    assert found["rails-macos-26-3.12/1"]["digest"] == first_digest
    assert "run 11" in said
    entry = be.decide("rails-macos-26-3.12/1", INPUTS, found)
    assert entry is not None and entry["result"] == be.RED
    assert entry["reused_from"] == {"run_id": 10, "digest": first_digest}


def test_a_newest_record_under_the_wrong_digest_is_refused() -> None:
    name, data = _stored(_record(**{"ubuntu-24.04-3.13/1": _leg()}))
    with pytest.raises(be.Refused):
        be.previous(_Source({11: {name: data.replace(b"success", b"failure")}}))


def test_a_shard_with_changed_inputs_or_no_earlier_result_runs() -> None:
    name, data = _stored(_record(**{"ubuntu-24.04-3.13/1": _leg()}))
    found, _ = be.previous(_Source({11: {name: data}}))
    assert be.decide("ubuntu-24.04-3.13/1", {**INPUTS, "uv": "0.13.2"}, found) is None
    assert be.decide("ubuntu-24.04-3.13/2", INPUTS, found) is None
    assert be.decide("canary/1", INPUTS, {"canary/1": found["ubuntu-24.04-3.13/1"]}) is None


class _Issues:
    def __init__(self, open_: dict[str, tuple[int, str]] | None = None) -> None:
        self.open = dict(open_ or {})
        self.log: list[tuple[str, object]] = []

    def find(self, title: str):
        return self.open.get(title)

    def create(self, title: str, body: str) -> None:
        self.log.append(("create", title))
        self.open[title] = (1, body)

    def comment(self, number: int, body: str) -> None:
        self.log.append(("comment", number))

    def close(self, number: int, body: str) -> None:
        self.log.append(("close", number))


def test_one_issue_per_red_leg_lists_its_red_shards_and_a_fix_closes_it() -> None:
    entries = {"macos-26-3.12/1": _leg(be.RED), "macos-26-3.12/2": _leg(), "audit/1": _leg()}
    record, actions = be.settle_record(entries, {}, run_id=5, tree="d" * 40)
    issues = _Issues()
    done = be.apply(actions, issues, run_url="https://example.com/r/5", record_digest="a" * 64)
    assert done == ["macos-26-3.12: open"], "exactly one issue, and none for the green leg"
    title = be.issue_title("macos-26-3.12")
    assert issues.log == [("create", title)]
    assert "`macos-26-3.12/1`" in issues.open[title][1]
    red_inputs = be.open_red_inputs(issues.open[title][1])
    assert red_inputs is not None
    fixed = {"macos-26-3.12/1": _leg(tree="e" * 40), "macos-26-3.12/2": _leg(tree="e" * 40)}
    _, actions = be.settle_record(fixed, {"macos-26-3.12": red_inputs}, run_id=6, tree="e" * 40)
    be.apply(actions, issues, run_url="https://example.com/r/6", record_digest="b" * 64)
    assert issues.log[-1] == ("close", 1)


def test_a_green_on_the_reds_inputs_is_recorded_red_and_keeps_the_issue_open() -> None:
    red = {"macos-26-3.12/1": _leg(be.RED), "macos-26-3.12/2": _leg()}
    _, actions = be.settle_record(red, {}, run_id=5, tree="d" * 40)
    flaky = {"macos-26-3.12/1": _leg(), "macos-26-3.12/2": _leg()}
    record, actions = be.settle_record(
        flaky, {"macos-26-3.12": frozenset({actions[0].inputs})}, run_id=6, tree="d" * 40
    )
    assert actions[0].action == "flake"
    assert {e["result"] for e in record["legs"].values()} == {be.RED}
    issues = _Issues({be.issue_title("macos-26-3.12"): (3, "")})
    be.apply(actions, issues, run_url="https://example.com/r/6", record_digest="c" * 64)
    assert issues.log == [("comment", 3)], "commented with the run, not closed"


def test_a_leg_carried_on_every_shard_changes_no_issue() -> None:
    entries = {"arm-3.13/1": be.carried(_leg(be.RED), 4, "a" * 64)}
    _, actions = be.settle_record(entries, {"arm-3.13": None}, run_id=5, tree="d" * 40)
    assert actions[0].action == "none" and actions[0].carried


_BOT = {"login": "github-actions[bot]", "type": "Bot"}
_SOMEONE = {"login": "someone", "type": "User"}


def test_only_the_bots_own_issue_and_comments_carry_the_inputs_marker() -> None:
    """On a public repository anyone may comment: a marker from anyone else is not read."""
    red = be.inputs_key({"macos-26-3.12/1": INPUTS})
    issue = {"user": _BOT, "body": f"{be.MARKER}{red}\n"}
    planted = [{"user": _SOMEONE, "body": f"{be.MARKER}{'0' * 64}\n"}]
    text = be.bot_text(issue, planted)
    assert text is not None and be.open_red_inputs(text) == frozenset({red})
    later = [{"user": _BOT, "body": f"{be.MARKER}{'1' * 64}\n"}]
    assert be.open_red_inputs(be.bot_text(issue, later) or "") == frozenset({red, "1" * 64})
    assert be.bot_text({"user": _SOMEONE, "body": f"{be.MARKER}{red}"}, []) is None
    impostor = {"login": "github-actions[bot]", "type": "User"}
    assert be.bot_text({"user": impostor, "body": f"{be.MARKER}{red}"}, []) is None


def test_a_crashed_decision_has_no_earlier_results_and_runs_the_shard(
    tmp_path, monkeypatch
) -> None:
    """freshness failed or this shard's download did: no previous.json, and the shard runs."""
    inputs = tmp_path / "inputs.json"
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    entry = tmp_path / "entry" / "ubuntu-24.04-3.13__1.json"
    code = be.main(
        [
            "decide",
            "--key",
            "ubuntu-24.04-3.13/1",
            "--inputs",
            str(inputs),
            "--previous",
            str(tmp_path / "missing" / "previous.json"),
            "--out",
            str(entry),
        ]
    )
    assert code == 0
    assert out.read_text(encoding="utf-8").splitlines() == ["run=true"]
    assert not entry.exists(), "nothing carried, so the shard writes its own entry when it ran"


# ── the commands themselves, driven through main() with a fake ``gh`` ─────────────────────────


class _Gh:
    """``gh`` as the commands call it, answering in the REST API's shapes."""

    def __init__(self) -> None:
        self.issues: list[dict] = []
        self.comments: dict[int, list[dict]] = {}
        self.queries: list[str] = []
        self.runs: dict[str, list[int]] = {}
        self.records: dict[int, dict[str, bytes]] = {}
        self.branches: list[str] = []
        #: The search fails, and gh says this on its stderr.
        self.unreadable = ""
        #: The search's pages as GitHub answers them, in place of the open issues'.
        self.pages: list[dict] | None = None
        #: How many issues each page of the search holds.
        self.page_size = 100
        self.comment_reads: list[int] = []
        #: The REST listing's order is not set: newest first is one it may use.
        self.newest_first = False
        self.listings: list[tuple[str, ...]] = []
        #: Per run: each job as its latest attempt left it, (attempt, conclusion, name); None
        #: makes the jobs read fail.
        self.jobs: dict[int, list[tuple[int, str, str]] | None] = {}

    def open_issue(self, title: str, body: str, user: dict = _BOT) -> int:
        number = len(self.issues) + 1
        self.issues.append(
            {"number": number, "title": title, "user": user, "body": body, "state": "open"}
        )
        self.comments[number] = []
        return number

    def __call__(self, *args: str) -> str:
        if args[0] == "issue":
            body = args[args.index("--body" if args[1] != "close" else "--comment") + 1]
            if args[1] == "create":
                self.open_issue(args[args.index("--title") + 1], body)
            elif args[1] == "comment":
                self.comments[int(args[2])].append({"user": _BOT, "body": body})
            else:
                self.issues[int(args[2]) - 1]["state"] = "closed"
            return ""
        if args[:2] == ("run", "download"):
            folder = Path(args[args.index("--dir") + 1])
            (folder / "record.json").write_bytes(self.records[int(args[2])][args[6]])
            return ""
        path = next(a for a in args[1:] if not a.startswith("-"))
        if path.startswith("search/issues"):
            assert "--paginate" in args and "--slurp" in args, "every page of the search is read"
            self.queries.append(urllib.parse.unquote(path))
            if self.unreadable:
                raise be.GhError(1, ["gh", *args], "", self.unreadable)
            if self.pages is not None:
                return be.json.dumps(self.pages)
            items = [i for i in self.issues if i["state"] == "open"]
            chunks = [items[n : n + self.page_size] for n in range(0, len(items), self.page_size)]
            return be.json.dumps(
                [
                    {"total_count": len(items), "incomplete_results": False, "items": chunk}
                    for chunk in chunks or [[]]
                ]
            )
        if path.endswith("/artifacts?per_page=100"):
            self.listings.append(args)
            listed = [
                f"{i} {name}"
                for i, name in enumerate(
                    self.records.get(int(path.split("/runs/")[1].split("/")[0]), {}), start=1
                )
            ]
            return "\n".join(reversed(listed) if self.newest_first else listed)
        if "/jobs?filter=latest" in path:
            jobs = self.jobs.get(int(path.split("/runs/")[1].split("/")[0]))
            if jobs is None:
                raise subprocess.CalledProcessError(1, ["gh", *args])
            return "\n".join(f"{a}\t{c}\t{n}" for a, c, n in jobs)
        if "/comments?" in path:
            number = int(path.split("/issues/")[1].split("/")[0])
            self.comment_reads.append(number)
            return be.json.dumps([self.comments.get(number, [])])
        if "/breadth.yml/runs?" in path:
            branch = urllib.parse.unquote(path.split("branch=")[1].split("&")[0])
            self.branches.append(branch)
            return " ".join(str(r) for r in self.runs.get(branch, []))
        raise AssertionError(f"the commands asked gh for {args}")


def _night(
    tmp_path,
    monkeypatch,
    gh: _Gh,
    entries: dict,
    run_id: int,
    *,
    issues: bool = True,
    report_code: int = 0,
):
    """One run's ``report``, then its ``issues`` step, as breadth.yml runs them on main."""
    monkeypatch.setattr(be, "_gh", gh)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    night = tmp_path / str(run_id)
    (night / "legs").mkdir(parents=True)
    for key, entry in entries.items():
        (night / "legs" / f"{key.replace('/', '__')}.json").write_text(
            be.json.dumps(entry), "utf-8"
        )
    record, actions = night / "record.json", night / "actions.json"
    argv = ["report", "--repo", "o/r", "--legs", str(night / "legs"), "--run-id", str(run_id)]
    assert (
        be.main([*argv, "--tree", "d" * 40, "--record", str(record), "--actions", str(actions)])
        == report_code
    )
    if issues:
        url = f"https://example.com/r/{run_id}"
        argv = ["issues", "--repo", "o/r", "--actions", str(actions), "--run-url", url]
        assert be.main([*argv, "--digest", "a" * 64]) == 0
    done = {a["leg"]: a["action"] for a in be.json.loads(actions.read_text("utf-8"))}
    return be.json.loads(record.read_bytes()), done


def _all_green(**overrides: dict) -> dict:
    return {**{key: _leg() for key in be.expected_keys()}, **overrides}


def test_a_shard_that_wrote_nothing_never_hides_the_inputs_its_red_ran_on(
    tmp_path, monkeypatch
) -> None:
    """Red, then the red shard writes nothing, then a green on the red's own inputs: a flake."""
    gh = _Gh()
    _, done = _night(tmp_path, monkeypatch, gh, _all_green(**{"macos-26-3.12/3": _leg(be.RED)}), 1)
    assert done["macos-26-3.12"] == "open"
    lost = {k: v for k, v in _all_green().items() if k != "macos-26-3.12/3"}
    _, done = _night(tmp_path, monkeypatch, gh, lost, 2)
    assert done["macos-26-3.12"] == "update", "the shard that wrote nothing is red"
    record, done = _night(tmp_path, monkeypatch, gh, _all_green(), 3)
    assert done["macos-26-3.12"] == "flake"
    assert record["legs"]["macos-26-3.12/3"]["result"] == be.RED
    assert [i["state"] for i in gh.issues] == ["open"]


def test_a_red_on_unknown_inputs_never_hides_the_inputs_an_earlier_red_ran_on(
    tmp_path, monkeypatch
) -> None:
    """install-smoke red, then red on an unreadable installer, then green on the first's inputs."""
    gh = _Gh()
    served = {"installer_sha256": "f" * 64, "night": "2026-10-17"}
    unknown = {**served, "installer_sha256": "unreadable"}
    _night(
        tmp_path,
        monkeypatch,
        gh,
        _all_green(**{"install-smoke/1": _leg(be.RED, external=served)}),
        1,
    )
    _night(
        tmp_path,
        monkeypatch,
        gh,
        _all_green(**{"install-smoke/1": _leg(be.RED, external=unknown)}),
        2,
    )
    record, done = _night(
        tmp_path, monkeypatch, gh, _all_green(**{"install-smoke/1": _leg(external=served)}), 3
    )
    assert done["install-smoke"] == "flake"
    assert record["legs"]["install-smoke/1"]["result"] == be.RED


#: What gh says when GitHub refuses the search: the words the report's error must carry.
_REFUSED = "gh: API rate limit exceeded for installation ID 1. (HTTP 403)"


def test_an_unreadable_search_fails_the_report_once_and_reds_no_green(
    tmp_path, monkeypatch, capsys
) -> None:
    gh = _Gh()
    gh.unreadable = _REFUSED
    tonight = _all_green(**{"macos-26-3.12/2": _leg(be.RED)})
    record, done = _night(tmp_path, monkeypatch, gh, tonight, 1, issues=False, report_code=1)
    said = capsys.readouterr().out
    assert said.count("### breadth issues unread") == 1, "one error for the whole run"
    assert _REFUSED in said, "the error carries gh's own words"
    assert "is recorded red" not in said, "no leg is named red for the lookup"
    assert len(gh.queries) == 1
    green, red = record["legs"]["macos-26-3.12/1"], record["legs"]["macos-26-3.12/2"]
    assert green["result"] == be.GREEN and green["unsettled"] is True
    assert red["result"] == be.RED and not red.get("unsettled"), "a measured red stays a red"
    measured_green = {k for k, e in tonight.items() if e["result"] == be.GREEN}
    assert {k for k, e in record["legs"].items() if e["result"] == be.GREEN} == measured_green
    assert done["macos-26-3.12"] == "open", "a measured red would open its issue"
    assert done["ubuntu-24.04-3.13"] == "none"
    assert gh.issues == [], "no issue is opened, updated or closed"
    # Carrying, on the one leg that may carry: an unsettled green runs again, a measured red is
    # carried red.
    assert record["legs"]["rails-macos-26-3.12/1"]["unsettled"] is True
    name, data = _stored(record)
    found, _ = be.previous(_Source({1: {name: data}}))
    assert (
        be.decide("rails-macos-26-3.12/1", INPUTS, found) is None
    ), "an unsettled green runs again"
    red_night = _all_green(**{"rails-macos-26-3.12/1": _leg(be.RED)})
    record, _ = _night(tmp_path, monkeypatch, gh, red_night, 2, issues=False, report_code=1)
    assert not record["legs"]["rails-macos-26-3.12/1"].get("unsettled")
    name, data = _stored(record)
    found, _ = be.previous(_Source({2: {name: data}}))
    carried = be.decide("rails-macos-26-3.12/1", INPUTS, found)
    assert carried is not None and carried["result"] == be.RED


def test_an_unsettled_green_never_satisfies_a_release() -> None:
    record, _ = be.settle_record(
        _all_green(), {}, run_id=9, tree="d" * 40, unsettled=frozenset({"audit"})
    )
    assert record["legs"]["audit/1"]["result"] == be.GREEN
    assert not be.satisfies_release_alone(record), "a green no one could settle is not evidence"
    record, _ = be.settle_record(_all_green(), {}, run_id=9, tree="d" * 40)
    assert be.satisfies_release_alone(record)


def test_every_legs_issue_is_found_by_one_search(tmp_path, monkeypatch) -> None:
    """Twenty-odd legs, one ``search/issues`` call a step, and only the bot's issues' comments."""
    gh = _Gh()
    gh.page_size = 2  # the open issues span several pages
    red_legs = ["audit", "canary", "coverage", "macos-26-3.12", "ubuntu-24.04-3.13"]
    for leg in red_legs:
        gh.open_issue(be.issue_title(leg), f"{be.MARKER}{'0' * 64}\n")
    someone = gh.open_issue(be.issue_title("security"), "", user=_SOMEONE)
    tonight = _all_green(**{f"{leg}/1": _leg(be.RED) for leg in red_legs})
    assert len({be.leg_of(k) for k in tonight}) >= 20
    _, done = _night(tmp_path, monkeypatch, gh, tonight, 1)
    assert len(gh.queries) == 2, "one search for the report and one for the issues step"
    assert "author:app/github-actions" in gh.queries[0]
    assert {done[leg] for leg in red_legs} == {"update"}, "each red leg found its own issue"
    assert someone not in gh.comment_reads, "only the bot's issues are read"
    assert len(gh.comment_reads) == 2 * len(red_legs), "each bot issue's comments once a step"
    assert [i["number"] for i in gh.issues if i["state"] == "open"] == [1, 2, 3, 4, 5, 6]


def test_a_leg_whose_issue_is_closed_or_missing_is_treated_as_before(tmp_path, monkeypatch) -> None:
    gh = _Gh()
    closed = gh.open_issue(be.issue_title("audit"), f"{be.MARKER}{'0' * 64}\n")
    gh.issues[closed - 1]["state"] = "closed"
    gh.open_issue(be.issue_title("canary"), "", user=_SOMEONE)
    tonight = _all_green(**{"audit/1": _leg(be.RED), "canary/1": _leg(be.RED)})
    _, done = _night(tmp_path, monkeypatch, gh, tonight, 1)
    assert done["audit"] == "open" and done["canary"] == "open" and done["security"] == "none"
    opened = [i for i in gh.issues if i["user"] == _BOT and i["state"] == "open"]
    assert sorted(i["title"] for i in opened) == [
        be.issue_title("audit"),
        be.issue_title("canary"),
    ], "a closed issue and someone else's are no leg's: the bot opens its own"
    fixed = {key: _leg(tree="e" * 40) for key in be.expected_keys()}
    record, done = _night(tmp_path, monkeypatch, gh, fixed, 2)
    assert done["audit"] == "close" and done["canary"] == "close"
    assert record["legs"]["audit/1"]["result"] == be.GREEN


def test_a_failed_gh_call_names_what_gh_said(monkeypatch) -> None:
    def refused(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", f"{_REFUSED}\n")

    monkeypatch.setattr(be.subprocess, "run", refused)
    with pytest.raises(subprocess.CalledProcessError) as raised:
        be._gh("api", "search/issues?q=x")
    assert _REFUSED in str(raised.value)
    with pytest.raises(be.IssuesUnread, match="HTTP 403"):
        be.GhIssues("o/r").read()


@pytest.mark.parametrize(
    ("created", "night"),
    [
        ("2026-10-17T09:37:00Z", "2026-10-17"),
        ("2026-10-17T09:36:59Z", "2026-10-16"),
        ("2026-10-17T23:59:00Z", "2026-10-17"),
        ("2026-10-18T00:01:00Z", "2026-10-17"),
    ],
)
def test_a_runs_night_is_its_scheduled_slot_never_its_attempts_clock(
    created: str, night: str, tmp_path, monkeypatch
) -> None:
    """A rerun across midnight UTC keeps its run's night, so its green on a red's inputs is a
    flake; only the next scheduled slot begins another night."""
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert be.main(["night", "--created-at", created]) == 0
    assert out.read_text(encoding="utf-8").splitlines() == [f"night={night}"]


def test_read_asks_once_for_the_bots_issues_and_refuses_pages_it_cannot_trust(
    monkeypatch,
) -> None:
    gh = _Gh()
    monkeypatch.setattr(be, "_gh", gh)
    title = be.issue_title("macos-26-3.12")
    gh.open_issue(title, f"{be.MARKER}{'0' * 64}\n", user=_SOMEONE)
    number = gh.open_issue(title, f"{be.MARKER}{'1' * 64}\n")
    later = gh.open_issue(title, f"{be.MARKER}{'3' * 64}\n")
    gh.comments[number].append({"user": _SOMEONE, "body": f"{be.MARKER}{'2' * 64}\n"})
    issues = be.GhIssues("o/r")
    found = issues.find(title)
    assert found is not None and found[0] == number, "the bot's older issue is the leg's"
    assert later != number
    assert be.open_red_inputs(found[1]) == frozenset({"1" * 64})
    assert issues.find(be.issue_title("audit")) is None
    assert len(gh.queries) == 1, "one search, however many legs ask"
    assert "author:app/github-actions" in gh.queries[-1]
    for pages, why in [
        ([{"total_count": 0, "incomplete_results": True, "items": []}], "incomplete"),
        ([{"total_count": 3, "incomplete_results": False, "items": [{"title": "x"}]}], "1 of"),
        ([], "no page"),
    ]:
        gh.pages = pages
        with pytest.raises(be.IssuesUnread, match=why):
            be.GhIssues("o/r").read()


# ── the issues step, which runs on main ─────────────────────────────────────────────────────────


#: The key the carry rows use: the rails on macOS, the one leg that may carry an earlier result.
_KEY = "rails-macos-26-3.12/1"


def test_a_failed_issue_does_not_stop_the_legs_after_it_and_fails_the_step(
    tmp_path, monkeypatch
) -> None:
    gh = _Gh()
    calls: list[str] = []

    def flaky(*args: str) -> str:
        if args[:2] == ("issue", "create") and "audit" in args[args.index("--title") + 1]:
            calls.append("audit")
            raise subprocess.CalledProcessError(1, ["gh", *args])
        return gh(*args)

    monkeypatch.setattr(be, "_gh", flaky)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    actions = [
        be.Action("audit", "open", "1" * 64, ("audit/1",), False),
        be.Action("canary", "open", "2" * 64, ("canary/1",), False),
    ]
    path = tmp_path / "actions.json"
    path.write_text(be.json.dumps([a.__dict__ for a in actions]), encoding="utf-8")
    argv = ["issues", "--repo", "o/r", "--actions", str(path), "--run-url", "https://example.com"]
    assert be.main([*argv, "--digest", "a" * 64]) == 1
    assert calls == ["audit"]
    assert [i["title"] for i in gh.issues] == [be.issue_title("canary")]


def test_an_unreadable_search_writes_no_issue_and_fails_the_step_once(
    tmp_path, monkeypatch, capsys
) -> None:
    gh = _Gh()
    gh.unreadable = _REFUSED
    monkeypatch.setattr(be, "_gh", gh)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    actions = [
        be.Action(leg, "open", "1" * 64, (f"{leg}/1",), False)
        for leg in ("audit", "canary", "coverage")
    ]
    path = tmp_path / "actions.json"
    path.write_text(be.json.dumps([a.__dict__ for a in actions]), encoding="utf-8")
    argv = ["issues", "--repo", "o/r", "--actions", str(path), "--run-url", "https://example.com"]
    assert be.main([*argv, "--digest", "a" * 64]) == 1
    said = capsys.readouterr().out
    failed = [line for line in said.splitlines() if line.startswith("- ")]
    assert len(failed) == 1 and _REFUSED in failed[0], "one line, in gh's words"
    assert len(gh.queries) == 1 and gh.issues == []


def test_apply_opens_the_issue_of_an_unsettled_legs_measured_red() -> None:
    entries = {"macos-26-3.12/1": _leg(), "macos-26-3.12/2": _leg(be.RED)}
    _, actions = be.settle_record(
        entries, {}, run_id=9, tree="d" * 40, unsettled=frozenset({"macos-26-3.12"})
    )
    assert actions[0].red == ("macos-26-3.12/2",), "only the shard that measured red"
    issues = _Issues()
    done = be.apply(actions, issues, run_url="https://example.com/r/9", record_digest="a" * 64)
    assert done == ["macos-26-3.12: open"]
    assert issues.log == [("create", be.issue_title("macos-26-3.12"))]
    assert be.open_red_inputs(issues.open[be.issue_title("macos-26-3.12")][1]) == frozenset(
        {actions[0].inputs}
    )


# ── on main: a run's every record, the listing, and a leg that cannot write its issue ─────────


def test_a_runs_later_attempt_never_turns_its_earlier_attempts_red_green() -> None:
    """Attempt 1 red on INPUTS, then "Re-run failed jobs" green on INPUTS: the run's result for
    the key stays the measured red, and a later dispatch carries it."""
    first, first_data = _stored(_record(**{_KEY: {**_leg(be.RED), "ran_at": "attempt 1"}}))
    second, second_data = _stored(_record(**{_KEY: {**_leg(), "ran_at": "attempt 2"}}))
    found, _ = be.previous(_Source({1: {first: first_data, second: second_data}}))
    assert found[_KEY]["entry"]["result"] == be.RED
    carried = be.decide(_KEY, INPUTS, found)
    assert carried is not None and carried["result"] == be.RED


def test_a_later_attempts_green_on_changed_inputs_is_the_runs_result() -> None:
    first, first_data = _stored(_record(**{_KEY: _leg(be.RED)}))
    second, second_data = _stored(_record(**{_KEY: _leg(uv="0.13.2")}))
    found, _ = be.previous(_Source({1: {first: first_data, second: second_data}}))
    assert found[_KEY]["entry"]["result"] == be.GREEN


def test_the_listing_is_paginated_and_sorted_oldest_first_by_id(monkeypatch) -> None:
    """The REST listing may come newest first, and one attempt uploads about 61 artifacts, so a
    full rerun overflows one page: every page is read, and the order is the ids'."""
    gh = _Gh()
    gh.newest_first = True
    gh.records = {1: {"breadth-evidence-a": b"", "junit-x": b"", "breadth-evidence-b": b""}}
    monkeypatch.setattr(be, "_gh", gh)
    assert be.GhSource("o/r", 9).artifacts(1) == [
        "breadth-evidence-a",
        "junit-x",
        "breadth-evidence-b",
    ]
    assert "--paginate" in gh.listings[-1]


def test_apply_tries_every_leg_whatever_one_raises() -> None:
    class Broken(_Issues):
        def find(self, title: str):
            if "audit" in title:
                raise TypeError("a reply of the wrong shape")
            return super().find(title)

    issues = Broken()
    actions = [
        be.Action("audit", "open", "1" * 64, ("audit/1",), False),
        be.Action("canary", "open", "2" * 64, ("canary/1",), False),
    ]
    with pytest.raises(be.Unwritten) as raised:
        be.apply(actions, issues, run_url="https://example.com", record_digest="a" * 64)
    assert raised.value.done == ["canary: open"]
    assert "TypeError" in raised.value.failed[0]


# ── off main nothing is reused; within a run a red stays red; a release refuses any red ───────


def _at(attempt: int, record: dict) -> dict:
    """``record``, and each of its entries, stamped with the attempt that wrote it."""
    legs = {k: {**e, "attempt": attempt} for k, e in record["legs"].items()}
    return {**record, "attempt": attempt, "legs": legs}


def _latest(attempt: int, conclusion: str = be.GREEN, **overrides: tuple[int, str]) -> list:
    """Every shard job as its latest attempt left it: ``attempt`` and ``conclusion``, but for
    ``overrides`` (key to (attempt, conclusion))."""
    jobs = {key: (attempt, conclusion) for key in be.expected_keys()}
    jobs.update({key.replace("__", "/"): v for key, v in overrides.items()})
    return [(a, c, be.job_name(key)) for key, (a, c) in sorted(jobs.items())]


def _report_off_main(
    tmp_path, monkeypatch, gh: _Gh, entries: dict, *, attempt: int, ref: str = "refs/tags/v0.3.0"
) -> dict:
    """``report`` on a tag, as breadth.yml runs it, for run 7's ``attempt``."""
    monkeypatch.setattr(be, "_gh", gh)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    legs = tmp_path / f"legs-{attempt}"
    legs.mkdir(parents=True)
    for key, entry in entries.items():
        (legs / f"{key.replace('/', '__')}.json").write_text(be.json.dumps(entry), "utf-8")
    record, actions = tmp_path / f"record-{attempt}.json", tmp_path / f"actions-{attempt}.json"
    argv = ["report", "--repo", "o/r", "--legs", str(legs), "--run-id", "7", "--tree", "d" * 40]
    argv += ["--record", str(record), "--actions", str(actions)]
    assert be.main([*argv, "--ref", ref, "--attempt", str(attempt)]) == 0
    return be.json.loads(record.read_bytes())


def test_a_reruns_green_after_a_red_on_the_same_run_stays_red(tmp_path, monkeypatch) -> None:
    """Attempt 1 red; "Re-run failed jobs" green, on the same inputs or not: still red."""
    gh = _Gh()
    first, first_data = _stored(_at(1, _record(**{_KEY: _leg(be.RED), "canary/1": _leg(be.RED)})))
    gh.records = {7: {first: first_data}}
    gh.jobs = {7: _latest(2)}
    rerun = _at(2, _record(**_all_green(**{"canary/1": _leg(image_version="20261019.1.0")})))
    legs = _report_off_main(tmp_path, monkeypatch, gh, rerun["legs"], attempt=2)["legs"]
    for key in (_KEY, "canary/1"):
        assert legs[key]["result"] == be.RED and legs[key]["red_on_an_earlier_attempt"] is True
    assert legs["ubuntu-24.04-3.13/1"]["result"] == be.GREEN, "a key never red stays green"


def test_an_attempt_missing_an_earlier_record_makes_every_key_red(tmp_path, monkeypatch) -> None:
    """Attempt 3 finds only attempt 2's record: what attempt 1 measured is not known."""
    gh = _Gh()
    second, second_data = _stored(_at(2, _record(**{_KEY: _leg()})))
    gh.records = {7: {second: second_data}}
    gh.jobs = {7: _latest(3)}
    tonight = _at(3, _record(**_all_green()))["legs"]
    legs = _report_off_main(tmp_path, monkeypatch, gh, tonight, attempt=3)["legs"]
    assert {e["result"] for e in legs.values()} == {be.RED}
    assert all(e["earlier_attempt_unread"] for e in legs.values())


def test_a_tag_dispatch_executes_every_leg_whatever_earlier_dispatches_measured(
    tmp_path, monkeypatch
) -> None:
    """An earlier dispatch measured these very inputs green: off main nothing is read, and
    even a map that holds the result is not carried."""

    def no_api(*args: str) -> str:
        raise AssertionError(f"off main nothing is read, yet gh was asked for {args}")

    monkeypatch.setattr(be, "_gh", no_api)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "previous.json"
    argv = ["previous", "--repo", "o/r", "--run-id", "3", "--ref", "v0.3.0", "--out", str(out)]
    assert be.main(argv) == 0
    assert be.json.loads(out.read_text("utf-8")) == {}
    held = {_KEY: {"entry": _leg(), "run_id": 1, "digest": "a" * 64}}
    previous, inputs = tmp_path / "held.json", tmp_path / "inputs.json"
    previous.write_text(be.json.dumps(held), encoding="utf-8")
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    said = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(said))
    argv = ["decide", "--key", _KEY, "--inputs", str(inputs), "--previous", str(previous)]
    entry = tmp_path / "entry.json"
    assert be.main([*argv, "--ref", "v0.3.0", "--out", str(entry)]) == 0
    assert said.read_text(encoding="utf-8").splitlines() == ["run=true"]
    assert not entry.exists()
    said.write_text("", encoding="utf-8")
    assert be.main([*argv, "--out", str(entry)]) == 0, "on main the same map carries it"
    assert said.read_text(encoding="utf-8").splitlines() == ["run=false"]


def test_a_release_refuses_a_run_with_any_red_attempt() -> None:
    red_then_green = [_at(1, _every_shard(**{_KEY: _leg(be.RED)})), _at(2, _every_shard())]
    assert not be.satisfies_release(red_then_green, run_attempt=2)
    assert be.satisfies_release([_at(1, _every_shard())], run_attempt=1)
    assert be.satisfies_release([_at(1, _every_shard()), _at(2, _every_shard())], run_attempt=2)
    assert not be.satisfies_release([_at(1, _every_shard(**{_KEY: _leg(be.RED)}))], run_attempt=1)
    assert not be.satisfies_release([], run_attempt=1)


def test_on_main_a_run_with_no_record_is_skipped() -> None:
    """A cancelled run on main wrote no record: the next night reads the run before it."""
    one, one_data = _stored(_record(**{_KEY: _leg(be.RED)}))
    found, said = be.previous(_Source({2: {}, 1: {one: one_data}}))
    assert found[_KEY]["entry"]["result"] == be.RED and "run 1" in said


# ── every record names its attempt; a rerun holds each key to its job; the full ref ────────────


def test_a_release_refuses_a_run_whose_newest_attempt_left_no_record() -> None:
    """Attempt 1 green wrote a record; attempt 2 measured a red and wrote none (cancelled, a
    failed upload, still running). The run is refused, not accepted on attempt 1's green."""
    green_first = [_at(1, _every_shard())]
    assert be.satisfies_release(green_first, run_attempt=1)
    assert not be.satisfies_release(green_first, run_attempt=2)
    unstamped = [_every_shard()]
    assert not be.satisfies_release(unstamped, run_attempt=1), "a record names its attempt"


def test_every_entry_and_record_names_the_attempt_that_wrote_it(tmp_path, monkeypatch) -> None:
    inputs, out = tmp_path / "inputs.json", tmp_path / "entry.json"
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    argv = ["ran", "--inputs", str(inputs), "--outcome", "success", "--out", str(out)]
    assert be.main([*argv, "--attempt", "3"]) == 0
    assert be.json.loads(out.read_text(encoding="utf-8"))["attempt"] == 3
    gh = _Gh()
    gh.records = {7: {}}
    gh.jobs = {7: _latest(2)}
    record = _report_off_main(
        tmp_path, monkeypatch, gh, _at(2, _record(**_all_green()))["legs"], attempt=2
    )
    assert record["attempt"] == 2


def test_a_reruns_shard_that_wrote_no_entry_is_red_not_its_earlier_attempts_entry(
    tmp_path, monkeypatch
) -> None:
    """On attempt 2 the newest entry of K is attempt 1's green, because K's rerun wrote none:
    K's job failed in attempt 2, so K is red. A key whose job succeeded in attempt 2 but whose
    entry is attempt 1's is red too; a key whose job last ran in attempt 1 keeps its entry."""
    gh = _Gh()
    gh.jobs = {
        7: _latest(1, **{_KEY: (2, "failure"), "canary/1": (2, be.GREEN), "audit/1": (2, be.GREEN)})
    }
    entries = _at(1, _record(**_all_green()))["legs"]
    entries["audit/1"] = {**_leg(), "attempt": 2}
    legs = _report_off_main(tmp_path, monkeypatch, gh, entries, attempt=2, ref=be.MAIN)["legs"]
    assert legs[_KEY]["result"] == be.RED and legs[_KEY]["shard_job"] is True
    assert legs["canary/1"]["result"] == be.RED and legs["canary/1"]["shard_job"] is True
    assert legs["audit/1"]["result"] == be.GREEN, "its job's latest attempt wrote this entry"
    assert legs["ubuntu-24.04-3.13/1"]["result"] == be.GREEN, "not rerun: attempt 1 stands"
    gh.jobs = {7: None}
    tonight = _at(2, _record(**_all_green()))["legs"]
    unread = _report_off_main(tmp_path / "x", monkeypatch, gh, tonight, attempt=2, ref=be.MAIN)
    assert {e["result"] for e in unread["legs"].values()} == {be.RED}, "jobs unread: all red"


def test_off_main_report_reads_no_issue(tmp_path, monkeypatch) -> None:
    """Main's open issue holds install-smoke's very inputs: on a tag it is not read, so the
    record is what the dispatch measured."""
    gh = _Gh()
    marker = be.inputs_key({"install-smoke/1": INPUTS})
    gh.open_issue(be.issue_title("install-smoke"), f"{be.MARKER}{marker}\n")
    legs = _report_off_main(tmp_path, monkeypatch, gh, _all_green(), attempt=1)["legs"]
    assert gh.queries == [], "no issue search off main"
    assert {e["result"] for e in legs.values()} == {be.GREEN}


@pytest.mark.parametrize("ref", ["refs/tags/main", "main", "refs/tags/v0.3.0"])
def test_only_the_full_main_ref_is_main(ref: str, tmp_path, monkeypatch) -> None:
    """A tag named `main` is off main: nothing read, nothing carried."""

    def no_api(*args: str) -> str:
        raise AssertionError(f"off main nothing is read, yet gh was asked for {args}")

    monkeypatch.setattr(be, "_gh", no_api)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "previous.json"
    argv = ["previous", "--repo", "o/r", "--run-id", "3", "--out", str(out)]
    assert be.main([*argv, "--ref", ref]) == 0
    assert be.json.loads(out.read_text("utf-8")) == {}
    held = {_KEY: {"entry": _leg(), "run_id": 1, "digest": "a" * 64}}
    previous, inputs = tmp_path / "held.json", tmp_path / "inputs.json"
    previous.write_text(be.json.dumps(held), encoding="utf-8")
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    said = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(said))
    argv = ["decide", "--key", _KEY, "--inputs", str(inputs), "--previous", str(previous)]
    assert be.main([*argv, "--ref", ref, "--out", str(tmp_path / "e.json")]) == 0
    assert said.read_text(encoding="utf-8").splitlines() == ["run=true"]
    said.write_text("", encoding="utf-8")
    assert be.main([*argv, "--ref", be.MAIN, "--out", str(tmp_path / "e.json")]) == 0
    assert said.read_text(encoding="utf-8").splitlines() == ["run=false"], "main carries"


# ── main's paths the off-main tests used to hold ────────────────────────────────────────────────


def test_main_reads_only_mains_runs_and_drops_its_own(monkeypatch) -> None:
    gh = _Gh()
    gh.runs = {"main": [99, 66, 55]}
    monkeypatch.setattr(be, "_gh", gh)
    assert be.GhSource("o/r", 99).runs() == [66, 55]
    assert gh.branches == ["main"]


@pytest.mark.parametrize(
    ("outcome", "result"),
    [("success", be.GREEN), ("failure", be.RED), ("skipped", be.RED), ("", be.RED)],
)
def test_ran_records_success_green_and_anything_else_red(outcome, result, tmp_path) -> None:
    inputs, out = tmp_path / "inputs.json", tmp_path / "entry.json"
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    argv = ["ran", "--inputs", str(inputs), "--outcome", outcome, "--out", str(out)]
    assert be.main(argv) == 0
    assert be.json.loads(out.read_text(encoding="utf-8"))["result"] == result


def test_on_main_unreadable_earlier_results_write_an_empty_map(tmp_path, monkeypatch) -> None:
    def broken(*args: str) -> str:
        raise subprocess.CalledProcessError(1, ["gh", *args])

    monkeypatch.setattr(be, "_gh", broken)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    out = tmp_path / "previous.json"
    argv = ["previous", "--repo", "o/r", "--run-id", "3", "--ref", be.MAIN, "--out", str(out)]
    assert be.main(argv) == 0
    assert be.json.loads(out.read_text("utf-8")) == {}, "every leg runs"


def test_on_main_a_carried_key_whose_origin_run_is_gone_is_left_out() -> None:
    """Run 202 ran macOS and carried ubuntu from run 201, which the API now answers with a 404:
    ubuntu is left out, so its shard runs; macOS is still known."""
    mac = "macos-26-3.12/1"
    gone = be.carried(_leg(), run_id=201, record_digest="f" * 64)
    name, data = _stored(_record(**{mac: _leg(), _KEY: gone}))
    found, _ = be.previous(_Source({202: {name: data}}, deleted=(201,)))
    assert found[mac]["run_id"] == 202
    assert _KEY not in found


def test_a_red_random_order_leg_names_its_seeds_in_its_issue() -> None:
    """The random-order leg's entries carry their seeds beside their inputs, and its issue names
    the seeds of its red shards, so the order that failed can be run again."""
    seeded = {"shard_files": "f" * 64}
    entries = {
        "random-order/1": {**_leg(be.RED, external=seeded), "seeds": ["4211", "4212"]},
        "random-order/2": {**_leg(external=seeded), "seeds": ["4211", "4212"]},
        "random-order/3": {**_leg(be.RED, external=seeded), "seeds": ["4211", "4212"]},
    }
    _, actions = be.settle_record(entries, {}, run_id=421, tree="d" * 40)
    [action] = actions
    assert action.action == "open" and action.seeds == ("4211", "4212")
    issues = _Issues()
    be.apply(actions, issues, run_url="https://example.com/r/421", record_digest="a" * 64)
    body = issues.open[be.issue_title("random-order")][1]
    assert "Seeds: `4211`, `4212`" in body
    assert be.open_red_inputs(body) == {action.inputs}


def _random_order_night(
    tmp_path, monkeypatch, gh: _Gh, run_id: int, seeds: str, *, tree: str, red: tuple[int, ...]
):
    """One night of the random-order leg as breadth.yml writes it: each shard's inputs, then its
    entry with the night's seeds beside them (``ran --seeds``), then ``report`` and ``issues``."""
    entries = _all_green()
    for shard in range(1, 5):
        folder = tmp_path / f"{run_id}-random-order-{shard}"
        folder.mkdir()
        inputs, out = folder / "inputs.json", folder / "entry.json"
        argv = ["inputs", "--out", str(inputs), "--tree", tree, "--image", "ubuntu24"]
        argv += ["--image-version", "20261012.1.0", "--arch", "X64", "--python", "3.13.16"]
        argv += ["--uv", "0.13.1", "--node", "none", "--external", f"shard_files={'f' * 64}"]
        assert be.main(argv) == 0
        outcome = be.RED if shard in red else be.GREEN
        argv = ["ran", "--inputs", str(inputs), "--outcome", outcome, "--seeds", seeds]
        assert be.main([*argv, "--out", str(out)]) == 0
        entries[f"random-order/{shard}"] = be.json.loads(out.read_text("utf-8"))
    return _night(tmp_path, monkeypatch, gh, entries, run_id)


def test_a_random_order_red_stays_open_on_other_seeds_until_the_tree_changes(
    tmp_path, monkeypatch
) -> None:
    """The seeds are not an input: a red order is the tree's, and a green on other seeds does not
    show it gone. On the same tree and runner it is a flake, as the security corpus's is, and
    only a changed tree closes it. Each issue names the seeds of its red shards."""
    gh = _Gh()
    _, done = _random_order_night(
        tmp_path, monkeypatch, gh, 1, "12341,12342", tree="d" * 40, red=(2,)
    )
    assert done["random-order"] == "open"
    [issue] = gh.issues
    assert "Seeds: `12341`, `12342`" in issue["body"]
    record, done = _random_order_night(
        tmp_path, monkeypatch, gh, 2, "12351,12352", tree="d" * 40, red=()
    )
    assert done["random-order"] == "flake"
    assert record["legs"]["random-order/2"]["result"] == be.RED
    assert record["legs"]["random-order/2"]["seeds"] == ["12351", "12352"]
    assert [i["state"] for i in gh.issues] == ["open"]
    _, done = _random_order_night(
        tmp_path, monkeypatch, gh, 3, "12361,12362", tree="e" * 40, red=()
    )
    assert done["random-order"] == "close"
    assert [i["state"] for i in gh.issues] == ["closed"]


@pytest.mark.parametrize("seeds", ["", "12a", "1,,2", "1, 2", "-1"])
def test_ran_refuses_seeds_that_are_not_whole_numbers(seeds, tmp_path) -> None:
    inputs, out = tmp_path / "inputs.json", tmp_path / "entry.json"
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    argv = ["ran", "--inputs", str(inputs), "--outcome", "success", "--seeds", seeds]
    with pytest.raises(SystemExit):
        be.main([*argv, "--out", str(out)])
    assert not out.exists()


def test_an_entry_without_seeds_holds_none(tmp_path) -> None:
    inputs, out = tmp_path / "inputs.json", tmp_path / "entry.json"
    inputs.write_text(be.json.dumps(INPUTS), encoding="utf-8")
    argv = ["ran", "--inputs", str(inputs), "--outcome", "success", "--out", str(out)]
    assert be.main(argv) == 0
    assert "seeds" not in be.json.loads(out.read_text(encoding="utf-8"))


def test_a_leg_with_no_seeds_names_none() -> None:
    _, [action] = be.settle_record({"audit/1": _leg(be.RED)}, {}, run_id=5, tree="d" * 40)
    assert action.seeds == ()
    issues = _Issues()
    be.apply([action], issues, run_url="https://example.com/r/5", record_digest="a" * 64)
    assert "Seeds" not in issues.open[be.issue_title("audit")][1]


def test_the_random_order_leg_is_never_reused_and_has_four_shards() -> None:
    assert "random-order" in be.NON_REUSABLE
    assert be.SHARDS["random-order"] == 4
    assert not be.reusable("random-order", _leg(), INPUTS)


# ── a quiet night: the second run on an unchanged tree (#3913) ───────────────────────────────


@pytest.mark.parametrize("result", [be.GREEN, be.RED], ids=["a-green-night", "a-red-night"])
def test_a_second_run_on_an_unchanged_tree_carries_exactly_the_reusable_legs(result: str) -> None:
    """A scheduled night's record, read by a dispatched run on the same tree through
    ``previous`` and ``decide``: the rails on macOS and the collection carry its results, red or
    green, and every other shard runs, the suite's among them, since a security result is never
    carried. Main's runs are read whatever started them (``GhSource.runs``)."""
    night = _every_shard(**{key: _leg(result) for key in be.expected_keys()})
    name, data = _stored(night)
    found, said = be.previous(_Source({40: {name: data}}))
    assert "run 40" in said
    carried = {key for key in found if be.decide(key, INPUTS, found) is not None}
    assert carried == {f"{leg}/1" for leg in CARRYING_LEGS}
    for key in carried:
        entry = be.decide(key, INPUTS, found)
        assert entry is not None and entry["result"] == result
        assert entry["reused_from"]["run_id"] == 40
    ran = set(found) - carried
    assert {be.leg_of(key) for key in ran} == set(be.SHARDS) - CARRYING_LEGS
    assert all(be.leg_of(key) in be.NON_REUSABLE for key in ran)


def test_a_second_run_on_another_tree_carries_nothing() -> None:
    name, data = _stored(_every_shard())
    found, _ = be.previous(_Source({40: {name: data}}))
    moved = {**INPUTS, "tree": "e" * 40}
    assert [key for key in found if be.decide(key, moved, found) is not None] == []


def test_main_s_runs_are_read_whatever_started_them(monkeypatch) -> None:
    """A dispatched run reads a scheduled night's record: the listing names main and no event."""
    gh = _Gh()
    gh.runs = {"main": [41, 40]}
    calls: list[tuple[str, ...]] = []

    def recording(*args: str) -> str:
        calls.append(args)
        return gh(*args)

    monkeypatch.setattr(be, "_gh", recording)
    assert be.GhSource("o/r", 41).runs() == [40]
    asked = [" ".join(call) for call in calls if any("breadth.yml/runs" in a for a in call)]
    assert asked and all("branch=main" in c and "event=" not in c for c in asked), asked
