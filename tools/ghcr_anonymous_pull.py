"""Assert both GHCR container packages are anonymously pullable — DIST-14 clause 2.

`DIST-14`'s `done_when` has two halves. The first is the owner's (flip
`personalclaw-gateway` and `personalclaw-web` to public). The second is this file:

    AND a scheduled CI job asserts both with no credentials in scope, red if either
    package stops being anonymously pullable

**Why this is not a one-line curl.** The endpoint a reader reaches for first is
`/v2/<repo>/tags/list`, and it is VACUOUS here. With no anonymous token obtainable it
answers `DENIED "invalid token"` **identically** for a private package and for a package
that does not exist — it is rejecting the empty bearer, not the repository. Measured
2026-09-22 against `personalclaw/personalclaw-gateway`, `personalclaw/personalclaw-web`
and a deliberately-absent control: three identical bodies. So a rail built on that
endpoint alone **stays green on a deleted package**, which is the exact regression it
exists to catch.

The token endpoint is the only probe that discriminates, and it discriminates on the
error **code**, not on the HTTP status (both failure modes arrive as a JSON error body,
and urllib raises for both — so this module reads the body out of the `HTTPError` rather
than branching on its status):

===================  ===========================================================
`token` in the body  public — an anonymous pull is possible
`UNAUTHORIZED`       the package EXISTS and is PRIVATE
`DENIED`             the package is ABSENT (never pushed, or deleted)
===================  ===========================================================

So the assertion per image is two-step, and neither step alone is sufficient:

1. the token endpoint grants a token — this catches privacy AND deletion, and tells them
   apart in the failure message;
2. `tags/list`, carrying that **anonymous** token, contains the latest release tag —
   this catches public-but-untagged, which step 1 cannot see.

**Three controls, because this rail's entire output is a zero.**

* **positive** (`open-webui/open-webui`) — a third-party package that is genuinely public.
  Without it a GHCR outage or a token-protocol change reads as "someone flipped our
  packages private" and the rail cries wolf.
* **absent** (a name held deliberately unpushed) — must answer `DENIED`. This is the
  guard on the DISCRIMINATOR itself: if GHCR ever starts answering `UNAUTHORIZED` for an
  absent repository, step 1's private/absent split silently inverts and every failure
  message afterwards names the wrong cause.
* **credential-free** — the environment must carry no registry credential. "No
  credentials in scope" is a literal clause requirement: the default `GITHUB_TOKEN` **can
  pull a private package**, which is precisely why the release job's own smoke pull (it is
  credentialed) never caught this. `permissions: {}` in the workflow is the declaration;
  this check is the enforcement, so adding an `env:` line later fails loudly instead of
  quietly making the green meaningless.

A control failing is NOT the same finding as our packages regressing, so it exits with a
different code and a differently-titled annotation. Both are red — an unmeasurable run
must never pass — but a human reading the annotation can tell which happened.

The expected release tag is not hardcoded and not re-derived: it comes from this repo's
own `scripts/release_tags.py`, the single parser `release.yml` uses to decide which tags a
release publishes. A copy of that rule here could drift, and then this rail would assert a
tag the publisher never pushed. `:latest` is deliberately NOT asserted — whether `:latest`
resolves to a stable release or a release candidate is a separate, credential-gated
question (`CHAIRMAN-ACTIONS.md` DIST-14 item 4) and not this clause.

Stdlib only: it runs on a bare runner python with no install step, same as
`scripts/release_tags.py`.

Usage::

    python3 tools/ghcr_anonymous_pull.py
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

REGISTRY = "ghcr.io"

#: The GHCR namespace our packages live in (lowercase — GHCR paths are case-sensitive).
OWNER = "personalclaw"

#: The two packages `release.yml`'s `images` matrix publishes.
OUR_IMAGES = ("personalclaw-gateway", "personalclaw-web")

#: A third-party package that is genuinely public. The positive control: if THIS stops
#: granting an anonymous token, the fault is GHCR's or the protocol's, not ours.
POSITIVE_CONTROL = "open-webui/open-webui"

#: A repository in our own namespace that must never exist. The absent control: it proves
#: `DENIED` still means "absent", which is what lets `UNAUTHORIZED` mean "private".
ABSENT_CONTROL = f"{OWNER}/personalclaw-absent-control-do-not-create"

#: The registry's error code for a repository that exists but is not publicly readable.
CODE_PRIVATE = "UNAUTHORIZED"

#: The registry's error code for a repository that is not there at all.
CODE_ABSENT = "DENIED"

#: Environment variables that would put a registry credential in scope. A pull that any of
#: these could have authenticated proves nothing about ANONYMOUS pullability.
CREDENTIAL_ENV = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GHCR_TOKEN",
    "CR_PAT",
    "DOCKER_PASSWORD",
    "REGISTRY_PASSWORD",
    "REGISTRY_TOKEN",
)

EXIT_OK = 0
#: Our packages are not anonymously pullable. The finding this rail exists to make.
EXIT_REGRESSED = 1
#: The run could not measure anything trustworthy (a control failed, or a credential was
#: in scope). Red, but a different finding — and never silently green.
EXIT_UNMEASURABLE = 2

_TIMEOUT_SECS = 30
_MAX_BODY_BYTES = 1 * 1024 * 1024

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_RELEASE_TAGS = _ROOT / "scripts" / "release_tags.py"


# ── the release-tag rule, loaded from its ONE owner ───────────────────────────


@functools.lru_cache(maxsize=1)
def load_release_tags(path: pathlib.Path = _RELEASE_TAGS) -> Any:
    """Import `scripts/release_tags.py` — a workflow script, not an installed module.

    By path, the same way `tests/test_release_tag_semantics.py` does it: `scripts/` is not
    a package, and resolving it through `sys.path` would make the import depend on the
    caller's cwd. Loading the real module rather than re-deriving the rule is the point —
    a second copy of "which tag does a release publish" is how a rail comes to assert a
    tag the publisher never pushed.

    The `sys.modules` registration is NOT optional and not bookkeeping. `@dataclass` (which
    `release_tags.ReleaseRef` uses) resolves `sys.modules[cls.__module__].__dict__` while
    deciding whether an annotation is `KW_ONLY`; on an unregistered module that lookup is
    `None` and the decorator raises `AttributeError` at import. Measured on CPython 3.14.7.
    """
    spec = importlib.util.spec_from_file_location("_release_tags", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable on a checkout
        raise RuntimeError(f"cannot load the release-tag rule from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def latest_release_ref(tag_lines: Iterable[str]) -> str:
    """The newest STABLE `vX.Y.Z` among *tag_lines*, or `""` if there is none.

    Ordered numerically, not lexicographically: `v0.1.10` is newer than `v0.1.9` and a
    string sort says the opposite. Prereleases are skipped — a release candidate is not
    "the latest release tag", and `release_tags` already refuses to move any stable
    pointer for one.
    """
    release_tags = load_release_tags()
    best: tuple[tuple[int, int, int], str] | None = None
    for line in tag_lines:
        candidate = line.strip()
        if not candidate:
            continue
        try:
            parsed = release_tags.parse_release_ref(candidate)
        except ValueError:
            continue  # not a release tag: a branch-shaped tag, build metadata, `v1.2`
        if parsed.is_prerelease:
            continue
        key = tuple(int(part) for part in parsed.version.split("."))
        assert len(key) == 3
        if best is None or key > best[0]:
            best = (key, candidate)  # type: ignore[assignment]
    return "" if best is None else best[1]


def expected_image_tag(tag_lines: Iterable[str]) -> str:
    """The immutable image tag the newest stable release published, or `""`.

    `release_tags.image_tag_names` returns `[version, minor, "latest"]` for a stable line;
    the first is the exact `X.Y.Z`, which is the one tag no later release can move. The
    moving pointers are deliberately not asserted (see the module docstring).
    """
    ref = latest_release_ref(tag_lines)
    if not ref:
        return ""
    release_tags = load_release_tags()
    parsed = release_tags.parse_release_ref(ref)
    return str(release_tags.image_tag_names(parsed)[0])


def git_release_tags(root: pathlib.Path = _ROOT) -> list[str]:
    """`v*` tags in the checkout. Empty when the clone was made without tags."""
    try:
        completed = subprocess.run(
            ["git", "tag", "--list", "v*"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    return completed.stdout.splitlines()


# ── the probes ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenProbe:
    """What the token endpoint said about one repository."""

    repository: str
    #: `granted` | `private` | `absent` | `unknown` | `unreachable`
    kind: str
    #: The registry's error code, verbatim. `""` when a token was granted.
    code: str
    #: The anonymous token, when one was granted. Never logged.
    token: str = ""
    #: Why the probe could not be classified. `""` otherwise.
    detail: str = ""


@dataclass(frozen=True)
class TagsProbe:
    """What `tags/list` said, using the anonymous token from :class:`TokenProbe`."""

    repository: str
    tags: tuple[str, ...] = ()
    #: `""` on success; otherwise why the tag list is unknown.
    detail: str = ""


def token_url(repository: str) -> str:
    return f"https://{REGISTRY}/token" f"?scope=repository:{repository}:pull" f"&service={REGISTRY}"


def tags_url(repository: str) -> str:
    return f"https://{REGISTRY}/v2/{repository}/tags/list"


def classify_token_body(repository: str, body: bytes | str) -> TokenProbe:
    """Classify a token-endpoint body by its error CODE, never by an HTTP status.

    A body carrying `token` is a grant. Otherwise the first error's `code` decides, and an
    unrecognised code is `unknown` rather than being folded into either failure — silently
    treating a new code as "private" would report the wrong cause forever.
    """
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return TokenProbe(repository, "unknown", "", detail="the body was not JSON")
    if not isinstance(payload, dict):
        return TokenProbe(repository, "unknown", "", detail="the body was not a JSON object")

    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str(first.get("code") or "")
        if code == CODE_PRIVATE:
            return TokenProbe(repository, "private", code)
        if code == CODE_ABSENT:
            return TokenProbe(repository, "absent", code)
        return TokenProbe(repository, "unknown", code, detail=str(first.get("message") or ""))

    token = payload.get("token")
    if isinstance(token, str) and token:
        return TokenProbe(repository, "granted", "", token=token)
    return TokenProbe(
        repository,
        "unknown",
        "",
        detail="the body carried neither an `errors` list nor a `token`",
    )


def tags_from_body(repository: str, body: bytes | str) -> TagsProbe:
    """The `tags` array from a `tags/list` body, or a TagsProbe naming what was wrong."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return TagsProbe(repository, detail="the body was not JSON")
    if not isinstance(payload, dict):
        return TagsProbe(repository, detail="the body was not a JSON object")
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        return TagsProbe(repository, detail=f"the registry answered {first.get('code')!r}")
    tags = payload.get("tags")
    if not isinstance(tags, list):
        return TagsProbe(repository, detail="the body carried no `tags` array")
    return TagsProbe(repository, tuple(str(tag) for tag in tags if isinstance(tag, str)))


