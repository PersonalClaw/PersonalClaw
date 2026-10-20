"""What the licence rail and the tests of its checks share: the sweep, and the checks it runs.

The rail, which runs them on this repository once per CI run in the rails job:
``tests/rails/test_licence_governance_tree.py``

The tests that prove each check fails, on trees they plant:
``tests/test_licence_governance.py``

This module sits beside the suite so that no rail imports a behaviour test module. Every check
takes the root it judges; none reads this repository unless it is handed it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


#: Directories that cannot hold a declaration of *this* project's licence: build output,
#: caches, dependency trees, sibling worktrees and a checkout's own scratch folder. Read only
#: where git cannot say what the tree holds (see :func:`_walk`).
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".local",
        ".venv",
        "venv",
        ".worktrees",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "htmlcov",
        "site-packages",
    }
)


#: Directories skipped by PREFIX rather than by exact name (#3462).
#:
#: 🔴 `.dev-home` USED TO BE A LITERAL IN `_SKIP_DIRS` ABOVE, AND THAT DISAGREED WITH
#: `.gitignore`. The ignore rule is the glob `/.dev-home*/`, with a comment saying not to
#: narrow it — every dev home holds live credentials, so an ad-hoc `.dev-home-<something>`
#: for a one-off validation run has to be ignored by default rather than each name being
#: added after the fact. The membership test here honoured only the canonical spelling, so
#: `.dev-home-i3` was invisible to `git status` AND walked by this sweep — and a dev home
#: holds installed app copies, i.e. real `LICENSE` files, so it reported them as undeclared
#: declaration sites.
#:
#: 🪤 A PREFIX, BECAUSE ANOTHER LITERAL FIXES ONE MACHINE. `_SKIP_DIRS` is a frozenset and
#: the walk tests membership, so adding `.dev-home-i3` would fix the checkout that reported
#: it and no other — and the next `make serve`-style run in a worktree mints a new name.
#:
#: Deliberately NOT a general glob engine: a prefix is what `.gitignore` expresses and it
#: cannot swallow an unrelated name by accident (`.dev-homework` and `dev-home` are still
#: walked, asserted in `test_dev_home_is_skipped_by_prefix.py`).
_SKIP_DIR_PREFIXES = (".dev-home",)


def _is_skipped_dir(name: str) -> bool:
    """Whether the walk prunes a directory called *name*. One owner for both rules, so the
    exact set and the prefix list cannot drift into two answers."""
    return name in _SKIP_DIRS or name.startswith(_SKIP_DIR_PREFIXES)


#: A lockfile is a record of OTHER projects' licences. See the module docstring.
_SKIP_FILES = frozenset({"package-lock.json", "uv.lock", "yarn.lock", "pnpm-lock.yaml"})


#: Extensions that can carry an `SPDX-License-Identifier:` header. Bounded so the sweep does
#: not read every PNG and font in the tree looking for a string that cannot be in one.
_TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".css",
        ".scss",
        ".html",
        ".md",
        ".txt",
        ".toml",
        ".cfg",
        ".ini",
        ".json",
        ".yml",
        ".yaml",
        ".sh",
        ".spec",
        ".rst",
        ".sql",
        "",
    }
)


#: The SPDX header tag, assembled at runtime so the literal string never appears in this
#: file's own bytes. It has to be: the sweep reads every text file in the tree including this
#: one, and the first version of this rail reported ITSELF as declaring `BUSL-1.1` — picked up
#: from the teeth test's deliberately fake header below. The tempting fix is to skip this
#: file, which is an allowlist hole in the one file that must not have one. Splitting the tag
#: means the rail cannot match its own source, with no exemption to forget about later.
_SPDX_TAG = "SPDX-License-" + "Identifier:"


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The conventional machine-readable header, valid in any file type.
    ("spdx header", re.compile(_SPDX_TAG + r"\s*([A-Za-z0-9.\-+]+)")),
    # `LICENSE`'s own first line: "MIT License", "Apache License", "Business Source License".
    (
        "licence file title",
        re.compile(r"^([A-Za-z0-9.\-+]+(?: [A-Za-z0-9.\-+]+)?) License\b", re.MULTILINE),
    ),
    # pyproject: `license = { text = "MIT" }` or `license = "MIT"`.
    #
    # `re.MULTILINE` is load-bearing and was missing in the first version of this file. `^`
    # without it anchors to the START OF THE STRING, so this pattern matched nothing in any
    # real `pyproject.toml` — the field is never on line 1. The rail still passed on an MIT
    # tree because the trove classifier below happened to say MIT too, and it still passed
    # when `license` was flipped to `Apache-2.0` by hand: a silently vacuous disjunct hiding
    # behind a working sibling. Caught by mutating the real tree, NOT by the tmp-tree teeth
    # test, whose fixture put the field at position 0 and so matched by accident.
    (
        "pyproject license",
        re.compile(r'^license\s*=\s*(?:\{\s*text\s*=\s*)?"([^"]+)"', re.MULTILINE),
    ),
    # pyproject classifier: `"License :: OSI Approved :: MIT License"`.
    ("trove classifier", re.compile(r"License :: OSI Approved :: ([A-Za-z0-9.\-+ ]+?) License")),
    # package.json: `"license": "MIT"`.
    ("package.json license", re.compile(r'"license"\s*:\s*"([^"]+)"')),
    # README's shields.io badge: `License-MIT-informational`.
    ("readme badge", re.compile(r"/badge/License-([A-Za-z0-9.\-+_%]+?)-")),
)


#: A file can declare this project's licence only if it is one of these. Matched on NAME,
#: so a new `LICENSE-BUSL` or a new package's `package.json` is discovered, not missed.
_CANDIDATE_NAMES = re.compile(
    r"^(LICEN[CS]E.*|COPYING.*|package\.json|pyproject\.toml|README\.md)$"
)


#: What a CLA looks like, in every spelling a repo actually uses. Matched on the file NAME
#: anywhere in the tree, because "we put it in `.github/`" is not a loophole.
_CLA_RE = re.compile(
    r"^(cla|icla|ccla|contributor[_\-. ]?licen[cs]e[_\-. ]?agreement.*|"
    r"contributor[_\-. ]?agreement.*|cla[_\-.].*)(\.(md|txt|rst|html|pdf|ya?ml|json))?$",
    re.IGNORECASE,
)


#: Variables a git hook sets for the repository it runs in. A suite run from a hook would
#: otherwise ask that repository's index about a planted tree.
_GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_PREFIX",
)


def _git_files(root: Path) -> list[Path] | None:
    """The files the project at *root* holds, as git sees them: tracked, plus untracked files
    git does not ignore, so a new declaration is judged before it is committed. ``None`` when
    git cannot answer (no git, or *root* is no checkout)."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_VARS}
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            env=env,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    names = sorted({n for n in listed.stdout.decode("utf-8", "surrogateescape").split("\0") if n})
    # A file deleted but not yet unstaged is still listed; a submodule is listed as its folder.
    return [root / n for n in names if (root / n).is_file()]


