"""KL-15's batching core: bounded retry, adaptive bisection, and nothing silently dropped.

The atom names one proof specifically — *"a fake provider that rejects batches above K proves the
bisection converges and that no chunk is silently dropped"* — because that is the property the
design rests on. Providers are removable app bundles, so core cannot know their batch ceilings: a
number core declares is wrong in both directions (too low wastes the batching, too high fails whole
imports). Discovery by bisection replaces the declaration, and the cost of discovering is
logarithmic rather than the import.

What the old path did, for contrast: `embed_item_chunks` called the provider once per chunk inside a
bare `except Exception: vec = None`, so a transient 429 and a permanently unembeddable chunk were
the same outcome, with no retry and no log.

The invariant every test here leans on: `embed_texts` returns a list of EXACTLY `len(texts)`,
positionally aligned. A short or reordered list would attach one chunk's vector to another's text,
and nothing downstream could detect it.
"""

from __future__ import annotations

import pytest

from personalclaw.knowledge import embed_batch as eb


class CeilingProvider:
    """A fake provider that rejects any batch larger than `ceiling` — the atom's own fixture.

    Counts every call and records each batch size, so a test can assert the SHAPE of the
    discovery (how it converged), not merely that it eventually worked.
    """

    def __init__(self, ceiling: int, *, fail_texts: set[str] | None = None):
        self.ceiling = ceiling
        self.fail_texts = fail_texts or set()
        self.sizes: list[int] = []
        self.calls = 0

    def embed_many(self, texts: list[str]) -> list[list[float] | None]:
        self.calls += 1
        self.sizes.append(len(texts))
        if len(texts) > self.ceiling:
            raise RuntimeError(f"batch size {len(texts)} exceeds maximum of {self.ceiling}")
        for t in texts:
            if t in self.fail_texts:
                raise RuntimeError("this specific input is not embeddable")
        return [[float(len(t))] for t in texts]


def _texts(n: int) -> list[str]:
    # Distinct lengths, so a mis-aligned result is detectable by VALUE rather than only by count.
    return ["x" * (i + 1) for i in range(n)]


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Backoff is asserted by the schedule a test collects, never by wall-clock waiting."""
    monkeypatch.setattr(eb.time, "sleep", lambda _s: None)


# ── The bisection ─────────────────────────────────────────────────────────


def test_bisection_converges_on_a_provider_that_rejects_large_batches():
    """The atom's named proof. K=4, batch of 16 → split until every text is embedded."""
    p = CeilingProvider(ceiling=4)
    texts = _texts(16)
    out = eb.embed_texts(texts, embed_many=p.embed_many, batch_size=16, retry_budget=1)
    assert len(out) == len(texts), "the result changed length — a chunk was dropped or invented"
    assert all(v is not None for v in out), f"some texts never embedded: {out}"
    assert max(p.sizes) == 16 and min(p.sizes) <= 4, f"no discovery happened: {p.sizes}"


def test_every_vector_lands_on_its_OWN_text_after_a_split():
    """Alignment, by value. The fake encodes each text's length, so a shifted result is caught."""
    p = CeilingProvider(ceiling=3)
    texts = _texts(11)
    out = eb.embed_texts(texts, embed_many=p.embed_many, batch_size=11, retry_budget=1)
    assert out == [[float(len(t))] for t in texts], "vectors were mis-attributed across the split"


def test_bisection_is_logarithmic_not_one_call_per_text():
    """The cost of discovery. 32 texts with a ceiling of 8 must not degrade to 32 calls."""
    p = CeilingProvider(ceiling=8)
    out = eb.embed_texts(_texts(32), embed_many=p.embed_many, batch_size=32, retry_budget=1)
    assert all(v is not None for v in out)
    assert p.calls < 32, f"bisection degenerated to per-text calls: {p.calls} calls, {p.sizes}"


def test_a_single_unembeddable_text_does_not_cost_the_others_their_vectors():
    """The failure is isolated to the text that caused it — the import continues.

    This is the whole reason bisection recurses to size 1: without it, one poison chunk in a
    2,000-chunk import would take its whole batch down, and with a big batch size that is most
    of the library.
    """
    bad = _texts(8)[3]
    p = CeilingProvider(ceiling=8, fail_texts={bad})
    out = eb.embed_texts(_texts(8), embed_many=p.embed_many, batch_size=8, retry_budget=1)
    assert len(out) == 8
    assert out[3] is None, "the poison text was reported as embedded"
    assert all(v is not None for i, v in enumerate(out) if i != 3), f"collateral damage: {out}"


