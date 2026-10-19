"""The governance promise is a control, not a paragraph: the licence rail, on this repository.

These tests read the repository itself and run once per CI run, in the rails job. The checks
they run live in ``tests/licence_governance_shared.py``, and ``tests/test_licence_governance.py``
proves each one fails, on trees it plants.


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
   this file so the negative case is exercised for all three disjuncts in one place.

Disjunct 1 is three claims, not one, because an identifier is the *cheapest* of the three to
keep honest and the least load-bearing. A licence file says MIT, carries a GRANT, and names a
HOLDER, and each can rot without moving the other two:

* the **identifier** — :func:`check_every_declaration_is_mit`;
* the **grant** — :func:`check_every_licence_file_carries_the_real_grant`, sha256 against the
  root file's body, over EVERY licence file in the tree rather than only the root one. A
  paraphrased or narrowed grant under an intact ``MIT License`` title is invisible to the
  identifier sweep by construction (arm (d) of the teeth test passes it on purpose), and five
  sibling licence files — the bundled ollama-models app's, the app template's, the three
  registry fixtures' — were pinned by nothing at all;
* the **holder** — :func:`check_every_licence_holder_is_a_real_holder` (this tree names
  ``PersonalClaw contributors``) and :func:`check_no_licence_holder_is_an_artefact_name` (no
  licence names the app beside it). The grant hash deliberately excludes the copyright line,
  so before these the holder was checked by nothing, and ``personalclaw app new``'s
  ``author or display`` fallback shipped ``Copyright (c) 2026 Channel Null`` to four
  published exemplar repositories — a grant naming an artefact, which holds no copyright and
  names nobody to ask. ``tests/test_app_scaffold.py`` drives the artefact check over freshly
  generated output rather than re-deriving the rule, so there is one definition of it.

**What this cannot answer, stated so nobody reads more into a green run.** It is a census of
*declarations*, not of rights. It reads the copyright line, so it can tell you what a file
SAYS about its holder — it cannot tell you who legally holds copyright, whether a contributor
assigned anything by another route, or what a future release will be licensed under. Only that
the tree as committed still says MIT in every place it says anything, that every grant is the
grant, that every holder is one this project uses, and that no CLA has appeared. A licence
change made *deliberately* is supposed to red this file: the point is that it takes an
explicit, reviewable edit to two committed artifacts, not a quiet one.

A lockfile is skipped BY RULE, not by allowlist entry: ``package-lock.json`` records
*other people's* licences (it carries MPL-2.0 and more today), so a non-MIT identifier there
is correct and says nothing about this project's terms.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pytest
from licence_governance_shared import (
    _CANDIDATE_NAMES,
    _PATTERNS,
    _SKIP_FILES,
    _TEXT_SUFFIXES,
    _minimal_tree,
    _walk,
    artefact_names_beside,
    check_every_declaration_is_mit,
    check_every_licence_file_carries_the_real_grant,
    check_every_licence_holder_is_a_real_holder,
    check_no_cla_file,
    check_no_licence_holder_is_an_artefact_name,
    declaration_sites,
    licence_files,
)
from rails_repo import REPO

_TABLE = REPO / "docs" / "architecture" / "licence-identity.txt"


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


@lru_cache(maxsize=1)
def _real_sites() -> dict[str, set[str]]:
    """The sweep over this repository, read once per session."""
    return declaration_sites(REPO)


@lru_cache(maxsize=1)
def _pattern_hits() -> dict[str, int]:
    """pattern label -> how many files in this repository it matches.

    Per PATTERN, not per file, because a disjunct that matches nothing is invisible in the
    combined result: a sibling pattern covering the same file keeps the census looking right
    while the dead one silently stops guarding its own case.
    """
    hits = dict.fromkeys((label for label, _ in _PATTERNS), 0)
    for path in _walk(REPO):
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


# ── the commitment exists, is dated, and is in both files ───────────────────────────────


@pytest.mark.parametrize("doc", ["README.md", "SECURITY.md"])
def test_the_governance_commitment_is_present_and_dated(doc: str) -> None:
    """Clause 1. Without this the paragraph is deletable in a docs tidy-up with no red.

    A *date* is required because the value of the commitment to a retained user is knowing
    how long it has held; an undated promise is indistinguishable from one written today.
    """
    text = (REPO / doc).read_text(encoding="utf-8")
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


# ── disjunct 1: the licence identifier ──────────────────────────────────────────────────


def test_every_licence_declaration_in_the_tree_says_mit() -> None:
    """The rail, against the real tree."""
    check_every_declaration_is_mit(REPO)


def test_every_licence_file_carries_the_pinned_grant() -> None:
    """A relicense can be done by rewriting a licence file without touching any identifier."""
    check_every_licence_file_carries_the_real_grant(REPO)


def test_every_licence_file_names_an_approved_holder() -> None:
    """The holder half of the grant, which the pinned hash deliberately cannot see."""
    check_every_licence_holder_is_a_real_holder(REPO)


def test_no_licence_file_names_an_app_as_its_holder() -> None:
    """The scaffolder's defect, as a standing rail over the bundles this tree ships."""
    check_no_licence_holder_is_an_artefact_name(REPO)