def _walk(root: Path):
    """Every file the project at *root* ships: what git lists there, or, where git cannot
    answer, every file under *root* that is not in a skipped directory.

    **Git's list first, because the rail judges what the project ships.** A checkout also holds
    what git ignores: a scratch folder (`.local/`) with third-party sources in it, a dev home,
    build output. A walk reads all of it, and a scratch copy of an Apache-2.0 file read as this
    project declaring Apache-2.0 in every checkout that had one, while CI, which has none, stayed
    green. Pruning by name chased each such folder after it bit; `.gitignore` already names
    them all.

    **The walk is the fallback**, for a tree git does not track: a planted tree in a test, or a
    copy with no git. Directories are PRUNED as the walk descends rather than filtered
    afterwards, and `.local/` is pruned with the rest. `rglob("*")` plus a filter is the obvious
    spelling and takes tens of seconds here, because it enumerates every object in `.git` and
    every file in `.venv` before discarding them.

    The skip check is against the path RELATIVE to *root*, never the absolute one: this
    repository is routinely checked out into a `.worktrees/<name>/` sibling, so matching
    absolute parts made the whole sweep return nothing there while looking perfectly clean —
    a rail that silently scans zero files. `test_the_census_is_not_vacuous` is what catches
    that class of mistake, and it caught exactly this one.

    Pruning goes through `_is_skipped_dir`, not a bare set membership, so a dev home is
    matched by prefix the way `.gitignore` matches it (#3462).
    """
    tracked = _git_files(root)
    if tracked is not None:
        yield from tracked
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _is_skipped_dir(d))
        here = Path(dirpath)
        for name in sorted(filenames):
            yield here / name


