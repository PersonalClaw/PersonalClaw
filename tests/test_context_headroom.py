"""The declared headroom contract at the assembly seam (CONTEXT-ECONOMY CE2-8).

A turn must stop discovering the context limit by failing at it. What that means, and what
each test here holds:

* **Three DECLARED states, not an exception.** The set is closed and callers branch on a
  value.
* **The output reserve is part of the bound.** A prompt that fills the window EXACTLY
  leaves no room to answer and must not read as ``fits``. The reserve is
  ``local_models.budgets.output_budget``'s number — asserted by parity, so a second
  output-budget notion cannot be minted quietly.
* **The window came from the registry, not a hardcoded default.** ``model_context_window``
  answers every query with 200k for a model it has never heard of; adopting that would be
  the defaulted-field-is-an-unsupplied-input defect and would leave the whole contract
  measured against a number nobody declared. Unknown is ``None``, never 200k, never 0.
* **The user is told when compression runs**, naming what was compressed.
* **Pressure is observable BEFORE the failure**, while the state is still ``fits``.
* **A driven assembly** past a small CATALOG-declared window produces a legible refusal
  that names the specific oversized component.
"""

from __future__ import annotations

import inspect

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.context_engine import (
    AssembledContext,
    DefaultContextEngine,
    check_headroom,
    headroom_components,
)
from personalclaw.context_headroom import (
    PRESSURE_CRITICAL_FRACTION,
    PRESSURE_WARN_FRACTION,
    WINDOW_UNKNOWN,
    Component,
    Headroom,
    HeadroomState,
    Window,
    bound_model_ref,
    check,
    check_for_model,
    resolve_window,
)
from personalclaw.local_models import registry as lm_registry
from personalclaw.local_models.budgets import output_budget
from personalclaw.local_models.provider import LocalModel, LocalModelProvider
from personalclaw.memory import MemoryStore
from personalclaw.model_windows import DEFAULT_CONTEXT_WINDOW, model_context_window
from personalclaw.skills import SkillsLoader


class _FakeLocalProvider(LocalModelProvider):
    """A local provider whose catalog is whatever the test hands it."""

    def __init__(self, name: str, models: list[LocalModel]) -> None:
        self._name = name
        self._models = models

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name.title()

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[LocalModel]:
        return list(self._models)

    async def download_model(self, model_name: str) -> bool:
        return True

    async def delete_model(self, model_name: str) -> bool:
        return True


