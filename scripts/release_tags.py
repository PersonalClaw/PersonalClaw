#!/usr/bin/env python3
"""What a release tag publishes — the ONE parser `release.yml` and its test share.

`release.yml` triggers on `tags: ["v*"]`, and until RUM-8 it derived the answer
three different times in three different shell dialects:

* `build`  — `version=${GITHUB_REF_NAME#v}`;
* `images` — a hardcoded `tags:` block pushing `:<version>` **and `:latest`,
  unconditionally**;
* `notes`  — `$([ "$GITHUB_REF_NAME" != "${GITHUB_REF_NAME%rc*}" ] && echo --prerelease)`.

Each was wrong in its own way, and the two failures compounded:

* **`:latest` moved for a prerelease.** Tagging `v0.3.0-rc.1` pushed
  `personalclaw-gateway:latest`, so every container install running the compose
  file's default (`${PERSONALCLAW_IMAGE_TAG:-latest}`) was silently moved onto a
  release candidate by the next `docker compose pull`.
* **The prerelease flag only saw `rc`.** The suffix-strip test matched `rc`
  anywhere in the ref and nothing else, so `v0.3.0-beta.2` was published as a
  full, non-prerelease GitHub Release — which `stable`'s
  `self_update.select_target` then offered to every user as an upgrade.
* **The moving tags the updater pulls were never pushed at all.** RUM-7 shipped
  `self_update.select_image_tag`, which resolves `stable` -> the moving minor
  `:X.Y` and `beta` -> `:beta`; `docs/guides/containers.md` documents both. No
  job ever created either tag, so the commands the Updates panel printed
  resolved to an image that does not exist.

So the rule lives here, once, and both the workflow and
`tests/test_release_tag_semantics.py` read it from this module. A test that
re-implemented the rule in order to check it would pass while the workflow did
something else — which is precisely the bug above.

**The scheme** (tag convention §3.6; the consumer half is
`self_update.select_image_tag`):

| Ref | GitHub Release | Image tags |
|---|---|---|
| `v1.2.3`       | latest    | `:1.2.3`, `:1.2`, `:latest` |
| `v1.2.3-rc.1`  | prerelease | `:1.2.3-rc.1`, `:beta` |

A prerelease moves **no** stable pointer: not `:latest`, and not the moving
minor `:X.Y`. The minor is deliberately withheld rather than pushed for the
prerelease's own line — once `v1.2.3` ships, `:1.2` must mean the stable 1.2
line, and a user who pinned `:1.2` never opted into release candidates.

Stable releases pass `--latest` explicitly instead of relying on `gh`'s
automatic date/version heuristic. Note this makes a *back-patch* of an older
line (`v0.1.9` cut after `v0.2.0`) claim "Latest" — the same thing the API's
`make_latest` default already did before RUM-8. Suppressing that needs the
releases list, which no job here fetches; it is not in this atom's scope.

Refusals are loud on purpose — this is a publish path, and a ref this module
cannot classify must never fall through to "push the moving tags anyway":

* a ref without the `v` prefix, or not `X.Y.Z` (`main`, `1.2.3`, `v1.2`);
* **build metadata** (`v1.2.3+build.5`). A `+` is not legal in a Docker tag
  (`[A-Za-z0-9_][A-Za-z0-9._-]*`), and the conventional `+`->`_` rewrite would
  mint a tag no consumer can ask for: `select_image_tag`'s pin arm returns
  `normalize_version(tag)`, which keeps the `+`. Publisher and consumer have to
  agree on the spelling, so this shape is rejected rather than mangled.

Usage (stdlib only — it runs on a bare runner python, before any install)::

    python3 scripts/release_tags.py --ref v1.2.3
    python3 scripts/release_tags.py --ref v1.2.3 --github-output >> "$GITHUB_OUTPUT"
    python3 scripts/release_tags.py --ref v1.2.3 --image ghcr.io/o/p \
        --github-output >> "$GITHUB_OUTPUT"
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

# vX.Y.Z with an optional `-<prerelease>` suffix. Deliberately NOT full semver:
# build metadata is refused (see the module docstring), so `+` has no branch here.
_TAG_RE = re.compile(
    r"^v(?P<major>0|[1-9][0-9]*)"
    r"\.(?P<minor>0|[1-9][0-9]*)"
    r"\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z][0-9A-Za-z.-]*))?$"
)

#: The moving image tag a STABLE release advances (and the compose file's default).
MOVING_STABLE = "latest"
#: The moving image tag a PRERELEASE advances — the only one it may touch.
MOVING_PRERELEASE = "beta"

#: A bare `X.Y` moving-minor tag. Used by the test's negative rail to prove a
#: prerelease publishes no moving minor for any line.
MOVING_MINOR_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True)
class ReleaseRef:
    """A parsed `vX.Y.Z[-pre]` release ref and everything the pipeline needs from it."""

    ref: str
    #: `X.Y.Z[-pre]` — exactly what `self_update.normalize_version` yields for `ref`,
    #: so a version pin resolves to a tag this pipeline actually pushed.
    version: str
    #: The moving minor `X.Y`. Published for stable lines only.
    minor: str
    #: The prerelease suffix (`rc.1`, `beta.2`, `rc`), or `""` on a stable line.
    prerelease: str

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)


def parse_release_ref(ref: str) -> ReleaseRef:
    """Classify a release ref, or raise `ValueError` naming why it is not one."""
    ref = (ref or "").strip()
    if not ref:
        raise ValueError("empty ref: expected a release tag like v1.2.3")
    if "+" in ref:
        raise ValueError(
            f"{ref!r} carries build metadata: a '+' is not a legal Docker tag character "
            "and rewriting it would mint an image tag the updater cannot pull. "
            "Release as vX.Y.Z or vX.Y.Z-<prerelease>."
        )
    match = _TAG_RE.match(ref)
    if match is None:
        raise ValueError(
            f"{ref!r} is not a release tag: expected vX.Y.Z or vX.Y.Z-<prerelease> "
            "(the leading 'v' is required)"
        )
    prerelease = match.group("prerelease") or ""
    core = f"{match.group('major')}.{match.group('minor')}.{match.group('patch')}"
    return ReleaseRef(
        ref=ref,
        version=f"{core}-{prerelease}" if prerelease else core,
        minor=f"{match.group('major')}.{match.group('minor')}",
        prerelease=prerelease,
    )


def image_tag_names(parsed: ReleaseRef) -> list[str]:
    """The bare image tags *parsed* publishes — the moving ones gated on stability."""
    if parsed.is_prerelease:
        # The exact immutable version, plus the ONE moving prerelease pointer.
        # No `:latest`, and no `:X.Y`: both belong to the stable line.
        return [parsed.version, MOVING_PRERELEASE]
    return [parsed.version, parsed.minor, MOVING_STABLE]


def image_tags(parsed: ReleaseRef, image: str) -> list[str]:
    """Fully-qualified `image:tag` refs for the `docker/build-push-action` `tags:` list."""
    image = image.rstrip("/:")
    if not image:
        raise ValueError("empty image repository")
    return [f"{image}:{tag}" for tag in image_tag_names(parsed)]


def github_release_flags(parsed: ReleaseRef) -> list[str]:
    """`gh release create` flags carrying this ref's stability.

    A prerelease gets `--prerelease` and NOT `--latest`: GitHub refuses to point
    "Latest" at a prerelease, so the flag is both unnecessary and (combined with
    `--prerelease`) a shape this pipeline would be asserting without ever running
    it. A stable release claims `--latest` explicitly.
    """
    return ["--prerelease"] if parsed.is_prerelease else ["--latest"]


# The GITHUB_OUTPUT heredoc delimiter. Fixed and unmistakable: the values are
# versions and image refs, so it cannot collide with content.
_OUTPUT_DELIMITER = "RELEASE_TAGS_EOF"


def render_github_output(parsed: ReleaseRef, image: str = "") -> str:
    """The `$GITHUB_OUTPUT` block for *parsed* (caller appends it to the file)."""
    lines = [
        f"version={parsed.version}",
        f"minor={parsed.minor}",
        f"prerelease={'true' if parsed.is_prerelease else 'false'}",
        f"gh_release_flags={' '.join(github_release_flags(parsed))}",
    ]
    if image:
        tags = image_tags(parsed, image)
        lines.append(f"image_tags<<{_OUTPUT_DELIMITER}")
        lines.extend(tags)
        lines.append(_OUTPUT_DELIMITER)
    return "\n".join(lines) + "\n"


def render_summary(parsed: ReleaseRef, image: str = "") -> str:
    """A human-readable summary for the step log."""
    kind = f"prerelease ({parsed.prerelease})" if parsed.is_prerelease else "stable"
    lines = [
        f"ref:        {parsed.ref}",
        f"version:    {parsed.version}",
        f"line:       {kind}",
        f"gh release: {' '.join(github_release_flags(parsed))}",
    ]
    if image:
        lines.append("image tags:")
        lines.extend(f"  {t}" for t in image_tags(parsed, image))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", required=True, help="the release ref, e.g. v1.2.3 (GITHUB_REF_NAME)")
    ap.add_argument("--image", default="", help="image repository, e.g. ghcr.io/owner/name")
    ap.add_argument(
        "--github-output",
        action="store_true",
        help="emit a $GITHUB_OUTPUT block on stdout instead of a human summary",
    )
    args = ap.parse_args(argv)
    try:
        parsed = parse_release_ref(args.ref)
    except ValueError as exc:
        # Loud and non-zero: a ref this module cannot classify must fail the
        # release, never fall through to publishing the moving tags.
        print(f"release_tags: {exc}", file=sys.stderr)
        return 2
    render = render_github_output if args.github_output else render_summary
    sys.stdout.write(render(parsed, args.image))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
