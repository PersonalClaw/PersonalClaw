"""What a chat turn that just ended teaches, before the next one: the learning gate's one
decision for the turn, the after-turn review it permits (with the stumble refinement and the
staging log's record of the pass), and the background skill-ladder review.

``chat_runner`` calls :func:`_learn_from_turn` once a turn ends. Moved out of ``chat_runner``
unchanged, so that module keeps the turn itself.
"""

from __future__ import annotations

import logging

from personalclaw import memory_locality, memory_writes
from personalclaw.dashboard.chat_persistence import save_session_to_history
from personalclaw.dashboard.chat_session_map import stamp_learned
from personalclaw.dashboard.chat_utils import _agent_label
from personalclaw.declined_calls import declined_a_memory_change
from personalclaw.own_words import own_words
from personalclaw.security import redact_credentials, redact_exfiltration_urls
from personalclaw.turn_source import by_turn, taught_by

logger = logging.getLogger(__name__)


def learning_decision_for_turn(
    session, user_message: str, tool_calls: int, cfg=None, *, memory_refused: bool = False
):
    """The turn's single gate decision, for every capture path to share.

    ``user_message`` is what the person typed this turn (``own_words``), not the message the model
    was sent; ``memory_refused`` says she refused a change to her memory in it (the gate's
    ``MEMORY_REFUSED``). Exists as its own function so the two reviews in one turn consume ONE
    object. Threaded as an argument rather than stashed on the session: ``_ChatSession``
    defines ``__slots__``, so an attribute would raise at runtime — and passing it
    explicitly makes the sharing visible at the call site instead of implicit in
    object state.
    """
    from personalclaw import after_turn_review as atr
    from personalclaw.config.loader import AppConfig
    from personalclaw.learning import Cadence, LearningGate

    if cfg is None:
        cfg = AppConfig.load().learning
    gate = LearningGate.for_session(session, cfg, memory_refused=memory_refused)
    # Permission is settled without reading the message at all. Classifying the
    # text of a restricted session — even with a free regex — inspects content
    # that the session's memory_mode promised was out of scope for learning.
    provisional = gate.decide(Cadence.PER_TURN, tool_calls=tool_calls or 0)
    if not provisional.permitted:
        return provisional
    return gate.decide(
        Cadence.PER_TURN,
        correction=atr.is_correction_signal(user_message),
        tool_calls=tool_calls or 0,
    )


def _announce_learned(state, session, origin: str, text: str, ref: str = "") -> dict:
    """Say what this turn learned: the live chip, and the record the turn keeps of it.

    Returns the record :func:`stamp_learned` persists on the turn, so a reload — or a
    restart that dropped the live event — still shows it. ``ref`` is what undoing it
    needs (a facet's key); the text is masked once, here, for both.
    """
    label, _ = redact_credentials(redact_exfiltration_urls(text[:200])[0])
    # `origin`: every learned-chip capture shares `kind: "learned"`, so without a
    # discriminator the frontend cannot route a tap on the chip to the surface that can
    # approve, edit or undo THAT artifact.
    event = {
        "session": session.key,
        "kind": "learned",
        "origin": origin,
        "text": f"Learned: {label}",
    }
    if ref:
        event["ref"] = ref
    state.broadcast_ws("activity_event", event)
    return {"origin": origin, "text": label, **({"ref": ref} if ref else {})}


