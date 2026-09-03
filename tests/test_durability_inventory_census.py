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

**The exception list is a BACKLOG, not an approval.** Thirty-two locations are undeclared
today, and roughly twenty of them are real state (`auth`, `inbox`, `sources`, `packs`,
`onboarding`, `session_key`, …). Declaring one demands a per-entry `kind`/`domain`/`merge`
decision, and a wrong `merge` on a live store is worse than an absent one — so they are pinned
here to make the debt visible and to stop a thirty-third arriving unnoticed, not to bless them.
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


#: Undeclared home locations, pinned. Each line is debt or a deliberate non-state file.
#:
#: DELIBERATE — logs, pids and locks are runtime noise, re-created on demand, and a backup
#: that restored a stale lock or a dead pid would be worse than one that omits them:
_NOT_STATE = frozenset(
    {
        "agent_pids.txt",
        "session_pids.txt",
        "audit.log",
        "gateway.log",
        "gateway-restart.log",
        "locks",
        "loop.md",  # a rendered prompt, re-rendered per run
    }
)

#: DEBT — real state that is not declared, so `personalclaw snapshot` does not carry it.
#: Filed as its own issue; each needs a `kind`/`domain`/`merge` decision that must not be
#: guessed. Shrinking this set is the point; adding to it should require the same argument.
_UNDECLARED_DEBT = frozenset(
    {
        "app_messages",
        "auth",
        # BA-5's browse kill switch, the sibling of `incident.json` below. Whether a restore
        # should carry "browse is stopped" is a real kind/domain/merge decision: a human pulled
        # it, so re-enabling browsing on restore may be wrong, yet carrying the stop onto a
        # different machine may be too. Pinned as debt rather than guessed.
        "browse_kill.json",
        "chat_plans",
        "control_bridge.json",
        "credentials.json",
        "crons",
        "digest_queue.jsonl",
        "doctor",
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
        "recent_projects.json",
        "research_reports.json",
        "runners",
        "session_key",
        "sessions.json",
        "settings",
        "sources",
        "surfaces",
        "update_check.json",
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
    """The ratchet. A new home location must be declared in the inventory, or added to
    `_NOT_STATE` / `_UNDECLARED_DEBT` with the reason — which is a decision someone makes,
    rather than a directory that silently misses every backup."""
    resolved, _ = _censused()
    declared = _declared_tops()
    unaccounted = sorted(
        name
        for name in resolved
        if name.split("/", 1)[0] not in declared
        and name not in _NOT_STATE
        and name not in _UNDECLARED_DEBT
    )
    assert not unaccounted, (
        "these home locations are in neither the durability inventory nor a pinned exception, "
        "so `personalclaw snapshot` will not carry them: "
        + ", ".join(f"{n} (used in {sorted(resolved[n])[:2]})" for n in unaccounted)
    )


def test_the_pinned_sets_have_no_stale_entries():
    """A pin for a location nothing uses any more, or one that has since been declared, is a
    row that pins nothing — and it would hide the next real gap behind a passing test."""
    resolved, _ = _censused()
    declared = _declared_tops()
    gone = sorted((_NOT_STATE | _UNDECLARED_DEBT) - set(resolved))
    assert not gone, f"nothing uses these any more — drop them from the pins: {gone}"
    now_declared = sorted(n for n in _UNDECLARED_DEBT if n.split("/", 1)[0] in declared)
    assert not now_declared, (
        "these are declared in the inventory now — remove them from `_UNDECLARED_DEBT` so the "
        f"debt set keeps shrinking: {now_declared}"
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
