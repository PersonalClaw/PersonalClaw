"""``tools/ci/breadth_evidence.py``: the breadth record, its digest, reuse and the leg issues."""

from __future__ import annotations

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
    only_three = _record(
        **{"security-corpus/1": _leg(), "audit/1": _leg(), "install-smoke/1": _leg()}
    )
    assert not be.satisfies_release_alone(only_three)


def test_a_shard_that_wrote_no_entry_is_recorded_red_and_runs_next_night() -> None:
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
    red_inputs = be.inputs_key({"macos-26-3.12/3": INPUTS})
    tonight = {k: _leg(tree="e" * 40) for k in be.expected_keys() if k != "macos-26-3.12/3"}
    _, actions = be.settle_record(
        be.fill_missing(tonight), {"macos-26-3.12": red_inputs}, run_id=8, tree="e" * 40
    )
    assert next(a for a in actions if a.leg == "macos-26-3.12").action == "update"


def test_a_leg_whose_issue_cannot_be_read_is_recorded_red_and_touches_no_issue() -> None:
    entries = {"macos-26-3.12/1": _leg(), "macos-26-3.12/2": _leg()}
    record, actions = be.settle_record(
        entries, {}, run_id=9, tree="d" * 40, unsettled=frozenset({"macos-26-3.12"})
    )
    assert {e["result"] for e in record["legs"].values()} == {be.RED}
    assert actions[0].action == "none"


def test_a_red_then_a_green_on_changed_inputs_closes_the_issue() -> None:
    first = be.settle(be.RED, INPUTS, None)
    assert first == be.Settled(be.RED, "open")
    fixed = be.settle(be.GREEN, {**INPUTS, "tree": "e" * 40}, be.inputs_key(INPUTS))
    assert fixed == be.Settled(be.GREEN, "close")


def test_a_red_then_a_green_on_the_same_inputs_is_a_flake_that_stays_red() -> None:
    assert be.settle(be.RED, INPUTS, be.inputs_key(INPUTS)) == be.Settled(be.RED, "update")
    flake = be.settle(be.GREEN, INPUTS, be.inputs_key(INPUTS))
    assert flake == be.Settled(be.RED, "flake"), "the record keeps the leg red, the issue open"


def test_a_green_with_no_open_issue_does_nothing_and_the_title_is_exact() -> None:
    assert be.settle(be.GREEN, INPUTS, None) == be.Settled(be.GREEN, "none")
    assert be.issue_title("macos-26-3.12") == "breadth: macos-26-3.12 is red"
    with pytest.raises(ValueError):
        be.settle("skipped", INPUTS, None)


# ── what breadth.yml runs: the earlier record, a shard's decision, the record and the issues ──


class _Source:
    """Completed runs on main, newest first, each with its artifacts."""

    def __init__(self, runs: dict[int, dict[str, bytes]]) -> None:
        self.by_run = runs

    def runs(self) -> list[int]:
        return sorted(self.by_run, reverse=True)

    def artifacts(self, run_id: int) -> list[str]:
        return list(self.by_run[run_id])

    def record(self, run_id: int, name: str) -> bytes:
        return self.by_run[run_id][name]


def _stored(record: dict) -> tuple[str, bytes]:
    data = be.canonical(record)
    return be.artifact_name(be.digest(data)), data


def test_the_earlier_results_follow_a_carried_leg_to_the_run_that_executed_it() -> None:
    ran = _record(**{"ubuntu-24.04-3.13/1": _leg(be.RED)})
    name, data = _stored(ran)
    first_digest = name[len(be.PREFIX) :]
    carried_record = _record(**{"ubuntu-24.04-3.13/1": be.carried(_leg(be.RED), 10, first_digest)})
    later_name, later_data = _stored(carried_record)
    found, said = be.previous(_Source({10: {name: data}, 11: {later_name: later_data}}))
    assert found["ubuntu-24.04-3.13/1"]["run_id"] == 10, "the origin, not the run that carried it"
    assert found["ubuntu-24.04-3.13/1"]["digest"] == first_digest
    assert "run 11" in said
    entry = be.decide("ubuntu-24.04-3.13/1", INPUTS, found)
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
        flaky, {"macos-26-3.12": actions[0].inputs}, run_id=6, tree="d" * 40
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
    assert text is not None and be.open_red_inputs(text) == red
    later = [{"user": _BOT, "body": f"{be.MARKER}{'1' * 64}\n"}]
    assert be.open_red_inputs(be.bot_text(issue, later) or "") == "1" * 64
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


def test_a_dispatch_on_a_tag_reads_that_tags_runs_first(monkeypatch) -> None:
    """A release dispatch's earlier red is carried, not run again on the same inputs."""
    asked: list[str] = []

    def fake_gh(*args: str) -> str:
        asked.append(args[1])
        return {"v0.3.0": "77", "main": "66 55"}[args[1].split("branch=")[1].split("&")[0]]

    monkeypatch.setattr(be, "_gh", fake_gh)
    assert be.GhSource("o/r", 99, "v0.3.0").runs() == [77, 66, 55]
    assert be.GhSource("o/r", 99).runs() == [66, 55]
    assert all("breadth.yml/runs" in path for path in asked)
    assert sum("branch=v0.3.0" in path for path in asked) == 1, "main never reads the tag's"