def test_nothing_is_dropped_when_EVERY_text_fails():
    """The degenerate case still returns a full-length list of Nones rather than a short one."""
    texts = _texts(5)
    p = CeilingProvider(ceiling=8, fail_texts=set(texts))
    out = eb.embed_texts(texts, embed_many=p.embed_many, batch_size=5, retry_budget=1)
    assert out == [None] * 5


# ── Retry and backoff ─────────────────────────────────────────────────────


def test_a_transient_failure_is_retried_within_the_budget():
    """The old path had no retry at all: one flake became a permanently vector-less chunk."""
    state = {"n": 0}

    def flaky(texts: list[str]) -> list[list[float] | None]:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("503 service temporarily unavailable")
        return [[1.0] for _ in texts]

    out = eb.embed_texts(_texts(3), embed_many=flaky, batch_size=3, retry_budget=3)
    assert all(v is not None for v in out), "a retryable failure was not retried"
    assert state["n"] == 2


def test_the_backoff_is_exponential_and_bounded_by_the_budget(monkeypatch):
    """The schedule, asserted rather than assumed. Collected, not waited on."""
    delays: list[float] = []
    monkeypatch.setattr(eb.time, "sleep", lambda s: delays.append(s))

    def always_fail(texts: list[str]) -> list[list[float] | None]:
        raise RuntimeError("503 unavailable")

    eb.embed_texts(["only"], embed_many=always_fail, batch_size=1, retry_budget=3)
    assert delays == [eb.BACKOFF_BASE_SECS, eb.BACKOFF_BASE_SECS * 2], delays


def test_a_TERMINAL_error_is_not_retried_at_all():
    """An auth failure retried 3x per batch across a 2,000-chunk import is 6,000 pointless calls.

    A wrong request is not an unlucky one, and the difference has to be visible in the call count.
    """
    calls: list[int] = []

    def unauthorized(texts: list[str]) -> list[list[float] | None]:
        calls.append(len(texts))
        raise RuntimeError("401 Unauthorized: invalid api key")

    out = eb.embed_texts(_texts(4), embed_many=unauthorized, batch_size=4, retry_budget=3)
    assert out == [None] * 4
    assert calls == [4], f"a terminal error was retried or bisected: {calls}"


def test_an_UNRECOGNISED_error_still_bisects(monkeypatch):
    """The bias that keeps this safe for app-bundle providers.

    Core cannot enumerate every provider's wording, so an unfamiliar message must be treated as
    "maybe the group was wrong" rather than "abandon the import". A strict allowlist of known
    batch-shaped messages would turn "we did not recognise this" into a failed import.
    """
    p = CeilingProvider(ceiling=2)
    monkeypatch.setattr(
        p,
        "embed_many",
        lambda texts: (
            (_ for _ in ()).throw(RuntimeError("zzq unknown"))
            if len(texts) > 2
            else [[1.0] for _ in texts]
        ),
    )
    out = eb.embed_texts(_texts(4), embed_many=p.embed_many, batch_size=4, retry_budget=1)
    assert all(v is not None for v in out), "an unrecognised error was not bisected"


# ── Shape errors ──────────────────────────────────────────────────────────


def test_a_provider_returning_the_WRONG_COUNT_is_a_failure_not_a_zip():
    """Zipping a short result would attach one text's vector to another's slot, silently.

    This is the defect class the alignment invariant exists to prevent, so the wrong-count case
    must be handled rather than trusted.
    """

    def short(texts: list[str]) -> list[list[float] | None]:
        return [[1.0]] * (len(texts) - 1)

    out = eb.embed_texts(_texts(4), embed_many=short, batch_size=4, retry_budget=1)
    assert len(out) == 4
    # It bisects on the shape error and each size-1 call still returns a 0-length list, so every
    # slot ends None — the important part is that NO slot got another text's vector.
    assert out == [None] * 4, f"a wrong-count response was zipped into the results: {out}"


