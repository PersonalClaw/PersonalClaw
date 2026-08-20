"""Batched embedding with bounded retry and adaptive bisection (KL-15).

**What this replaces.** `pipeline/runner.embed_item_chunks` embedded one chunk per provider
call inside a bare `except Exception: vec = None` — no retry, no log, and no way to tell a
transient 429 from a permanently unembeddable chunk. `chunk_backfill.py`'s own comment stated
the consequence outright — *"chunks are embedded one at a time anyway — a bigger batch buys no
throughput"* — which is why its `BATCH_SIZE` was small; that comment is corrected in this same
change, since the round trips now amortize inside each item. Meanwhile
`EmbeddingProvider.embed_batch` has existed on the ABC the whole time
and, measured, had **zero callers in core** — implemented by the shipped `bedrock-models` and
`sentence-transformers` apps and unreached from the code that would benefit.

**Why bisection rather than a configured ceiling.** Providers are removable app bundles, so
core cannot know their batch limits: a ceiling is per-provider, per-model, sometimes per-token
rather than per-item, and it changes when the app updates. A configured maximum would be a
number core invents and gets wrong in both directions — too low wastes the batching, too high
fails whole imports. So a batch that fails in a batch-shaped way SPLITS IN HALF and each half
retries, recursively, until it succeeds or a single text fails on its own. The provider's real
ceiling is then discovered per run instead of declared, and the cost of discovering it is
logarithmic rather than the whole import.

**The invariant that makes this safe to put on an import path.** The returned list is always
exactly as long as `texts` and positionally aligned with it. A text that could not be embedded
comes back `None` — which the caller already knows how to store (a chunk with no vector stays
keyword-searchable). Nothing is ever dropped, reordered, or silently truncated, because a
short list would land as "these chunks belong to different text" and no test downstream would
notice.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

#: Texts per provider call. 32 rather than the largest number that usually works: it is
#: comfortably under the smallest ceiling among the shipped providers, so the common path
#: never pays for a bisection, and it is still 32x fewer round trips than one-per-chunk.
DEFAULT_BATCH_SIZE = 32

#: Attempts per batch before it is bisected (or, at size 1, given up on). 3 with exponential
#: backoff is ~0.5s + 1s of waiting — enough to ride out a rate-limit blip, short enough that
#: an import does not appear to hang.
DEFAULT_RETRY_BUDGET = 3

#: Base for the exponential backoff, in seconds.
BACKOFF_BASE_SECS = 0.5

#: Substrings that mark an error as BATCH-SHAPED — the batch was too large or otherwise
#: malformed as a group, so splitting it is the right response. Matched case-insensitively
#: against `str(exc)`.
#:
#: Deliberately a hint list and NOT the gate: an unrecognised error also bisects (see
#: `_should_bisect`). Providers are app bundles and their wording is theirs to change, so a
#: strict allowlist here would turn "we did not recognise the message" into "fail the import",
#: which is the failure mode this atom exists to remove.
BATCH_SHAPED_HINTS = (
    "too many",
    "too large",
    "batch size",
    "batch_size",
    "exceeds",
    "maximum",
    "max input",
    "input too long",
    "413",
    "payload",
    "request too big",
)

#: Substrings that mark an error as NOT worth retrying — the request is wrong, not unlucky.
#: Retrying an auth failure 3x per batch across a 2,000-chunk import is 6,000 pointless calls
#: and a slow, confusing failure.
TERMINAL_HINTS = (
    "unauthorized",
    "forbidden",
    "invalid api key",
    "authentication",
    "not found",
    "unsupported model",
)


def batch_size_from_config() -> int:
    """Configured texts-per-call, clamped to >= 1.

    Clamped in the READER as well as the config bounds because config.json is hand-editable
    and a 0 would make `_chunks` produce empty batches forever — a hang, not an error.
    """
    return max(1, _config_int("embed_batch_size", DEFAULT_BATCH_SIZE))


def retry_budget_from_config() -> int:
    """Configured attempts per batch, clamped to >= 1 (one attempt is "no retry", not "none")."""
    return max(1, _config_int("embed_retry_budget", DEFAULT_RETRY_BUDGET))


def _config_int(field: str, default: int) -> int:
    try:
        from personalclaw.config.loader import AppConfig

        return int(getattr(AppConfig.load().knowledge, field, default) or default)
    except Exception:  # noqa: BLE001 — an unreadable config must not stop an import
        logger.debug("embed batching: %s unreadable; using %d", field, default, exc_info=True)
        return default


def _is_terminal(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(h in text for h in TERMINAL_HINTS)


def _should_bisect(exc: BaseException, size: int) -> bool:
    """Whether this failure means "the GROUP was wrong", so splitting it may help.

    Returns True for anything that is not clearly terminal, as long as there is more than one
    text to split. The bias is deliberate: an unrecognised error from an app-bundle provider
    is far more likely to be a limit core has never heard of than a reason to abandon the
    import, and the downside of an unnecessary split is one extra call.
    """
    if size <= 1:
        return False
    if _is_terminal(exc):
        return False
    return True


def _looks_batch_shaped(exc: BaseException) -> bool:
    """Whether the message names a size/shape problem. Used only for the LOG line, so a
    reader can tell a discovered ceiling from a flaky provider."""
    text = str(exc).lower()
    return any(h in text for h in BATCH_SHAPED_HINTS)


def embed_texts(
    texts: Sequence[str],
    *,
    embed_many: Callable[[list[str]], list[list[float] | None]] | None = None,
    embed_one: Callable[[str], list[float] | None] | None = None,
    batch_size: int | None = None,
    retry_budget: int | None = None,
    sleep: Callable[[float], None] | None = None,
) -> list[list[float] | None]:
    """Embed `texts`, returning a list of the SAME LENGTH, positionally aligned.

    `embed_many` is the batch entry point (`EmbeddingProvider.embed_batch` via the registry).
    `embed_one` is the per-text fallback for a provider that has no batch path — the result is
    identical, only slower, so a provider without batching is not a second code path for the
    caller to reason about.

    `sleep` is injectable so a test can assert the backoff schedule without waiting for it.

    🔴 Resolved at CALL time (`None` -> `time.sleep`) rather than as a default argument. The
    first version wrote `sleep: Callable = time.sleep`, which snapshots the real function at
    import — so `monkeypatch.setattr(embed_batch.time, "sleep", ...)` bound nothing, an autouse
    fixture that looked like it controlled time did not, and the backoff tests really slept
    (measured: the suite took 48s). A default argument is an unsupplied input.
    """
    sleeper = sleep if sleep is not None else time.sleep
    out: list[list[float] | None] = [None] * len(texts)
    if not texts:
        return out
    size = batch_size if batch_size is not None else batch_size_from_config()
    budget = retry_budget if retry_budget is not None else retry_budget_from_config()
    size = max(1, int(size))
    budget = max(1, int(budget))

    if embed_many is None and embed_one is None:
        logger.debug(
            "embed batching: no embedder available; %d text(s) stay vector-less", len(texts)
        )
        return out

    # Indices travel WITH the texts through every split, so a bisected half writes its results
    # back to the right slots. Carrying only the texts and relying on order after a recursive
    # split is how an off-by-one becomes a mis-attributed vector nobody can see.
    for start in range(0, len(texts), size):
        group = [(i, texts[i]) for i in range(start, min(start + size, len(texts)))]
        _embed_group(
            group,
            out,
            embed_many=embed_many,
            embed_one=embed_one,
            budget=budget,
            sleep=sleeper,
        )
    return out


def _embed_group(
    group: list[tuple[int, str]],
    out: list[list[float] | None],
    *,
    embed_many: Callable[[list[str]], list[list[float] | None]] | None,
    embed_one: Callable[[str], list[float] | None] | None,
    budget: int,
    sleep: Callable[[float], None],
) -> None:
    """Embed one group with retries, bisecting on a group-shaped failure. Never raises."""
    if not group:
        return
    texts = [t for _i, t in group]
    last_exc: BaseException | None = None

    for attempt in range(budget):
        try:
            vectors = _call(texts, embed_many=embed_many, embed_one=embed_one)
        except Exception as exc:  # noqa: BLE001 — the whole point is to classify, not propagate
            last_exc = exc
            if _is_terminal(exc):
                logger.warning(
                    "embed batching: terminal error on %d text(s), not retrying: %s",
                    len(texts),
                    exc,
                )
                return
            if attempt + 1 < budget:
                delay = BACKOFF_BASE_SECS * (2**attempt)
                logger.debug(
                    "embed batching: attempt %d/%d for %d text(s) failed (%s); retrying in %.1fs",
                    attempt + 1,
                    budget,
                    len(texts),
                    exc,
                    delay,
                )
                sleep(delay)
            continue
        # A provider that returns the wrong number of vectors is a shape error, not a success:
        # zipping a short list would silently attach one text's vector to another.
        if len(vectors) != len(texts):
            last_exc = ValueError(
                f"provider returned {len(vectors)} vector(s) for {len(texts)} text(s)"
            )
            break
        for (idx, _t), vec in zip(group, vectors):
            out[idx] = vec
        return

    # Every attempt failed. Split if there is anything to split.
    if last_exc is not None and _should_bisect(last_exc, len(group)):
        mid = len(group) // 2
        logger.info(
            "embed batching: %d text(s) failed%s — splitting into %d + %d and retrying",
            len(group),
            " (batch-shaped)" if _looks_batch_shaped(last_exc) else "",
            mid,
            len(group) - mid,
        )
        _embed_group(
            group[:mid], out, embed_many=embed_many, embed_one=embed_one, budget=budget, sleep=sleep
        )
        _embed_group(
            group[mid:], out, embed_many=embed_many, embed_one=embed_one, budget=budget, sleep=sleep
        )
        return

    # A single text that will not embed. Left as None — the caller stores a vector-less chunk,
    # which stays keyword-searchable. Logged at WARNING once per text, because the old path
    # swallowed this into `vec = None` with no record at all and that is what made an
    # unembeddable library indistinguishable from a working one.
    logger.warning(
        "embed batching: giving up on %d text(s) after %d attempt(s): %s",
        len(group),
        budget,
        last_exc,
    )


def _call(
    texts: list[str],
    *,
    embed_many: Callable[[list[str]], list[list[float] | None]] | None,
    embed_one: Callable[[str], list[float] | None] | None,
) -> list[list[float] | None]:
    """One provider call for `texts` — batched when possible, per-text otherwise."""
    if embed_many is not None:
        result = embed_many(list(texts))
        return list(result) if result is not None else []
    assert embed_one is not None  # guarded by the caller
    # The per-text fallback deliberately does NOT swallow: a failure here must reach
    # `_embed_group`'s classifier so it retries and reports, which is the behaviour the old
    # inline `except Exception: vec = None` removed.
    return [embed_one(t) for t in texts]
