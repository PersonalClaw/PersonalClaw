"""A prerelease tag must never move a stable pointer (RUM-8).

Measured on `main` at 4803c515a, before this atom: `release.yml`'s `images` job
pushed `:${version}` **and `:latest`** unconditionally, and the GitHub-Release
step decided "is this a prerelease?" with
`[ "$REF" != "${REF%rc*}" ] && echo --prerelease`. Two live consequences:

* tagging `v0.3.0-rc.1` moved `personalclaw-{gateway,web}:latest`, and the
  compose file's default is `${PERSONALCLAW_IMAGE_TAG:-latest}` — so the next
  `docker compose pull` on any container install silently landed on a release
  candidate;
* `v0.3.0-beta.2` was not recognized as a prerelease at all (the suffix test only
  ever matched `rc`), so it published as a full Release and `stable`'s
  `select_target` offered it to every user as an upgrade.

And the moving tags the updater *pulls* were never pushed by anything:
`self_update.select_image_tag` (RUM-7) resolves `stable` -> `:X.Y` and `beta` ->
`:beta`, `docs/guides/containers.md` documents both, and no job created either —
so the commands the Updates panel printed named an image that does not exist.

The rule now lives once, in `scripts/release_tags.py`, and this file asserts
**that module** rather than re-deriving the rule: the workflow reads its `tags:`
list and its `gh release create` flags from the same function, and the wiring
rails below fail if a job stops doing so. A test that re-implemented the
classification in order to check it would go green while the workflow published
something else, which is exactly the bug above.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from personalclaw.self_update import select_image_tag

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "release_tags.py"
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"

IMAGE = "ghcr.io/personalclaw/personalclaw-gateway"


def _load_parser():
    """Import `scripts/release_tags.py` (a workflow script, not an installed module)."""
    assert SCRIPT.is_file(), f"parser missing at {SCRIPT}"
    spec = importlib.util.spec_from_file_location("_release_tags", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rt = _load_parser()


# ── 1. the tag-parse matrix ───────────────────────────────────────────────────
#
# (ref, version, minor, is_prerelease, the bare image tags it publishes)
MATRIX: list[tuple[str, str, str, bool, set[str]]] = [
    # Stable: the immutable version, the moving minor, and `latest`.
    ("v1.2.3", "1.2.3", "1.2", False, {"1.2.3", "1.2", "latest"}),
    ("v0.2.1", "0.2.1", "0.2", False, {"0.2.1", "0.2", "latest"}),
    ("v10.0.0", "10.0.0", "10.0", False, {"10.0.0", "10.0", "latest"}),
    # Prerelease shapes: the immutable version and `beta`. Nothing else.
    ("v1.2.3-rc1", "1.2.3-rc1", "1.2", True, {"1.2.3-rc1", "beta"}),
    ("v1.2.3-rc.1", "1.2.3-rc.1", "1.2", True, {"1.2.3-rc.1", "beta"}),
    ("v1.2.3-beta.2", "1.2.3-beta.2", "1.2", True, {"1.2.3-beta.2", "beta"}),
    # A bare `-rc` with no number is still a prerelease.
    ("v1.2.3-rc", "1.2.3-rc", "1.2", True, {"1.2.3-rc", "beta"}),
    ("v0.3.0-rc1", "0.3.0-rc1", "0.3", True, {"0.3.0-rc1", "beta"}),
]

# Refused shapes: (ref, a fragment of the reason).
REFUSED: list[tuple[str, str]] = [
    ("1.2.3", "not a release tag"),  # no `v` prefix
    ("main", "not a release tag"),  # a branch, e.g. a workflow_dispatch run
    ("v1.2", "not a release tag"),  # not X.Y.Z
    ("v1.2.3.4", "not a release tag"),
    ("v1.2.3-", "not a release tag"),  # empty prerelease suffix
    ("vX.Y.Z", "not a release tag"),
    ("", "empty ref"),
    # Build metadata: `+` is illegal in a Docker tag, and the conventional `+`->`_`
    # rewrite would mint a tag `select_image_tag`'s pin arm can never ask for.
    ("v1.2.3+build.5", "build metadata"),
    ("v1.2.3-rc.1+build.5", "build metadata"),
]


@pytest.mark.parametrize("ref,version,minor,is_pre,tags", MATRIX)
def test_matrix_classification(ref, version, minor, is_pre, tags) -> None:
    parsed = rt.parse_release_ref(ref)
    assert parsed.version == version
    assert parsed.minor == minor
    assert parsed.is_prerelease is is_pre
    assert set(rt.image_tag_names(parsed)) == tags
    # The fully-qualified refs are the bare tags on the image, nothing invented.
    assert set(rt.image_tags(parsed, IMAGE)) == {f"{IMAGE}:{t}" for t in tags}


@pytest.mark.parametrize("ref,reason", REFUSED)
def test_unparseable_refs_are_refused(ref, reason) -> None:
    """A ref the parser cannot classify must RAISE, never fall through to a push."""
    with pytest.raises(ValueError) as exc:
        rt.parse_release_ref(ref)
    assert reason in str(exc.value)


@pytest.mark.parametrize("ref,_reason", REFUSED)
def test_refused_refs_exit_nonzero(ref, _reason) -> None:
    """The CLI the workflow calls fails the job on a ref it cannot classify."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", ref, "--image", IMAGE, "--github-output"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, f"{ref!r} was accepted: {proc.stdout!r}"
    # And it must not have emitted a tag list on the way out.
    assert "image_tags" not in proc.stdout