@pytest.fixture
def clean_registry():
    """Snapshot + restore the process-global local-model registry.

    The registry is a module-level dict; a leaked fake provider would change another
    test's window and make this suite's own vacuity assertion pass for the wrong reason.
    """
    before = dict(lm_registry._providers)
    yield lm_registry
    lm_registry._providers.clear()
    lm_registry._providers.update(before)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """Never touch the real ``~/.personalclaw``: the assembly path reads config and the
    prompt provider, both of which resolve under ``PERSONALCLAW_HOME``."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


def _tiny_window(*, window: int = 8_000, reserve: int = 4_096) -> Window:
    """A hand-built window, for the pure-decision tests that are not about resolution."""
    return Window(
        tokens=window,
        output_reserve_tokens=reserve,
        input_tokens=window - reserve,
        source="catalog",
    )


# ── 1. Three declared states, and the set is closed ────────────────────────────


def test_the_state_set_is_exactly_three():
    """A fourth state would break every exhaustive branch a caller writes.

    Notably an UNMEASURED window does NOT get one: it is a property of the evidence
    (``window.measured``), not a fourth outcome.
    """
    assert {s.value for s in HeadroomState} == {
        "fits",
        "fits_after_compression",
        "cannot_fit",
    }


def test_a_small_assembly_fits_and_says_nothing():
    verdict = check(
        [Component(name="the user's request", text="hi", compressible=False)], window=_tiny_window()
    )

    assert verdict.state is HeadroomState.FITS
    assert verdict.level == "ok"
    assert verdict.notice() == ""  # a healthy turn is silent
    assert verdict.text == "hi"


def test_the_decision_is_a_value_not_a_raised_exception():
    """The refusal path returns; it does not raise. Three states in, zero exceptions out."""
    for comps in (
        [Component(name="a", text="x")],
        [Component(name="b", text="y" * 60_000, compressible=False)],
        [Component(name="c", text="z" * 60_000)],
    ):
        verdict = check(comps, window=_tiny_window())
        assert isinstance(verdict, Headroom)
        assert verdict.state in set(HeadroomState)


# ── 2. The output reserve is part of the bound ──────────────────────────────────


def test_a_prompt_that_fills_the_window_exactly_does_not_fit():
    """The load-bearing clause: "a prompt that fits exactly leaves no room to answer and
    fails identically to one that does not fit".

    The component is sized to the WHOLE window, which is inside the window and outside the
    input room. Drop the reserve from the bound and this test goes green while a real turn
    gets a provider error — which is exactly the failure CE2-8 exists to remove.
    """
    win = _tiny_window(window=8_000, reserve=4_096)
    exact = Component(name="session context", text="tok " * win.tokens, compressible=False)

    verdict = check([exact], window=win)

    assert verdict.assembled_tokens >= win.tokens  # it really does fill the window
    assert verdict.state is HeadroomState.CANNOT_FIT
    assert "reserved so there is room to reply" in verdict.reason
    assert f"{win.output_reserve_tokens:,}" in verdict.reason


def test_the_bound_is_the_window_minus_the_reserve():
    """One token over the input room refuses; one token under it fits — and the boundary
    sits at ``window − reserve``, not at ``window``."""
    win = _tiny_window(window=8_000, reserve=4_096)
    assert win.input_tokens == 3_904

    under = check([Component(name="ctx", text="tok " * 3_800, compressible=False)], window=win)
    over = check([Component(name="ctx", text="tok " * 4_000, compressible=False)], window=win)

    assert under.state is HeadroomState.FITS
    assert over.state is HeadroomState.CANNOT_FIT
    # …and both are BELOW the raw window, so only the reserve can explain the difference.
    assert under.assembled_tokens < win.tokens
    assert over.assembled_tokens < win.tokens


@pytest.mark.asyncio
async def test_the_reserve_is_output_budgets_number_not_a_second_one(clean_registry):
    """Parity, not resemblance. ``output_budget`` is what ``llm_helpers`` puts in the
    provider's ``max_tokens``; a headroom contract reserving a DIFFERENT amount would let a
    prompt through that the provider then refuses for lack of output room."""
    clean_registry.register_provider(
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=8_192, output_tokens=1_024)]
        ),
        capabilities=["chat"],
        name="FakeLocal",
    )

    for ref in ("FakeLocal:tiny-chat", "gpt-4o", "no-such-model-anywhere"):
        win = await resolve_window(ref)
        assert win.output_reserve_tokens == await output_budget(ref), ref


# ── 3. The window came from the registry, not a hardcoded default ───────────────


@pytest.mark.asyncio
async def test_a_catalog_declared_window_is_sourced_to_the_catalog(clean_registry):
    """The strongest form of the vacuity assertion: the figure moves when the model CARD
    moves, so it cannot be a constant."""
    clean_registry.register_provider(
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=6_000, output_tokens=2_000)]
        ),
        capabilities=["chat"],
        name="FakeLocal",
    )

    win = await resolve_window("FakeLocal:tiny-chat")

    assert win.source == "catalog"
    assert win.tokens == 6_000
    assert win.output_reserve_tokens == 2_000
    assert win.input_tokens == 4_000
    assert win.tokens != DEFAULT_CONTEXT_WINDOW


@pytest.mark.asyncio
async def test_a_table_declared_window_is_the_tables_own_number():
    """A hosted model the table NAMES gets the table's figure, sourced honestly."""
    win = await resolve_window("gpt-4o")

    assert win.source == "window-table"
    assert win.tokens == model_context_window("gpt-4o")
    assert win.tokens != DEFAULT_CONTEXT_WINDOW  # the table really names this one


