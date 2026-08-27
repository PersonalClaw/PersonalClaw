"""`DCU-2` — the operator's target-app allowlist, stored in the keystone enable document.

The allowlist answers "the agent may drive WHAT", where `DCU-1`'s keystone answers "may the
agent drive anything at all". It lives in the same out-of-band document for the same reason:
it is what stands between "computer use is on" and "the agent may drive your password
manager", so a PATCH-editable home for it would hand an agent with config-write access a
route to widen its own reach.

What this file pins, and why each one needs pinning:

1. **Fail closed on emptiness.** Absent or `[]` → nothing may be driven. Asserted as a
   *meaning* (membership refuses every name) rather than as `== ()`, because the tuple being
   empty is not the property that matters — the property is that a consumer asking "is this
   app allowed?" gets no for everything.
2. **Refused, not normalised.** A malformed entry must red the document rather than become a
   broader value. Every case asserts the allowlist is *also* empty, since the failure worth
   catching is the one where `" Mail "` quietly becomes `Mail`.
3. **Exact comparison.** Includes NEARBY names that must NOT be members. A rail that only
   proves `"TextEdit" in allowed_apps()` cannot detect a match rule that is too generous,
   which is the only direction that costs anything here.
4. **`DCU-1`'s strict parse survives the extension** — an unknown key is still refused, and a
   wrong `SCHEMA_VERSION` is still refused rather than best-effort parsed.
5. **One reader.** An AST rail proves `EnableState.apps` is touched only inside
   `allowed_apps()`. `require_enabled`'s docstring records why: while `enabled` briefly had
   two readers, forcing one to return True left every refusal test green.
6. **`ENABLE_DOCUMENT` is a document that works.** It is quoted verbatim in the refusal's FIX
   line, so if it armed a capability that could drive nothing, an operator would follow the
   instructions exactly and hit a second refusal whose own FIX named nothing further to do.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from personalclaw.computer_use import enable_state as ES


@pytest.fixture()
def keystone(tmp_path, monkeypatch):
    """An operator-owned keystone at a tmp path, with the process cache dropped either side.

    Both halves are load-bearing. The env override keeps every write in this file off the
    real governance directory (the fixture never touches `~/.personalclaw`), and the resets
    keep one test's resolved state out of the next one's — `_ACTIVE` is module-global by
    design, so under xdist a leaked cache is a pass that measured the previous test.
    """
    path = tmp_path / "governance" / ES.ENABLE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setenv(ES.ENABLE_PATH_ENV, str(path))
    ES.reset_enable_state()
    yield path
    ES.reset_enable_state()


def _write(path: Path, document: str) -> None:
    """Write the document and drop the cache — an operator editing the file, then restarting."""
    path.write_text(document, encoding="utf-8")
    ES.reset_enable_state()


#: Names an operator did NOT write when they wrote "TextEdit". Each is one normalisation a
#: generous implementation might apply: case-folding, superstring, substring, bundle-id
#: equivalence, whitespace tolerance, path/extension tolerance.
_NEARBY_TO_TEXTEDIT = (
    "textedit",
    "TEXTEDIT",
    "TextEditPro",
    "Text",
    "Edit",
    "com.apple.TextEdit",
    " TextEdit",
    "TextEdit ",
    "TextEdit.app",
    "/Applications/TextEdit.app",
)


# ── 1. the allowlist parses, and the accessor is deterministic ────────────────


def test_an_allowlist_parses_and_reads_back_in_a_deterministic_order(keystone):
    """The positive case. Sorted rather than document-ordered so two documents naming the
    same targets resolve identically — an allowlist is a set, and a caller that happened to
    depend on typing order would be depending on nothing."""
    _write(keystone, '{"version": 1, "enabled": true, "apps": ["Safari", "Mail", "Notes"]}')
    state = ES.active_enable_state()
    assert state.enabled is True
    assert ES.allowed_apps() == ("Mail", "Notes", "Safari")
    # Same set, different typing order → same tuple. Without this the order assertion above
    # could be satisfied by "whatever the operator typed", which is not deterministic.
    _write(keystone, '{"version": 1, "enabled": true, "apps": ["Notes", "Safari", "Mail"]}')
    assert ES.allowed_apps() == ("Mail", "Notes", "Safari")


def test_the_allowlist_is_read_through_the_accessor_not_the_field(keystone):
    """`allowed_apps()` is the contract the policy layer codes against, so it must agree with
    the resolved state rather than being a convenience alias that can drift from it."""
    _write(keystone, '{"version": 1, "enabled": true, "apps": ["Mail"]}')
    assert ES.allowed_apps() == ES.active_enable_state().apps == ("Mail",)


# ── 2. fail closed: empty means NOTHING, never everything ────────────────────


@pytest.mark.parametrize(
    "document",
    [
        '{"version": 1, "enabled": true}',
        '{"version": 1, "enabled": true, "apps": []}',
    ],
    ids=["absent-apps", "explicit-empty-apps"],
)
def test_an_empty_allowlist_permits_nothing_while_the_keystone_stays_armed(keystone, document):
    """Absent and `[]` behave IDENTICALLY, and both mean no app may be driven.

    They are identical by construction (`data.get("apps", [])`) rather than by two branches,
    because there is no third meaning available to either one. Neither can mean "all apps"
    without inverting the narrower of the operator's two grants into the widest possible one,
    so the only way to make them differ would be to make one of them fail open.

    An empty allowlist is NOT a parse refusal either: `enabled` stays True. "Armed, targets
    not chosen yet" is a coherent thing for a human to have written, and reporting it as a
    malformed document would collapse it with "your JSON has a typo" — the distinction
    `EnableState.detail` exists to preserve.
    """
    _write(keystone, document)
    assert ES.is_enabled() is True, "the capability is armed; only its target list is empty"

    allowed = ES.allowed_apps()
    assert allowed == ()
    assert len(allowed) == 0
    assert bool(allowed) is False
    # The meaning, asserted rather than implied: a consumer's `if name in allowed_apps()`
    # says no to every name there is. An empty tuple that some caller read as "unset, so
    # allow all" is exactly the fail-open this test exists to make impossible.
    for name in ("TextEdit", "Mail", "Safari", "1Password", "Terminal", "*", ""):
        assert name not in allowed, f"an empty allowlist admitted {name!r}"


def test_the_empty_allowlist_is_visible_in_the_state_detail(keystone):
    """An operator who armed the keystone but named no targets must be able to find out why
    nothing works. "On, but pointed at nothing" is the confusing state here, so the detail
    that lands in the boot log has to say it in words."""
    _write(keystone, '{"version": 1, "enabled": true, "apps": []}')
    detail = ES.active_enable_state().detail
    assert "EMPTY app allowlist" in detail
    assert "no app" in detail


# ── 3. refused, never normalised ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("document", "needle"),
    [
        ('{"version": 1, "enabled": true, "apps": "Mail"}', "not a list of app names"),
        ('{"version": 1, "enabled": true, "apps": {"Mail": true}}', "not a list of app names"),
        ('{"version": 1, "enabled": true, "apps": true}', "not a list of app names"),
        ('{"version": 1, "enabled": true, "apps": 3}', "not a list of app names"),
        ('{"version": 1, "enabled": true, "apps": null}', "not a list of app names"),
        ('{"version": 1, "enabled": true, "apps": ["Mail", 7]}', "which is not a string"),
        ('{"version": 1, "enabled": true, "apps": ["Mail", null]}', "which is not a string"),
        ('{"version": 1, "enabled": true, "apps": [["Mail"]]}', "which is not a string"),
        ('{"version": 1, "enabled": true, "apps": ["Mail", true]}', "which is not a string"),
        ('{"version": 1, "enabled": true, "apps": [""]}', "an empty name"),
        ('{"version": 1, "enabled": true, "apps": ["Mail", ""]}', "an empty name"),
        ('{"version": 1, "enabled": true, "apps": ["   "]}', "an empty name"),
        ('{"version": 1, "enabled": true, "apps": [" Mail "]}', "padded with whitespace"),
        ('{"version": 1, "enabled": true, "apps": ["Mail\\n"]}', "padded with whitespace"),
        ('{"version": 1, "enabled": true, "apps": ["\\tMail"]}', "padded with whitespace"),
        ('{"version": 1, "enabled": true, "apps": ["Mail", "Mail"]}', "twice"),
        ('{"version": 1, "enabled": true, "apps": ["Mail", "Safari", "Mail"]}', "twice"),
    ],
    ids=[
        "apps-is-a-string",
        "apps-is-an-object",
        "apps-is-a-bool",
        "apps-is-an-int",
        "apps-is-null",
        "entry-is-an-int",
        "entry-is-null",
        "entry-is-a-list",
        "entry-is-a-bool",
        "entry-is-empty",
        "one-entry-is-empty",
        "entry-is-whitespace",
        "entry-is-padded",
        "entry-has-a-trailing-newline",
        "entry-has-a-leading-tab",
        "duplicate-entry",
        "duplicate-among-others",
    ],
)
def test_a_malformed_allowlist_is_refused_rather_than_normalised(keystone, document, needle):
    """Every malformed shape resolves to OFF with an allowlist of nothing.

    The `enabled is False` assertion is the one that catches normalisation: an implementation
    that stripped `" Mail "`, coerced `7` to `"7"`, or deduplicated silently would arm the
    keystone here with a list the operator never wrote. Refusing the whole document instead
    is the same choice the `enabled` check makes when it rejects the string `"true"` — the
    operator gets told which entry is wrong and fixes one character, which is cheaper for
    them than a grant they cannot predict.

    The `allowed_apps() == ()` assertion is the second half: a refusal that nevertheless left
    a populated allowlist behind would be a refusal in name only.
    """
    _write(keystone, document)
    state = ES.active_enable_state()
    assert state.enabled is False, f"{document!r} armed the keystone"
    assert not ES.is_enabled()
    assert needle in state.detail, f"detail was {state.detail!r}"
    assert ES.allowed_apps() == (), "a refused document must leave no allowlist behind"


def test_a_padded_entry_is_not_silently_trimmed_into_a_working_one(keystone):
    """Called out separately from the table because trimming is the *plausible* mistake here —
    it looks like kindness. It is a widening: with exact comparison, `" Mail "` matches
    nothing and is inert; trimmed, it starts matching a real application. Refusing tells the
    operator their file does not mean what it looks like it means."""
    _write(keystone, '{"version": 1, "enabled": true, "apps": [" Mail "]}')
    assert ES.is_enabled() is False
    assert "Mail" not in ES.allowed_apps()
    assert " Mail " not in ES.allowed_apps()


# ── 4. exact comparison, including the names that must NOT match ─────────────


def test_the_allowlist_is_exactly_what_was_written_and_nothing_near_it(keystone):
    """The comparison rule: EXACT string equality, no coercion of any kind.

    Every normalisation available widens the allowlist past the bytes a human wrote —
    case-folding reaches a differently-cased app they never named (and app names are
    case-sensitive on the platforms this drives), substring or superstring matching reaches
    `TextEditPro`, and display-name<->bundle-id equivalence reaches `com.apple.TextEdit`. The
    operator writes the identifier their driver reports; this module refuses to guess which
    namespace they meant, because guessing in the permissive direction is the only mistake
    that costs anything.

    The membership operator itself belongs to the consumer (`policy.check_app`). What is
    provable here — and what a too-generous *set* would break — is that this module hands
    that consumer exactly the names on the document and not one more.
    """
    _write(keystone, '{"version": 1, "enabled": true, "apps": ["TextEdit"]}')
    allowed = ES.allowed_apps()
    assert allowed == ("TextEdit",)
    for nearby in _NEARBY_TO_TEXTEDIT:
        assert nearby not in allowed, f"the allowlist admitted the nearby name {nearby!r}"


def test_case_variant_names_are_two_distinct_entries_not_a_duplicate(keystone):
    """The other side of exact comparison, and deliberately not treated as a duplicate.

    Under case-insensitive matching `["Mail", "mail"]` would be a contradiction worth
    refusing. Under exact matching they are simply two names, and on a case-sensitive
    filesystem two genuinely different applications can differ only in case. Refusing here
    would mean guessing which one the operator meant — and guessing is what this rule
    exists to avoid. Duplicate detection is exact-equality only, stated so nobody "fixes"
    it into a casefold comparison later.
    """
    _write(keystone, '{"version": 1, "enabled": true, "apps": ["Mail", "mail"]}')
    assert ES.is_enabled() is True
    assert ES.allowed_apps() == ("Mail", "mail")


# ── 5. DCU-1's strict parse survives the extension ───────────────────────────


def test_an_unknown_key_is_still_refused_alongside_a_valid_allowlist(keystone):
    """`DCU-1`'s strict parse is the property most at risk from an extension: the change that
    adds a key is the change that is tempted to relax the check that rejects keys. A
    well-formed `apps` list sitting next to an unenforced key must not buy the document any
    leniency."""
    _write(
        keystone,
        '{"version": 1, "enabled": true, "apps": ["Mail"], "windows": ["Inbox"]}',
    )
    state = ES.active_enable_state()
    assert state.enabled is False
    assert "does not enforce" in state.detail
    assert "windows" in state.detail
    assert ES.allowed_apps() == ()


def test_a_different_schema_version_is_still_refused_not_best_effort_parsed(keystone):
    """Version handling is unchanged by this atom. A future document may carry scoping keys
    this build cannot enforce, so a version mismatch must refuse the whole document — parsing
    the parts it recognises would be exactly the "honour the flag, drop the scope" widening
    the unknown-key rule already forbids."""
    _write(keystone, '{"version": 2, "enabled": true, "apps": ["Mail"]}')
    state = ES.active_enable_state()
    assert state.enabled is False
    assert "declares version 2" in state.detail
    assert ES.allowed_apps() == (), "a version this build cannot read granted an allowlist"
    assert ES.SCHEMA_VERSION == 1


@pytest.mark.parametrize(
    ("key", "document", "needle"),
    [
        ("version", '{"version": 9, "enabled": true, "apps": ["Mail"]}', "declares version 9"),
        ("enabled", '{"version": 1, "enabled": "yes", "apps": ["Mail"]}', "not the literal true"),
        ("apps", '{"version": 1, "enabled": true, "apps": "Mail"}', "not a list of app names"),
        (
            "unattended",
            '{"version": 1, "enabled": true, "unattended": "computer_click"}',
            "not a list of computer-use tool names",
        ),
    ],
)
def test_every_allowed_key_is_actually_enforced(keystone, key, document, needle):
    """The anti-vacuity half of the membership floor below. Pinning `_ALLOWED_KEYS` to four
    names proves nothing on its own — an allowed key with no parser branch would be a scope
    an operator writes and this build silently ignores, which is the widening the whole
    refuse-unknown-keys rule exists to prevent. So each of the four must be shown to have
    teeth."""
    _write(keystone, document)
    state = ES.active_enable_state()
    assert state.enabled is False, f'"{key}" is an allowed key with no enforcement'
    assert needle in state.detail


def test_the_allowed_key_set_is_exactly_these_four(keystone):
    """The vacuity floor: a fifth key added later without a parser branch reds HERE, next to
    the comment explaining why that pairing is mandatory, rather than shipping as an accepted
    field nothing enforces.

    ``unattended`` is `DCU-5`'s third grant — which tools a run with nobody watching may invoke.
    It is deliberately NOT in ``ENABLE_DOCUMENT``: the quoted document has to be the SMALLEST one
    that works, and an unattended grant is not needed to drive the desktop from a session a human
    is present in. Its own refusal names the key, and the subset assertion below is what keeps
    that choice honest rather than accidental."""
    assert ES._ALLOWED_KEYS == ("version", "enabled", "apps", "unattended")
    assert ES.UNATTENDED_KEY in ES._ALLOWED_KEYS
    assert set(json.loads(ES.ENABLE_DOCUMENT)) <= set(ES._ALLOWED_KEYS)


# ── 6. the quoted document is a document that works ─────────────────────────


def test_the_quoted_enable_document_parses_and_can_actually_drive_something(keystone):
    """The invariant the module's own comment claims: the bytes the FIX line tells an operator
    to write are bytes this parser accepts, resolving to a state that can drive something.

    Before this atom the constant was `{"version": 1, "enabled": true}`. With an allowlist
    that fails closed, that document arms a capability with nowhere to point — an operator
    would follow the FIX line exactly, get refused a second time, and this time the message
    would name no further fix. A FIX line that produces another refusal is worse than no FIX
    line, because it teaches the operator that the message cannot be trusted. So the constant
    is a *working* document with one deliberately benign real target, and this test is what
    keeps that true rather than aspirational.
    """
    _write(keystone, ES.ENABLE_DOCUMENT)
    state = ES.require_enabled("computer_fixture_press")
    assert state.enabled is True
    assert state.detail and "EMPTY" not in state.detail
    assert ES.allowed_apps(), "the document quoted in the FIX line can drive nothing"
    # And the FIX line still quotes it verbatim, so the two cannot drift apart.
    fix = ES.disabled_error("computer_fixture_press", ES.EnableState()).fix
    assert ES.ENABLE_DOCUMENT in fix
    assert "apps" in fix, "the FIX line must say what the allowlist is for"
    assert "matched exactly" in fix, "the FIX line must state the comparison rule"


def test_the_boot_record_names_the_allowlist(keystone, caplog):
    """The armed log line is the tamper-evidence surface, and "armed" and "armed for what"
    are different facts to an operator skimming it. The second one is the blast radius."""
    _write(keystone, '{"version": 1, "enabled": true, "apps": ["Mail", "Safari"]}')
    with caplog.at_level("WARNING"):
        state = ES.ensure_computer_use_boot()
    assert state.enabled is True
    armed = [r.getMessage() for r in caplog.records if "ENABLED" in r.getMessage()]
    assert armed, "an armed keystone must be logged at WARNING"
    assert "Mail" in armed[0] and "Safari" in armed[0]


# ── 7. one reader ────────────────────────────────────────────────────────────


def test_the_apps_field_has_exactly_one_reader(keystone):
    """`EnableState.apps` may be read only inside `allowed_apps()`.

    Not style. `require_enabled`'s docstring records the measured version of this: while
    `enabled` had two readers, forcing `is_enabled` to return True left every refusal test
    GREEN, because the guard was consulting the field directly. A second place that reaches
    into `state.apps` is a second place that can default, normalise or widen it differently
    from the accessor — and the divergence is invisible until the day it matters.

    Walks the AST rather than grepping: this module's prose mentions ``state.apps`` several
    times, and a text scanner reads comments as code.
    """
    module = Path(ES.__file__)
    tree = ast.parse(module.read_text(encoding="utf-8"))

    readers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Attribute) and inner.attr == "apps":
                readers.append(node.name)

    assert readers == ["allowed_apps"], (
        "EnableState.apps must be read only through allowed_apps(); "
        f"found readers in {sorted(set(readers))}"
    )
