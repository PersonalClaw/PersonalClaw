"""RET-5 — the governance promise is a control, not a paragraph.

The abandonment driver behind this file is the one where users state refusal outright:
**project/licence churn**. Four peers in the same category relicensed, added a CLA,
reserved stricter terms, or were acquired without a licence commitment, and the measured
user response was to leave. PersonalClaw currently wins that axis *by having done nothing*
— which is an advantage that is completely invisible until it is written down, and
completely worthless if the writing-down is the only thing holding it.

So `README.md` and `SECURITY.md` now carry a dated, four-part commitment (MIT only, no CLA,
no telemetry, no relicensing of already-published releases), and this file is the rail that
keeps the first three of those four honest against the tree. It is deliberately the same
idiom as ``test_network_egress_hosts.py`` — **patterns + a judgment table + a stale-entry
check + a teeth check** — rather than a second rail dialect, because that file already owns
the telemetry third of the same promise and there is no reason for two shapes.

Three disjuncts, any one of which reds CI:

1. **A licence identifier that is not MIT**, anywhere this project declares its own licence.
   The declaring files are *discovered by pattern* (``LICENSE*``, ``pyproject.toml``, every
   ``package.json``, ``README.md``'s badge, and any ``SPDX-License-Identifier:`` header in
   the tree), so adding a new declaration site is caught too — it appears as an undeclared
   census entry rather than slipping through as a path nobody thought to list.
2. **A CLA file appearing.** A contributor licence agreement is the mechanism by which
   three of the four peers acquired the *right* to relicense. The absence of one is the
   structural half of the promise; the paragraph is only its statement.
3. **A new outbound host.** Owned by ``test_network_egress_hosts.py``, not re-implemented
   here. :func:`test_the_rail_reds_on_a_test_phone_home_host` drives *that* rail red from
   this file so clause 3 of the atom is exercised for all three disjuncts in one place.

**What this cannot answer, stated so nobody reads more into a green run.** It is a census of
*declarations*, not of rights. It cannot tell you who holds copyright, whether a contributor
assigned anything by another route, or what a future release will be licensed under — only
that the tree as committed still says MIT in every place it says anything, and that no CLA
has appeared. A licence change made *deliberately* is supposed to red this file: the point
is that it takes an explicit, reviewable edit to two committed artifacts, not a quiet one.

A lockfile is skipped BY RULE, not by allowlist entry: ``package-lock.json`` records
*other people's* licences (it carries MPL-2.0 and more today), so a non-MIT identifier there
is correct and says nothing about this project's terms.
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TABLE = _ROOT / "docs" / "architecture" / "licence-identity.txt"

#: The one identifier this project is allowed to declare about itself.
_EXPECTED = "MIT"

#: The MIT body with the copyright line removed and whitespace collapsed. Pinned so an edit
#: to the GRANT reds while a copyright-year or holder change does not — the year is
#: bookkeeping, the grant is the promise.
_LICENCE_BODY_SHA256 = "7c9b48b52decb9837c70f608678129e1ac79e056829c8d1e82e8cdd8aed562f8"

#: Directories that cannot hold a declaration of *this* project's licence: build output,
#: caches, dependency trees and sibling worktrees.
_SKIP_DIRS = frozenset(
    {
        ".git",
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
        ".dev-home",
        "site-packages",
    }
)

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

# ── the patterns. Each captures a licence IDENTIFIER, never a whole line ────────────────
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

#: Every promise the dated paragraph has to keep making. Matched case-insensitively inside a
#: single window of `README.md` / `SECURITY.md`, so all four must appear TOGETHER — four
#: tokens scattered across a long README would satisfy a whole-file search while telling a
#: reader nothing.
#:
#: `mit only` rather than `mit`: the bare word is a substring of "commit", "submit" and
#: "limitations", all of which appear near the top of this README, so it anchored the window
#: thousands of characters away from the commitment and the date check failed on a document
#: that carried the date.
_COMMITMENT_TOKENS = (
    "mit only",
    "no cla",
    "telemetry",
    "relicens",
)

#: The distinctive token used to LOCATE the commitment block. "no cla" appears nowhere else in
#: either document.
_COMMITMENT_ANCHOR = "no cla"

#: How far either side of the anchor the commitment may spread. Wide enough for a bulleted
#: list with prose around it, narrow enough that a date elsewhere in the file cannot satisfy
#: the check.
_COMMITMENT_WINDOW = 2500

_DATE_RE = re.compile(r"\b20\d{2}-[01]\d-[0-3]\d\b")


def _walk(root: Path):
    """Every file under *root* that is not in a skipped directory.

    Directories are PRUNED as the walk descends rather than filtered afterwards. `rglob("*")`
    plus a filter is the obvious spelling and takes tens of seconds here, because it
    enumerates every object in `.git` and every file in `.venv` before discarding them.

    The skip check is against the path RELATIVE to *root*, never the absolute one: this
    repository is routinely checked out into a `.worktrees/<lane>/` sibling, so matching
    absolute parts made the whole sweep return nothing there while looking perfectly clean —
    a rail that silently scans zero files. `test_the_census_is_not_vacuous` is what catches
    that class of mistake, and it caught exactly this one.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
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