def credentials_in_scope(environ: dict[str, str] | None = None) -> list[str]:
    """Names of :data:`CREDENTIAL_ENV` variables that are set AND non-empty."""
    env = os.environ if environ is None else environ
    return [name for name in CREDENTIAL_ENV if (env.get(name) or "").strip()]


def _read(url: str, token: str = "") -> bytes:
    """GET *url*, returning the body for success AND for an error status.

    No `Authorization` header unless *token* is the anonymous token the registry itself
    just handed out — that token is not a credential, it is the thing being proven
    obtainable. The `HTTPError` branch is load-bearing: 401 and 403 both carry the JSON
    error body this module classifies, and urllib raises on both.
    """
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECS) as response:
            return bytes(response.read(_MAX_BODY_BYTES))
    except urllib.error.HTTPError as exc:
        return bytes(exc.read(_MAX_BODY_BYTES))


def probe_token(repository: str) -> TokenProbe:
    try:
        return classify_token_body(repository, _read(token_url(repository)))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TokenProbe(repository, "unreachable", "", detail=f"{exc}")


def probe_tags(repository: str, token: str) -> TagsProbe:
    try:
        return tags_from_body(repository, _read(tags_url(repository), token=token))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return TagsProbe(repository, detail=f"{exc}")


# ── the verdict ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reading:
    """Everything one run measured. Adjudicated by a pure function, so it is testable."""

    ours: tuple[tuple[TokenProbe, TagsProbe | None], ...]
    positive: TokenProbe
    absent: TokenProbe
    expected_tag: str
    credentials: tuple[str, ...] = ()