def _maybe_after_turn_review(
    state,
    session,
    user_message: str,
    assistant_text: str,
    tool_calls: int,
    provider=None,
    decision=None,
    *,
    its_work_teaches: bool = True,
) -> bool:
    """Run the after-turn self-improvement review when the turn warrants it.

    ``user_message`` is what the person typed this turn (``own_words``): every capture below reads
    only that. Eligibility comes from ONE :class:`LearningGate` decision, computed by
    :func:`learning_decision_for_turn` and consumed by both the cheap facet capture
    and the expensive review — and by the skill-ladder review, which is handed the
    same object. Two independent computations of one rule is how they drift.

    ``its_work_teaches`` is whether what the turn did (its tool outcomes, read by procedural memory,
    the self-model observer and the stumble refinement) teaches too (:func:`_learn_from_turn`).
    When not, its outcomes still come off the agent, unread, or the next turn would learn them.

    The actual capture is best-effort and synchronous-but-cheap (a heuristic + a
    guarded write_lesson — no LLM call in this path). Surfaces a 'Learned: …' chip and
    records it on the turn; returns whether it did, because this runs after the turn's
    save and the caller must save again for the record to outlive a reload.
    """
    from personalclaw import after_turn_review as atr
    from personalclaw.config.loader import AppConfig

    # When what the turn did teaches nothing, its outcomes come off the agent here, unread.
    drain = getattr(provider, "drain_tool_outcomes", None)
    if not its_work_teaches and callable(drain):
        try:
            drain()
        except Exception:
            logger.debug("tool outcome drain failed", exc_info=True)
        drain = None
    cfg = AppConfig.load().learning
    # The gate owns the whole permission question — config, ephemeral, AND the
    # incognito/temporary registry. Restricted sessions promise "no memory
    # writes", so a denial here suppresses every capture path below it.
    if decision is None:
        decision = learning_decision_for_turn(session, user_message, tool_calls, cfg)
    if not decision.permitted:
        # Every negative decision leaves a row. A permission denial means
        # NOTHING below this line runs, so without a record an owner cannot tell
        # a config-off session from a broken capture path.
        from personalclaw.learning import record_denial

        record_denial(decision)
        return False
    correction = atr.is_correction_signal(user_message)
    from personalclaw.memory_service import service_for

    # The memory this chat's turns read (``memory_locality.chat_folder``), handed over with its
    # record store: what a folder chat learns stays in its folder, kept before any embedding model
    # is bound.
    folder = memory_locality.chat_folder(session) or None
    memory = state.context_builder.get_memory_for(folder, writes=True)
    svc = service_for(memory)
    # Preference-facet capture is a cheap no-LLM heuristic and the passive-learning
    # core — it must run on EVERY (non-ephemeral) turn, NOT be gated behind the
    # expensive-review threshold (≥N tools / correction). A standing style preference
    # ("keep your answers concise") does no tool work and isn't a correction, so
    # gating it there silently dropped the common case. Run it first, unconditionally.
    facet_learned = atr.capture_preference_facet(svc, user_message)
    # Glossary capture rides the same pre-gate position for the same reason: "by CR I mean a
    # code review" does no tool work and is not a correction, so behind the `worthwhile`
    # threshold it would never fire. No chip — the captured term is visible where the user can
    # edit or delete it (Settings → Memory → Slots → Glossary), and a chip that linked
    # anywhere else would be the wrong surface.
    atr.capture_glossary_term(svc, user_message)
    # What this turn learned, said live AND kept on the turn: a chip that only rode the socket
    # was gone after a reload or a restart, so a preference could be saved with nothing on the
    # page ever saying so. A veto the detector finds is a LESSON, so its chip says lesson.
    announced: list[dict] = []
    surface = getattr(cfg, "surface_chip", True)
    if facet_learned and surface:
        announced.append(
            _announce_learned(
                state, session, facet_learned.origin, facet_learned.text, facet_learned.ref
            )
        )
    # The expensive review (procedural drain + correction→lesson) needs the strict
    # answer — `worthwhile`, not just `permitted`. Same decision object as the
    # cheap path above, so the two cannot disagree about this turn.
    if not decision.worthwhile:
        # Permitted but below this cadence's cost threshold. Recorded too: the
        # cheap facet path above already ran, so "no expensive review happened"
        # is a real, explainable outcome rather than silence.
        from personalclaw.learning import record_denial

        record_denial(decision)
        return stamp_learned(session, announced)
    # Procedural memory: drain this turn's tool outcomes into how-to-work priors.
    # BOTH runtimes accumulate them now — the native ReAct loop from inside its own
    # dispatch, and the ACP providers from the translated event stream (see
    # acp/outcomes.py). Before that, `getattr` missed on every ACP provider and a
    # six-tool-call ACP turn produced zero procedural rows. The provider is the
    # ModelProvider returned by get_or_create (threaded in by the caller) — the
    # dashboard session has no `.provider` attribute, so reading it off the session
    # silently no-oped this whole class.
    #
    # Drained ONCE and shared: `drain_tool_outcomes` clears the accumulator, so a second reader
    # would see an empty list. Procedural memory and the self-model observer both need this turn's
    # (tool, failed) tuples, so they read the one drained copy (already gone, unread, when what
    # the turn did teaches nothing).
    tool_outcomes: list[tuple[str, str]] = []
    if callable(drain):
        try:
            tool_outcomes = list(drain() or [])
            atr.record_procedural_outcomes(svc, tool_outcomes, scope_ref=folder)
        except Exception:
            logger.debug("procedural outcome capture failed", exc_info=True)
    learned = atr.run_after_turn_review(
        service=svc,
        user_message=user_message,
        assistant_text=assistant_text,
        correction=correction,
        capture_facets=False,  # already captured before the gate, above
    )
    if learned and surface:
        announced.append(_announce_learned(state, session, "lesson", learned))
    # Self-model observer: the ONLY learning path that learns from what quietly
    # WORKS. Runs on the SAME `worthwhile` gate as the review above (a "significant turn"), reusing
    # its already-computed answer — a second heuristic here is how two capture paths in one turn
    # drift. The turn's route is its agent, its tools are the distinct drained tool names, and the
    # turn SUCCEEDED when no tool failed; `correction` is this turn read as the reaction to the
    # PREVIOUS turn's parked work (the reaction is not observable until the user's next move).
    if its_work_teaches and getattr(cfg, "self_model_enabled", True):
        try:
            from personalclaw.learning import self_model_observer

            self_model_observer.observe_turn(
                svc,
                session_key=str(getattr(session, "key", "") or ""),
                route=_agent_label(session),
                tools=tuple(sorted({t for t, _outcome in tool_outcomes})),
                # A turn SUCCEEDED when every tool outcome was `success`. Same verdict
                # the `not any(failed)` form gave: a denial used to arrive as
                # failed=True, and being refused is still not a clean success.
                succeeded=all(outcome == "success" for _t, outcome in tool_outcomes),
                correction=correction,
            )
        except Exception:
            logger.debug("self-model observer failed", exc_info=True)
    if its_work_teaches:
        _maybe_refine_stumble(state, session, user_message, assistant_text, tool_outcomes, cfg)
    _stage_turn_capture(
        session, user_message, learned or (facet_learned.text if facet_learned else None), cfg
    )
    return stamp_learned(session, announced)


