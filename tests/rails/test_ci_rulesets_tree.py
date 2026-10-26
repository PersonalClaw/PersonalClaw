"""The checked-in rulesets: ``.github/rulesets/*.json`` (#3907) and the tag rulesets (#4584).

Every file is one ruleset in the ruleset API's request shape, named as its file is, with no
bypass actor. ``main-protect`` holds ``main`` and only ``main``, is active, and carries the rules
that keep every change on the pull-request path for everyone: a pull request with every
conversation resolved and stale reviews dismissed, merged without a merge commit, linear history,
no force push and no deletion, and one required check, ``merge-verdict``, bound to the controller
App that ``release-tag-creation`` names, on a branch up to date with ``main`` (#3910). The weekly
settings report compares the live rulesets with these files; this rail keeps the files themselves
from losing a rule.

``release-tags`` refuses moving and deleting a release tag, with no bypass actor.
``release-tag-creation`` refuses creating one, and is the one ruleset with a bypass actor: exactly
one, an App (``Integration``), the release controller's. Both are applied; a ruleset not yet
applied waits under ``.github/rulesets/pending/``, where the report does not read it, and only
there may that id be unset: an applied ruleset's bypass actor names a real id. A ruleset's bypass
list covers every rule in it, which is why the two are apart. Each rule fails on a planted edit
of the real files.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from rails_repo import REPO

_DIR = REPO / ".github" / "rulesets"
_PENDING = _DIR / "pending"
_MAIN = "main-protect"
_TAGS, _CREATION = "release-tags", "release-tag-creation"
_TAG_REF = {"include": ["refs/tags/v*"], "exclude": []}
_TAG_RULES = {"update", "deletion", "non_fast_forward"}
_MAIN_RULES = {
    "deletion",
    "non_fast_forward",
    "required_linear_history",
    "pull_request",
    "required_status_checks",
}


def _rulesets(where: Path = _DIR) -> dict[str, dict[str, Any]]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(where.glob("*.json"))}


def _tag_problems(
    applied: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]
) -> list[str]:
    found: list[str] = []
    for stem, data in pending.items():
        if stem in applied:
            found.append(f"{stem}.json is both applied and pending")
        if data.get("name") != stem:
            found.append(f"pending/{stem}.json names the ruleset {data.get('name')!r}")
    every = {**pending, **applied}
    for stem in (_TAGS, _CREATION):
        data = every.get(stem)
        if data is None:
            found.append(f"no {stem}.json, applied or pending")
            continue
        ref = (data.get("conditions") or {}).get("ref_name")
        if data.get("target") != "tag" or data.get("enforcement") != "active" or ref != _TAG_REF:
            found.append(
                f"{stem} is {data.get('target')!r} / {data.get('enforcement')!r} on {ref!r}, "
                "not an active rule on every v* tag"
            )
    tags = every.get(_TAGS) or {}
    kinds = {r.get("type") for r in tags.get("rules") or []}
    if not _TAG_RULES <= kinds:
        found.append(f"{_TAGS} lacks {sorted(_TAG_RULES - kinds)}")
    if "creation" in kinds:
        found.append(
            f"{_TAGS} refuses creation, which its empty bypass list would hold for the App too"
        )
    if tags.get("bypass_actors") != []:
        found.append(f"{_TAGS}.json has bypass actors {tags.get('bypass_actors')!r}")
    creation = every.get(_CREATION) or {}
    if [r.get("type") for r in creation.get("rules") or []] != ["creation"]:
        found.append(f"{_CREATION} has rules {creation.get('rules')!r}, not creation alone")
    actors = creation.get("bypass_actors")
    one = actors[0] if isinstance(actors, list) and len(actors) == 1 else None
    if (
        not isinstance(one, dict)
        or set(one) != {"actor_id", "actor_type", "bypass_mode"}
        or one.get("actor_type") != "Integration"
        or one.get("bypass_mode") != "always"
    ):
        found.append(f"{_CREATION}'s bypass list is {actors!r}, not the controller's one App")
    elif _CREATION in applied and not _real_id(one.get("actor_id")):
        found.append(f"{_CREATION} is applied with no App id ({one.get('actor_id')!r})")
    elif one.get("actor_id") is not None and not _real_id(one.get("actor_id")):
        found.append(f"{_CREATION}'s App id is {one.get('actor_id')!r}")
    for stem, data in applied.items():
        for actor in data.get("bypass_actors") or []:
            if not isinstance(actor, dict) or not _real_id(actor.get("actor_id")):
                found.append(f"{stem}.json is applied with a bypass actor that names no real id")
    return found


def _real_id(value: object) -> bool:
    """An id GitHub could have issued: a positive integer, never a bool or a stand-in."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _problems(
    rulesets: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]] | None = None
) -> list[str]:
    found: list[str] = []
    if _MAIN not in rulesets:
        return [f"no {_MAIN}.json"]
    found += _tag_problems(rulesets, pending if pending is not None else _rulesets(_PENDING))
    for stem, data in rulesets.items():
        if data.get("name") != stem:
            found.append(f"{stem}.json names the ruleset {data.get('name')!r}")
        if stem != _CREATION and data.get("bypass_actors") != []:
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
    extra = set(rules) - _MAIN_RULES
    if extra:
        found.append(f"{_MAIN} has unnamed rules {sorted(extra)}")
    pr = rules.get("pull_request") or {}
    if pr.get("required_review_thread_resolution") is not True:
        found.append(f"{_MAIN}'s pull requests may merge with conversations open")
    if pr.get("dismiss_stale_reviews_on_push") is not True:
        found.append(f"{_MAIN} keeps stale reviews")
    methods = pr.get("allowed_merge_methods")
    if methods != ["squash"]:
        found.append(f"{_MAIN} allows merge methods {methods!r}, not squash alone")
    checks = rules.get("required_status_checks") or {}
    if checks.get("strict_required_status_checks_policy") is not True:
        found.append(f"{_MAIN} lets a branch behind main merge")
    every = {**(pending if pending is not None else _rulesets(_PENDING)), **rulesets}
    actors = (every.get(_CREATION) or {}).get("bypass_actors") or [{}]
    app = actors[0].get("actor_id") if isinstance(actors[0], dict) else None
    required = checks.get("required_status_checks")
    if required != [{"context": "merge-verdict", "integration_id": app}] or not _real_id(app):
        found.append(
            f"{_MAIN} requires {required!r}, not merge-verdict from the controller's App ({app!r})"
        )
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


