"""A recovery notice is about something that was down, in words the user knows.

Measured on day 8: binding the first model posted eleven notifications at once, each
"<slug> recovered — Model available again for <use case>" (`inbox_enrichment recovered`,
`assistant_reasoning recovered`, …). None of those surfaces had ever been up: a fresh home has no
model, so every surface's silent first sight is "down", and the first bind flipped them all. And
the names were the registry's slugs.

So a recovery is announced only after a degradation the user was told about, and every notice,
like the degraded chip, names the surface by its contract's `label` and the model by the name the
Models page gives it. Driven through the real built-in contract set.
"""

from __future__ import annotations

import pytest

from personalclaw.resilience import degraded


@pytest.fixture(autouse=True)
def _fresh_transitions():
    degraded.reset_transition_state()
    yield
    degraded.reset_transition_state()


class _Bell:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))


@pytest.fixture
def models(monkeypatch):
    """Whether a model resolves, for every use case at once: the first-bind shape."""
    bound = {"value": False}
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda uc: bound["value"]
    )
    return bound


def test_binding_the_first_model_is_not_a_recovery(models):
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)  # a fresh home: nothing bound (silent baseline)
    models["value"] = True
    degraded.evaluate(notify=True, state=bell)  # the user binds their first model
    recovered = [title for _kind, title, _body in bell.notes if "recovered" in title]
    assert recovered == [], f"{len(recovered)} surfaces that were never up announced a recovery"


def test_an_outage_the_user_was_told_about_is_announced_when_it_ends(models):
    """Control, on both trees: up, down (announced), up again is a real recovery."""
    models["value"] = True
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)
    models["value"] = False
    degraded.evaluate(notify=True, state=bell)
    models["value"] = True
    degraded.evaluate(notify=True, state=bell)
    chat = [(kind, title) for kind, title, _body in bell.notes if title.lower().startswith("chat ")]
    assert [kind for kind, _title in chat] == ["warning", "info"]


def test_every_notice_names_the_surface_and_the_model_as_the_user_knows_them(models):
    models["value"] = True
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)
    models["value"] = False
    degraded.evaluate(notify=True, state=bell)
    models["value"] = True
    degraded.evaluate(notify=True, state=bell)

    slugs = {c.surface for c in degraded.all_contracts()} | {
        uc for c in degraded.all_contracts() for uc in c.use_cases
    }
    assert bell.notes, "the outage must have been announced for this test to mean anything"
    for _kind, title, body in bell.notes:
        words = set(title.replace(",", " ").split()) | set(body.replace(",", " ").split())
        leaked = sorted(w for w in words if w in slugs and "_" in w)
        assert not leaked, f"{title!r} / {body!r} names a slug: {leaked}"
        assert "_" not in title, title
    titles = {title for _kind, title, _body in bell.notes}
    assert "Inbox triage degraded" in titles and "Background tasks recovered" in titles


def test_every_contract_declares_a_name_and_every_model_it_needs_has_one():
    for contract in degraded.all_contracts():
        assert contract.label and "_" not in contract.label, contract.surface
        for use_case in contract.use_cases:
            assert use_case in degraded.USE_CASE_NAMES, (contract.surface, use_case)


def test_the_degraded_chip_reads_the_same_name(models):
    rows = {row["surface"]: row for row in degraded.evaluate()}
    for contract in degraded.all_contracts():
        assert rows[contract.surface]["label"] == contract.label
