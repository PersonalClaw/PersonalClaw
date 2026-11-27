"""An app event's excerpt is 200 characters of its words, and a person never sees a cut marker.

🔴 The defect (Backlog: "A notification from an app event shows a cut fence marker
('</untrusted_') after its text"). An app's event text arrives fenced at origin
(`trigger_sources.registry.emit`), and the fire's `$CONTEXT` excerpt cut that fenced text at 200
characters (`event_triggers._truncate_fenced`). The 200 counted the open marker and its
provenance, so a short text's close marker was sliced, a whole one was put back after the slice,
and a notification's body (`render_for_a_person`, which takes only whole markers off) ended
"\\n</untrusted_". A longer text kept only the few characters the open marker left of the 200.

Now the limit counts the words inside the fence, never a marker: each span keeps its open marker
as it arrived and is closed, and only its words are cut.

Driven through the real app-source ingestion, the real gateway fire and the real notify action, in
a home of the test's own.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fakes import fire_through_the_gateway
from test_trigger_sources import _app_trigger, _fake_provider, _source, _store  # noqa: F401

from personalclaw.event_triggers import BusEvent, fire_payload
from personalclaw.outside_text import ends_inside_a_fence, is_whole_fence
from personalclaw.security import UNTRUSTED_CLOSE, fence_untrusted

pytestmark = pytest.mark.security


def _fired(source, store, monkeypatch, text: str):
    """The action context one app event hands its automation's action."""
    store.upsert(_app_trigger())
    calls: list = []
    monkeypatch.setattr(
        "personalclaw.action_providers.get_action_provider", lambda _n: _fake_provider(calls)
    )
    fire_through_the_gateway(lambda: source.observe("thing_happened", key="101", text=text))
    (ctx,) = calls
    return ctx


def _noted(monkeypatch, ctx) -> str:
    """The body the real notify action posts for ``$CONTEXT``."""
    from personalclaw.action_providers import notify_provider
    from personalclaw.action_providers.notify_provider import NotifyActionProvider

    notes: list[str] = []
    state = SimpleNamespace(notify=lambda _kind, _title, body, **_: notes.append(body))
    monkeypatch.setattr(
        notify_provider, "get_action_services", lambda: SimpleNamespace(state=state)
    )
    result = asyncio.run(
        NotifyActionProvider().execute(
            {"title_template": "Released", "body_template": "$CONTEXT"}, ctx
        )
    )
    assert result.success, result.error
    (body,) = notes
    return body


def test_a_short_app_events_notification_shows_its_words_and_no_marker(
    _source, _store, monkeypatch  # noqa: F811
):
    """🔴 Before: the body ended '\\n</untrusted'."""
    said = "a/b released v1.0.0: First, at long last"
    ctx = _fired(_source, _store, monkeypatch, said)
    # The case: words well inside 200, and a fence whose close the 200th character falls in.
    whole = ctx.payload["value"]
    assert len(whole) - len(UNTRUSTED_CLOSE) < 200 < len(whole), len(whole)
    body = _noted(monkeypatch, ctx)
    assert body == f"101: {said}", repr(body)


def test_a_long_app_events_excerpt_is_200_characters_of_its_words(
    _source, _store, monkeypatch  # noqa: F811
):
    """🔴 Before: the open marker took most of the 200, and a few words were left."""
    said = " ".join(f"word{i}" for i in range(200))
    ctx = _fired(_source, _store, monkeypatch, said)
    assert _noted(monkeypatch, ctx) == f"101: {said[:200]}"
    assert is_whole_fence(ctx.context) and not ends_inside_a_fence(ctx.context), ctx.context
    assert "app-source:emit" in ctx.context, "the origin's provenance was cut away"


def test_the_payloads_value_keeps_2000_characters_of_the_words(
    _source, _store, monkeypatch  # noqa: F811
):
    said = "z" * 5000
    value = _fired(_source, _store, monkeypatch, said).payload["value"]
    assert "z" * 2000 in value and "z" * 2001 not in value
    assert is_whole_fence(value) and "app-source:emit" in value


def _event(value: str) -> BusEvent:
    return BusEvent(source="app", event_type="app:x:y", key="k", value=value, now=1.0)


def _app(text: str, source_id: str = "101") -> str:
    return fence_untrusted(
        text,
        source="trigger:app:x:y",
        source_type="app:x",
        source_id=source_id,
        transformation_path="app-source:emit",
    )


def test_a_text_that_fits_is_handed_on_as_it_arrived():
    value = _app("short")
    payload, _ = fire_payload("event:t", _event(value))
    assert payload["value"] == value


def test_text_of_several_fences_is_cut_across_them_and_every_one_is_closed():
    """The limit runs across spans: a later span gets what the earlier ones left, then nothing."""
    from personalclaw.action_providers.template import without_fence

    value = f"{_app('a' * 150, 'one')}\n{_app('b' * 100, 'two')}\n{_app('c' * 10, 'three')}"
    payload, context = fire_payload("event:t", _event(value))
    assert is_whole_fence(context) and not ends_inside_a_fence(context), context
    assert without_fence(context) == f"k: {'a' * 150}\n{'b' * 50}", repr(without_fence(context))
    assert "source_id=three" not in context
    assert payload["value"] == value, "2000 characters hold every word, so nothing is cut"


def test_a_span_left_open_is_closed_by_the_cut():
    """An open marker with no close takes the rest of the text with it; the cut closes it."""
    from personalclaw.security import cut_fenced_words

    opened = _app("x" * 50).removesuffix("\n</untrusted_content>")
    cut = cut_fenced_words(opened, 20)
    assert cut.endswith("x" * 20 + "\n</untrusted_content>") and not ends_inside_a_fence(cut)
    whole = cut_fenced_words(opened, 200)
    assert whole.endswith("x" * 50 + "\n</untrusted_content>"), "a span that fits is closed too"
