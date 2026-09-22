"""The credential-free GHCR rail must go red on a DELETED package, not only a private one.

`tools/ghcr_anonymous_pull.py` exists because the endpoint a reader reaches for first
(`/v2/<repo>/tags/list`) is VACUOUS with no anonymous token: it answers `DENIED` identically
for a private package and for one that does not exist, so a rail built on it stays GREEN
after somebody deletes the package — the exact regression it is supposed to catch.

Every test here is a pure-function test over `adjudicate`/`classify_token_body`, so the suite
makes no network call. The rail's own three live legs (positive control, absent control,
credential-free environment) are what prove the measurement; these tests prove the
*adjudication* of that measurement, which is the half a live run cannot check — a live run
against healthy packages exercises only the green branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools import ghcr_anonymous_pull as rail

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO / ".github" / "workflows" / "ghcr-anonymous-pull.yml"

_OURS = f"{rail.OWNER}/{rail.OUR_IMAGES[0]}"


def _granted(repository: str) -> rail.TokenProbe:
    return rail.TokenProbe(repository, "granted", "", token="an-anonymous-token")


def _private(repository: str) -> rail.TokenProbe:
    return rail.TokenProbe(repository, "private", rail.CODE_PRIVATE)


def _absent(repository: str) -> rail.TokenProbe:
    return rail.TokenProbe(repository, "absent", rail.CODE_ABSENT)


def _reading(
    *ours: tuple[rail.TokenProbe, rail.TagsProbe | None],
    positive: rail.TokenProbe | None = None,
    absent: rail.TokenProbe | None = None,
    expected_tag: str = "0.1.3",
    credentials: tuple[str, ...] = (),
) -> rail.Reading:
    return rail.Reading(
        ours=ours,
        positive=positive if positive is not None else _granted(rail.POSITIVE_CONTROL),
        absent=absent if absent is not None else _absent(rail.ABSENT_CONTROL),
        expected_tag=expected_tag,
        credentials=credentials,
    )


def _titles(annotations: list[str]) -> str:
    return "\n".join(annotations)


# ── the discriminator ─────────────────────────────────────────────────────────


def test_the_token_endpoint_tells_private_apart_from_absent() -> None:
    granted = rail.classify_token_body(_OURS, json.dumps({"token": "abc"}))
    private = rail.classify_token_body(_OURS, json.dumps({"errors": [{"code": "UNAUTHORIZED"}]}))
    gone = rail.classify_token_body(_OURS, json.dumps({"errors": [{"code": "DENIED"}]}))

    assert (granted.kind, granted.token) == ("granted", "abc")
    assert (private.kind, private.code) == ("private", rail.CODE_PRIVATE)
    assert (gone.kind, gone.code) == ("absent", rail.CODE_ABSENT)


def test_an_unrecognised_code_is_not_folded_into_either_failure() -> None:
    """Quietly reading a new code as "private" would name the wrong cause forever."""
    probe = rail.classify_token_body(
        _OURS, json.dumps({"errors": [{"code": "TOOMANYREQUESTS", "message": "slow down"}]})
    )

    assert probe.kind == "unknown"
    assert probe.code == "TOOMANYREQUESTS"
    assert probe.detail == "slow down"


def test_a_body_that_is_not_json_is_unknown_rather_than_a_grant() -> None:
    assert rail.classify_token_body(_OURS, b"<html>502</html>").kind == "unknown"
    assert rail.classify_token_body(_OURS, json.dumps({})).kind == "unknown"


# ── the bar: a deleted package must not read green ────────────────────────────


def test_a_deleted_package_is_red_and_named_as_gone() -> None:
    code, annotations = rail.adjudicate(_reading((_absent(_OURS), None)))

    assert code == rail.EXIT_REGRESSED
    assert "IS GONE" in _titles(annotations)


def test_a_private_package_is_red_and_named_as_private() -> None:
    code, annotations = rail.adjudicate(_reading((_private(_OURS), None)))

    assert code == rail.EXIT_REGRESSED
    assert "is not anonymously pullable" in _titles(annotations)
    assert rail.CODE_PRIVATE in _titles(annotations)


def test_public_but_untagged_is_red_because_the_documented_pull_still_fails() -> None:
    reading = _reading((_granted(_OURS), rail.TagsProbe(_OURS, ("0.1.2", "latest"))))

    code, annotations = rail.adjudicate(reading)

    assert code == rail.EXIT_REGRESSED
    assert "has no 0.1.3 tag" in _titles(annotations)


def test_both_public_and_tagged_is_the_only_green() -> None:
    """The positive control ON THE ADJUDICATOR: green is reachable, so the reds mean something."""
    reading = _reading(
        *(
            (_granted(f"{rail.OWNER}/{image}"), rail.TagsProbe(f"{rail.OWNER}/{image}", ("0.1.3",)))
            for image in rail.OUR_IMAGES
        )
    )

    assert rail.adjudicate(reading) == (rail.EXIT_OK, [])


# ── the controls: an unmeasurable run is red, and a DIFFERENT finding ─────────


def test_the_absent_control_guards_the_discriminator_itself() -> None:
    """If GHCR ever answers UNAUTHORIZED for an absent repo, private/absent silently inverts."""
    reading = _reading((_granted(_OURS), rail.TagsProbe(_OURS, ("0.1.3",))), absent=_private(_OURS))

    code, annotations = rail.adjudicate(reading)

    assert code == rail.EXIT_UNMEASURABLE
    assert "discriminator changed" in _titles(annotations)


def test_a_registry_wide_outage_is_not_reported_as_our_packages_regressing() -> None:
    reading = _reading(
        (_private(_OURS), None),
        positive=rail.TokenProbe(rail.POSITIVE_CONTROL, "unreachable", "", detail="timed out"),
    )

    code, annotations = rail.adjudicate(reading)

    assert code == rail.EXIT_UNMEASURABLE
    assert "do not flip anything" in _titles(annotations)
    assert "is not anonymously pullable" not in _titles(annotations)


def test_a_credential_in_scope_can_never_read_green() -> None:
    """A token that can pull a PRIVATE package makes the whole assertion vacuous."""
    reading = _reading(
        *(
            (_granted(f"{rail.OWNER}/{image}"), rail.TagsProbe(f"{rail.OWNER}/{image}", ("0.1.3",)))
            for image in rail.OUR_IMAGES
        ),
        credentials=("GITHUB_TOKEN",),
    )

    code, annotations = rail.adjudicate(reading)

    assert code == rail.EXIT_UNMEASURABLE
    assert "credential in scope" in _titles(annotations)


def test_credentials_in_scope_ignores_an_empty_variable() -> None:
    assert rail.credentials_in_scope({"GITHUB_TOKEN": "ghp_x"}) == ["GITHUB_TOKEN"]
    assert rail.credentials_in_scope({"GITHUB_TOKEN": "  "}) == []
    assert rail.credentials_in_scope({}) == []


def test_an_unresolved_release_tag_never_passes_on_an_empty_expectation() -> None:
    reading = _reading((_granted(_OURS), rail.TagsProbe(_OURS, ("0.1.3",))), expected_tag="")

    code, annotations = rail.adjudicate(reading)

    assert code == rail.EXIT_UNMEASURABLE
    assert "could not resolve the latest release tag" in _titles(annotations)


# ── the expectation comes from the release-tag rule's one owner ───────────────


def test_the_expected_tag_is_the_newest_stable_ordered_numerically() -> None:
    """A string sort puts v0.1.9 after v0.1.10, and a prerelease is not "the latest release"."""
    lines = ["v0.1.9", "v0.1.10", "v0.2.0-rc.1", "feature-branch", "v1.2", ""]

    assert rail.expected_image_tag(lines) == "0.1.10"
    assert rail.expected_image_tag(["v0.2.0-rc.1"]) == ""
    assert rail.expected_image_tag([]) == ""


def test_the_absent_control_is_in_our_namespace_and_is_not_a_published_image() -> None:
    assert rail.ABSENT_CONTROL.startswith(f"{rail.OWNER}/")
    assert rail.ABSENT_CONTROL not in {f"{rail.OWNER}/{image}" for image in rail.OUR_IMAGES}


# ── the workflow's half of "no credentials in scope" ──────────────────────────


def test_the_workflow_declares_no_credentials_and_fetches_the_tags() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    job = workflow["jobs"]["anonymous-pull"]
    checkout, probe = job["steps"]

    # Both levels: the job level is the one that binds, because a job may widen the default.
    assert workflow["permissions"] == {}
    assert job["permissions"] == {}
    # An `env:` block is how a token gets back into scope; the tool would then exit 2.
    assert "env" not in workflow
    assert "env" not in job
    assert not [step for step in job["steps"] if "env" in step]
    assert "secrets." not in text

    assert checkout["uses"].startswith("actions/checkout@")
    # Without the tags the expectation resolves empty and the tag half would pass vacuously.
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    assert probe["run"].strip() == "python3 tools/ghcr_anonymous_pull.py"
    # `on:` is the YAML 1.1 boolean `True` once parsed, hence the two-key lookup.
    assert "schedule" in (workflow.get("on") or workflow[True])
