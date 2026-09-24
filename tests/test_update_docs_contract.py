"""The update documentation says what the code does — and cannot silently drift back (RUM-11).

The prose this file guards is CURRENTLY CORRECT BY ACCIDENT. RUM-3 rewrote README's privacy
paragraph when it built the check kill switch, and RUM-4/6/7 rewrote the container guide when
they made the apply channel-aware — but nothing asserted any of it, so the next edit to either
file could reinstate "no setting turns the check off" and every test in the suite would stay
green. A behaviour whose only proof is a person having read the file once is undocumented.

Three claims are pinned here, and each is pinned with its own VACUITY FLOOR — the anchor is
located first and the test FAILS when the anchor is missing, rather than passing because a
`not in` matched an empty string:

1. README's "One outbound call" section exists AND states that the check can be turned off,
   naming `updates.check_enabled`. Plus the negative: no sentence anywhere in README claims
   it cannot be.
2. The three install-kind guides each describe the four things a user has to decide —
   channels, pinning, opt-in staging, and the check kill switch.
3. `CHANGELOG.md` carries the class-B entry for the main-tracking → release-tracking flip —
   in whichever section currently holds it — and that entry advises `personalclaw snapshot`
   and names the four `updates.*` fields a reader has to act on.

🪤 EVERY MATCH IS CASE-INSENSITIVE AND SUBSTRING-BASED ON PURPOSE, but the negative checks are
phrase lists rather than keyword sweeps: "cannot be turned off" is the claim, and a keyword
sweep for "cannot" would red on any honest sentence that happens to use the word.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
GUIDES = REPO_ROOT / "docs" / "guides"

#: The four decisions the `updates` block asks a user to make, as
#: (subject, the phrases that would satisfy a reader looking for it).
#: A guide that omits one leaves its readers to discover the field in `config/loader.py`.
UPDATE_SUBJECTS: list[tuple[str, tuple[str, ...]]] = [
    ("channels", ("updates.channel", "channel")),
    ("pinning", ("updates.pin",)),
    ("opt-in staging", ("updates.auto", "staged")),
    ("the check kill switch", ("updates.check_enabled",)),
]

#: Every guide whose readers install PersonalClaw a different way, so each has to answer the
#: four subjects in its own terms — a wheel user reading the container guide is not served.
KIND_GUIDES = ("getting-started.md", "containers.md", "desktop.md")


def _read(path: Path) -> str:
    assert path.exists(), f"{path} is missing — this rail has nothing to check"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty — this rail would pass vacuously"
    return text


def _section(text: str, heading_contains: str) -> str:
    """The body of the markdown section whose heading contains *heading_contains*.

    Returns "" when no such heading exists, which every caller asserts against FIRST — a
    "the bad phrase is absent" check over an empty string is the classic vacuous rail.
    """
    lines = text.splitlines()
    want = heading_contains.lower()
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and want in line.lower():
            depth = len(line) - len(line.lstrip("#"))
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if nxt.startswith("#") and len(nxt) - len(nxt.lstrip("#")) <= depth:
                    return "\n".join(lines[i:j])
            return "\n".join(lines[i:])
    return ""


def _entry(text: str, phrase: str) -> tuple[str, str]:
    """``(enclosing section heading, bullet block)`` for the CHANGELOG entry citing *phrase*.

    An entry is its ``- `` line plus every following indented continuation line — most of
    this file's bullets wrap — so the block returned is the whole entry a reader sees, not
    the one physical line the phrase happened to land on.

    Deliberately searches the WHOLE file rather than one section. A release cut moves
    `[Unreleased]` content into `## [X.Y.Z]` and leaves `_Nothing yet._` behind, so an
    assertion anchored on `[Unreleased]` reds on every cut while the entry it guards is
    still there, one section down. The heading comes back with the block so the caller can
    still require the entry to be FILED under a release section rather than stranded in the
    file preamble.

    Returns ``("", "")`` when no entry cites the phrase — callers assert on that first.
    """
    lines = text.splitlines()
    want = phrase.lower()
    heading = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line
        if not line.startswith("- "):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and lines[j].strip() and lines[j][:1] in (" ", "\t"):
            j += 1
        block = "\n".join(lines[i:j])
        if want in block.lower():
            return heading, block
        i = j
    return "", ""


# ── 1. README: the check CAN be turned off ──────────────────────────────────


def test_readme_privacy_section_is_locatable() -> None:
    """Vacuity floor for everything below: the anchor paragraph must exist.

    Without this, a README that lost the whole privacy section would pass the
    "no false claim" test below — because there would be no claim at all.
    """
    body = _section(_read(README), "Privacy")
    assert body, "README has no Privacy section — the update-check disclosure has no home"
    assert "one outbound call" in body.lower(), (
        "README's Privacy section must still name the ONE outbound call this project makes; "
        "the kill-switch sentence is meaningless without the disclosure it qualifies"
    )


def test_readme_says_the_update_check_can_be_turned_off() -> None:
    """The positive claim, with the field name a reader can act on.

    'You can turn it off' with no field name is not actionable, so the assertion is on
    `updates.check_enabled` — the thing you actually set.
    """
    body = _section(_read(README), "Privacy")
    low = body.lower()
    assert (
        "updates.check_enabled" in body
    ), "README must name `updates.check_enabled` — the setting that silences the check"
    assert re.search(
        r"turn (that|the|it) check off|turn it off|can turn", low
    ), "README must state plainly that the check can be turned off"
    assert (
        "zero" in low
    ), "and say what off MEANS: zero outbound calls, not merely a longer interval"


#: Phrases that each assert the OPPOSITE of what the code does since RUM-3. Any one of them
#: in README is the regression this rail exists to catch.
FALSE_CLAIMS = (
    "no setting turns the check off",
    "no setting turns it off",
    "cannot be turned off",
    "can't be turned off",
    "cannot be disabled",
    "can't be disabled",
    "there is no way to disable",
    "always checks",
)


def test_readme_never_claims_the_check_cannot_be_turned_off() -> None:
    """The negative. `updates.check_enabled=false` makes the updater issue zero calls —
    ``self_update.fetch_latest_release`` returns the cache before opening a session, and
    ``_do_update_check`` reads the flag before any subprocess — so a README saying otherwise
    would be telling users to accept egress they can refuse."""
    low = _read(README).lower()
    offenders = [p for p in FALSE_CLAIMS if p in low]
    assert not offenders, (
        f"README claims the update check cannot be turned off: {offenders}. It can — "
        "`updates.check_enabled=false` stops every outbound call."
    )


def test_the_kill_switch_this_rail_describes_is_really_wired() -> None:
    """The rail's own floor: the DOCUMENTED field must exist in the code it describes.

    A docs rail whose subject was deleted keeps passing while the prose becomes fiction. This
    reads the config surface directly, so the claim and the mechanism are checked together.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert AppConfig().updates.check_enabled is True, "the documented default is ON"
    assert (
        "updates.check_enabled" in _EDITABLE_CONFIG
    ), "README tells users to set it; it must be settable"


