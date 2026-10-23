"""The checked-in rulesets: ``.github/rulesets/*.json`` (#3907) and the tag rulesets (#4584).

Every file is one ruleset in the ruleset API's request shape, named as its file is, with no
bypass actor. ``main-protect`` holds ``main`` and only ``main``, is active, and carries the rules
that keep every change on the pull-request path for everyone: a pull request with every
conversation resolved and stale reviews dismissed, merged without a merge commit, linear history,
no force push and no deletion. The weekly settings report compares the live rulesets with these
files; this rail keeps the files themselves from losing a rule.

``release-tags`` refuses moving and deleting a release tag, with no bypass actor, and is applied.
``release-tag-creation`` refuses creating one, and waits under ``.github/rulesets/pending/`` until
the release controller's App exists, so the report does not expect it live yet; it is the one
ruleset with a bypass actor: exactly one, an App (``Integration``), whose id may be unset only
while the file waits. A ruleset's bypass list covers every rule in it, which is
why the two are apart. Each rule fails on a planted edit of the real files.
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
_MAIN_RULES = {"deletion", "non_fast_forward", "required_linear_history", "pull_request"}
#: Rules #3910 adds to main-protect beside these; any other type is a change this rail must name.
_LATER_RULES = {"required_status_checks"}


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


def _both() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    return copy.deepcopy(_rulesets()), copy.deepcopy(_rulesets(_PENDING))


def _tags(applied: dict[str, dict[str, Any]], pending: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """release-tags, applied or pending."""
    return applied.get(_TAGS) or pending[_TAGS]


def _creation_actor(pending: dict[str, dict[str, Any]]) -> dict[str, Any]:
    actor: dict[str, Any] = pending[_CREATION]["bypass_actors"][0]
    return actor


@pytest.mark.parametrize(
    "plant, says",
    [
        (lambda a, p: (a.pop(_TAGS, None), p.pop(_TAGS, None)), "no release-tags.json"),
        (lambda a, p: p.pop(_CREATION), "no release-tag-creation.json"),
        (lambda a, p: _tags(a, p).update(enforcement="evaluate"), "not an active rule"),
        (lambda a, p: _tags(a, p).update(target="branch"), "not an active rule"),
        (
            lambda a, p: p[_CREATION]["conditions"]["ref_name"].update(include=["refs/tags/*"]),
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
        (lambda a, p: p[_CREATION]["rules"].append({"type": "update"}), "not creation alone"),
        (lambda a, p: p[_CREATION].update(bypass_actors=[]), "not the controller's one App"),
        (
            lambda a, p: p[_CREATION]["bypass_actors"].append(
                {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}
            ),
            "not the controller's one App",
        ),
        (lambda a, p: _creation_actor(p).update(actor_type="RepositoryRole"), "one App"),
        (lambda a, p: _creation_actor(p).update(bypass_mode="pull_request"), "one App"),
        (lambda a, p: _creation_actor(p).update(actor_id="815"), "App id is '815'"),
        (
            lambda a, p: (
                _creation_actor(p).update(actor_id=None),
                a.update({_CREATION: p.pop(_CREATION)}),
            ),
            "applied with no App id (None)",
        ),
        (
            lambda a, p: (
                _creation_actor(p).update(actor_id=0),
                a.update({_CREATION: p.pop(_CREATION)}),
            ),
            "applied with no App id (0)",
        ),
        (
            lambda a, p: (
                _creation_actor(p).update(actor_id=True),
                a.update({_CREATION: p.pop(_CREATION)}),
            ),
            "applied with no App id (True)",
        ),
        (
            lambda a, p: (
                _creation_actor(p).update(actor_id=-1),
                a.update({_CREATION: p.pop(_CREATION)}),
            ),
            "names no real id",
        ),
        (lambda a, p: _creation_actor(p).update(actor_id=0), "App id is 0"),
        (lambda a, p: p.update({_TAGS: _tags(a, p)}), "both applied and pending"),
        (
            lambda a, p: p[_CREATION].update(name="creation"),
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


def test_the_creation_ruleset_applied_with_its_app_id_passes() -> None:
    applied, pending = _both()
    creation = pending.pop(_CREATION)
    creation["bypass_actors"][0]["actor_id"] = 815
    applied[_CREATION] = creation
    assert _problems(applied, pending) == []