@lru_cache(maxsize=1)
def _real_sites() -> dict[str, set[str]]:
    """The sweep over this repository, read once per session."""
    return declaration_sites(_ROOT)


@lru_cache(maxsize=1)
def _pattern_hits() -> dict[str, int]:
    """pattern label -> how many files in this repository it matches.

    Per PATTERN, not per file, because a disjunct that matches nothing is invisible in the
    combined result: a sibling pattern covering the same file keeps the census looking right
    while the dead one silently stops guarding its own case.
    """
    hits = dict.fromkeys((label for label, _ in _PATTERNS), 0)
    for path in _walk(_ROOT):
        if path.name in _SKIP_FILES:
            continue
        if not _CANDIDATE_NAMES.match(path.name) and path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        is_licence_file = path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))
        for label, pattern in _PATTERNS:
            if label == "licence file title" and not is_licence_file:
                continue
            if pattern.search(text[:200] if label == "licence file title" else text):
                hits[label] += 1
    return hits


def _table() -> dict[str, str]:
    """path -> judgment, from the census table."""
    out: dict[str, str] = {}
    for line in _TABLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, _sep, judgment = line.partition("—")
        out[path.strip()] = judgment.strip()
    return out


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


def check_the_licence_grant_is_unmodified(root: Path) -> None:
    """RED if `LICENSE`'s grant text changed. A year or holder change is allowed."""
    text = (root / "LICENSE").read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lower().startswith("copyright"))
    digest = hashlib.sha256(re.sub(r"\s+", " ", body).strip().encode("utf-8")).hexdigest()
    assert digest == _LICENCE_BODY_SHA256, (
        f"LICENSE's grant text changed (sha256 {digest}, pinned {_LICENCE_BODY_SHA256}).\n"
        "The copyright line is excluded from this hash, so a year or holder update does not "
        "reach it — this is the licence GRANT itself. If the change is deliberate, update "
        "the pin and the dated commitment in README.md and SECURITY.md together."
    )


# ── clause 1: the commitment exists, is dated, and is in both files ─────────────────────


@pytest.mark.parametrize("doc", ["README.md", "SECURITY.md"])
def test_the_governance_commitment_is_present_and_dated(doc: str) -> None:
    """Clause 1. Without this the paragraph is deletable in a docs tidy-up with no red.

    A *date* is required because the value of the commitment to a retained user is knowing
    how long it has held; an undated promise is indistinguishable from one written today.
    """
    text = (_ROOT / doc).read_text(encoding="utf-8")
    low = text.lower()
    assert _COMMITMENT_ANCHOR in low, (
        f"{doc} no longer states the no-CLA commitment. A CLA is the mechanism by which a "
        "project acquires the right to relicense its contributors' work, so this is the one "
        "of the four that is structural rather than declarative."
    )
    anchor = low.index(_COMMITMENT_ANCHOR)
    window = text[max(0, anchor - _COMMITMENT_WINDOW) : anchor + _COMMITMENT_WINDOW]
    window_low = window.lower()
    missing = [token for token in _COMMITMENT_TOKENS if token not in window_low]
    assert not missing, (
        f"{doc} no longer states part of the governance commitment (missing {missing} within "
        f"{_COMMITMENT_WINDOW} characters of {_COMMITMENT_ANCHOR!r}). All four parts — MIT "
        "only, no CLA, no telemetry, no relicensing of already-published releases — are "
        "load-bearing and belong together; a user reading three of them cannot tell which "
        "one was dropped on purpose."
    )
    assert _DATE_RE.search(window), (
        f"{doc}'s governance commitment carries no ISO date within {_COMMITMENT_WINDOW} "
        f"characters of {_COMMITMENT_ANCHOR!r}. 'Held since <date>' is the whole claim — an "
        "undated promise says nothing a brand-new project could not also say."
    )


# ── clause 2, disjunct 1: the licence identifier ────────────────────────────────────────