# ── The per-text fallback ─────────────────────────────────────────────────


def test_a_provider_with_no_batch_path_still_embeds_everything():
    """`embed_many=None` is a provider without batching, not an error. Same result, more calls."""
    calls: list[str] = []

    def one(text: str) -> list[float] | None:
        calls.append(text)
        return [float(len(text))]

    texts = _texts(5)
    out = eb.embed_texts(texts, embed_one=one, batch_size=2, retry_budget=1)
    assert out == [[float(len(t))] for t in texts]
    assert len(calls) == 5


def test_no_embedder_at_all_returns_a_full_length_list_of_Nones():
    """Vacuity: the no-model path must still be aligned, because callers index into it."""
    out = eb.embed_texts(_texts(3))
    assert out == [None, None, None]


def test_an_empty_input_is_an_empty_result():
    assert eb.embed_texts([]) == []


# ── Config round-trip ─────────────────────────────────────────────────────


def test_batch_size_and_retry_budget_round_trip_through_config(tmp_path, monkeypatch):
    """The clause says both round-trip; this asserts all the way to the reader."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    assert cfg.knowledge.embed_batch_size == 32
    assert cfg.knowledge.embed_retry_budget == 3
    d = cfg.to_dict()["knowledge"]
    assert "embed_batch_size" in d and "embed_retry_budget" in d

    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    for key in ("knowledge.embed_batch_size", "knowledge.embed_retry_budget"):
        assert _EDITABLE_CONFIG.get(key, {}).get("type") == "int", f"{key} is not PATCH-writable"

    class _K:
        embed_batch_size = 8
        embed_retry_budget = 2

    class _C:
        knowledge = _K()

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _C()))
    assert eb.batch_size_from_config() == 8
    assert eb.retry_budget_from_config() == 2


def _patch_knowledge_cfg(monkeypatch, *, batch: int, budget: int) -> None:
    from personalclaw.config.loader import AppConfig

    class _K:
        embed_batch_size = batch
        embed_retry_budget = budget

    class _C:
        knowledge = _K()

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _C()))


def test_a_zero_in_config_falls_back_to_the_default(tmp_path, monkeypatch):
    """0 is handled by `_config_int`'s `or default`, BEFORE the clamp ever sees it.

    Worth pinning separately from the clamp below: the two guards cover different inputs, and a
    single test using 0 cannot tell them apart — measured, because that is exactly the test I
    wrote first and it passed with the clamp deleted.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _patch_knowledge_cfg(monkeypatch, batch=0, budget=0)
    assert eb.batch_size_from_config() == eb.DEFAULT_BATCH_SIZE
    assert eb.retry_budget_from_config() == eb.DEFAULT_RETRY_BUDGET


def test_a_NEGATIVE_batch_size_cannot_hang_the_import(tmp_path, monkeypatch):
    """What the reader-side clamp actually guards.

    `or default` does not catch a negative (`-5 or 32` is -5), and a negative step would make the
    grouping loop yield nothing at all — an import that reports success having embedded zero
    chunks. config.json is hand-editable, so the floor lives in the reader as well as in
    `_EDITABLE_CONFIG`'s bounds, which only guard the PATCH path.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    _patch_knowledge_cfg(monkeypatch, batch=-5, budget=-2)
    assert eb.batch_size_from_config() == 1, "a negative batch size reached the grouping loop"
    assert eb.retry_budget_from_config() == 1
    # End to end: every text still embeds rather than the loop yielding nothing.
    p = CeilingProvider(ceiling=99)
    out = eb.embed_texts(_texts(3), embed_many=p.embed_many)
    assert all(v is not None for v in out), f"a negative config emptied the import: {out}"


def test_the_configured_batch_size_is_actually_used(tmp_path, monkeypatch):
    """A knob nothing reads is the defect class this repo keeps finding — assert the group size."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    from personalclaw.config.loader import AppConfig

    class _K:
        embed_batch_size = 3
        embed_retry_budget = 1

    class _C:
        knowledge = _K()

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _C()))
    p = CeilingProvider(ceiling=99)
    eb.embed_texts(_texts(7), embed_many=p.embed_many)
    assert p.sizes == [3, 3, 1], f"the configured batch size was not used: {p.sizes}"
