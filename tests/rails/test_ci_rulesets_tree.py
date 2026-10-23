"""The checked-in rulesets: ``.github/rulesets/*.json`` (#3907).

Every file is one ruleset in the ruleset API's request shape, named as its file is, with no
bypass actor. ``main-protect`` holds ``main`` and only ``main``, is active, and carries the rules
that keep every change on the pull-request path for everyone: a pull request with every
conversation resolved and stale reviews dismissed, merged without a merge commit, linear history,
no force push and no deletion. The weekly settings report compares the live rulesets with these
files; this rail keeps the files themselves from losing a rule. Each rule fails on a planted
edit of the real file.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
from rails_repo import REPO

_DIR = REPO / ".github" / "rulesets"
_MAIN = "main-protect"
_MAIN_RULES = {"deletion", "non_fast_forward", "required_linear_history", "pull_request"}
#: Rules #3910 adds to main-protect beside these; any other type is a change this rail must name.
_LATER_RULES = {"required_status_checks"}


def _rulesets() -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(_DIR.glob("*.json"))}


def _problems(rulesets: dict[str, dict[str, Any]]) -> list[str]:
    found: list[str] = []
    if _MAIN not in rulesets:
        return [f"no {_MAIN}.json"]
    for stem, data in rulesets.items():
        if data.get("name") != stem:
            found.append(f"{stem}.json names the ruleset {data.get('name')!r}")
        if data.get("bypass_actors") != []:
            found.append(f"{stem}.json has bypass actors {data.get('bypass_actors')!r}")
    main = rulesets[_MAIN]
    if main.get("target") != "branch" or main.get("enforcement") != "active":
        found.append(f"{_MAIN} is {main.get('target')!r} / {main.get('enforcement')!r}")
    ref = (main.get("conditions") or {}).get("ref_name") or {}
    if ref.get("include") != ["refs/heads/main"] or ref.get("exclude") != []:
        found.append(f"{_MAIN} holds {ref!r}, not main alone")
    rules = {r.get("type"): r.get("parameters") or {} for r in main.get("rules") or []}
    missing = _MAIN_RULES - set(rules)
    if missing:
        found.append(f"{_MAIN} lacks {sorted(missing)}")
    extra = set(rules) - _MAIN_RULES - _LATER_RULES
    if extra:
        found.append(f"{_MAIN} has unnamed rules {sorted(extra)}")
    pr = rules.get("pull_request") or {}
    if pr.get("required_review_thread_resolution") is not True:
        found.append(f"{_MAIN}'s pull requests may merge with conversations open")
    if pr.get("dismiss_stale_reviews_on_push") is not True:
        found.append(f"{_MAIN} keeps stale reviews")
    methods = pr.get("allowed_merge_methods")
    if not methods or not set(methods) <= {"squash", "rebase"}:
        found.append(f"{_MAIN} allows merge methods {methods!r}")
    return found


def test_the_rulesets_hold_main_to_the_pull_request_path_for_everyone() -> None:
    assert _problems(_rulesets()) == []


def _rule(data: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(r for r in data["rules"] if r["type"] == kind)


def _drop_rule(kind: str) -> Callable[[dict[str, Any]], None]:
    def plant(data: dict[str, Any]) -> None:
        data["rules"] = [r for r in data["rules"] if r["type"] != kind]

    return plant


def _pr(key: str, value: object) -> Callable[[dict[str, Any]], None]:
    def plant(data: dict[str, Any]) -> None:
        _rule(data, "pull_request")["parameters"][key] = value

    return plant


@pytest.mark.parametrize(
    "plant, says",
    [
        (
            lambda d: d.update(
                bypass_actors=[
                    {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
                ]
            ),
            "has bypass actors",
        ),
        (lambda d: d.pop("bypass_actors"), "has bypass actors None"),
        (lambda d: d.update(enforcement="evaluate"), "'branch' / 'evaluate'"),
        (lambda d: d.update(enforcement="disabled"), "'branch' / 'disabled'"),
        (
            lambda d: d["conditions"]["ref_name"].update(include=["refs/heads/release"]),
            "not main alone",
        ),
        (
            lambda d: d["conditions"]["ref_name"].update(exclude=["refs/heads/main"]),
            "not main alone",
        ),
        (_drop_rule("deletion"), "lacks ['deletion']"),
        (_drop_rule("non_fast_forward"), "lacks ['non_fast_forward']"),
        (_drop_rule("required_linear_history"), "lacks ['required_linear_history']"),
        (_drop_rule("pull_request"), "lacks ['pull_request']"),
        (lambda d: d["rules"].append({"type": "update"}), "unnamed rules ['update']"),
        (_pr("required_review_thread_resolution", False), "conversations open"),
        (_pr("dismiss_stale_reviews_on_push", False), "stale reviews"),
        (_pr("allowed_merge_methods", ["squash", "rebase", "merge"]), "merge methods"),
        (_pr("allowed_merge_methods", []), "merge methods"),
        (lambda d: d.update(name="main"), "names the ruleset 'main'"),
    ],
)
def test_a_planted_edit_of_main_protect_fails(
    plant: Callable[[dict[str, Any]], None], says: str
) -> None:
    rulesets = copy.deepcopy(_rulesets())
    plant(rulesets[_MAIN])
    found = _problems(rulesets)
    assert any(says in p for p in found), found


def test_squash_only_and_a_required_check_still_pass() -> None:
    rulesets = copy.deepcopy(_rulesets())
    _pr("allowed_merge_methods", ["squash"])(rulesets[_MAIN])
    rulesets[_MAIN]["rules"].append(
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [{"context": "merge-verdict", "integration_id": 1}],
            },
        }
    )
    assert _problems(rulesets) == []


def test_a_missing_main_protect_fails() -> None:
    assert _problems({}) == [f"no {_MAIN}.json"]