@pytest.mark.asyncio
async def test_an_unnamed_model_is_unmeasured_never_the_hardcoded_default():
    """``model_context_window`` answers 200k for a model it has never heard of. Adopting
    that answer is the defaulted-field-is-an-unsupplied-input defect: the contract would
    then be measured against a number nobody declared, and every assertion above it would
    be vacuous. So an unnamed model is UNMEASURED."""
    assert model_context_window("no-such-model-anywhere") == DEFAULT_CONTEXT_WINDOW

    win = await resolve_window("no-such-model-anywhere")

    assert win.source == WINDOW_UNKNOWN
    assert win.tokens is None
    assert win.tokens != DEFAULT_CONTEXT_WINDOW
    assert win.input_tokens is None
    assert win.measured is False


# ── 4. Unknown is neither zero nor infinite ────────────────────────────────────


def test_an_unmeasured_window_neither_refuses_nor_claims_headroom():
    """Zero would make a mistyped model id an outage; infinite would reintroduce the exact
    silent failure this contract removes. So: permit the turn, and report the pressure as
    UNKNOWN rather than as comfortable."""
    unmeasured = Window(
        tokens=None, output_reserve_tokens=4_096, input_tokens=None, source=WINDOW_UNKNOWN
    )

    verdict = check([Component(name="ctx", text="tok " * 50_000)], window=unmeasured)

    assert verdict.state is HeadroomState.FITS  # not an outage
    assert verdict.pressure is None  # not "0% used"
    assert verdict.headroom_tokens is None
    assert verdict.level == "unmeasured"
    assert verdict.raw_tokens > 0  # the size WAS counted
    assert "unmeasured" in verdict.reason
    assert verdict.fix  # and it says how to make it measurable


def test_unmeasured_is_distinguishable_from_measured_and_full():
    """``local_models.fit``'s discipline: ``None`` = unmeasured, a real number = measured.
    Collapsing the two produced a real bug there, so they stay apart here."""
    unmeasured = check(
        [Component(name="ctx", text="x" * 10_000)],
        window=Window(
            tokens=None, output_reserve_tokens=10, input_tokens=None, source=WINDOW_UNKNOWN
        ),
    )
    measured_and_full = check(
        [Component(name="ctx", text="x" * 10_000, compressible=False)],
        window=Window(tokens=11, output_reserve_tokens=10, input_tokens=1, source="catalog"),
    )

    assert unmeasured.pressure is None
    assert unmeasured.state is HeadroomState.FITS
    assert measured_and_full.pressure is not None
    assert measured_and_full.state is HeadroomState.CANNOT_FIT


# ── 5. The user is told what was compressed, at the point it happens ───────────


def test_compression_names_what_it_compressed():
    """The load-bearing clause: "a silent drop is indistinguishable from a wrong answer"."""
    verdict = check(
        [
            Component(name="system prompt", text="S" * 400, compressible=False),
            Component(name="episodic memory", text="E" * 30_000),
            Component(name="the user's request", text="hi", compressible=False),
        ],
        window=_tiny_window(),
    )

    assert verdict.state is HeadroomState.FITS_AFTER_COMPRESSION
    assert [c.name for c in verdict.compressed] == ["episodic memory"]
    note = verdict.compressed[0]
    assert note.tokens_after < note.tokens_before
    assert note.tokens_saved > 0

    told = verdict.notice()
    assert told, "a compression the user is never told about is a silent drop"
    assert "episodic memory" in told
    assert f"{note.tokens_before:,}" in told and f"{note.tokens_after:,}" in told
    # And the compressed text is what will be sent — not the original.
    assert len(verdict.text) < 40_000
    assert verdict.assembled_tokens <= verdict.window.input_tokens