def _checks(key: str, value: object) -> Callable[[dict[str, Any]], None]:
    def plant(data: dict[str, Any]) -> None:
        _rule(data, "required_status_checks")["parameters"][key] = value

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
        (_pr("allowed_merge_methods", ["squash", "rebase"]), "merge methods"),
        (_pr("allowed_merge_methods", ["rebase"]), "merge methods"),
        (_pr("allowed_merge_methods", []), "merge methods"),
        (lambda d: d.update(name="main"), "names the ruleset 'main'"),
        (_drop_rule("required_status_checks"), "lacks ['required_status_checks']"),
        (_checks("strict_required_status_checks_policy", False), "behind main"),
        (_checks("required_status_checks", []), "not merge-verdict from the controller's App"),
        (
            _checks("required_status_checks", [{"context": "merge-verdict"}]),
            "not merge-verdict from the controller's App",
        ),
        (
            _checks(
                "required_status_checks", [{"context": "merge-verdict", "integration_id": 15368}]
            ),
            "not merge-verdict from the controller's App",
        ),
        (
            _checks("required_status_checks", [{"context": "results", "integration_id": 5444147}]),
            "not merge-verdict from the controller's App",
        ),
    ],
)
def test_a_planted_edit_of_main_protect_fails(
    plant: Callable[[dict[str, Any]], None], says: str
) -> None:
    rulesets = copy.deepcopy(_rulesets())
    plant(rulesets[_MAIN])
    found = _problems(rulesets)
    assert any(says in p for p in found), found


def test_a_missing_main_protect_fails() -> None:
    assert _problems({}) == [f"no {_MAIN}.json"]


def _both() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    return copy.deepcopy(_rulesets()), copy.deepcopy(_rulesets(_PENDING))