def test_every_licence_declaration_in_the_tree_says_mit() -> None:
    """The rail, against the real tree."""
    check_every_declaration_is_mit(_ROOT)


def test_the_licence_grant_text_is_pinned() -> None:
    """A relicense can be done by rewriting `LICENSE` without touching any identifier."""
    check_the_licence_grant_is_unmodified(_ROOT)


def test_every_declaration_site_is_in_the_census() -> None:
    """A new place this project declares its licence must be written down and judged."""
    table = _table()
    undeclared = sorted(p for p in _real_sites() if p not in table)
    assert not undeclared, (
        "these files declare this project's licence but are not in the census:\n"
        + "\n".join(f"  {u}" for u in undeclared)
        + "\n\nAdd them to docs/architecture/licence-identity.txt with a judgment saying "
        "WHAT the declaration is for and WHO reads it."
    )


def test_the_census_has_no_stale_entries() -> None:
    """A site that no longer declares anything must leave, or the table stops being a mirror."""
    sites = _real_sites()
    stale = sorted(p for p in _table() if p not in sites)
    assert not stale, (
        "these census entries no longer declare a licence — remove them so the census keeps "
        "meaning what it says:\n" + "\n".join(f"  {s}" for s in stale)
    )


def test_every_census_entry_carries_a_judgment() -> None:
    """A bare path is a list, not a decision.

    ``pyproject.toml`` (what PyPI shows every installer) and a registry test fixture's
    ``LICENSE`` are both one line here and could not matter less alike.
    """
    thin = sorted(p for p, j in _table().items() if len(j) < 30)
    assert (
        not thin
    ), f"these census entries have no real judgment — say what reads the declaration: {thin}"


def test_no_pattern_is_silently_dead() -> None:
    """Every disjunct that should match this tree TODAY does.

    This is the test that would have caught the missing ``re.MULTILINE`` on the pyproject
    pattern immediately: it matched zero files while the census looked complete, because the
    trove classifier in the same file said MIT too. A dead pattern is not a cosmetic problem —
    it is precisely a licence change this rail would have waved through.

    ``spdx header`` is deliberately excluded: there is no ``SPDX-License-Identifier:`` header
    anywhere in this tree today, so zero hits is the correct answer for it. Its teeth are
    proven by :func:`test_the_rail_reds_on_a_fake_spdx_change` arm (a) instead, which injects
    one and watches the rail red.
    """
    hits = _pattern_hits()
    dead = sorted(label for label, n in hits.items() if n == 0 and label != "spdx header")
    assert not dead, (
        f"these licence patterns match NOTHING in this repository: {dead}. Either the thing "
        "they look for is genuinely gone (then delete the pattern and say so) or the pattern "
        f"is broken and guards nothing. Full hit counts: {hits}"
    )


def test_the_census_is_not_vacuous() -> None:
    """Both sides are populated. Two empty sets agree about nothing."""
    sites = _real_sites()
    table = _table()
    assert (
        len(sites) >= 5
    ), f"the sweep found only {len(sites)} declaration sites — it is not reading"
    assert len(table) >= 5, f"the census lists only {len(table)} sites — it looks truncated"
    assert "LICENSE" in sites, "the sweep did not even find the root LICENSE file"
    assert "pyproject.toml" in sites, "the sweep did not find pyproject.toml's licence field"


# ── clause 2, disjunct 2: no CLA ────────────────────────────────────────────────────────


def test_no_contributor_licence_agreement_exists() -> None:
    """The rail, against the real tree."""
    check_no_cla_file(_ROOT)


# ── clause 3: the negative case, for all three disjuncts ────────────────────────────────


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


def test_the_teeth_test_s_own_baseline_is_green(tmp_path: Path) -> None:
    """The control arm. A baseline that already failed would make every red below vacuous."""
    root = _minimal_tree(tmp_path)
    check_every_declaration_is_mit(root)
    check_no_cla_file(root)
    check_the_licence_grant_is_unmodified(root)


