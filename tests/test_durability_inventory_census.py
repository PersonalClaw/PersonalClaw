"""Every home location the CODE can create is either declared state or a pinned exception.

`durability/inventory.py` calls itself "the single manifest of what PersonalClaw's state IS",
and `snapshot._everything_paths` derives the `everything` component from it — so an
undeclared location is silently absent from `personalclaw snapshot`. That command is what the
pre-1.0 release notes tell users to run BEFORE upgrading, which makes a gap here a data-loss
gap rather than a coverage nicety.

**Why a census and not a list of examples.** `themes/` was missed (issue 647) and the existing
coverage test is a hard-coded list of nine paths that were fixed once (`TestGapClosure`) — it
could never have caught a tenth. The nine before it were found the same way: by someone
looking. This walks the source instead.

**How it reads the source.** Every `config_dir() / X` in `src/personalclaw`, where `X` is a
string literal or a module-level constant resolved in the same file. That second form is not a
nicety: `themes` is spelled `config_dir() / _THEMES_DIR_NAME`, so a literal-only scan would
have missed exactly the bug that prompted this.

**What it cannot see, stated rather than implied.** A path built from a runtime value
(`config_dir() / name`) is invisible to any static scan, and `test_the_blind_spot_is_bounded`
records how many of those exist so the number cannot grow unnoticed. The primary defence for
those is `inventory.audit_home()` against a real home.

**"Accounted for" is the inventory's question, not this file's.** A location is settled when
`inv.is_accounted` says so — an entry CLAIMS it, or `IGNORED` deliberately excludes it. This
file used to re-derive a NARROWER version that read only the claims, plus its own copy of
IGNORED's rows. Two things followed (#2217): five locations settled the *correct* way for
machine-local state (`session_key`, `sessions.json`, `update_check.json`, `update_releases.json`,
`doctor`) were still counted as undeclared debt, and the ratchet could only notice a pin that
became *declared* — so the debt set could shrink by declaring and never by ignoring. Both
surfaces now ask one predicate.

**The exception list is a BACKLOG, not an approval.** `_UNDECLARED_DEBT` holds the locations
that are real, genuinely-undecided state (`inbox`, `sources`, `packs`, `onboarding`, `history`,
…). Declaring one demands a per-entry `kind`/`domain`/`merge` decision, and `merge` is live — a
wrong value silently corrupts on convergence, which is worse than an absent entry that merely
omits a backup. They are pinned here to make the debt visible and to stop the next one arriving
unnoticed, not to bless them.
"""

from __future__ import annotations

import pathlib
import re

from personalclaw.durability import inventory as inv

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"

#: `config_dir() / "literal"` or `config_dir() / CONSTANT`.
_USE = re.compile(r'config_dir\(\)\s*/\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))')


def _censused() -> tuple[dict[str, set[str]], set[str]]:
    """(resolved name → files that use it, identifiers that could not be resolved)."""
    resolved: dict[str, set[str]] = {}
    unresolved: set[str] = set()
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _USE.finditer(text):
            literal, ident = match.group(1), match.group(2)
            if literal:
                resolved.setdefault(literal, set()).add(path.name)
                continue
            const = re.search(rf'^{re.escape(ident)}\s*[:=][^=]*?"([^"]+)"', text, re.M)
            if const:
                resolved.setdefault(const.group(1), set()).add(path.name)
            else:
                unresolved.add(ident)
    return resolved, unresolved


#: There is no second exception list here. "Deliberately not state" is a decision
#: `durability.inventory.IGNORED` already owns, and `audit_home()` — the guard that runs against
#: a REAL home — has always honoured it. This file used to keep its own `_NOT_STATE` copy of that
#: list: eight of its nine rows duplicated IGNORED verbatim (three even said so in a comment),
#: and the ninth (`loop.md`) was a decision this test file had made and the inventory had never
#: heard about, so `audit_home()` reported it as unmanaged drift on every real home that had run
#: a loop. `loop.md` is now one row in IGNORED, and the accounting question goes to
#: `inv.is_accounted` (#2217).

