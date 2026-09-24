"""The audit surface's outcome filters, checked against the vocabulary writers actually emit.

Settings → Audit log offers outcome filter pills. They used to be defined in the dashboard as
two literal substrings — ``denied`` and ``failed`` — against a log whose writers emit **62**
distinct outcome words. Measured across ``src/personalclaw``:

    denied 163 · rejected 24 · blocked 5 · refused 1        "Denied" matched 163 of 193
    failure 23 · error 21 · failed 4                        "Failed" matched 4 of 48

and confirmed on a live instance before the fix: a real ``DELETE /api/terminal/sessions/…``
recorded ``outcome=error`` was invisible to the Failed pill (``outcome=failed`` → 0 rows,
``outcome=error`` → 1 row). **On an audit surface a filter that silently omits matching records
is the worst available failure**: the operator reads the empty list as "nothing happened". The
handler already refuses an unknown filter KEY for exactly this reason; the VALUES had no such
guard.

So the families moved next to the log (``sel.AUDIT_OUTCOME_FAMILIES``) and the endpoint ships
them to the panel. These tests are the invariants that keep them honest, in both directions:

1. no family offers a term no writer emits (a pill that can only ever return zero),
2. no family CAPTURES a word another family claims (a pill that lies about a row), and
3. the matcher really is any-of within a field, and still AND across fields.

**Issue #535, second pass.** The pills were still a view over 0.6% of the log: measured on a live
1,040-entry instance, ``ok`` alone was 1,021 rows (98.2%) and matched neither pill, so an operator
could narrow to what went wrong and never to what happened. The success vocabulary existed here —
as the tuple this file's coverage rail used — but was never OFFERED. It is now the ``ok`` family,
which is also why matching became token-bounded: measured over the tree's real vocabulary, a
SUBSTRING ``ok`` captures ``hook_blocked`` and ``hook_error``, and ``approved`` captures
``not_auto_approved``. A "Succeeded" pill returning a blocked hook is the same silent lie as a
"Failed" pill that misses one, so :func:`test_no_family_captures_another_familys_word` is the rail
for it — derived from the tree, not hand-listed.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

from personalclaw.sel import (
    AUDIT_OUTCOME_FAMILIES,
    AUDIT_OUTCOME_SUCCESS,
    AUDIT_OUTCOME_TONES,
    _audit_matches,
    _outcome_token_match,
    audit_outcome_tone,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The scanner this file USED to be, kept as the falsification control for
#: :func:`test_the_scanner_sees_a_constant_valued_outcome`. It matches only an inline
#: ``outcome="LITERAL"``, which is why a subsystem that named its outcome words as module
#: constants was invisible to the ceiling below (#3443).
_INLINE_ONLY = re.compile(r'outcome="([a-z_]+)"')

#: An outcome word: lower snake_case. Used to reject a resolved value that is plainly not
#: one (a path, a sentence, an f-string fragment) rather than letting it into the census.
_OUTCOME_WORD = re.compile(r"^[a-z][a-z0-9_]*$")

#: A module-level constant whose NAME declares it is an outcome word. The convention already
#: exists in the tree (``browse_provider.OUTCOME_SKIP``, ``outbox.OUTCOME_DELIVERED``), and it
#: is what makes a word reachable through a runtime hop the scanner cannot follow — the
#: ``outcome=result.outcome`` in #3443 carries one of ``browse/vision.py``'s ``OUTCOME_*``.
_DECLARED_OUTCOME_CONST = re.compile(r"^(OUTCOME_.+|.+_OUTCOME)$")


def _family(key: str) -> dict:
    return next(f for f in AUDIT_OUTCOME_FAMILIES if f["key"] == key)


def _values(family: dict) -> tuple[str, ...]:
    return tuple(str(v) for v in family["values"])


def _claims(family: dict, word: str) -> bool:
    """Whether ``family`` matches ``word`` under the SERVER's matcher — not a local re-derivation.
    A test that re-implements the matcher proves only that the test agrees with itself."""
    return any(_outcome_token_match(v, word) for v in _values(family))


def _string_constants(body: list[ast.stmt]) -> dict[str, str]:
    """``NAME = "literal"`` (and the annotated form) in one statement list."""
    out: dict[str, str] = {}
    for node in body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for name in names:
                out[name] = value.value
    return out


class _Vocabulary:
    """The outcome words reachable from an ``outcome=`` keyword anywhere in the tree.

    🔑 WHY THIS IS AN AST PASS AND NOT A REGEX (#3443). The old scanner matched only an
    inline ``outcome="LITERAL"``, so the ceiling below was never a count of the outcome
    vocabulary — it was a count of *the portion written inline*. Two consequences, both
    measured:

    * Six of ``browse/vision.py``'s eight outcome words were invisible, because they are
      module constants handed over as ``outcome=result.outcome``.
    * A ternary was HALF read: ``outcome="executed" if executed else "declined"`` showed the
      scanner ``executed`` (classified ``ok``) and hid ``declined`` — so the audit surface
      rendered one branch of one decision as a success and the other as nothing at all.

    And the incentive ran backwards, which is the real defect: the cheapest way to satisfy
    the ceiling was to move a literal into a constant, i.e. to make the word MORE silent.
    Naming outcomes as constants is the better practice; the rail penalised it.

    🪤 IT DOES NOT CLAIM COMPLETENESS, AND THAT IS ASSERTED. Some sites resolve only at
    runtime (``outcome=result.outcome``, ``outcome=str(verdict)``); :attr:`unresolved` counts
    them and :func:`test_the_scan_reports_what_it_cannot_see` holds that residue down, so
    "the scanner saw nothing here" can never pass as "there is nothing here". The constant
    convention is what covers most of that gap: a word only has to be *declared* as an
    outcome somewhere to be in the census, even when the hop to the call site is dynamic.

    🪤 IT IS ALSO DELIBERATELY TREE-WIDE, not SEL-only. A few ``outcome=`` fields belong to
    internal ledgers and enums rather than the audit log, so the census is over-inclusive —
    which is the safe direction, because it demands a decision about a word that may not
    need one. Narrowing the scan to SEL writers would SHRINK the census, and shrinking the
    census is the same "go green by making a word invisible" move this rail exists to stop.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.sites: dict[str, list[str]] = {}
        self.unresolved: list[str] = []
        self._consts_by_module: dict[str, dict[str, str]] = {}
        self._enums: dict[str, dict[str, str]] = {}
        self._consts: dict[str, str] = {}
        trees: dict[Path, ast.Module] = {}
        for path in sorted(_SRC.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - the tree is lint-clean
                continue
            trees[path] = tree
            module = _string_constants(tree.body)
            self._consts_by_module[path.stem] = module
            self._consts.update(module)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    members = _string_constants(node.body)
                    if members:
                        self._enums.setdefault(node.name, {}).update(members)
        for path, tree in trees.items():
            where = path.relative_to(_SRC).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "outcome":
                        continue
                    for word in self._resolve(keyword.value, path.stem):
                        self._record(word, f"{where}:{keyword.value.lineno}")
        # Every DECLARED outcome constant, whatever reaches the call site. This is the half
        # that survives a runtime hop: `outcome=result.outcome` names no word, but the word
        # it will carry is written down as `OUTCOME_*` in the module that owns it.
        for stem, module in self._consts_by_module.items():
            for name, value in module.items():
                if _DECLARED_OUTCOME_CONST.match(name):
                    self._record(value, f"{stem}.py:{name}")

    def _record(self, word: str, site: str) -> None:
        if not _OUTCOME_WORD.match(word):
            return
        self.counts[word] = self.counts.get(word, 0) + 1
        self.sites.setdefault(word, []).append(site)

    def _resolve(self, node: ast.expr, stem: str) -> set[str]:
        """Every string an ``outcome=`` expression can statically evaluate to."""
        if isinstance(node, ast.Constant):
            return {node.value} if isinstance(node.value, str) else set()
        if isinstance(node, ast.Name):
            value = self._consts_by_module.get(stem, {}).get(node.id, self._consts.get(node.id))
            if value is not None:
                return {value}
        elif isinstance(node, ast.IfExp):
            # BOTH branches. Reading one was the second blind spot (#3443).
            return self._resolve(node.body, stem) | self._resolve(node.orelse, stem)
        elif isinstance(node, ast.BoolOp):
            return set().union(*(self._resolve(v, stem) for v in node.values))
        elif isinstance(node, ast.Attribute):
            if node.attr == "value":  # Enum.MEMBER.value
                return self._resolve(node.value, stem)
            if isinstance(node.value, ast.Name):
                owner = node.value.id
                enum = self._enums.get(owner, {})
                if node.attr in enum:
                    return {enum[node.attr]}
                module = self._consts_by_module.get(owner, {})
                if node.attr in module:
                    return {module[node.attr]}
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "str" and node.args:
                return self._resolve(node.args[0], stem)
            # `d.get("outcome", DEFAULT)` / `d.pop(..., DEFAULT)` — the default is a word.
            if (
                isinstance(func, ast.Attribute)
                and func.attr in ("get", "pop")
                and len(node.args) == 2
            ):
                return self._resolve(node.args[1], stem)
        self.unresolved.append(ast.unparse(node))
        return set()


@lru_cache(maxsize=1)
def _vocabulary() -> _Vocabulary:
    """One AST pass over ``src/personalclaw`` for the whole module (it is walked ~10 times)."""
    return _Vocabulary()


def _emitted_outcomes() -> dict[str, int]:
    """Every outcome word reachable from an ``outcome=`` keyword, with how many sites use it."""
    return _vocabulary().counts


#: THE LEDGER THAT REPLACED THE CEILING (#3443). Every emitted word that belongs in no
#: filter family, and WHY — keyed by the reason, because the reasons are shared and 62
#: near-identical sentences would be filler rather than a decision.
#:
#: 🔴 IT IS A NAMED SET, NOT A NUMBER, AND THAT IS THE WHOLE POINT. The old rail asserted
#: ``len(unclassified) <= 34``, which had two escapes a number always has: a new word could
#: be absorbed by editing one digit, and — because the scanner only saw inline literals —
#: could be made to disappear entirely by moving it into a constant. There is no digit to
#: edit here. A new word fails BY NAME, and the only ways to make it pass are to put it in a
#: family or to write down why it belongs in none. Both are decisions; neither is a bump.
#:
#: Asserted in both directions: a word here must NOT be claimed by a family (the two records
#: cannot disagree), and it must still be emitted (the ledger cannot rot into a list of words
#: nobody writes any more — the same silent-zero defect as an unoffered value, from the other
#: side).
_NO_FAMILY: dict[str, tuple[str, ...]] = {
    "a LIFECYCLE step rather than a verdict: the record says what happened next, not whether "
    "anything was allowed, refused or broke — so every pill would be wrong about it": (
        "accepted",
        "continue",
        "created",
        "delivered",
        "flush_produced",
        "grounded",
        "installed",
        "invoked",
        "launched",
        "outcome_resolved",
        "passed",
        "pending_outcome",
        "queued",
        "ran",
        "reused",
        "scanned",
        "spawned",
        "started",
        "suggested",
        "surfaced",
        "tie",
    ),
    "the work did not need doing, so nothing ran: a no-op is not a success, not a refusal of "
    "a caller, and not a fault": (
        "expired",
        "flush_skipped",
        "no_change",
        "noop",
        "not_triggered",
        "off_duty",
        "quiet",
        "skip",
        "skipped",
    ),
    "a PRECONDITION was absent, so nothing was asked for and denied and nothing broke — "
    "putting it in a family would report a refusal or a fault that never happened": (
        "no_model",
        "no_screenshot",
        "no_signal",
        "no_target",
        "verifier_absent",
    ),
    "the MODE a control resolved to, or the SHAPE of a result, rather than its verdict — a "
    "family here would make a pill accuse a working control every time it fires": (
        "bounded",
        "default",
        "downgraded",
        "halted_on_budget",
        "hard",
        "interrupted",
        "narrowed",
        "open",
        "partial",
        "permanent",
        "preview",
        "session_scope_only",
        "transient",
        "trusted",
    ),
    "ARGUABLE, and deliberately left narrow: classifying it is a judgement per word rather "
    "than a sweep, and getting it wrong on a security surface is worse than leaving a pill "
    "narrow — a Denied pill that returns a reaped subagent is a lie about a refusal": (
        "bypass",
        "fail_open",
        "fanout_breaker_tripped",
        "fanout_budget_exceeded",
        "killed",
        "reaped",
        "sigkill",
        "tampered",
        "too_large",
        "ungated",
        "ungated_declared",
    ),
    "a NEGATION, which must never read as its own affirmative: under the old substring match "
    "`approved` claimed it, so the Succeeded pill would have returned a refused auto-approval": (
        "not_auto_approved",
    ),
    "not an audit record at all — an internal ledger row whose field is also called "
    "`outcome`, which the deliberately tree-wide scan also sees. Recorded rather than "
    "excluded, because narrowing the scan is the same make-a-word-invisible move #3443 is "
    "about": ("scope_violation",),
}


def _no_family_words() -> dict[str, str]:
    """word → the reason it is in no family. Flattened once, so a word listed twice is a
    collected error rather than a silent overwrite."""
    out: dict[str, str] = {}
    duplicated = []
    for reason, words in _NO_FAMILY.items():
        for word in words:
            if word in out:
                duplicated.append(word)
            out[word] = reason
    assert not duplicated, f"listed under two reasons — one word, one decision: {duplicated}"
    return out


def test_the_vocabulary_scan_resolves() -> None:
    """Vacuity floor. Every assertion below is 'for each emitted outcome …', so a scan that
    finds nothing passes everything — the failure mode this whole file exists to prevent."""
    emitted = _emitted_outcomes()
    assert len(emitted) >= 95, f"only {len(emitted)} outcome words found; the scan broke"
    # The four that motivated the fix must be in it, or the scanner has drifted.
    for value in ("denied", "rejected", "failure", "error"):
        assert emitted.get(value, 0) > 0, value


def test_the_scanner_sees_a_constant_valued_outcome() -> None:
    """THE control for #3443, and it falsifies in the right direction.

    The old scanner is kept as ``_INLINE_ONLY`` and asserted BLIND to each of these, so the
    widening is demonstrated rather than claimed. Remove the AST pass and this test reds; keep
    the AST pass and remove the old regex and there is nothing left to compare against.

    * ``grounded`` / ``no_model`` / ``refused_challenge`` — ``browse/vision.py`` module
      constants, handed to the audit writer as ``outcome=result.outcome``. Six of that file's
      eight words were invisible.
    * ``declined`` / ``needs_human`` — the losing branch of a ternary whose WINNING branch the
      old regex did see (``outcome="executed" if executed else "declined"``,
      ``outcome="blocked" if … else "needs_human"``). One decision, one word classified and
      its sibling not even counted.
    """
    inline = {
        word
        for path in _SRC.rglob("*.py")
        for word in _INLINE_ONLY.findall(path.read_text(encoding="utf-8"))
    }
    emitted = _emitted_outcomes()
    for word in ("grounded", "no_model", "refused_challenge", "declined", "needs_human"):
        assert word in emitted, f"the widened scan must see {word!r}"
        assert word not in inline, f"{word!r} is inline after all — pick a real control"
    # And the widening is strictly a widening: the old scanner found nothing this one misses.
    assert not (
        inline - set(emitted)
    ), f"the AST pass LOST words the regex saw: {inline - set(emitted)}"


def test_the_scan_reports_what_it_cannot_see() -> None:
    """A scanner that silently resolves nothing is the defect one level up.

    Some ``outcome=`` values exist only at runtime (``outcome=result.outcome``,
    ``outcome=str(verdict)``), and the honest thing is to COUNT them rather than let "the scan
    found nothing here" pass as "there is nothing here". The residue is held down so a change
    that starts hiding words behind a dynamic hop reds instead of quietly shrinking the census.
    """
    unresolved = _vocabulary().unresolved
    assert len(set(unresolved)) <= 20, (
        "more outcome sites became statically unreadable — the census is shrinking, which is "
        f"the direction this rail exists to refuse:\n{sorted(set(unresolved))}"
    )
    # Non-vacuous: the residue is real, and `result.outcome` — the shape #3443 named — is in it.
    assert unresolved, "the residue counter found nothing; the walk is not reaching call sites"
    assert "result.outcome" in set(unresolved)


@pytest.mark.parametrize("family", AUDIT_OUTCOME_FAMILIES, ids=lambda f: str(f["key"]))
def test_no_family_offers_a_term_nobody_writes(family: dict) -> None:
    """A filter value that matches no emitted outcome can only ever return zero rows — the
    same silent-zero defect as a missing value, arriving from the other direction. (This test
    caught two invented values, ``not_permitted`` and ``timeout``, before they shipped.)"""
    emitted = _emitted_outcomes()
    for value in family["values"]:
        matching = [word for word in emitted if _outcome_token_match(str(value), word)]
        assert matching, f"{family['key']}: no writer emits anything matching {value!r}"


def test_the_families_cover_what_motivated_them() -> None:
    """The specific words the old two-substring pills missed."""
    denied, failed = _family("denied"), _family("failed")

    for word in ("denied", "rejected", "blocked", "refused"):
        assert _claims(denied, word), word
    for word in ("failure", "failed", "error"):
        assert _claims(failed, word), word
    # And the prefixed variants come along, which is why matching is on token boundaries and
    # not on the whole word.
    for word in ("denied_running", "denied_mismatch", "rejected_spawn", "refused_incident"):
        assert _claims(denied, word), word
    assert _claims(failed, "hook_error")


def test_the_success_family_can_select_the_bulk_of_the_log() -> None:
    """Issue #535's residue: the pills covered 0.6%.

    ``ok`` is 98.2% of a live log's rows and matched NEITHER of the two failure pills, so the
    filter row could narrow to what went wrong and never to what happened. The words were already
    classified — they were the tuple this file's coverage rail used — and simply were not offered.
    """
    ok = _family("ok")
    assert (
        AUDIT_OUTCOME_SUCCESS
    ), "the success vocabulary is derived from this family, not beside it"
    assert _values(ok) == AUDIT_OUTCOME_SUCCESS, "one list, or the two will drift"
    for word in ("ok", "success", "completed", "allowed", "approved", "granted", "auto_approved"):
        assert _claims(ok, word), word


@pytest.mark.parametrize("family", AUDIT_OUTCOME_FAMILIES, ids=lambda f: str(f["key"]))
def test_no_family_captures_another_familys_word(family: dict) -> None:
    """THE rail for the collision the success family would have introduced.

    Derived from the tree's real vocabulary rather than a hand-checked list, because the whole
    defect class here is a hand-maintained view over a vocabulary that moved. Measured: a
    SUBSTRING match puts ``hook_blocked`` (denied) and ``hook_error`` (failed) into the ``ok``
    family as well, so a "Succeeded" pill would have returned a blocked hook and a hook error —
    an audit surface asserting success for a record another pill calls a refusal.
    """
    emitted = _emitted_outcomes()
    others = [f for f in AUDIT_OUTCOME_FAMILIES if f["key"] != family["key"]]
    for word in emitted:
        if not _claims(family, word):
            continue
        clashing = [str(other["key"]) for other in others if _claims(other, word)]
        assert (
            not clashing
        ), f"{word!r} is claimed by {family['key']} AND {clashing} — one row, two verdicts"


def test_a_negation_is_not_captured_as_its_own_affirmative() -> None:
    """``not_auto_approved`` must not read as a success, and ``not_found`` must still read as a
    failure. The guard is a prefix rule, so it has to be checked in both directions or it just
    becomes a way to lose the ``not_*`` words that ARE classified."""
    assert audit_outcome_tone("not_auto_approved") == "neutral"
    assert not _claims(_family("ok"), "not_auto_approved")
    assert audit_outcome_tone("not_found") == "danger"
    assert _claims(_family("failed"), "not_found")


def test_every_family_declares_a_renderable_tone() -> None:
    """A tone the frontend does not map falls back to neutral, silently — the exact
    hand-maintained-list failure the tone moved here to end. ``sel`` checks this at import; this
    pins it as a rail so the check cannot be dropped."""
    for family in AUDIT_OUTCOME_FAMILIES:
        assert str(family["tone"]) in AUDIT_OUTCOME_TONES, family["key"]
    assert "neutral" in AUDIT_OUTCOME_TONES, "an unclassified word needs somewhere honest to land"


def test_the_tone_of_a_word_is_the_tone_of_the_family_that_claims_it() -> None:
    """The row colour and the pill are one decision. Measured over the whole emitted vocabulary,
    because the drift that motivated this was a single missing word (``not_found``)."""
    for word in _emitted_outcomes():
        owners = [f for f in AUDIT_OUTCOME_FAMILIES if _claims(f, word)]
        expected = str(owners[0]["tone"]) if owners else "neutral"
        assert audit_outcome_tone(word) == expected, word
    assert audit_outcome_tone("") == "neutral", "a blank outcome is not a verdict"


def test_the_ledger_and_the_families_do_not_disagree() -> None:
    """A word cannot be both filtered and deliberately unfiltered. Without this the ledger
    would drift into a second, contradictory classification of the same vocabulary — which is
    the hand-maintained-view defect this whole file exists to end, reintroduced by the fix."""
    both = sorted(
        word for word in _no_family_words() if any(_claims(f, word) for f in AUDIT_OUTCOME_FAMILIES)
    )
    assert not both, (
        "recorded as belonging in no family AND claimed by one — remove the _NO_FAMILY entry, "
        f"the family now owns it: {both}"
    )


def test_the_ledger_cannot_rot() -> None:
    """Every recorded word must still be emitted.

    The mirror of :func:`test_no_family_offers_a_term_nobody_writes`, and it matters for the
    same reason: a ledger entry for a word no writer emits is a decision about nothing, and a
    ledger padded with dead words would let the next real word in unnoticed. It is also the
    thing that stops "delete the emitter, keep the entry" from being a way to go quiet.
    """
    emitted = _emitted_outcomes()
    dead = sorted(word for word in _no_family_words() if word not in emitted)
    assert not dead, f"recorded in _NO_FAMILY but no longer emitted anywhere — drop them: {dead}"


def test_the_families_are_disjoint() -> None:
    """A value in two families makes the pills lie about each other."""
    seen: dict[str, str] = {}
    for family in AUDIT_OUTCOME_FAMILIES:
        for value in family["values"]:
            assert value not in seen, f"{value!r} is in both {seen.get(value)} and {family['key']}"
            seen[value] = str(family["key"])


def test_every_emitted_word_is_accounted_for() -> None:
    """A NAMED LEDGER, not a ceiling — the replacement #3443 asked for.

    Most words are informational (``launched``, ``queued``, ``noop``, ``narrowed``) and belong
    in no filter; a handful are arguable (``tampered``, ``too_large``, ``sigkill``,
    ``fanout_breaker_tripped``) and classifying them is a judgement per word, not a sweep,
    because getting it wrong on a security surface is worse than leaving a pill narrow. All of
    that is unchanged. What changed is the INSTRUMENT.

    🔴 THE OLD FORM WAS ``len(unclassified) <= 34`` AND IT COULD BE SATISFIED TWO DISHONEST
    WAYS. A new word could be absorbed by editing one digit — a re-baselined ratchet reads
    green while guarding nothing — and, because the scanner saw only inline literals, a word
    could be made to vanish entirely by moving it into a module constant. That second escape
    is the perverse incentive: naming outcomes as constants is the BETTER practice (one
    spelling, refactor-safe, greppable by symbol) and it was the cheapest way to defeat the
    rail. There is no digit here. A new word fails by name with its emitting site, and the only
    two ways to make it pass — a family, or a written reason — are both decisions.
    """
    emitted = _emitted_outcomes()
    sites = _vocabulary().sites
    recorded = _no_family_words()
    unclassified = sorted(
        word for word in emitted if not any(_claims(f, word) for f in AUDIT_OUTCOME_FAMILIES)
    )
    unaccounted = [w for w in unclassified if w not in recorded]
    assert not unaccounted, (
        "a new outcome word appeared. Classify it into an AUDIT_OUTCOME_FAMILIES family, or "
        "record in _NO_FAMILY why it belongs in none — there is no number to raise:\n"
        + "\n".join(f"  {w!r} at {sites[w][0]}" for w in unaccounted)
    )


def test_a_family_is_one_any_of_query() -> None:
    """The comma form is what lets a family stay a single SERVER-side query, so the pill and
    the pagination cursor cannot disagree."""
    failed = ",".join(_values(_family("failed")))
    assert _audit_matches({"outcome": "error"}, {"outcome": failed}, "", "")
    assert _audit_matches({"outcome": "failure"}, {"outcome": failed}, "", "")
    assert _audit_matches({"outcome": "hook_error"}, {"outcome": failed}, "", "")
    assert not _audit_matches({"outcome": "success"}, {"outcome": failed}, "", "")
    # The single-value form must keep behaving exactly as before.
    assert _audit_matches({"outcome": "denied_running"}, {"outcome": "denied"}, "", "")
    assert not _audit_matches({"outcome": "error"}, {"outcome": "failed"}, "", "")

    ok = ",".join(_values(_family("ok")))
    assert _audit_matches({"outcome": "ok"}, {"outcome": ok}, "", "")
    assert _audit_matches({"outcome": "auto_approved_spawn"}, {"outcome": ok}, "", "")
    # The collision the success family would have shipped under a substring match.
    assert not _audit_matches({"outcome": "hook_error"}, {"outcome": ok}, "", "")
    assert not _audit_matches({"outcome": "hook_blocked"}, {"outcome": ok}, "", "")
    assert not _audit_matches({"outcome": "not_auto_approved"}, {"outcome": ok}, "", "")


def test_outcome_is_token_matched_but_free_text_fields_stay_substring() -> None:
    """The two fields are matched differently ON PURPOSE, and getting either backwards is a real
    defect: a substring over the closed outcome vocabulary mis-classifies (``ok`` in
    ``hook_error``), while token matching over free text would break the operation filter — you
    type ``terminal`` and expect ``DELETE /api/terminal/sessions/abc``."""
    row = {"outcome": "denied_running", "operation": "DELETE /api/terminal/sessions/abc"}
    assert _audit_matches(row, {"operation": "terminal"}, "", "")
    assert _audit_matches(row, {"operation": "erminal/sess"}, "", "")
    # ...and the outcome half does NOT do that: a partial word is not a member of the vocabulary.
    assert not _audit_matches(row, {"outcome": "enied"}, "", "")
    assert _audit_matches(row, {"outcome": "denied"}, "", "")


def test_or_is_within_a_field_and_and_is_across_fields() -> None:
    """The regression this change could have introduced: turning the field AND into an OR would
    widen every audit query silently."""
    row = {"outcome": "error", "operation": "DELETE /api/terminal/sessions/abc"}
    assert _audit_matches(row, {"outcome": "failure,error", "operation": "DELETE"}, "", "")
    assert not _audit_matches(row, {"outcome": "failure,error", "operation": "POST"}, "", "")
    assert not _audit_matches(row, {"outcome": "denied,rejected", "operation": "DELETE"}, "", "")
    # An empty or comma-only needle must not become "match everything but claim a filter".
    assert _audit_matches(row, {"outcome": ""}, "", "")
    assert _audit_matches(row, {"outcome": " , "}, "", "")