def _tags(applied: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """release-tags, applied or pending."""
    return applied.get(_TAGS) or pending[_TAGS]


def _creation(
    applied: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """release-tag-creation, applied or pending."""
    return applied.get(_CREATION) or pending[_CREATION]


def _actor(
    applied: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    actor: dict[str, Any] = _creation(applied, pending)["bypass_actors"][0]
    return actor


def _to_pending(applied: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]) -> None:
    """Put release-tag-creation back where it waited, as it was before the App existed."""
    if _CREATION in applied:
        pending[_CREATION] = applied.pop(_CREATION)


@pytest.mark.parametrize(
    "plant, says",
    [
        (lambda a, p: (a.pop(_TAGS, None), p.pop(_TAGS, None)), "no release-tags.json"),
        (lambda a, p: (a.pop(_CREATION, None), p.pop(_CREATION, None)), "no release-tag-creation"),
        (lambda a, p: _tags(a, p).update(enforcement="evaluate"), "not an active rule"),
        (lambda a, p: _tags(a, p).update(target="branch"), "not an active rule"),
        (
            lambda a, p: _creation(a, p)["conditions"]["ref_name"].update(include=["refs/tags/*"]),
            "not an active rule on every v* tag",
        ),
        (
            lambda a, p: _tags(a, p).update(
                rules=[r for r in _tags(a, p)["rules"] if r["type"] != "update"]
            ),
            "lacks ['update']",
        ),
        (
            lambda a, p: _tags(a, p).update(
                rules=[r for r in _tags(a, p)["rules"] if r["type"] != "deletion"]
            ),
            "lacks ['deletion']",
        ),
        (lambda a, p: _tags(a, p)["rules"].append({"type": "creation"}), "refuses creation"),
        (
            lambda a, p: _tags(a, p).update(
                bypass_actors=[
                    {"actor_id": 1, "actor_type": "Integration", "bypass_mode": "always"}
                ]
            ),
            "release-tags.json has bypass actors",
        ),
        (lambda a, p: _creation(a, p)["rules"].append({"type": "update"}), "not creation alone"),
        (lambda a, p: _creation(a, p).update(bypass_actors=[]), "not the controller's one App"),
        (
            lambda a, p: _creation(a, p)["bypass_actors"].append(
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
            ),
            "not the controller's one App",
        ),
        (lambda a, p: _actor(a, p).update(actor_type="RepositoryRole"), "one App"),
        (lambda a, p: _actor(a, p).update(bypass_mode="pull_request"), "one App"),
        (lambda a, p: _actor(a, p).update(actor_id="815"), "applied with no App id ('815')"),
        (lambda a, p: _actor(a, p).update(actor_id=None), "applied with no App id (None)"),
        (lambda a, p: _actor(a, p).update(actor_id=0), "applied with no App id (0)"),
        (lambda a, p: _actor(a, p).update(actor_id=True), "applied with no App id (True)"),
        (lambda a, p: _actor(a, p).update(actor_id=-1), "names no real id"),
        (
            lambda a, p: (_to_pending(a, p), _actor(a, p).update(actor_id=0)),
            "App id is 0",
        ),
        (lambda a, p: p.update({_TAGS: _tags(a, p)}), "both applied and pending"),
        (
            lambda a, p: (_to_pending(a, p), _creation(a, p).update(name="creation")),
            "pending/release-tag-creation.json names",
        ),
        (lambda a, p: _tags(a, p).update(name="tags"), "release-tags.json names the ruleset"),
    ],
)
def test_a_planted_edit_of_a_tag_ruleset_fails(
    plant: Callable[[dict[str, Any], dict[str, Any]], None], says: str
) -> None:
    applied, pending = _both()
    plant(applied, pending)
    found = _problems(applied, pending)
    assert any(says in p for p in found), found


def test_the_creation_ruleset_may_wait_with_no_app_id_but_not_go_live_without_one() -> None:
    applied, pending = _both()
    _to_pending(applied, pending)
    _actor(applied, pending)["actor_id"] = None
    # main-protect's required check names the App, so only the tag rulesets are judged here.
    assert _tag_problems(applied, pending) == []
    applied[_CREATION] = pending.pop(_CREATION)
    assert any("applied with no App id" in p for p in _problems(applied, pending))