def test_the_licence_file_sweep_is_not_vacuous() -> None:
    """Both holder rails iterate ``licence_files``, so a sweep that finds nothing is silent.

    The bug this guards is measured, not hypothetical: ``_walk``'s sibling-worktree skip once
    matched on the ABSOLUTE path and made the whole census return nothing while looking clean
    (see that function). Two of the three checks above are ``for … in licence_files(root)``
    loops whose assertion never runs on an empty list, so they would pass a tree with no
    licence in it at all. The bundle arm needs its own floor for the same reason: every
    licence file living at a root with no ``app.json`` beside it would make
    :func:`check_no_licence_holder_is_an_artefact_name` a loop that compares against the
    empty set every time.
    """
    files = licence_files(REPO)
    assert len(files) >= 3, (
        f"the licence-file sweep found only {len(files)} file(s) — it is not reading. "
        "This tree carries the root LICENSE and one beside each code-owning bundled app "
        f"(bundled-chat, ollama-models): {[str(f) for f in files]}"
    )
    assert any(
        f.name == "LICENSE" and f.parent == REPO for f in files
    ), "the sweep did not even find the root LICENSE file"
    with_bundle = [f for f in files if artefact_names_beside(f)]
    assert len(with_bundle) >= 2, (
        f"only {len(with_bundle)} licence file(s) sit beside an app.json, so the artefact-"
        "holder rail is comparing against the empty set almost everywhere. Either the bundled "
        "apps moved or `artefact_names_beside` stopped resolving a manifest."
    )
    # The over-matching half, measured: the census TABLE uppercases to `LICENCE-IDENTITY.TXT`,
    # so a case-insensitive name match swept it in and both holder rails reported the table
    # itself as a licence with no copyright line. See `_LICENCE_FILE_RE`.
    assert _TABLE not in files, (
        f"{_TABLE.name} is the census table, not a licence — `_LICENCE_FILE_RE` has gone "
        "case-insensitive again and the holder rails are now grading a document that only "
        "discusses the licence"
    )


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


# ── disjunct 2: no CLA ──────────────────────────────────────────────────────────────────


def test_no_contributor_licence_agreement_exists() -> None:
    """The rail, against the real tree."""
    check_no_cla_file(REPO)


def test_the_lockfile_skip_rule_is_still_needed() -> None:
    """``package-lock.json`` records other people's licences, and the sweep skips it by rule
    (``tests/test_licence_governance.py`` proves the skip on a planted lockfile). While this
    repository's lockfile carries a non-MIT licence, that rule is what keeps the rail green."""
    assert "MPL-2.0" in (REPO / "package-lock.json").read_text(
        encoding="utf-8"
    ), "package-lock.json no longer carries a non-MIT licence, so this skip rule is untested"


# ── the CLA and the egress third, on planted trees ──────────────────────────────────────


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
    import test_network_egress_hosts_tree as egress

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
    # The fake core ships no DATA records (no bundled-chat sign-off, no source recipes), so the
    # rail's record half reads none. That half, and its own "the sign-off must parse" floor, are
    # exercised against the real tree by `test_network_egress_hosts` itself; reading the real
    # records here would judge a tree this test did not build.
    monkeypatch.setattr(egress, "_record_fetches", dict)
    egress.test_every_egress_host_is_a_declared_destination()

    (fake_core / "phone_home.py").write_text(
        "import requests\n"
        "requests.post('https://telemetry.personalclaw-metrics.net/v1/events', json=payload)\n",
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="telemetry.personalclaw-metrics.net"):
        egress.test_every_egress_host_is_a_declared_destination()