# ── 2. the negative cases — what a prerelease must NEVER publish ──────────────


def _prerelease_refs() -> list[str]:
    return [ref for ref, _v, _m, is_pre, _t in MATRIX if is_pre]


def _stable_refs() -> list[str]:
    return [ref for ref, _v, _m, is_pre, _t in MATRIX if not is_pre]


def test_the_matrix_covers_both_lines() -> None:
    """Vacuity floor: the negative rails below iterate a NON-empty prerelease set."""
    assert len(_prerelease_refs()) >= 4, "negative rails would be vacuous"
    assert len(_stable_refs()) >= 2, "positive rails would be vacuous"


@pytest.mark.parametrize("ref", _prerelease_refs())
def test_a_prerelease_never_publishes_latest(ref) -> None:
    """The bug this atom exists to prevent: `:latest` moving onto a candidate."""
    tags = rt.image_tag_names(rt.parse_release_ref(ref))
    assert rt.MOVING_STABLE not in tags
    assert "latest" not in tags
    assert f"{IMAGE}:latest" not in rt.image_tags(rt.parse_release_ref(ref), IMAGE)


@pytest.mark.parametrize("ref", _prerelease_refs())
def test_a_prerelease_never_publishes_a_moving_minor_for_any_line(ref) -> None:
    """No bare `X.Y` tag at all — not its own line's, and not another line's.

    Withholding its OWN minor is the deliberate half: once `v0.3.0` ships,
    `:0.3` has to mean the stable 0.3 line, and someone who pinned `:0.3` never
    opted into release candidates.
    """
    tags = rt.image_tag_names(rt.parse_release_ref(ref))
    moving = [t for t in tags if rt.MOVING_MINOR_RE.match(t)]
    assert moving == [], f"{ref} would move the minor tag(s) {moving}"


def test_a_prerelease_of_a_new_line_leaves_the_previous_lines_minor_alone() -> None:
    """The named case: tagging `v0.3.0-rc1` must not touch `:0.2` (nor `:0.3`)."""
    tags = set(rt.image_tags(rt.parse_release_ref("v0.3.0-rc1"), IMAGE))
    assert f"{IMAGE}:0.2" not in tags, "an rc moved the shipped 0.2 line's minor tag"
    assert f"{IMAGE}:0.3" not in tags, "an rc claimed the 0.3 line's minor tag early"
    assert f"{IMAGE}:latest" not in tags
    assert tags == {f"{IMAGE}:0.3.0-rc1", f"{IMAGE}:beta"}
    # ...while the STABLE cut of that same line does move its minor. Without this
    # the rail above would be satisfied by a parser that publishes nothing.
    stable = set(rt.image_tags(rt.parse_release_ref("v0.3.0"), IMAGE))
    assert f"{IMAGE}:0.3" in stable
    assert f"{IMAGE}:latest" in stable


@pytest.mark.parametrize("ref", _prerelease_refs())
def test_a_prerelease_never_claims_the_github_latest_pointer(ref) -> None:
    flags = rt.github_release_flags(rt.parse_release_ref(ref))
    assert "--prerelease" in flags
    assert not any(f.startswith("--latest") for f in flags), flags


@pytest.mark.parametrize("ref", _stable_refs())
def test_a_stable_release_claims_latest_and_is_not_a_prerelease(ref) -> None:
    flags = rt.github_release_flags(rt.parse_release_ref(ref))
    assert flags == ["--latest"]


# ── 3. the emitted $GITHUB_OUTPUT block ───────────────────────────────────────