def test_an_incompressible_component_is_refused_rather_than_silently_trimmed():
    """The session-context block carries the user's lessons. Cutting it in half quietly is
    the silent drop; naming it and refusing is the honest outcome."""
    verdict = check(
        [
            Component(
                name="session context (memory · lessons · history)",
                text="word " * 40_000,
                compressible=False,
            ),
            Component(name="the user's request", text="hi", compressible=False),
        ],
        window=_tiny_window(),
    )

    assert verdict.state is HeadroomState.CANNOT_FIT
    assert verdict.compressed == ()
    assert verdict.text == "", "a refusal must have nothing sendable"


# ── 6. A refusal names the specific component, with a reason AND a fix ─────────


def test_a_refusal_names_the_specific_oversized_component():
    """ "instead of a generic overflow": the user needs to know WHICH block to remove."""
    verdict = check(
        [
            Component(name="system prompt", text="S" * 200, compressible=False),
            Component(name="tool result: run_command", text="L" * 100_000, compressible=False),
            Component(name="retrieved document: notes.md", text="D" * 900, compressible=False),
            Component(name="the user's request", text="hi", compressible=False),
        ],
        window=_tiny_window(),
    )

    assert verdict.state is HeadroomState.CANNOT_FIT
    assert verdict.oversized, "a refusal with no named component IS a generic overflow"
    assert verdict.oversized[0].name == "tool result: run_command"
    assert "tool result: run_command" in verdict.reason
    assert "not compressible" in verdict.reason
    # The reason states the arithmetic; the fix states an action.
    assert "Over by" in verdict.reason
    assert verdict.fix and "tool result: run_command" in verdict.fix
    # notice() carries both, so one broadcast is enough.
    assert verdict.reason in verdict.notice() and verdict.fix in verdict.notice()


def test_a_compressed_but_still_oversized_component_says_so():
    """ "already compressed" and "not compressible" lead to different user actions, so the
    refusal must not guess between them."""
    verdict = check(
        [Component(name="tool result: dump", text="J" * 200_000, compressible=True)],
        window=_tiny_window(window=200, reserve=100),
    )

    assert verdict.state is HeadroomState.CANNOT_FIT
    assert verdict.compressed, "a compressible component should have been compressed"
    assert verdict.oversized[0].compressed is True
    assert "already compressed" in verdict.oversized[0].note


# ── 7. Pressure is observable BEFORE the failure ───────────────────────────────


def test_pressure_warns_while_the_state_is_still_fits():
    """ "a headroom signal, not only a post-hoc error" — the warning has to arrive while
    there is still room to act on it, so it fires on a turn that FITS."""
    win = _tiny_window(window=8_000, reserve=4_096)
    room = win.input_tokens

    def at(fraction: float) -> Headroom:
        # `tok ` is one token per word for cl100k and 1 per 4 chars for the fallback, so
        # the same source text lands on the same fraction under either counter.
        return check(
            [Component(name="ctx", text="tok " * int(room * fraction), compressible=False)],
            window=win,
        )

    calm = at(0.4)
    warn = at(PRESSURE_WARN_FRACTION + 0.05)
    critical = at(PRESSURE_CRITICAL_FRACTION + 0.05)

    assert (calm.state, calm.level) == (HeadroomState.FITS, "ok")
    assert calm.notice() == ""
    assert (warn.state, warn.level) == (HeadroomState.FITS, "warn")
    assert (critical.state, critical.level) == (HeadroomState.FITS, "critical")
    for pressured in (warn, critical):
        told = pressured.notice()
        assert "headroom" in told.lower()
        assert f"{pressured.window.output_reserve_tokens:,}" in told
        assert pressured.headroom_tokens is not None and pressured.headroom_tokens > 0


# ── 8. The seam: named components, and they cover the whole prompt ─────────────


@pytest.fixture
def builder(tmp_path):
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


