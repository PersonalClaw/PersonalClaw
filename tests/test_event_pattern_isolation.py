"""Rule (d): payload is DATA, never a pattern — plus the ReDoS surface that audit exposed (S128).

§7/R4 rule (d): *"payload content never participates in event-pattern/template matching — only
trigger spec patterns match; payload is data."*

**The rule HOLDS, and this file is the guard rather than a fix.** Verified rather than assumed: the
regex in `matches` comes from `trigger.content_re`, the glob from `trigger.key_glob`, and the
payload
is only ever matched AGAINST. `render_template` does not re-expand a substituted value either
(checked in S126), so a payload carrying `$OTHER_KEY` cannot reach a second key's contents. Saying
"it already holds" plainly matters — inventing a fix here would be worse than finding none.

🔴 WHAT THE AUDIT DID FIND: a real ReDoS surface on the memory-write path. Measured on `matches`
itself, with an author regex of `(a+)+$` — a shape people write by accident, not an attack:

    value len 22: 0.165s
    value len 24: 0.649s
    value len 26: 2.539s
    value len 28: 10.122s
    value len 30: 40.7s

`matches` runs on every memory write (`vector_memory` → `emit_memory_event` →
`on_memory_event`), and
the value was not length-bounded. **A length cap does NOT fix exponential backtracking** — that is
recorded on `CONTENT_MATCH_SCAN_LIMIT` rather than pretended otherwise — so catastrophic
patterns are
caught where they are AUTHORED.
"""

from __future__ import annotations

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.template import render_template
from personalclaw.event_triggers import (
    CONTENT_MATCH,
    CONTENT_MATCH_SCAN_LIMIT,
    MEMORY_KEY_PATTERN,
    EventTrigger,
    catastrophic_regex_hint,
    matches,
)


def _trigger(**kw) -> EventTrigger:
    return EventTrigger(id=kw.pop("id", "e:t"), pattern=kw.pop("pattern", CONTENT_MATCH), **kw)


# ── rule (d): the payload never supplies a pattern ──


def test_the_PATTERN_comes_from_the_TRIGGER_not_the_value():
    """The rule's core. A value that looks like a regex is matched as literal data."""
    t = _trigger(content_re="deploy")
    assert matches(t, event_type="set", key="k", value="deploy finished") is True
    # The value's own regex-ish text is not compiled — it is the haystack, never the needle.
    assert matches(t, event_type="set", key="k", value=".*") is False


def test_a_value_containing_a_REGEX_cannot_match_everything():
    """🔴 If the value were ever used as the pattern, `.*` in a memory write would fire every
    ContentMatch trigger on the machine."""
    t = _trigger(content_re="^SPECIFIC$")
    assert matches(t, event_type="set", key="k", value=".*") is False
    assert matches(t, event_type="set", key="k", value="(?s).*") is False


def test_the_KEY_GLOB_comes_from_the_trigger_too():
    t = _trigger(pattern=MEMORY_KEY_PATTERN, key_glob="project.acme.*")
    assert matches(t, event_type="set", key="project.acme.x", value="v") is True
    assert matches(t, event_type="set", key="project.other.x", value="v") is False
    # A value shaped like a glob changes nothing.
    assert matches(t, event_type="set", key="other", value="project.acme.*") is False


def test_a_payload_value_is_NOT_re_expanded_as_a_template():
    """🔴 The second-order version of rule (d): if a substituted value were re-expanded, a payload
    carrying `$SECRET_KEY` would pull in another payload key's contents."""
    ctx = ActionContext(
        event="trigger.fired",
        context="",
        payload={"new_items": "innocent $SECRET_KEY", "SECRET_KEY": "s3cr3t"},
    )
    out = render_template("$new_items", ctx)
    assert "s3cr3t" not in out
    assert "$SECRET_KEY" in out, "the placeholder stays literal — one substitution pass only"


