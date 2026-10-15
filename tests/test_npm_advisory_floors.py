"""🔴 A PATCHED TOP-LEVEL COPY DOES NOT MEAN THE TREE IS PATCHED — the nested duplicate does.

Seven open Dependabot advisories all named `package-lock.json`, and all seven read as
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

🪤 AND A PACKAGE CAN KEEP A COPY THE LOCKFILE NEVER LISTS. The override fixed monaco-editor's
nested `dompurify` ENTRY, not the editor's code: monaco-editor's `domSanitize.js` imports a
DOMPurify of its own, `esm/vs/base/browser/dompurify/dompurify.js`, which ships inside the
package's files (3.4.8 in 0.56.0, 3.4.15 in 0.57.0). A lockfile lists packages, not the files a
package carries, so the second half below reads the INSTALLED tree for such copies by their
licence header and floors them as well. It needs `node_modules` (`npm ci` from the repository
root): without one it skips, unless `PERSONALCLAW_REQUIRE_NPM_TREE=1` says the tree must be
there, as CI's `rails` job does.

🪤 THE TRAP THIS EXISTS TO CATCH is the one-line probe that reads only the hoisted copy. It is the
reading every tool defaults to, it is what makes these advisories look already-fixed, and it is
wrong in the only direction that matters. So this walks EVERY entry in `package-lock.json` and
floors all of them, nested or not.

The floors are the advisories' own `first_patched_version`, not "latest" — a floor that tracks
latest would red on every upstream release and teach the next reader to re-baseline it.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

_LOCKFILE = Path(__file__).resolve().parents[1] / "package-lock.json"
_NODE_MODULES = _LOCKFILE.parent / "node_modules"

#: package -> (minimum non-vulnerable version, the advisories it closes). Raise a floor only when
#: a NEW advisory names a higher `first_patched_version`; never lower one to make a red go green.
_FLOORS: dict[str, tuple[tuple[int, ...], str]] = {
    "dompurify": (
        (3, 4, 17),
        "GHSA-55q2-fjhq-7xh7 (IN_PLACE hook removal leaves a detached subtree executable), "
        "GHSA-cmwh-pvxp-8882 (permanent ALLOWED_ATTR pollution via setConfig()), "
        "GHSA-c2j3-45gr-mqc4 (CUSTOM_ELEMENT_HANDLING bypasses afterSanitizeElements), "
        "GHSA-vxr8-fq34-vvx9 (Trusted Types policy survives clearConfig()), "
        "GHSA-p98j-92pf-mc4p (IN_PLACE: a node-removing afterSanitize hook leaves a detached "
        "subtree's event handlers armed), "
        "GHSA-6688-9rhm-gjv2 (IN_PLACE returns a force-removed rawtext root whose text carries "
        "markup), "
        "GHSA-mrv2-3pqr-j5jr (IN_PLACE: a hook that removes a parent or sibling leaves the "
        "detached subtree's event handlers armed, an incomplete fix of GHSA-p98j-92pf-mc4p), "
        "GHSA-cg2g-xh64-v4q7 (SAFE_FOR_TEMPLATES: split template expressions survive in attached "
        "shadow root content on DOM output modes), "
        "GHSA-c6gr-qvv4-5v3f (the SMIL attributeName=href guard is skipped under "
        "PARSER_MEDIA_TYPE application/xhtml+xml)",
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
    "source-map-js": (
        (1, 2, 2),
        "GHSA-68fv-2mgg-jv7q (event-loop denial of service through indexed source-map section "
        "offsets)",
    ),
    "katex": (
        (0, 18, 2),
        "GHSA-238p-pmpm-9mq7 (existing prototype pollution can bypass trust restrictions)",
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
    # Every floored package is installed for a dependent that is still here (the web workspace
    # itself, mermaid and monaco-editor, the mobile CLI's xcode, postcss and the CSS toolchain),
    # so a tree missing any of them means the finder, not the tree, changed.
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
        "  A nested duplicate appears when a parent's range (an exact pin, or a caret range on "
        "an older 0.x) cannot reach the hoisted copy, so `npm update` will not shift it. Add or "
        "raise the entry in the root package.json `overrides` block, then run `npm update "
        f"{package}` from the repository root (`npm install` keeps a locked copy the new override "
        "no longer admits) — do not edit this floor."
    )


# ── Copies a package keeps inside its own files ─────────────────────────────────────────────

#: A floored library a package can carry inside its own files, and the licence header that names
#: such a copy's version. Its own installed package is the lockfile half's business, so its
#: directories are not read here.
_VENDORED_HEADERS: dict[str, re.Pattern[bytes]] = {
    "dompurify": re.compile(rb"@license DOMPurify (\d+\.\d+\.\d+)"),
}

#: (installed path, version) of a copy below its floor that is known, and why it is accepted.
#: Keyed by version too, so a different copy at the same path is judged afresh. Each must still be
#: found below its floor: an exception for a copy that moved on is a hole waiting for the next one.
_VENDORED_EXCEPTIONS: dict[tuple[str, str], str] = {
    ("node_modules/monaco-editor/esm/vs/base/browser/dompurify/dompurify.js", "3.4.15"): (
        "monaco-editor 0.57.0 keeps this copy in its own sources, and its domSanitize.js imports "
        "it, so no override reaches it. That sanitizer returns a DOM fragment or TrustedHTML and "
        "sets none of IN_PLACE, SAFE_FOR_TEMPLATES or PARSER_MEDIA_TYPE, the options every "
        "advisory between 3.4.15 and the floor needs. Remove this once monaco-editor ships "
        "3.4.17 or later."
    ),
}

#: Set where the root workspace is installed on purpose (CI's `rails` job): there, an absent tree
#: is a failure, never a skip, because a skip reads like a pass.
_REQUIRE_TREE = "PERSONALCLAW_REQUIRE_NPM_TREE"


def _vendored_copies(node_modules: Path) -> list[tuple[str, str, str]]:
    """Every (path from the repository root, library, version) of a floored library that a
    package keeps inside its own files, read from the licence header in a script's first bytes.
    Links are not followed: the workspace members appear in node_modules as links to web/,
    desktop/ and mobile/, which are not installed packages."""
    found: list[tuple[str, str, str]] = []
    for dirpath, _dirnames, filenames in os.walk(node_modules):
        where = Path(dirpath).relative_to(node_modules.parent).as_posix() + "/"
        for filename in filenames:
            if not filename.endswith((".js", ".mjs", ".cjs")):
                continue
            try:
                with open(os.path.join(dirpath, filename), "rb") as handle:
                    head = handle.read(600)
            except OSError:
                continue
            for library, header in _VENDORED_HEADERS.items():
                hit = header.search(head)
                if hit and f"node_modules/{library}/" not in where:
                    found.append((where + filename, library, hit.group(1).decode()))
    return found


def _judge(copies: list[tuple[str, str, str]]) -> tuple[list[str], list[str]]:
    """The copies below their floor that no exception names, and the exceptions that named no
    copy below its floor."""
    below = [
        (path, library, version)
        for path, library, version in copies
        if _version_tuple(version) < _FLOORS[library][0]
    ]
    unexcused = [
        f"{path}  {library} {version}"
        for path, library, version in below
        if (path, version) not in _VENDORED_EXCEPTIONS
    ]
    named = {(path, version) for path, _library, version in below}
    stale = [
        f"{path}  {version}"
        for path, version in _VENDORED_EXCEPTIONS
        if (path, version) not in named
    ]
    return unexcused, stale


def _installed_tree() -> Path:
    if _NODE_MODULES.is_dir():
        return _NODE_MODULES
    if os.environ.get(_REQUIRE_TREE) == "1":
        pytest.fail(f"{_REQUIRE_TREE}=1, but {_NODE_MODULES} is missing: run `npm ci` first")
    pytest.skip(
        "node_modules is not installed here (`npm ci` from the repository root installs it); "
        "the lockfile half above still ran"
    )


def test_the_header_reader_agrees_with_the_lockfile_on_the_package_itself():
    """Vacuity floor for the half below: read with the same pattern, DOMPurify's own installed
    package must name the version the lockfile installed. A pattern that matched nothing would
    leave every copy unread and the half below green."""
    tree = _installed_tree()
    [(_, _, locked)] = [row for row in _installed() if row[0] == "node_modules/dompurify"]
    hit = _VENDORED_HEADERS["dompurify"].search(
        (tree / "dompurify" / "dist" / "purify.es.mjs").read_bytes()[:600]
    )
    assert hit, "the DOMPurify licence header no longer reads as @license DOMPurify <version>"
    assert hit.group(1).decode() == locked, (
        f"node_modules/dompurify says {hit.group(1).decode()} but package-lock.json installed "
        f"{locked}: run `npm ci` from the repository root"
    )


def test_no_copy_a_package_keeps_inside_itself_is_below_its_floor():
    unexcused, stale = _judge(_vendored_copies(_installed_tree()))
    assert not unexcused, (
        "a package ships its own copy of a floored library below the floor, which no lockfile "
        "entry and no override can reach:\n"
        + "\n".join(f"    {row}" for row in unexcused)
        + "\n  Move the package to a release that carries a patched copy. Only if no such release "
        "exists, name the copy in _VENDORED_EXCEPTIONS with the reason it is safe until then."
    )
    assert not stale, (
        "these exceptions no longer name a copy below its floor, so they excuse nothing; delete "
        "them:\n" + "\n".join(f"    {row}" for row in stale)
    )


def test_the_copy_check_fails_a_planted_old_copy_and_excuses_only_what_it_names(tmp_path):
    """Each half of the judgement must be able to say no."""
    tree = tmp_path / "node_modules"

    def plant(relative: str, version: str) -> None:
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"/*! @license DOMPurify {version} | (c) Cure53 */\n", encoding="utf-8")

    [(excused, excused_version)] = list(_VENDORED_EXCEPTIONS)
    plant(excused.removeprefix("node_modules/"), excused_version)
    plant("dompurify/dist/purify.es.mjs", "3.4.1")  # the package itself: the lockfile's business
    plant("some-editor/vendor/purify.js", "3.4.8")
    copies = _vendored_copies(tree)
    assert sorted(path for path, _, _ in copies) == sorted(
        [excused, "node_modules/some-editor/vendor/purify.js"]
    )
    assert _judge(copies) == (["node_modules/some-editor/vendor/purify.js  dompurify 3.4.8"], [])

    plant(excused.removeprefix("node_modules/"), "3.4.9")  # a different copy at the same path
    unexcused, stale = _judge(_vendored_copies(tree))
    assert f"{excused}  dompurify 3.4.9" in unexcused and stale == [f"{excused}  {excused_version}"]


def test_ci_runs_this_file_where_the_tree_is_installed_and_requires_it():
    """The copy check skips without node_modules, and a skip reads as a pass, so the CI job that
    installs the tree for it must also say the tree has to be there. Without this rail, dropping
    that step, its `npm ci` or its lever would leave the check skipped on every run."""
    import yaml

    workflow = _LOCKFILE.parent / ".github" / "workflows" / "ci.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    runs = []
    for job, spec in jobs.items():
        steps = spec.get("steps", [])
        for index, step in enumerate(steps):
            if "tests/test_npm_advisory_floors.py" in str(step.get("run", "")):
                installed = any("npm ci" in str(s.get("run", "")) for s in steps[:index])
                required = (step.get("env") or {}).get(_REQUIRE_TREE) == "1"
                runs.append((job, installed, required))
    assert runs, "no ci.yml step runs tests/test_npm_advisory_floors.py on an installed tree"
    assert all(installed and required for _, installed, required in runs), (
        "each ci.yml step that runs this file needs an `npm ci` before it in its job and "
        f'`{_REQUIRE_TREE}: "1"` in its env; found (job, installed, required): {runs}'
    )