def _annotation(title: str, message: str) -> str:
    return f"::error title={title}::{message}"


def adjudicate(reading: Reading) -> tuple[int, list[str]]:
    """`(exit code, GitHub annotations)` for one :class:`Reading`.

    Controls first, on purpose. A failing control means the run measured nothing about our
    packages, and reporting "our packages went private" off an unmeasurable run is the
    cry-wolf failure the controls exist to prevent.
    """
    if reading.credentials:
        return EXIT_UNMEASURABLE, [
            _annotation(
                "GHCR rail had a credential in scope",
                f"{', '.join(reading.credentials)} is set, and a credential that can pull a "
                "PRIVATE package makes a green here meaningless. DIST-14 asks for the assertion "
                "with no credentials in scope: keep `permissions: {}` on the job and map no "
                "token into the step env.",
            )
        ]

    if reading.absent.kind != "absent":
        return EXIT_UNMEASURABLE, [
            _annotation(
                "GHCR's absent/private discriminator changed",
                f"the absent control {ABSENT_CONTROL} answered "
                f"{reading.absent.kind}/{reading.absent.code or '-'} instead of {CODE_ABSENT}. "
                f"{CODE_ABSENT} is what makes {CODE_PRIVATE} mean 'private', so until this is "
                "re-derived every failure message below may name the wrong cause. Measure the "
                "two codes by hand before trusting this rail again.",
            )
        ]

    if reading.positive.kind != "granted":
        return EXIT_UNMEASURABLE, [
            _annotation(
                "GHCR anonymous pull is broken registry-wide",
                f"the positive control {POSITIVE_CONTROL} answered "
                f"{reading.positive.kind}/{reading.positive.code or '-'} instead of granting a "
                "token, so anonymous pull is failing for a package that is definitely public. "
                "This is GHCR or the token protocol, NOT our packages — do not flip anything.",
            )
        ]

    if not reading.expected_tag:
        return EXIT_UNMEASURABLE, [
            _annotation(
                "GHCR rail could not resolve the latest release tag",
                "no stable `vX.Y.Z` tag is present in the checkout, so the tag half of the "
                "assertion would silently pass on an empty expectation. The workflow needs the "
                "tags fetched (`fetch-depth: 0`).",
            )
        ]

    annotations: list[str] = []
    for token, tags in reading.ours:
        image = f"{REGISTRY}/{token.repository}"
        if token.kind == "private":
            annotations.append(
                _annotation(
                    f"{token.repository} is not anonymously pullable",
                    f"the token endpoint answered {CODE_PRIVATE}, which means the package EXISTS "
                    f"and is PRIVATE. `docker pull {image}` fails for every user, and the default "
                    "compose file pulls exactly this. Flip the package to public in its GitHub "
                    "package settings.",
                )
            )
            continue
        if token.kind == "absent":
            annotations.append(
                _annotation(
                    f"{token.repository} IS GONE",
                    f"the token endpoint answered {CODE_ABSENT}, the code for a repository that "
                    "does not exist — the absent control confirms that reading this run. The "
                    "package was deleted or never pushed; a `tags/list` probe would have called "
                    "this 'private' and stayed green.",
                )
            )
            continue
        if token.kind != "granted":
            annotations.append(
                _annotation(
                    f"{token.repository} answered something new",
                    f"the token endpoint returned {token.kind}/{token.code or '-'} "
                    f"({token.detail or 'no detail'}), which is neither a grant nor a code this "
                    "rail knows. Classify it by hand before changing anything.",
                )
            )
            continue

        if tags is None or tags.detail:
            annotations.append(
                _annotation(
                    f"{token.repository} granted a token but its tags are unreadable",
                    f"the anonymous token worked and `tags/list` then failed: "
                    f"{(tags.detail if tags else 'the tag probe never ran')}.",
                )
            )
            continue
        if reading.expected_tag not in tags.tags:
            annotations.append(
                _annotation(
                    f"{token.repository} is public but has no {reading.expected_tag} tag",
                    f"an anonymous pull is possible, but the latest release tag "
                    f"{reading.expected_tag!r} is not in the tag list "
                    f"({', '.join(sorted(tags.tags)) or 'empty'}), so "
                    f"`docker pull {image}:{reading.expected_tag}` fails. The release that "
                    "should have pushed it did not.",
                )
            )

    return (EXIT_REGRESSED if annotations else EXIT_OK), annotations