def _maybe_refine_stumble(
    state,
    session,
    user_message: str,
    assistant_text: str,
    tool_outcomes: list[tuple[str, str]],
    cfg,
) -> None:
    """The stumble refinement arm: a turn that USED a skill and still went wrong proposes a refine.

    Runs on the same permitted turn as the memory review above (its caller already returned on
    a denied gate) and behind the same ``skill_ladder`` flag, deliberately: that flag is the
    user's answer to "may this system propose skills from my turns?", and a second config knob
    for the deterministic half of the same queue would let the two answers disagree.

    ``session._skills_used`` and not the ladder's ``loaded_skills``: the latter is the
    CANDIDATE index (every indexed skill), so a refine target picked from it would name a skill
    that had no part in the turn. ``_skills_used`` is the narrowing to the allocations
    whose content actually reached the prompt, which is the same list the turn-time
    ``record_uses`` counter consumes — so "used" cannot mean two things here.

    Synchronous and model-free (a classifier plus a ``difflib`` diff), so it cannot delay the
    turn and has no degraded path other than proposing nothing. Never raises into the turn.
    """
    if not getattr(cfg, "skill_ladder", True):
        return
    try:
        from personalclaw import after_turn_review as atr
        from personalclaw.skills import refine

        used = [
            str(s.get("name") or "")
            for s in (getattr(session, "_skills_used", None) or [])
            if isinstance(s, dict) and s.get("name")
        ]
        # Explicit, not incidental. ``detect_stumble`` refuses an empty set too, but without this
        # line the only thing stopping ``used[0]`` below was the ``except`` — so removing the
        # detector's own guard would have left the call site silent for the wrong reason, and a
        # test of that silence would have been pinning an IndexError. It did.
        if not used:
            return
        signal = atr.detect_stumble(
            user_message=user_message,
            assistant_text=assistant_text,
            used_skills=used,
            tool_outcomes=tool_outcomes,
        )
        if signal is None:
            return
        # One stumble files one proposal against one target; refining everything that happened
        # to load would turn one bad turn into N proposals. A denied or retried CALL goes to the
        # first used skill whose procedure names it — the one that asked for it — and to none when
        # no skill did: a skill that joined the turn on a matching word never asked the agent for
        # the call. Anything else goes to the first used skill, in the allocator's admission order.
        target = refine.refine_target(signal.trigger, signal.detail, used)
        if not target:
            return
        prop = refine.propose_refinement(
            trigger=signal.trigger,
            detail=signal.detail,
            skill=target,
            user_message=user_message,
            session_key=str(getattr(session, "key", "") or ""),
        )
    except Exception:
        logger.debug("stumble refinement arm failed", exc_info=True)
        return
    if prop is None or not getattr(cfg, "surface_chip", True):
        return
    # The SAME learned-chip emitter the turn's own chips use, with `origin: "proposal"` — so the
    # chip's tap-through already lands on `#/skills?mode=proposals`, where the diff renders. A new
    # channel or a new origin would be a second idiom for an answer this one already gives.
    label, _ = redact_credentials(redact_exfiltration_urls(f"Proposed refinement: {prop.slug}")[0])
    state.broadcast_ws(
        "activity_event",
        {"session": session.key, "kind": "learned", "origin": "proposal", "text": label},
    )


