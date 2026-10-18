"""The source-tree checks split from
``tests/test_release_tag_semantics.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import pytest
from release_tag_semantics_shared import REPO_ROOT

RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def release_yml() -> str:
    """release.yml with whole-line `#` comments dropped — what the jobs actually DO.

    The comments are kept out on purpose. They quote the two expressions this
    change deleted (`${REF%rc*}`, `lstrip("v")`) in order to explain what was wrong
    with them, and a rail that scanned them would red on its own documentation —
    which is a false positive about commentary, not a finding about behaviour.
    Every pattern asserted below is a step's `run:`/`with:` content, never a note.
    """
    lines = RELEASE_YML.read_text(encoding="utf-8").splitlines()
    effective = [ln for ln in lines if not ln.lstrip().startswith("#")]
    assert len(effective) > 150, "release.yml is unexpectedly small — the rails below would lie"
    # Vacuity floor: stripping comments must not have taken the anchors with it.
    text = "\n".join(effective)
    for anchor in ("gh release create", "scripts/release_tags.py", "docker/build-push-action"):
        assert anchor in text, f"anchor {anchor!r} is only in a comment — rails would be vacuous"
    return text


def test_the_image_job_takes_its_tags_from_the_parser(release_yml: str) -> None:
    """`tags:` is rendered by `scripts/release_tags.py`, not written by hand."""
    refs = re.findall(
        r"tags:\s*\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.image_tags\s*\}\}", release_yml
    )
    assert refs, "the images job no longer reads its tag list from a step output"
    for step_id in refs:
        assert f"id: {step_id}" in release_yml, f"tags: references a missing step id {step_id!r}"
    assert "scripts/release_tags.py" in release_yml
    assert "--image" in release_yml and "--github-output" in release_yml


def test_no_job_hardcodes_a_moving_image_tag(release_yml: str) -> None:
    """The `:latest` / `:X.Y` literals are gone — a constant cannot be conditional."""
    literals = re.findall(r"ghcr\.io/[\w./-]+:(latest|beta|\d+\.\d+)\b", release_yml)
    assert literals == [], f"release.yml still hardcodes moving image tag(s): {literals}"


def test_the_release_step_takes_its_stability_from_the_parser(release_yml: str) -> None:
    assert "gh release create" in release_yml, "anchor missing — this rail would be vacuous"
    assert "$GH_RELEASE_FLAGS" in release_yml
    assert "gh_release_flags: ${{ steps.ver.outputs.gh_release_flags }}" in release_yml
    # The shell suffix test that only ever recognized `rc` must be gone.
    assert "%rc*" not in release_yml
    assert "--prerelease)" not in release_yml


def test_the_version_is_derived_exactly_once(release_yml: str) -> None:
    """No job re-derives the version with its own shell/python expression."""
    assert "GITHUB_REF_NAME#v" not in release_yml
    assert 'lstrip("v")' not in release_yml
    assert "version: ${{ steps.ver.outputs.version }}" in release_yml