def _outcome(probe: TokenProbe) -> str:
    return probe.kind if not probe.code else f"{probe.kind} ({probe.code})"


def render(reading: Reading) -> str:
    """The step log. Every leg is printed whatever the verdict — a red is diagnosed here."""
    every = [token for token, _ in reading.ours] + [reading.positive, reading.absent]
    repo_width = max([len("repository")] + [len(probe.repository) for probe in every]) + 2
    code_width = max([len("token endpoint")] + [len(_outcome(probe)) for probe in every]) + 2

    def row(leg: str, repository: str, outcome: str, last: str) -> str:
        return f"{leg:<10}{repository:<{repo_width}}{outcome:<{code_width}}{last}"

    lines = [
        f"expected release tag: {reading.expected_tag or '(none resolved)'}",
        "",
        row("leg", "repository", "token endpoint", "tags"),
    ]
    for token, tags in reading.ours:
        if tags is None:
            tag_cell = "-"
        elif tags.detail:
            tag_cell = f"unreadable ({tags.detail})"
        else:
            shown = ", ".join(sorted(tags.tags)[:6]) or "empty"
            tag_cell = f"{len(tags.tags)} tags: {shown}"
        lines.append(row("ours", token.repository, _outcome(token), tag_cell))
    for label, probe, wanted in (
        ("positive", reading.positive, "granted"),
        ("absent", reading.absent, "absent"),
    ):
        verdict = "as expected" if probe.kind == wanted else f"EXPECTED {wanted}"
        lines.append(row(label, probe.repository, _outcome(probe), verdict))
    return "\n".join(lines)


# ── the entry point ───────────────────────────────────────────────────────────


def measure(images: Sequence[str] = OUR_IMAGES) -> Reading:
    """Run every leg. Ours are probed even when a control fails, so the log diagnoses."""
    ours: list[tuple[TokenProbe, TagsProbe | None]] = []
    for image in images:
        repository = f"{OWNER}/{image}"
        token = probe_token(repository)
        tags = probe_tags(repository, token.token) if token.kind == "granted" else None
        ours.append((token, tags))
    return Reading(
        ours=tuple(ours),
        positive=probe_token(POSITIVE_CONTROL),
        absent=probe_token(ABSENT_CONTROL),
        expected_tag=expected_image_tag(git_release_tags()),
        credentials=tuple(credentials_in_scope()),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)

    reading = measure()
    print(render(reading))
    exit_code, annotations = adjudicate(reading)
    if annotations:
        print()
        for annotation in annotations:
            print(annotation)
    elif exit_code == EXIT_OK:
        print()
        print(
            f"both packages are anonymously pullable and carry {reading.expected_tag}, "
            "with no credentials in scope."
        )
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