def test_the_assembly_hands_back_named_components_covering_the_whole_prompt(builder):
    """The vacuity assertion for the LABELS: if the named components do not reconstruct the
    message, the contract measures a smaller prompt than the one about to be sent — and
    would report "fits" for a prompt that does not. This is the rail that catches a future
    `parts.add` that forgets its name (or a raw append sneaking back in)."""
    assembled = DefaultContextEngine().assemble(
        builder,
        "what did we decide about the retry budget?",
        is_new_session=True,
        session_key="dashboard:ce28",
        active_recall=False,
    )

    assert assembled.components, "the default engine must label its parts"
    assert "".join(c.text for c in assembled.components) == assembled.message
    names = [c.name for c in assembled.components]
    assert "the user's request" in names
    # Every component is named — an empty label is a generic overflow waiting to happen.
    assert all(c.name.strip() for c in assembled.components)
    # And the pieces that must never be trimmed declare it.
    by_name = {c.name: c for c in assembled.components}
    assert by_name["the user's request"].compressible is False


def test_unlabelled_components_degrade_to_one_named_whole_not_to_a_wrong_measurement():
    """A custom engine that fills nothing, and a labelling that does not reconstruct the
    message, both land on the same honest fallback: less specific, never wrong."""
    empty = AssembledContext(message="abc")
    assert [c.name for c in headroom_components(empty)] == [
        "assembled prompt (components unlabelled by this engine)"
    ]

    lying = AssembledContext(message="abcdef", components=[Component(name="half", text="abc")])
    fallback = headroom_components(lying)
    assert len(fallback) == 1
    assert fallback[0].text == "abcdef"  # measured whole, not the labelled half


@pytest.mark.asyncio
async def test_a_driven_assembly_past_a_small_declared_window_refuses_legibly(
    builder, clean_registry
):
    """The driven proof. A real assembly, a real small window that came from the model
    REGISTRY, and a refusal a human can act on.

    The window is CATALOG-declared (6,000 tokens off a model card), so the refusal cannot
    be an artifact of a hardcoded figure — change the card and the numbers in the message
    change with it.

    The oversized piece is the inline-action payload, which assembles as an INCOMPRESSIBLE
    component: there is genuinely nothing to do but refuse, and the refusal has to name it.
    """
    clean_registry.register_provider(
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=6_000, output_tokens=2_000)]
        ),
        capabilities=["chat"],
        name="FakeLocal",
    )

    assembled = DefaultContextEngine().assemble(
        builder,
        "summarize the attached report",
        is_new_session=True,
        session_key="dashboard:ce28",
        active_recall=False,
        action_context="D" * 200_000,
    )
    verdict = await check_headroom(assembled, model_ref="FakeLocal:tiny-chat")

    assert verdict.state is HeadroomState.CANNOT_FIT
    # Vacuity: the figure came from the registry, not from a default.
    assert verdict.window.source == "catalog"
    assert verdict.window.tokens == 6_000
    assert verdict.window.output_reserve_tokens == 2_000
    assert verdict.window.tokens != DEFAULT_CONTEXT_WINDOW
    # Legible: the reason names a specific component and the arithmetic, the fix names an
    # action, and nothing sendable comes back.
    assert verdict.oversized
    assert verdict.oversized[0].name == "action button context"
    assert verdict.oversized[0].name in verdict.reason
    assert "6,000" in verdict.reason and "2,000" in verdict.reason
    assert verdict.fix
    assert verdict.text == ""


