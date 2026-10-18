"""The source-tree checks split from
``tests/test_audit_outcome_families.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest
import source_index
from audit_outcome_families_shared import _claims, _no_family_words
from rails_repo import REPO

from personalclaw.sel import AUDIT_OUTCOME_FAMILIES, _outcome_token_match, audit_outcome_tone

_SRC = REPO / "src" / "personalclaw"


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

    A call to a function that CHOOSES the word is read through: ``outcome=refusal_outcome(stale)``
    resolves to every word that function returns, when each of its returns is itself readable
    (:meth:`_returned_words`). The alternative was the same ternary copied into every route that
    audits a stale-write refusal, which is exactly the "make the choice local and duplicated"
    shape this rail should not be the reason for.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.sites: dict[str, list[str]] = {}
        self.unresolved: list[str] = []
        self._consts_by_module: dict[str, dict[str, str]] = {}
        self._enums: dict[str, dict[str, str]] = {}
        self._consts: dict[str, str] = {}
        #: Module-level functions by name, with the stem of the module that defines each.
        self._functions: dict[str, list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]] = {}
        self._reading: set[str] = set()
        trees: dict[Path, ast.Module] = {}
        for path, tree in source_index.package_trees():
            trees[path] = tree
            module = _string_constants(tree.body)
            self._consts_by_module[path.stem] = module
            self._consts.update(module)
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    members = _string_constants(node.body)
                    if members:
                        self._enums.setdefault(node.name, {}).update(members)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._functions.setdefault(node.name, []).append((path.stem, node))
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
            if isinstance(func, ast.Name):
                words = self._returned_words(func.id, stem)
                if words:
                    return words
        self.unresolved.append(ast.unparse(node))
        return set()

    def _returned_words(self, name: str, stem: str) -> set[str]:
        """Every word the module-level function *name* can return, or ``set()`` when that is not
        statically known: the name is ambiguous (defined in two modules, neither of them *stem*),
        a return is bare or unreadable, or the function returns through itself.

        All-or-nothing on purpose. A function with one readable return and one dynamic one is
        still a runtime hop, and reading its readable half would be the half-read ternary again,
        so it stays in :attr:`unresolved`. Reading a return records no residue of its own: the
        call site is the one unresolved expression when the function cannot be read."""
        defs = self._functions.get(name, [])
        own = [d for d in defs if d[0] == stem]
        chosen = own or defs
        if len(chosen) != 1 or name in self._reading:
            return set()
        home, fn = chosen[0]
        returns = [n for n in _own_nodes(fn) if isinstance(n, ast.Return)]
        if not returns:
            return set()
        mark = len(self.unresolved)
        self._reading.add(name)
        words: set[str] = set()
        try:
            for ret in returns:
                got = self._resolve(ret.value, home) if ret.value is not None else set()
                if not got:
                    return set()
                words |= got
            return words
        finally:
            del self.unresolved[mark:]
            self._reading.discard(name)


def _own_nodes(fn: ast.AST):
    """The nodes of *fn*'s own body — not of a function, lambda or class nested inside it, whose
    returns are not *fn*'s."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(node))


@lru_cache(maxsize=1)
def _vocabulary() -> _Vocabulary:
    """One AST pass over ``src/personalclaw`` for the whole module (it is walked ~10 times)."""
    return _Vocabulary()


def _emitted_outcomes() -> dict[str, int]:
    """Every outcome word reachable from an ``outcome=`` keyword, with how many sites use it."""
    return _vocabulary().counts


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
        for path in source_index.package_files()
        for word in _INLINE_ONLY.findall(source_index.text(path))
    }
    emitted = _emitted_outcomes()
    for word in ("grounded", "no_model", "refused_challenge", "declined", "needs_human"):
        assert word in emitted, f"the widened scan must see {word!r}"
        assert word not in inline, f"{word!r} is inline after all — pick a real control"
    # And the widening is strictly a widening: the old scanner found nothing this one misses.
    assert not (
        inline - set(emitted)
    ), f"the AST pass LOST words the regex saw: {inline - set(emitted)}"


def test_the_scanner_reads_a_function_that_chooses_the_word() -> None:
    """The control for reading through a word-choosing function, in both directions.

    ``refusal_outcome`` (``stale_write.py``) returns one of two declared words, so the routes
    that audit a stale-write refusal with ``outcome=refusal_outcome(stale)`` are readable call
    sites: both words are counted AT them, not only at their declaration, and the call is not
    residue. A function with a return that cannot be read stays residue — ``run_outcome``
    returns ``None`` on one path and a table lookup on the other."""
    vocabulary = _vocabulary()
    assert "refusal_outcome(stale)" not in set(vocabulary.unresolved)
    for word in ("denied_stale_write", "denied_revision_required"):
        at_call_sites = [
            s for s in vocabulary.sites.get(word, []) if not s.startswith("stale_write.py:")
        ]
        assert at_call_sites, f"{word!r} is counted only where it is declared"
    assert "run_outcome(run)" in set(vocabulary.unresolved), "an unreadable function was read"


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


@pytest.mark.parametrize("family", AUDIT_OUTCOME_FAMILIES, ids=lambda f: str(f["key"]))
def test_no_family_captures_another_familys_word(family: dict) -> None:
    """THE rail for the collision the success family would have introduced.

    Derived from the tree's real vocabulary rather than a hand-checked list, because the whole
    defect class here is a hand-maintained view over a vocabulary that moved. A
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


def test_the_tone_of_a_word_is_the_tone_of_the_family_that_claims_it() -> None:
    """The row colour and the pill are one decision. Measured over the whole emitted vocabulary,
    because the drift that motivated this was a single missing word (``not_found``)."""
    for word in _emitted_outcomes():
        owners = [f for f in AUDIT_OUTCOME_FAMILIES if _claims(f, word)]
        expected = str(owners[0]["tone"]) if owners else "neutral"
        assert audit_outcome_tone(word) == expected, word
    assert audit_outcome_tone("") == "neutral", "a blank outcome is not a verdict"


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
