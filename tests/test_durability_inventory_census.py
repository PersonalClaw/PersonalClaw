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

**How it reads the source.** Four spellings of "a path under the home", because a census that
only knows one of them is an enumerated rail wearing a census's clothes — it cannot see the
locations the other three spellings introduce, and it does not report that it cannot:

1. `config_dir() / "literal"`.
2. `config_dir() / CONSTANT`, resolved against a module-level constant in the same file. Not a
   nicety: `themes` is spelled `config_dir() / _THEMES_DIR_NAME`, so a literal-only scan would
   have missed exactly the bug that prompted this.
3. `config_dir() / f"prefix{...}"` — the STATIC PREFIX of an f-string. Measured (#2217): this
   had been landing in the unresolved bucket as the identifier `f`, so
   `session_pid_<pid>.txt` — one file per ACP agent, on every home that has run a chat — was
   never checked at all, and `audit_home()` reported each one as unmanaged drift on the live
   Doctor probe. A prefix is enough: it is what `IGNORED`'s globs and `claim_for`'s
   longest-prefix match both key on.
4. `home = config_dir()` followed by `home / "literal"`. Fifteen call sites bind the home to a
   local name first, and the three-spelling scan could not see ONE of them. Measured (#2217):
   seven locations reach the home only this way, and `use_case_settings` (`evals/ablation.py`)
   was in neither the inventory nor the debt pin nor the bounded blind-spot count — invisible,
   and invisible in a way the rail did not report. That is the failure this file exists to
   prevent, reached through its own regex rather than through a hand-written list.

**What it STILL cannot see, stated rather than implied.** A path built from a runtime value
with no static part (`config_dir() / name`) is invisible to any static scan, and
`test_the_blind_spot_is_bounded` records how many of those exist so the number cannot grow
unnoticed. The primary defence for those is `inventory.audit_home()` against a real home,
which is wired as the `durability.inventory` Doctor probe (`resilience/doctor.py`).

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

#: `config_dir() / f"prefix{...}"`, `config_dir() / "literal"`, or `config_dir() / CONSTANT`.
#: The f-string alternative is FIRST because the identifier alternative would otherwise swallow
#: its `f` and report the whole path as a runtime value (#2217).
_USE = re.compile(
    r'config_dir\(\)\s*/\s*(?:f"([^"{]+)\{|"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))',
)

#: `home = config_dir()` — a local name bound to the home, so `home / "x"` is a home location
#: the `config_dir() /` spellings above cannot see.
_BIND = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*config_dir\(\)\s*$", re.M)


def _via_bound_home(text: str) -> set[str]:
    """Locations reached as `<name> / "literal"` where `<name> = config_dir()` in this file."""
    out: set[str] = set()
    for name in set(_BIND.findall(text)):
        use = re.compile(rf'\b{re.escape(name)}\s*/\s*(?:f"([^"{{]+)\{{|"([^"]+)")')
        for match in use.finditer(text):
            out.add(match.group(1) or match.group(2))
    return out


def _censused() -> tuple[dict[str, set[str]], set[str]]:
    """(resolved name → files that use it, identifiers that could not be resolved)."""
    resolved: dict[str, set[str]] = {}
    unresolved: set[str] = set()
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _USE.finditer(text):
            fprefix, literal, ident = match.group(1), match.group(2), match.group(3)
            if fprefix or literal:
                resolved.setdefault(fprefix or literal, set()).add(path.name)
                continue
            const = re.search(rf'^{re.escape(ident)}\s*[:=][^=]*?"([^"]+)"', text, re.M)
            if const:
                resolved.setdefault(const.group(1), set()).add(path.name)
            else:
                unresolved.add(ident)
        for loc in _via_bound_home(text):
            resolved.setdefault(loc, set()).add(path.name)
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
#: Each needs a `kind`/`domain`/`merge` decision that must not be guessed. Shrinking this set is
#: the point; adding to it should require the same argument.
#:
#: 🔴 #2217 drained this from twenty-one to five, and the five that remain are not leftovers —
#: they are the ones whose call site genuinely does NOT decide the question. For the other
#: sixteen the write shape named the `kind` and an already-declared store of the same shape
#: named the `merge`, so declaring them was a reading rather than a guess (fourteen declared,
#: `control_bridge.json` and `graph_maintenance.json` resolved into `IGNORED` as the twins of
#: `gateway.runtime.json` and `doctor`). Four of the five left are a genuine product question:
#: each store says either "a human stopped this" or "this one physical device", and whether a
#: RESTORE should re-plant that cannot be read off a file shape. The fifth is the one whose
#: shape-derived `merge` turned out to be measurably WRONG — see `digest_queue.jsonl` below.
_UNDECLARED_DEBT = frozenset(
    {
        # 🔴 The one #2217 tried to declare and BACKED OUT OF, on measured evidence — recorded
        # here because the reason is the useful part. `jsonl_append` + `append_dedup` is what its
        # shape says, and `test_every_declared_APPEND_DEDUP_entry_now_has_a_path` in
        # `test_snapshot.py` immediately demanded a restore-merge executor for it. Writing one
        # would have been wrong: `drain_digest_queue()` is read-then-TRUNCATE, and
        # `_trim_digest_queue` keeps only the newest 500 of 1000, so a snapshot holds notes the
        # live file has deliberately shed. Union-ing them back on restore re-queues
        # already-digested notifications and the user gets a second digest of things they have
        # read. That is precisely the "a wrong `merge` silently corrupts on convergence" hazard
        # this issue says not to guess at, so the queue stays pinned until someone decides
        # whether a restore should resume a drained queue at all.
        "digest_queue.jsonl",
        # BA-5's browse kill switch, the sibling of `incident.json` below. Whether a restore
        # should carry "browse is stopped" is a real kind/domain/merge decision: a human pulled
        # it, so re-enabling browsing on restore may be wrong, yet carrying the stop onto a
        # different machine may be too. Pinned as debt rather than guessed.
        "browse_kill.json",
        # AUTONOMY-GUARDRAILS §1.3's incident switch, the wider sibling of `browse_kill.json`:
        # it suspends ALL unattended work. Same undecided question, with more at stake — a
        # restore that re-plants `active: true` silently freezes every cron, hook and trigger on
        # the new machine, and a restore that drops it resumes unattended work a human had
        # deliberately stopped. Both are defensible; neither is readable off the file's shape,
        # which is why the fifteen declarations in #2217 did not touch it.
        "incident.json",
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
    }
)


def _declared_tops() -> set[str]:
    return {entry.path.split("/", 1)[0] for entry in inv.all_entries()}


def test_the_census_is_not_vacuous():
    """The floor: a scan that finds nothing would make every assertion below pass."""
    resolved, _ = _censused()
    assert len(resolved) >= 80, f"only {len(resolved)} home locations censused — regex broke"
    assert len(_declared_tops()) >= 90, "the inventory read back nearly empty"


def test_the_scan_reads_an_fstring_paths_static_prefix():
    """A floor on spelling 3. Without it `config_dir() / f"session_pid_{pid}.txt"` matched the
    IDENTIFIER alternative, resolved to the name `f`, and landed in the unresolved bucket — so a
    file written once per ACP agent was never checked, and `audit_home()` reported it as drift
    on the live Doctor probe (#2217). A prefix is enough to ask the accounting question."""
    hits = [m.group(1) for m in _USE.finditer('p = config_dir() / f"session_pid_{pid}.txt"')]
    assert hits == ["session_pid_"], f"the f-string spelling is not being read: {hits}"
    resolved, unresolved = _censused()
    assert "session_pid_" in resolved, "the real tree's f-string path stopped resolving"
    assert "f" not in unresolved, "an f-string is being counted as a runtime value again"


def test_the_scan_follows_a_home_bound_to_a_local_name():
    """A floor on spelling 4. Fifteen call sites do `home = config_dir()` first, and a scan that
    only matches `config_dir() /` sees NONE of the locations they introduce — it does not even
    count them in the bounded blind spot, so the miss is silent. Measured on the real tree
    (#2217): `.stop-` reaches the home only this way."""
    assert _via_bound_home('base = config_dir()\nq = base / "brand_new_store"') == {
        "brand_new_store"
    }
    assert _via_bound_home('base = config_dir()\nq = base / f".stop-{key}"') == {".stop-"}
    # A name NOT bound to the home must not be followed, or every `path / "x"` in the tree
    # becomes a phantom home location and the ratchet drowns.
    assert _via_bound_home('base = some_other_root()\nq = base / "not_a_home_path"') == set()
    resolved, _ = _censused()
    assert ".stop-" in resolved, "the real tree's bound-home path stopped resolving"


def test_the_prefix_globs_match_a_real_filename_not_only_the_censused_prefix(tmp_path):
    """The half of the two new rows that a static assertion cannot reach.

    `session_pid_*` and `.stop-*` have to satisfy TWO readers: this census, which resolves an
    f-string to its static prefix, and `audit_home()`, which sees the real file on disk. A glob
    written for the prefix alone (`session_pid_*.txt` does not match `session_pid_`) would pass
    every test above while leaving the Doctor probe coral for exactly the files these rows exist
    to settle. Driven against real names, both directions.

    `tmp_path`, and `audit_home` takes the home as an argument — nothing here can reach
    `config_dir()`, which CREATES the home it resolves.
    """
    home = tmp_path
    (home / "session_pid_4711.txt").write_text("chat-1", encoding="utf-8")
    (home / ".stop-chat_1").write_text("", encoding="utf-8")
    result = inv.audit_home(home)
    assert result.unclaimed == [], f"the prefix globs miss the real filenames: {result.unclaimed}"
    for prefix in ("session_pid_", ".stop-"):
        assert inv.is_accounted(prefix), f"{prefix} stopped matching the censused prefix"


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
    # Pinned EXACTLY, and it fell from 16 to 13 in #2217 rather than being left with slack: the
    # f-string spelling used to land here as the identifier `f`, and slack in this bound is
    # precisely how many new dynamic home paths can arrive without anyone noticing.
    assert len(unresolved) <= 13, (
        "more home paths are now built from runtime values than when this was measured, so the "
        f"census covers proportionally less: {sorted(unresolved)}"
    )