@pytest.mark.asyncio
async def test_a_driven_assembly_compresses_and_says_so(builder, clean_registry):
    """The other half of the driven proof: an oversized but COMPRESSIBLE block in a real
    assembly is projected down rather than refused — and the user is told which block, with
    both sizes, so a shorter answer is attributable to a named compression."""
    clean_registry.register_provider(
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=6_000, output_tokens=2_000)]
        ),
        capabilities=["chat"],
        name="FakeLocal",
    )
    # An oversized retrieved document arriving through the hook-context seam — the shape the
    # clause names ("which retrieved document"), assembled for real.
    builder.hooks.on_message = lambda text: _InjectedContext("D" * 200_000)

    assembled = DefaultContextEngine().assemble(
        builder,
        "summarize the attached report",
        is_new_session=True,
        session_key="dashboard:ce28",
        active_recall=False,
    )
    verdict = await check_headroom(assembled, model_ref="FakeLocal:tiny-chat")

    assert verdict.state is HeadroomState.FITS_AFTER_COMPRESSION
    assert verdict.window.source == "catalog"  # vacuity: registry, not a default
    assert "hook context" in [c.name for c in verdict.compressed]
    assert "hook context" in verdict.notice()
    assert verdict.assembled_tokens <= verdict.window.input_tokens
    # The compressed prompt is what will be sent, and it is genuinely smaller.
    assert verdict.text and len(verdict.text) < len(assembled.message)


class _InjectedContext:
    """Minimal stand-in for a hook result that injects context."""

    def __init__(self, text: str) -> None:
        from personalclaw.hooks import HOOK_INJECT_CONTEXT

        self.action = HOOK_INJECT_CONTEXT
        self.text = text


# ── 9. The seam ACTS on the verdict (not a declared-but-inert control) ─────────


def test_the_chat_seam_branches_on_all_three_states():
    """A contract nothing consumes is an inert control. The runner must resolve the verdict
    BEFORE the provider call and handle each state: refuse, send the compressed text, or
    proceed — so this asserts the CALL SITE, not just the mechanism."""
    from personalclaw.dashboard import chat_runner

    src = inspect.getsource(chat_runner)
    assert "await check_headroom(" in src
    assert "HeadroomState.CANNOT_FIT" in src
    assert "HeadroomState.FITS_AFTER_COMPRESSION" in src
    # The refusal reaches the user as an error card, and the notice as an activity line.
    assert '"kind": "headroom"' in src
    # …and it is decided before the message is handed on.
    assert src.index("await check_headroom(") < src.index("full_message = _apply_incognito_prefix")


@pytest.mark.asyncio
async def test_check_for_model_resolves_the_bound_model(clean_registry):
    """``check_for_model`` is the seam's entry point: it resolves the window and decides."""
    clean_registry.register_provider(
        _FakeLocalProvider(
            "FakeLocal", [LocalModel(name="tiny-chat", context_tokens=6_000, output_tokens=2_000)]
        ),
        capabilities=["chat"],
        name="FakeLocal",
    )

    verdict = await check_for_model(
        [Component(name="ctx", text="tok " * 10_000, compressible=False)],
        model_ref="FakeLocal:tiny-chat",
    )

    assert verdict.window.source == "catalog"
    assert verdict.state is HeadroomState.CANNOT_FIT


def test_auto_is_not_treated_as_a_model_id():
    """ "auto" is the ABSENCE of a selection, not a model. Asking the registry about it
    would return an unmeasured window for a turn whose real model is perfectly known."""
    assert bound_model_ref("claude-opus-4-8") == "claude-opus-4-8"
    assert bound_model_ref("auto") != "auto"
    assert bound_model_ref("") == bound_model_ref("auto")


# ── 10. The assembly's own silent drop is now reported ────────────────────────


def test_the_session_context_char_cap_reports_its_drop(builder, monkeypatch):
    """`build_session_context` used to cut oversized history and tell only a SERVER LOG.
    The reply that followed was indistinguishable from one built on the whole history."""
    import personalclaw.context as ctx

    monkeypatch.setattr(ctx, "_MAX_CONTEXT_CHARS", 200)
    monkeypatch.setattr(ContextBuilder, "_slots_block", lambda self, vector_store: "x" * 5_000)

    notices: list[str] = []
    builder.build_message(
        "hello",
        True,
        session_key="dashboard:ce28",
        notices_out=notices,
    )

    assert notices, "an assembly-time drop the user never hears about is a silent drop"
    assert "dropped" in notices[0]
    assert "200" in notices[0]