def licence_identifiers(text: str, filename: str) -> set[str]:
    """Licence identifiers *text* declares, normalised.

    ``filename`` gates the file-shaped patterns: a "MIT License" title only means something
    at the top of a licence file, and treating the phrase as a declaration everywhere would
    make every doc that mentions the licence a census entry.
    """
    found: set[str] = set()
    is_licence_file = filename.upper().startswith(("LICENSE", "LICENCE", "COPYING"))
    for label, pattern in _PATTERNS:
        if label == "licence file title" and not is_licence_file:
            continue
        for match in pattern.finditer(text if label != "licence file title" else text[:200]):
            found.add(match.group(1).strip())
    return found


def declaration_sites(root: Path) -> dict[str, set[str]]:
    """relative path -> the licence identifiers it declares about THIS project.

    Discovery is by pattern in two arms: candidate filenames (a licence file, a manifest, the
    README badge), and — for every other text file — an ``SPDX-License-Identifier:`` header,
    which is a declaration wherever it appears.

    Deliberately NOT cached. The teeth tests below mutate a tmp tree and re-run the real
    assertion over the same root; a cache keyed on the path would hand them the pre-mutation
    answer and every `pytest.raises` would pass for the wrong reason. Only the sweep over the
    fixed repo root is memoised (:func:`_real_sites`), where the tree cannot change mid-run.
    """
    sites: dict[str, set[str]] = {}
    for path in _walk(root):
        if path.name in _SKIP_FILES:
            continue
        is_candidate = bool(_CANDIDATE_NAMES.match(path.name))
        if not is_candidate and path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if is_candidate:
            ids = licence_identifiers(text, path.name)
        elif _SPDX_TAG in text:
            ids = {m.group(1).strip() for m in _PATTERNS[0][1].finditer(text)}
        else:
            continue
        if ids:
            sites[str(path.relative_to(root))] = ids
    return sites


def cla_paths(root: Path) -> list[str]:
    """Every file in the tree whose NAME reads as a contributor licence agreement."""
    return sorted(str(p.relative_to(root)) for p in _walk(root) if _CLA_RE.match(p.name))


def check_no_cla_file(root: Path) -> None:
    """RED if a contributor licence agreement has appeared in *root*."""
    found = cla_paths(root)
    assert not found, (
        "a contributor licence agreement has appeared: "
        + ", ".join(found)
        + "\n\nREADME.md and SECURITY.md promise there is no CLA. A CLA is the mechanism by "
        "which a project acquires the right to relicense its contributors' work, so adding "
        "one is the structural half of a relicensing move even if no licence has changed "
        "yet. Contributions here are MIT-in, MIT-out under the DCO sign-off already required "
        "by CONTRIBUTING.md."
    )


def _pyproject(identifier: str) -> str:
    """A pyproject whose licence field is NOT on line 1.

    Shaped like the real file on purpose. The first version of this fixture wrote the bare
    `license = ...` line at position 0, which made the pyproject pattern match by accident
    even without ``re.MULTILINE`` — so the teeth test was green for a disjunct that guarded
    nothing on any real file. A fixture that is easier to match than production is worse than
    no fixture: it certifies the rail against a case that cannot occur.
    """
    return f'[project]\nname = "demo"\nversion = "0.0.1"\nlicense = {{ text = "{identifier}" }}\n'