# ── 2. the three install-kind guides describe the four decisions ────────────


@pytest.mark.parametrize("guide", KIND_GUIDES)
def test_each_install_guide_describes_every_update_decision(guide: str) -> None:
    """Channels, pinning, opt-in staging and the kill switch, per install kind.

    Before RUM-11 the count was 0/4 in `getting-started.md` and `desktop.md`: a wheel user
    following the primary install path was never told that `beta` exists, that a pin is how
    you stay put, that auto-apply is opt-in, or that the release check has an off switch.
    """
    text = _read(GUIDES / guide)
    low = text.lower()
    missing = [
        subject
        for subject, phrases in UPDATE_SUBJECTS
        if not any(p.lower() in low for p in phrases)
    ]
    assert not missing, f"docs/guides/{guide} never describes: {', '.join(missing)}"


@pytest.mark.parametrize("guide", KIND_GUIDES)
def test_each_install_guide_advises_a_snapshot_before_moving_versions(guide: str) -> None:
    """Pre-1.0 releases carry no migrations in EITHER direction, so this is the only way back.

    Named per guide rather than once in the README because the reader who is about to run
    `personalclaw update` is reading their own install's guide.
    """
    assert "personalclaw snapshot" in _read(
        GUIDES / guide
    ), f"docs/guides/{guide} must advise `personalclaw snapshot` before an update/rollback"


@pytest.mark.parametrize("guide", KIND_GUIDES)
def test_each_install_guide_documents_rolling_back(guide: str) -> None:
    """RUM-9 made rollback a supported path; a supported path a user cannot find is not one."""
    low = _read(GUIDES / guide).lower()
    assert (
        "roll back" in low or "rolling back" in low
    ), f"docs/guides/{guide} never mentions rolling back"


# ── 3. the class-B CHANGELOG entry for the flip ─────────────────────────────

#: Any ONE of these identifies the flip entry. A disjunction rather than one exact sentence
#: so a reworded entry that still makes the claim is not a false red.
FLIP_PHRASES = ("tracks releases, not `main`", "release-tracking", "not `main`")


def test_changelog_records_the_main_tracking_to_release_tracking_flip() -> None:
    """The class-B entry EXISTS in the CHANGELOG, advises a snapshot, and names its fields.

    Class B is "changes the shape of a user's install or its state", which this is: the
    default auto-apply flipped off, the git kind stopped following a branch, and a home
    carrying the legacy `auto_update` bool is remapped on load. The entry is how a user who
    upgrades learns any of that happened — which is the property pinned here.

    The claim is about EXISTENCE, not position. This asserted `[Unreleased]` before, and a
    release cut moves that section's content into `## [X.Y.Z]` and leaves `_Nothing yet._`
    behind, so every cut red this rail while the entry was still recorded one section down —
    the test failed precisely when the change it guards reached the users it was written for.

    Scoped TIGHTER than before in exchange: the snapshot advice and the four field names are
    now required in the flip ENTRY, where a reader meets them, rather than anywhere in a
    section that also holds dozens of unrelated entries. That is what the old code comment
    claimed ("nameable from the entry itself") without enforcing.
    """
    text = _read(CHANGELOG)
    assert any(
        ln.startswith("## [") for ln in text.splitlines()
    ), "CHANGELOG has no version sections — this rail has no structure to check"

    heading = entry = ""
    for phrase in FLIP_PHRASES:
        heading, entry = _entry(text, phrase)
        if entry:
            break
    assert entry, (
        "no CHANGELOG entry describes the main-tracking → release-tracking flip; looked for "
        f"{FLIP_PHRASES} in every entry, in every section"
    )
    assert heading.startswith("## ["), (
        "the flip entry must be filed under a release section — `[Unreleased]` before a cut, "
        f"`[X.Y.Z]` after one — not stranded in the preamble; found it under {heading!r}"
    )
    assert "personalclaw snapshot" in entry, (
        "a class-B entry under the pre-1.0 banner advises `personalclaw snapshot` instead of "
        "shipping migration machinery"
    )
    # The four fields a reader has to act on must be nameable from the entry itself.
    for field in ("updates.channel", "updates.pin", "updates.auto", "updates.check_enabled"):
        assert field in entry, f"the entry must name {field}"