def test_github_output_block_is_well_formed_for_a_prerelease() -> None:
    parsed = rt.parse_release_ref("v0.3.0-beta.2")
    block = rt.render_github_output(parsed, IMAGE)
    assert "version=0.3.0-beta.2" in block
    assert "prerelease=true" in block
    assert "gh_release_flags=--prerelease" in block
    # Multi-line values need the heredoc form, and the delimiter must not collide.
    body = block.split(f"image_tags<<{rt._OUTPUT_DELIMITER}\n", 1)[1]
    listed = body.split(f"\n{rt._OUTPUT_DELIMITER}", 1)[0].splitlines()
    assert listed == [f"{IMAGE}:0.3.0-beta.2", f"{IMAGE}:beta"]
    assert rt._OUTPUT_DELIMITER not in "".join(listed)


def test_github_output_block_omits_image_tags_when_no_image_is_given() -> None:
    """The `build` job classifies the tag with no image repository in hand."""
    block = rt.render_github_output(rt.parse_release_ref("v1.2.3"))
    assert "image_tags" not in block
    assert "version=1.2.3" in block
    assert "prerelease=false" in block
    assert "gh_release_flags=--latest" in block


def test_cli_emits_the_same_block_the_workflow_appends() -> None:
    """The workflow's real invocation, run for real."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "v1.2.3", "--image", IMAGE, "--github-output"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == rt.render_github_output(rt.parse_release_ref("v1.2.3"), IMAGE)


# ── 4. the wiring — release.yml cannot say something this file does not check ──


@pytest.fixture(scope="module")
def release_yml() -> str:
    """release.yml with whole-line `#` comments dropped — what the jobs actually DO.

    The comments are kept out on purpose. They quote the two expressions this
    atom deleted (`${REF%rc*}`, `lstrip("v")`) in order to explain what was wrong
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


# ── 5. publisher/consumer coherence — RUM-7 pulls what RUM-8 pushes ───────────
#
# `select_image_tag` is the PRODUCTION consumer (the Updates panel and
# `personalclaw update` on a container install both route through it). Every tag
# it can name has to be a tag some release actually published, or the commands the
# panel prints 404. That was the state on `main`: it resolved `:X.Y` and `:beta`,
# and nothing ever pushed either.


def _view(tag: str, prerelease: bool = False) -> dict[str, object]:
    return {"tag": tag, "name": tag, "body": "", "prerelease": prerelease}


RELEASES = [
    _view("v0.3.0-rc.1", prerelease=True),
    _view("v0.2.1"),
    _view("v0.2.0"),
]


def test_the_stable_channels_moving_minor_is_published_by_that_release() -> None:
    want = select_image_tag(RELEASES, "stable")
    assert want == "0.2"  # the consumer's answer, unchanged by this atom
    assert f"{IMAGE}:{want}" in rt.image_tags(rt.parse_release_ref("v0.2.1"), IMAGE)


def test_the_beta_channels_moving_tag_is_published_by_a_prerelease() -> None:
    want = select_image_tag(RELEASES, "beta")
    assert want == "beta"
    assert f"{IMAGE}:{want}" in rt.image_tags(rt.parse_release_ref("v0.3.0-rc.1"), IMAGE)
    # ...and NOT by a stable cut: `:beta` must not be dragged back onto a stable image.
    assert f"{IMAGE}:beta" not in rt.image_tags(rt.parse_release_ref("v0.2.1"), IMAGE)


def test_a_version_pin_resolves_to_a_tag_the_pipeline_published() -> None:
    for pin, ref in (("0.2.1", "v0.2.1"), ("0.3.0-rc.1", "v0.3.0-rc.1")):
        want = select_image_tag(RELEASES, "stable", pin)
        assert want, f"pin {pin} resolved to nothing"
        assert f"{IMAGE}:{want}" in rt.image_tags(
            rt.parse_release_ref(ref), IMAGE
        ), f"pin {pin} names image tag :{want}, which {ref} does not publish"


def test_the_compose_default_tag_is_published_by_stable() -> None:
    """`compose.yaml` falls back to `${PERSONALCLAW_IMAGE_TAG:-latest}`."""
    compose = (REPO_ROOT / "deploy" / "compose" / "compose.yaml").read_text(encoding="utf-8")
    assert "${PERSONALCLAW_IMAGE_TAG:-latest}" in compose, "compose default changed — re-check"
    assert f"{IMAGE}:latest" in rt.image_tags(rt.parse_release_ref("v0.2.1"), IMAGE)