def _stage_turn_capture(session, user_message: str, learned: str | None, cfg) -> None:
    """Record this per-turn pass in the staging log — outcome included.

    Runs whether or not anything was captured, which is the entire point: a pass
    that found nothing and a pass that crashed used to look identical from
    outside. The hygiene policy is applied here too, so a fenced payload that
    reached the extractor still cannot reach the durable log.

    Never raises into the turn. A staging failure must not cost the user their
    reply — but it IS logged, because a silently broken observability floor is
    worse than none.
    """
    if not getattr(cfg, "staging_enabled", True):
        return
    try:
        from personalclaw.learning import Cadence, scrub
        from personalclaw.learning.staging import get_store

        store = get_store()
        with store.flush(Cadence.PER_TURN.value) as result:
            if learned:
                verdict = scrub(learned)
                if verdict.usable and store.stage(
                    cadence=Cadence.PER_TURN.value,
                    kind="lesson",
                    content=verdict.text,
                    session_key=str(getattr(session, "key", "") or ""),
                    meta={"removed": verdict.removed},
                ):
                    result["staged"] = 1
                elif verdict.removed:
                    result["detail"] = "hygiene: " + ",".join(verdict.removed)
    except Exception:
        logger.debug("learning staging failed", exc_info=True)


def _maybe_skill_ladder_review(
    state, session, user_message: str, assistant_text: str, tool_calls: int, decision=None
) -> None:
    """Schedule the forked-LLM 4-tier skill-ladder review (the after-turn review's skill
    axis) as a background task — non-blocking, never delays the next turn.
    ``user_message`` is what the person typed this turn (``own_words``).

    Consumes the SAME gate ``decision`` the memory review used (passed in by the
    caller; recomputed only if this runs standalone), plus its own ``skill_ladder``
    cadence flag. Every skill it decides on is ENQUEUED as a propose-only proposal
    (never a live write). Best-effort; a 'Proposed skill: …' chip surfaces if
    something lands."""
    import asyncio

    from personalclaw import after_turn_review as atr
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load().learning
    if decision is None:
        decision = learning_decision_for_turn(session, user_message, tool_calls, cfg)
    # The ladder is an expensive LLM pass: it needs the strict answer AND its own
    # cadence flag. `permitted` alone would run a forked model on every turn.
    #
    # Checked BEFORE anything reads `user_message`: a restricted session promised
    # that its content feeds no learning, and inspecting the message to classify
    # it is already a read of content that should have been out of scope.
    if not decision.allowed or not getattr(cfg, "skill_ladder", True):
        return
    # Candidate skills to bias refinement toward (the always-on + indexed set).
    try:
        loaded = [s["key"] for s in state.context_builder.skills.list_skills()][:40]
    except Exception:
        loaded = []

    async def _run() -> None:
        try:
            summary = await atr.run_skill_ladder_review(
                session_key=session.key,
                user_message=user_message,
                assistant_text=assistant_text,
                loaded_skills=loaded,
            )
        except Exception:
            logger.debug("skill-ladder review failed", exc_info=True)
            return
        if summary and getattr(cfg, "surface_chip", True):
            label, _ = redact_credentials(redact_exfiltration_urls(summary[:200])[0])
            state.broadcast_ws(
                "activity_event",
                {
                    "session": session.key,
                    "kind": "learned",
                    "origin": "proposal",
                    "text": label,
                },
            )

    try:
        t = asyncio.create_task(_run())
        state._background_tasks.add(t)
        t.add_done_callback(state._background_tasks.discard)
    except RuntimeError:
        logger.debug("skill-ladder review: no running loop to schedule on", exc_info=True)


