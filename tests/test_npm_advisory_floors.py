"""🔴 A PATCHED TOP-LEVEL COPY DOES NOT MEAN THE TREE IS PATCHED — the nested duplicate does.

Seven open Dependabot advisories (2026-09-24) all named `package-lock.json`, and all seven read as
"bump a transitive dependency". They were not. `npm ls` at the time resolved the HOISTED copy of
each package to a version that was already fully patched — `dompurify@3.4.13`, `lodash-es@4.18.1`,
`uuid@14.0.1` — so every "is it patched?" probe that asks `node_modules/<pkg>/package.json` answered
YES while the tree carried five vulnerable duplicates one level down:

    node_modules/monaco-editor/node_modules/dompurify        3.4.8    (4 advisories)
    node_modules/chevrotain/node_modules/lodash-es           4.17.23  (2 advisories)
    node_modules/@chevrotain/gast/node_modules/lodash-es     4.17.23
    node_modules/@chevrotain/cst-dts-gen/node_modules/lodash-es  4.17.23
    node_modules/xcode/node_modules/uuid                     7.0.3    (1 advisory)

Each nested copy exists because its parent pins an EXACT version that the hoisted one cannot
satisfy (`monaco-editor` declares `dompurify: "3.4.8"`, the chevrotain family declares
`lodash-es: "4.17.23"`). So `npm update` is inert against them by construction — an exact pin has
exactly one satisfying version — and the fix is the root `overrides` block, which is what this
ratchet guards.

🪤 THE TRAP THIS EXISTS TO CATCH is the one-line probe that reads only the hoisted copy. It is the
reading every tool defaults to, it is what makes these advisories look already-fixed, and it is
wrong in the only direction that matters. So this walks EVERY entry in `package-lock.json` and
floors all of them, nested or not.

The floors are the advisories' own `first_patched_version`, not "latest" — a floor that tracks
latest would red on every upstream release and teach the next reader to re-baseline it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_LOCKFILE = Path(__file__).resolve().parents[1] / "package-lock.json"

#: package -> (minimum non-vulnerable version, the advisories it closes). Raise a floor only when
#: a NEW advisory names a higher `first_patched_version`; never lower one to make a red go green.
_FLOORS: dict[str, tuple[tuple[int, ...], str]] = {
    "dompurify": (
        (3, 4, 13),
        "GHSA-55q2-fjhq-7xh7 (IN_PLACE hook removal leaves a detached subtree executable), "
        "GHSA-cmwh-pvxp-8882 (permanent ALLOWED_ATTR pollution via setConfig()), "
        "GHSA-c2j3-45gr-mqc4 (CUSTOM_ELEMENT_HANDLING bypasses afterSanitizeElements), "
        "GHSA-vxr8-fq34-vvx9 (Trusted Types policy survives clearConfig())",
    ),
    "lodash-es": (
        (4, 18, 0),
        "GHSA-r5fr-rjxr-66jc (code injection via _.template imports key names), "
        "GHSA-f23m-r3pf-42rh (prototype pollution via array path bypass in _.unset/_.omit)",
    ),
    "uuid": (
        (11, 1, 1),
        "GHSA-w5hq-g745-h8pq (missing buffer bounds check in v3/v5/v6 when buf is provided)",
    ),
}


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Leading numeric components of a semver string. Prereleases compare as their base
    version, which is deliberately lenient: this floors releases, and a prerelease of a
    patched version is not what any of these advisories is about."""
    head = raw.split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for chunk in head.split("."):
        if not chunk.isdigit():
            break
        parts.append(int(chunk))
    return tuple(parts)


def _installed() -> list[tuple[str, str, str]]:
    """Every (lockfile path, package name, version) for the floored packages — hoisted AND
    nested. The name comes from the path's last `node_modules/` segment, because that is what
    npm keys a nested duplicate by; the entry's own `name` field is absent for most rows."""
    lock = json.loads(_LOCKFILE.read_text(encoding="utf-8"))
    found: list[tuple[str, str, str]] = []
    for path, meta in lock.get("packages", {}).items():
        if "node_modules/" not in path:
            continue  # the root project and the workspace links carry no version of interest
        name = path.rsplit("node_modules/", 1)[1]
        if name in _FLOORS and isinstance(meta.get("version"), str):
            found.append((path, name, meta["version"]))
    return found


def test_the_lockfile_is_the_thing_that_is_parsed():
    """Vacuity floor. Every assertion below is a statement about rows this finder returned, so a
    finder that returns NOTHING would make all of them pass while guarding nothing — the exact
    shape of a green whose defect arm never ran."""
    assert _LOCKFILE.is_file(), f"{_LOCKFILE} is missing — the ratchet below cannot mean anything"
    rows = _installed()
    assert rows, (
        "found ZERO entries for any of "
        f"{sorted(_FLOORS)} in package-lock.json. Either the finder broke or these packages left "
        "the tree; in both cases the floors below are vacuous and this ratchet must be re-derived."
    )
    # All three are reachable from the web bundle (mermaid, monaco-editor) and the mobile CLI
    # (xcode), so a tree missing any of them means the finder, not the tree, changed.
    assert {name for _, name, _ in rows} == set(_FLOORS), (
        "the floored packages present in the lockfile are "
        f"{sorted({n for _, n, _ in rows})}, expected {sorted(_FLOORS)}"
    )


@pytest.mark.parametrize("package", sorted(_FLOORS))
def test_no_copy_of_an_advisory_package_is_below_its_floor(package: str):
    floor, advisories = _FLOORS[package]
    rows = [(path, ver) for path, name, ver in _installed() if name == package]
    assert rows, f"no {package} entry found — see test_the_lockfile_is_the_thing_that_is_parsed"
    below = [(path, ver) for path, ver in rows if _version_tuple(ver) < floor]
    assert not below, (
        f"{package} must be >= {'.'.join(map(str, floor))} in EVERY lockfile entry, including "
        f"nested duplicates. These are below the floor:\n"
        + "\n".join(f"    {path}  {ver}" for path, ver in below)
        + f"\n  Unpatched advisories: {advisories}\n"
        "  A nested duplicate appears when a parent pins an exact version the hoisted copy "
        "cannot satisfy, so `npm update` will not shift it. Add or raise the entry in the root "
        "package.json `overrides` block, then re-run `npm install` — do not edit this floor."
    )
