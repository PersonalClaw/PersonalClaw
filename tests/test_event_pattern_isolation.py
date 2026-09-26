"""Rule (d): payload is DATA, never a pattern — plus the ReDoS surface that audit exposed (S128).

§7/R4 rule (d): *"payload content never participates in event-pattern/template matching — only
trigger spec patterns match; payload is data."*

**The rule HOLDS, and this file is the guard rather than a fix.** Verified rather than assumed: the
regex in `matches` comes from the row's `spec.content_re`, the glob from its `spec.key_glob`, and
the payload is only ever matched AGAINST. `render_template` does not re-expand a substituted value
either (checked in S126), so a payload carrying `$OTHER_KEY` cannot reach a second key's contents.
Saying "it already holds" plainly matters — inventing a fix here would be worse than finding none.

🔴 WHAT THE AUDIT DID FIND: a real ReDoS surface on the memory-write path. Measured on `matches`
itself, with an author regex of `(a+)+$` — a shape people write by accident, not an attack:

    value len 22: 0.165s
    value len 24: 0.649s
    value len 26: 2.539s
    value len 28: 10.122s
    value len 30: 40.7s

`matches` runs on every memory write (`vector_memory` → `emit_event` → the gateway's event
router, or the spool's want-check in a process without one), and the value was not
length-bounded. **A length cap does NOT fix exponential backtracking** — that is recorded on
`CONTENT_MATCH_SCAN_LIMIT` rather than pretended otherwise — so catastrophic patterns are caught
where they are AUTHORED.
"""

from __future__ import annotations

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.template import render_template
from personalclaw.event_triggers import (
    CONTENT_MATCH,
    CONTENT_MATCH_SCAN_LIMIT,
    MEMORY_KEY_PATTERN,
    SOURCE_MEMORY,
    catastrophic_regex_hint,
    event_spec,
)
from personalclaw.event_triggers import matches as _matches
from personalclaw.triggers.models import Trigger


def matches(t, **kw):
    """This file exercises the payload-is-data rule for MEMORY triggers; source scoping is
    covered in ``test_event_triggers.py``, so default the source here to keep the calls focused."""
    return _matches(t, source=kw.pop("source", SOURCE_MEMORY), **kw)


def _trigger(pattern: str = CONTENT_MATCH, matcher: str = "") -> Trigger:
    """A `kind: "event"` store row whose spec carries `pattern` and its one matcher."""
    return Trigger(id="event:t", name="t", kind="event", spec=event_spec(pattern, matcher))


# ── rule (d): the payload never supplies a pattern ──


def test_the_PATTERN_comes_from_the_TRIGGER_not_the_value():
    """The rule's core. A value that looks like a regex is matched as literal data."""
    t = _trigger(matcher="deploy")
    assert matches(t, event_type="set", key="k", value="deploy finished") is True
    # The value's own regex-ish text is not compiled — it is the haystack, never the needle.
    assert matches(t, event_type="set", key="k", value=".*") is False


def test_a_value_containing_a_REGEX_cannot_match_everything():
    """🔴 If the value were ever used as the pattern, `.*` in a memory write would fire every
    ContentMatch trigger on the machine."""
    t = _trigger(matcher="^SPECIFIC$")
    assert matches(t, event_type="set", key="k", value=".*") is False
    assert matches(t, event_type="set", key="k", value="(?s).*") is False


def test_the_KEY_GLOB_comes_from_the_trigger_too():
    t = _trigger(MEMORY_KEY_PATTERN, "project.acme.*")
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
    t = _trigger(matcher="NEEDLE")
    beyond = "x" * (CONTENT_MATCH_SCAN_LIMIT + 100) + "NEEDLE"
    assert matches(t, event_type="set", key="k", value=beyond) is False


def test_a_match_INSIDE_the_cap_still_fires():
    t = _trigger(matcher="NEEDLE")
    assert matches(t, event_type="set", key="k", value="NEEDLE at the front") is True


def test_the_cap_does_not_truncate_what_is_STORED_or_FIRED():
    """The cap applies to the SCAN only. Truncating the value itself would silently change what the
    automation sees — asserted on the source, because the property is that `matches` is pure and
    never writes."""
    import inspect

    # Inspect the real matcher, not this file's source-defaulting wrapper.
    src = inspect.getsource(_matches)
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


def test_the_CREATE_handler_surfaces_the_hint(tmp_path, monkeypatch):
    """🔴 A hint nothing returns is the inert-control defect this program keeps finding. Driven
    through the Triggers page's create route, not read off its source."""
    import asyncio
    import json
    import types

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    import personalclaw.config.loader as loader
    from personalclaw.dashboard.handlers import triggers as T

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
    app = web.Application()
    app["state"] = types.SimpleNamespace(push_refresh=lambda *k: None)
    req = make_mocked_request("POST", "/api/triggers", app=app)
    req["user"] = "tester"
    body = {
        "trigger_type": "event",
        "name": "slow",
        "pattern": CONTENT_MATCH,
        "content_re": r"(a+)+$",
        "action": {"provider": "notify", "config": {"title_template": "x"}},
    }

    async def _json():
        return body

    req.json = _json  # type: ignore[assignment]
    resp = asyncio.run(T.api_trigger_create(req))
    assert resp.status == 201
    assert "quantifier" in json.loads(resp.body.decode())["warning"]


def test_an_EDIT_that_introduces_the_pattern_is_warned_on_the_row(tmp_path):
    """An edit that INTRODUCES a catastrophic pattern must say so, or the author only finds out when
    their memory writes get slow. The warning is the row's own validation issue, so the Triggers
    page's warning chip and the doctor's `spec_warning` both carry it."""
    from personalclaw.triggers import tools as Tools
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=tmp_path)
    made = Tools.create(
        store,
        name="watch",
        kind="event",
        spec={"pattern": CONTENT_MATCH, "content_re": "deploy"},
        workflow={"inline": {"provider": "notify", "config": {}}},
        created_by="user",
    )
    assert made.ok, made.text
    trigger_id = made.data["trigger"]["id"]
    assert not store.get(trigger_id).warnings
    edited = Tools.update(
        store,
        trigger_id=trigger_id,
        patch={"spec": {"pattern": CONTENT_MATCH, "content_re": r"(a+)+$"}},
    )
    assert edited.ok, edited.text
    assert any("quantifier" in w.message for w in store.get(trigger_id).warnings)


def test_a_catastrophic_pattern_is_WARNED_not_REFUSED():
    """Refusing would break triggers people already have — the same warn-and-keep-working reasoning
    S119 recorded for a verbatim webhook token. The trigger still matches."""
    t = _trigger(matcher=r"(a+)+$")
    assert matches(t, event_type="set", key="k", value="aaa!") is False  # ran, did not raise
    assert catastrophic_regex_hint(t.spec["content_re"]), "and the author was warned"