def _learn_from_turn(
    state,
    session,
    row,
    assistant_text: str,
    tool_calls: int,
    *,
    provider=None,
    asked_for_by=None,
    steers=(),
) -> None:
    """What the turn that just ended teaches, before the next one: best-effort and gated. Both
    reviews read what she typed in *row*, the message that started it, and in *steers*, sent into
    it as it ran (``own_words``, joined as her queued messages are), never the message the model
    was sent, and share ONE gate decision, as two copies of one rule drift. Her words teach as hers
    wherever they sit, so the reviews run as asked for by whoever sent the words they read
    (``turn_source.taught_by``) or the work the turn carries on: a row her queued message and a
    friend's run as together still teaches her correction. What the turn did (its tools' outcomes,
    the answer the ladder drafts from) teaches only when nobody else asked for any of it
    (``memory_writes.asker``, the turn's own). A turn in which she refused a change to her memory
    teaches nothing, its words or its work (``declined_calls.declined_a_memory_change``)."""
    try:
        at = next((i for i, m in enumerate(session.messages) if m is row), None)
        refused = at is not None and declined_a_memory_change(by_turn(session.messages[at:])[0])
        typed = "" if refused else "\n\n".join(w for w in map(own_words, (row, *steers)) if w)
        its_work_teaches = not (refused or memory_writes.asker())
        words_from = (taught_by(row) if row is not None else {}) or dict(asked_for_by or {})
    except Exception:  # noqa: BLE001 - fail closed: whose words they are is unknown, so none teach
        logger.debug("the turn's learning could not tell whose words it reads", exc_info=True)
        return
    with memory_writes.learning_from_words(words_from):
        try:
            decision = learning_decision_for_turn(
                session, typed, tool_calls, memory_refused=refused
            )
        except Exception:
            logger.debug("learning gate evaluation failed", exc_info=True)
            decision = None
        try:
            # It may take a guarded lesson write after the turn's save, so a turn that learned
            # something is saved once more to keep the record of it on the turn.
            if _maybe_after_turn_review(
                state,
                session,
                typed,
                assistant_text,
                tool_calls,
                provider=provider,
                decision=decision,
                its_work_teaches=its_work_teaches,
            ):
                save_session_to_history(state, session)
        except Exception:
            logger.debug("after-turn review failed", exc_info=True)
        if not its_work_teaches:
            return
        try:  # The skill axis: a background review that may PROPOSE a skill.
            _maybe_skill_ladder_review(
                state, session, typed, assistant_text, tool_calls, decision=decision
            )
        except Exception:
            logger.debug("skill-ladder review scheduling failed", exc_info=True)