def _minimal_tree(tmp_path: Path) -> Path:
    """A root the real assertions can run over: an MIT LICENSE and an MIT pyproject."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "LICENSE").write_text((_ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "pyproject.toml").write_text(_pyproject("MIT"), encoding="utf-8")
    return root


# ── what a licence file must say ────────────────────────────────────────────────────────

#: The one identifier this project is allowed to declare about itself.
_EXPECTED = "MIT"

#: The MIT body with the copyright line removed and whitespace collapsed. Pinned so an edit
#: to the GRANT reds while a copyright-year or holder change does not — the year is
#: bookkeeping, the grant is the promise.
_LICENCE_BODY_SHA256 = "7c9b48b52decb9837c70f608678129e1ac79e056829c8d1e82e8cdd8aed562f8"

#: Every copyright holder a licence file in THIS tree may name. One entry, because every
#: licence file here covers work this project wrote, and a grant is only as useful as the
#: certainty about who granted it: six files carrying six holders would mean a contributor
#: could not tell whose terms their change lands under. The whole org uses this string —
#: measured 2026-09-23, all nine repositories with a licence carried byte-identical files.
#:
#: This is deliberately NOT the same question as "is the holder a real holder at all", which
#: :func:`check_no_licence_holder_is_an_artefact_name` answers for any bundle anywhere. Only
#: a first-party tree can be held to an exact string.
_APPROVED_HOLDERS = frozenset({"PersonalClaw contributors"})

#: A file that IS a licence, matched on NAME. Case-SENSITIVE, and that is the whole point:
#: the first version of this matched `name.upper().startswith(("LICENSE", "LICENCE", ...))`
#: like :func:`licence_identifiers` does, which swept in `docs/architecture/licence-identity.txt`
#: — the census TABLE, whose name uppercases to `LICENCE-IDENTITY.TXT`. Both holder rails
#: promptly reported the table as a licence file with no copyright line. Uppercase is the
#: universal convention for the real thing (`LICENSE`, `LICENSE.md`, `LICENSE-MIT`, `COPYING`),
#: so requiring it is what separates a grant from a document that merely discusses one.
#: `licence_identifiers` can afford the loose form because `_CANDIDATE_NAMES` gates it first.
_LICENCE_FILE_RE = re.compile(r"^(LICEN[CS]E|COPYING)([-.][A-Za-z0-9.\-]*)?$")

#: A licence file's copyright line, with the year discarded and the holder captured. Accepts
#: the `(c)` / `©` / bare spellings and a year range, because a rail that only reads the one
#: spelling this tree uses today would go quietly vacuous the first time someone wrote `©`.
_COPYRIGHT_RE = re.compile(
    r"^\s*copyright\s*(?:\(c\)|©|\(C\))?\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?)?\s*(?P<holder>.*?)\s*$",
    re.IGNORECASE,
)


def licence_files(root: Path) -> list[Path]:
    """Every licence FILE under *root* — the files that actually grant something.

    Distinct from :func:`declaration_sites`, which also returns manifests and the README
    badge. Those declare an identifier; only these carry a grant and a copyright holder.
    """
    return sorted(
        p
        for p in _walk(root)
        if _LICENCE_FILE_RE.match(p.name) and p.suffix.lower() in _TEXT_SUFFIXES
    )


def licence_holder(text: str) -> str | None:
    """The copyright holder *text* names, or ``None`` if it carries no copyright line.

    The year is discarded: this rail is about WHO, and the year is bookkeeping already
    excluded from the grant hash.
    """
    for line in text.splitlines():
        match = _COPYRIGHT_RE.match(line)
        if match:
            return match.group("holder").strip() or None
    return None


def artefact_names_beside(path: Path) -> frozenset[str]:
    """Casefolded identities of the app whose bundle *path* sits in, if any.

    An app bundle is a directory holding an ``app.json``, so a licence file's sibling
    manifest is what says which artefact the directory IS. Returns the manifest's ``name``,
    its ``displayName``, and the title-cased expansion of ``name`` that the scaffolder
    derives when ``--display-name`` is omitted (``channel-null`` -> ``Channel Null``) — that
    derived form is the exact string the broken fallback wrote, and it appears in no file.
    """
    manifest = path.parent / "app.json"
    if not manifest.is_file():
        return frozenset()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(data, dict):
        return frozenset()
    name = str(data.get("name") or "")
    names = {name, str(data.get("displayName") or "")}
    if name:
        names.add(" ".join(part.capitalize() for part in name.split("-")))
    return frozenset(n.casefold() for n in names if n)


# ── the three assertions, each callable against an arbitrary root so the teeth tests can
#    run the REAL rail over a mutated tree instead of re-implementing it ─────────────────


def check_every_declaration_is_mit(root: Path) -> None:
    """RED if anything in *root* declares a licence other than MIT."""
    wrong = sorted(
        f"{path} declares {sorted(ids - {_EXPECTED})}"
        for path, ids in declaration_sites(root).items()
        if ids - {_EXPECTED}
    )
    assert not wrong, (
        "this project declares a licence that is not MIT:\n"
        + "\n".join(f"  {w}" for w in wrong)
        + "\n\nREADME.md and SECURITY.md carry a dated commitment that PersonalClaw is MIT "
        "and that already-published releases are never relicensed. Changing an identifier "
        "here breaks that promise to every user who installed on the strength of it. If the "
        "change is deliberate, it is a governance decision: edit the commitment in both "
        "files in the same PR, and say what it means for releases already published."
    )


def grant_digest(text: str) -> str:
    """sha256 of *text*'s licence body with the copyright line removed, whitespace collapsed.

    Excluding the copyright line is deliberate: a year or holder change is bookkeeping, the
    grant is the promise, and the two want different rails.
    :func:`check_every_licence_file_carries_the_real_grant` owns the grant;
    :func:`check_every_licence_holder_is_a_real_holder` owns the holder.
    """
    body = "\n".join(line for line in text.splitlines() if not line.lower().startswith("copyright"))
    return hashlib.sha256(re.sub(r"\s+", " ", body).strip().encode("utf-8")).hexdigest()


def check_every_licence_file_carries_the_real_grant(root: Path) -> None:
    """RED if ANY licence file in *root* is not the pinned MIT grant, verbatim.

    Every one of these files is this project's own grant — the root `LICENSE` and each
    bundled app's — so "MIT" has to mean the same bytes in all of them. The identifier
    sweep above cannot see this: its "licence file title" pattern reads the first line, so
    a file whose title still says `MIT License` over a *paraphrased* or narrowed grant is
    invisible to it.
    Proven by arm (d) of :func:`test_the_rail_reds_on_a_fake_spdx_change`, where the
    identifier check passes on exactly that mutation.

    Scoped to every licence file rather than the root one alone because the root file was
    the only pinned copy while five siblings were pinned by nothing, and a bundled app's
    LICENSE is the copy that travels when the bundle leaves the wheel.
    """
    wrong = []
    for path in licence_files(root):
        digest = grant_digest(path.read_text(encoding="utf-8"))
        if digest != _LICENCE_BODY_SHA256:
            wrong.append(f"{path.relative_to(root)} (sha256 {digest})")
    assert not wrong, (
        "these licence files do not carry the pinned MIT grant:\n"
        + "\n".join(f"  {w}" for w in wrong)
        + f"\n\nPinned: {_LICENCE_BODY_SHA256}. The copyright line is excluded from this "
        "hash, so a year or holder update does not reach it — this is the licence GRANT "
        "itself, and a paraphrase of it is not MIT however the first line is titled. If the "
        "change is deliberate, update the pin and the dated commitment in README.md and "
        "SECURITY.md together."
    )


def check_every_licence_holder_is_a_real_holder(root: Path) -> None:
    """RED if a licence file in *root* names a holder outside :data:`_APPROVED_HOLDERS`.

    A copyright line names whoever holds the copyright. Every licence file in THIS tree
    covers work this project wrote, so there is exactly one right answer, and anything else
    is either a mistake or a governance decision that belongs in review.
    """
    wrong = []
    for path in licence_files(root):
        holder = licence_holder(path.read_text(encoding="utf-8"))
        if holder is None:
            wrong.append(f"{path.relative_to(root)} has no copyright line at all")
        elif holder not in _APPROVED_HOLDERS:
            wrong.append(f"{path.relative_to(root)} names {holder!r}")
    assert not wrong, (
        "these licence files name a copyright holder this project does not use:\n"
        + "\n".join(f"  {w}" for w in wrong)
        + f"\n\nApproved: {sorted(_APPROVED_HOLDERS)}. Keeping one holder across the org is "
        "what makes a contributed change land under the same grant wherever it lands. If a "
        "new holder is correct, add it here and say why in the same PR."
    )


def check_no_licence_holder_is_an_artefact_name(root: Path) -> None:
    """RED if a licence file's holder is the name or display name of the app beside it.

    This is the rule the app scaffolder broke. ``personalclaw app new`` wrote
    ``_license_text(author or display, …)``, so every app generated without ``--author`` got
    a grant whose holder was the app itself — `Copyright (c) 2026 Channel Null`. An app is an
    artefact; it cannot hold a copyright, so that licence grants nothing and names nobody to
    ask. Four published exemplar repositories shipped it.

    Separate from :func:`check_every_licence_holder_is_a_real_holder` on purpose. That one
    asks "is this THE holder", which only a first-party tree can answer; this one asks "is
    this a holder AT ALL", which is true of any app bundle anywhere — so the scaffolder's own
    test drives *this* function over its generated output rather than re-deriving the rule.
    """
    wrong = []
    for path in licence_files(root):
        holder = licence_holder(path.read_text(encoding="utf-8"))
        if holder is None:
            continue
        artefacts = artefact_names_beside(path)
        if holder.casefold() in artefacts:
            wrong.append(f"{path.relative_to(root)} names the app itself: {holder!r}")
    assert not wrong, (
        "these licence files name an APP as the copyright holder:\n"
        + "\n".join(f"  {w}" for w in wrong)
        + "\n\nA copyright line names a person or an organisation. An app name or display "
        "name is an artefact: it holds nothing, so the grant is defective and there is "
        "nobody to ask for permission. Name the author (`app new --author`), or leave the "
        "scaffold's obviously-unfilled placeholder so the omission is visible."
    )