def test_a_payload_value_cannot_inject_CONTEXT():
    ctx = ActionContext(event="e", context="the-real-context", payload={"x": "see $CONTEXT"})
    assert "the-real-context" not in render_template("$x", ctx)


# ── the scan cap ──


def test_the_scan_is_LENGTH_CAPPED():
    """A sane regex over a multi-megabyte value is a linear cost this bounds. (It does NOT bound a
    catastrophic one — see the module docstring.)"""
    t = _trigger(content_re="NEEDLE")
    beyond = "x" * (CONTENT_MATCH_SCAN_LIMIT + 100) + "NEEDLE"
    assert matches(t, event_type="set", key="k", value=beyond) is False


def test_a_match_INSIDE_the_cap_still_fires():
    t = _trigger(content_re="NEEDLE")
    assert matches(t, event_type="set", key="k", value="NEEDLE at the front") is True


def test_the_cap_does_not_truncate_what_is_STORED_or_FIRED():
    """The cap applies to the SCAN only. Truncating the value itself would silently change what the
    automation sees — asserted on the source, because the property is that `matches` is pure and
    never writes."""
    import inspect

    src = inspect.getsource(matches)
    assert "scanned" in src, "the cap applies to a local scan copy"
    assert "value =" not in src, "matches must never rebind the caller's value"


# ── the catastrophic-regex hint ──


@pytest.mark.parametrize(
    "pattern", [r"(a+)+$", r"(\w+)+", r"(a*)*", r"(a+)*", r"(a|a)+", r"^(x|y)*$"]
)
def test_a_CATASTROPHIC_pattern_is_flagged(pattern):
    """🔴 The shapes behind essentially every real ReDoS: a quantifier on a quantified group, or an
    alternation inside a quantified group. Both are almost always an accident."""
    assert catastrophic_regex_hint(pattern)


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bERROR\b",
        r"user\.\w+",
        r"(?i)deploy",
        r"^\d{4}-\d{2}-\d{2}$",
        r"(alpha|beta)",
        r"a+b+",
        r"(?:x)+",
        r"[a-z]+@[a-z]+\.com",
    ],
)
def test_a_NORMAL_pattern_is_NOT_flagged(pattern):
    """False positives matter more than usual: this warning appears while someone is
    authoring, and a
    guard that cried wolf on `(alpha|beta)` would train people to ignore it."""
    assert catastrophic_regex_hint(pattern) == ""


def test_an_empty_pattern_is_not_flagged():
    assert catastrophic_regex_hint("") == ""


def test_the_hint_says_HOW_TO_FIX_IT():
    """A warning that names the problem but not the remedy leaves the author guessing."""
    hint = catastrophic_regex_hint(r"(\w+)+")
    assert "quantifier" in hint
    assert "Simplify" in hint


# ── the hint is WIRED, not inert ──


def test_the_CREATE_handler_surfaces_the_hint():
    """🔴 A hint nothing returns is the inert-control defect this program keeps finding."""
    import inspect

    from personalclaw.dashboard.handlers import triggers as T

    src = inspect.getsource(T._create_event)
    assert "_regex_hint" in src and "warning" in src


def test_the_UPDATE_handler_surfaces_the_hint_too():
    """An edit that INTRODUCES a catastrophic pattern must say so, or the author only finds out when
    their memory writes get slow."""
    import inspect

    from personalclaw.dashboard.handlers import triggers as T

    src = inspect.getsource(T._update_event)
    assert "_regex_hint" in src and "warning" in src


def test_a_catastrophic_pattern_is_WARNED_not_REFUSED():
    """Refusing would break triggers people already have — the same warn-and-keep-working reasoning
    S119 recorded for a verbatim webhook token. The trigger still matches."""
    t = _trigger(content_re=r"(a+)+$")
    assert matches(t, event_type="set", key="k", value="aaa!") is False  # ran, did not raise
    assert catastrophic_regex_hint(t.content_re), "and the author was warned"