#: DEBT — real state that is not declared, so `personalclaw snapshot` does not carry it.
#: Filed as its own issue; each needs a `kind`/`domain`/`merge` decision that must not be
#: guessed. Shrinking this set is the point; adding to it should require the same argument.
_UNDECLARED_DEBT = frozenset(
    {
        "app_messages",
        # BA-5's browse kill switch, the sibling of `incident.json` below. Whether a restore
        # should carry "browse is stopped" is a real kind/domain/merge decision: a human pulled
        # it, so re-enabling browsing on restore may be wrong, yet carrying the stop onto a
        # different machine may be too. Pinned as debt rather than guessed.
        "browse_kill.json",
        "chat_plans",
        "control_bridge.json",
        "digest_queue.jsonl",
        "engagement.json",
        "graph_maintenance.json",
        "history",
        "inbox",
        "inbox_state.json",
        "incident.json",
        "onboarding",
        "packs",
        # Caught BY THIS RAIL during review, on code that landed while the PR was open
        # (MOBILE-COMPANION MC-5's web-push delivery). Web-push subscriptions identify a
        # specific browser/device, so whether a restore should carry them is a real question —
        # keeping them means notifications survive a same-machine restore, and means stale
        # endpoints on a different one. Pinned rather than guessed, and raised on the debt issue
        # for the atom's owner.
        "push_subscriptions.json",
        # MC-9's native-push sibling of the entry above, pinned on the same argument: a relay
        # token identifies one physical handset, so whether a restore should carry it is the
        # SAME real kind/domain/merge question — keeping it means native pings survive a
        # same-machine restore, and means a stale token pointed at a different machine's app
        # install on any other. Raised on the same debt issue as push_subscriptions.json.
        "push_relay_tokens.json",
        "recent_projects.json",
        "research_reports.json",
        "runners",
        "settings",
        "sources",
        "surfaces",
    }
)


def _declared_tops() -> set[str]:
    return {entry.path.split("/", 1)[0] for entry in inv.all_entries()}


def test_the_census_is_not_vacuous():
    """The floor: a scan that finds nothing would make every assertion below pass."""
    resolved, _ = _censused()
    assert len(resolved) >= 60, f"only {len(resolved)} home locations censused — regex broke"
    assert len(_declared_tops()) >= 50, "the inventory read back nearly empty"


def test_themes_is_declared_so_snapshot_carries_a_custom_theme():
    """Issue 647. A theme saved from Settings › Design lands in `config_dir()/themes/<slug>.json`
    and the manifest did not know the directory existed, so `personalclaw snapshot` dropped
    every one and a restore came back without them."""
    assert "themes" in _declared_tops()
    claim = inv.claim_for("themes/nurse-handoff-night.json")
    assert claim is not None and claim.id == "themes"


def test_every_censused_location_is_declared_or_pinned():
    """The ratchet. A new home location must be ACCOUNTED FOR by the inventory — declared as
    state, or deliberately ignored — or pinned in `_UNDECLARED_DEBT` with the reason. All three
    are decisions someone makes, which is the point; a directory that silently misses every
    backup is not."""
    resolved, _ = _censused()
    unaccounted = sorted(
        name for name in resolved if not inv.is_accounted(name) and name not in _UNDECLARED_DEBT
    )
    assert not unaccounted, (
        "these home locations are in neither the durability inventory nor a pinned exception, "
        "so `personalclaw snapshot` will not carry them: "
        + ", ".join(f"{n} (used in {sorted(resolved[n])[:2]})" for n in unaccounted)
    )


def test_the_pinned_sets_have_no_stale_entries():
    """A pin for a location nothing uses any more is a row that pins nothing — and it would hide
    the next real gap behind a passing test."""
    resolved, _ = _censused()
    gone = sorted(_UNDECLARED_DEBT - set(resolved))
    assert not gone, f"nothing uses these any more — drop them from the pins: {gone}"


def test_a_pin_the_inventory_already_accounts_for_is_not_debt():
    """The ratchet could only see ONE of the inventory's two ways of accounting for a path.

    `audit_home()` — the production guard — excuses a path if an entry CLAIMS it **or** if
    `IGNORED` deliberately excludes it. The old `_declared_tops()` check read only the first, so
    a location resolved the *correct* way for machine-local state (`session_key`,
    `sessions.json`, `update_check.json`, `update_releases.json` and `doctor` are all in
    `inventory.IGNORED`, each with a written argument) stayed pinned here as undecided debt for
    good: the debt set could shrink by declaring and never by ignoring, which inflated the
    number #2217 reports and left five pins standing for decisions somebody had already made.

    That is the failure mode `test_the_pinned_sets_have_no_stale_entries` says it exists to
    prevent — "a row that pins nothing … would hide the next real gap behind a passing test" —
    reached through the one accounting mechanism it could not see.
    """
    settled = sorted(n for n in _UNDECLARED_DEBT if inv.is_accounted(n))
    assert not settled, (
        "the inventory already claims or deliberately ignores these, so they are decisions "
        f"already made — drop them from `_UNDECLARED_DEBT`: {settled}"
    )


def test_the_blind_spot_is_bounded():
    """What this scan cannot see, recorded as a number rather than left implied.

    A path built from a runtime value (`config_dir() / name`) is invisible to any static scan.
    Pinning the count means a new dynamic home path is a visible change, and the honest place
    to catch those is `inventory.audit_home()` against a real home.
    """
    _, unresolved = _censused()
    assert len(unresolved) <= 16, (
        "more home paths are now built from runtime values than when this was measured, so the "
        f"census covers proportionally less: {sorted(unresolved)}"
    )