def test_the_rail_reds_on_a_fake_spdx_change(tmp_path: Path) -> None:
    """Clause 3, disjunct 1 — a deliberately introduced non-MIT SPDX identifier.

    Three separate mutations, because a rail that catches one spelling and not the others is
    the shape of the peers' actual moves: n8n went to a Sustainable-Use licence in its
    manifest, Open WebUI rewrote the licence FILE.
    """
    root = _minimal_tree(tmp_path)

    # (a) an SPDX header in an ordinary source file. Composed from `_SPDX_TAG` rather than
    # written out, so this file's own bytes stay free of a real header — see that constant.
    (root / "app.py").write_text(f"# {_SPDX_TAG} BUSL-1.1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="BUSL-1.1"):
        check_every_declaration_is_mit(root)
    (root / "app.py").unlink()

    # (b) the manifest's own field, on a line that is NOT the first — see `_pyproject`.
    (root / "pyproject.toml").write_text(_pyproject("Apache-2.0"), encoding="utf-8")
    with pytest.raises(AssertionError, match="Apache-2.0"):
        check_every_declaration_is_mit(root)
    (root / "pyproject.toml").write_text(_pyproject("MIT"), encoding="utf-8")

    # (c) the licence FILE rewritten — no identifier field anywhere touched
    (root / "LICENSE").write_text(
        "Business Source License 1.1\n\nUse is limited.\n", encoding="utf-8"
    )
    with pytest.raises(AssertionError, match="Business Source"):
        check_every_declaration_is_mit(root)
    with pytest.raises(AssertionError, match="grant text changed"):
        check_the_licence_grant_is_unmodified(root)

    # (d) and the subtler one: MIT's title kept, the grant quietly narrowed
    (root / "LICENSE").write_text(
        "MIT License\n\nCopyright (c) 2026 someone\n\nPermission is NOT granted.\n",
        encoding="utf-8",
    )
    check_every_declaration_is_mit(root)  # the identifier still says MIT — and that is the point
    with pytest.raises(AssertionError, match="grant text changed"):
        check_the_licence_grant_is_unmodified(root)


@pytest.mark.parametrize(
    "name",
    [
        "CLA.md",
        "cla.txt",
        "CONTRIBUTOR_LICENSE_AGREEMENT.md",
        "contributor-agreement.md",
        "ICLA.pdf",
    ],
)
def test_the_rail_reds_when_a_cla_file_appears(tmp_path: Path, name: str) -> None:
    """Clause 3, disjunct 2 — every spelling, including one nested out of sight."""
    root = _minimal_tree(tmp_path)
    nested = root / ".github"
    nested.mkdir()
    (nested / name).write_text("sign here\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="contributor licence agreement has appeared"):
        check_no_cla_file(root)


def test_the_rail_reds_on_a_test_phone_home_host(tmp_path: Path, monkeypatch) -> None:
    """Clause 3, disjunct 3 — a test phone-home host, driven through the OWNING rail.

    ``test_network_egress_hosts.py`` owns the egress third of the same promise and its
    negative case is already exercised there at the scanner level. What was missing was
    running the *assertion* red, so this points that module's core root at a tmp tree
    carrying an analytics endpoint and watches its rail fail. Nothing in the real tree is
    touched, and the egress rail is not re-implemented here.
    """
    import test_network_egress_hosts as egress

    fake_core = tmp_path / "core"
    fake_core.mkdir()
    fake_web = tmp_path / "web"
    fake_web.mkdir()

    # Control arm first: a core with no hosts at all leaves the rail green, so the red below
    # is caused by the injected host and not by the monkeypatch itself. `_ROOT` moves with
    # the scan roots because the rail reports findings as paths relative to it.
    (fake_core / "clean.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(egress, "_CORE", fake_core)
    monkeypatch.setattr(egress, "_WEB", fake_web)
    monkeypatch.setattr(egress, "_ROOT", tmp_path)
    egress.test_every_egress_host_is_a_declared_destination()

    (fake_core / "phone_home.py").write_text(
        "import requests\n"
        "requests.post('https://telemetry.personalclaw-metrics.net/v1/events', json=payload)\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="telemetry.personalclaw-metrics.net"):
        egress.test_every_egress_host_is_a_declared_destination()


def test_the_declaration_sweep_ignores_other_peoples_licences(tmp_path: Path) -> None:
    """The skip rule, proven — otherwise the rail reds on every dependency bump.

    ``package-lock.json`` carries MPL-2.0 today. If the sweep read it, the honest fix would
    be to stop reading lockfiles; asserting the rule here is what stops someone instead
    widening ``_EXPECTED`` to make the red go away, which would disarm the whole rail.
    """
    root = _minimal_tree(tmp_path)
    (root / "package-lock.json").write_text('{"license": "MPL-2.0"}\n', encoding="utf-8")
    check_every_declaration_is_mit(root)
    assert "MPL-2.0" in (_ROOT / "package-lock.json").read_text(
        encoding="utf-8"
    ), "package-lock.json no longer carries a non-MIT licence, so this skip rule is untested"
