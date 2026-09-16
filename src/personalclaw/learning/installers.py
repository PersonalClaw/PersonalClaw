"""Accept-time installation — the ONE owner of "what does accepting this proposal WRITE".

:func:`personalclaw.learning.proposals.accept` used to take the installer as an OPTIONAL
injected callable and skip the install entirely when nobody passed one. The queue module's
reason for injecting was sound — it "must not also know how to write a skill, a template and
a tier migration" — but an optional dependency every real caller has to remember is a defect
waiting to recur, and it did:

**Measured on ``origin/main`` @ ``f292be7b1``** (isolated ``PERSONALCLAW_HOME``, real gateway on
:10777, one proposal per kind enqueued through ``proposals.enqueue`` and accepted through
``POST /api/learning/proposals/{id}/accept``):

======================  ==================  =====================================================
kind                    route said          what was on disk afterwards
======================  ==================  =====================================================
``skill``               200 ``accepted``    ``skills/auto/rebuild-the-spa/SKILL.md`` — installed
``knowledge_draft``     200 ``accepted``    ``workspace/knowledge/knowledge.db`` ``items`` = 0
``retirement``          200 ``accepted``    ``skills/auto/doomed/SKILL.md`` still there
``tier_migration``      200 ``accepted``    nothing
``template``            200 ``accepted``    no definition saved
======================  ==================  =====================================================

Every one of those recorded an ``accepted`` decision, after which re-filing the identical
proposal returned ``SKIP`` / ``"already accepted"`` — the permanent suppression of a change that
was never applied, which is exactly the failure ``accept``'s own "the decision is recorded ONLY
after the install succeeds" comment exists to prevent.

**So the store resolves its installer through this module instead of asking callers to inject
one.** Two things follow, and they are the whole point:

* the dispatch has ONE owner. It used to be spelled twice — ``handlers/learning._installer_for``
  and a local default inside ``proposals_contract._apply_skill_promotion`` — and the two had
  already diverged (the second omits the self-model branch), so "which entity does Approve write"
  depended on which surface you clicked;
* a proposal nothing here can install is a **refusal**, not a silent success.
  :class:`~personalclaw.learning.proposals.NoProposalInstallerError` leaves the row PENDING and
  records no decision, so it is still in the queue — and still re-filable — once an installer
  lands.

Injection survives as an explicit OVERRIDE, because one production caller genuinely owns its own
write: ``knowledge.updates.propose_update`` closes over the item it is updating. Nothing is
forced to pass one, so nothing can forget to.

This module holds no write of its own. Every branch delegates to the module that already owns
the entity being written, which is what keeps the queue generic.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from personalclaw.learning import proposals

logger = logging.getLogger(__name__)

#: Branch names :func:`branch_for` returns. Strings rather than an enum because the caller only ever
#: logs them or compares them here — a public enum would invite a surface to switch on them.
PROMPT_CARD = "prompt_card"
PROJECT_CONTEXT = "project_context"
SKILL = "skill"
SELF_MODEL = "self_model"
#: Claimed, and writing nothing is the CORRECT outcome — see :data:`NOTHING_TO_INSTALL`.
NOTHING = "nothing"

#: Kinds whose accept legitimately writes nothing here, named individually so a kind can never
#: reach this set by accident:
#:
#: * ``lesson_batch`` — a correction-derived lesson is ALREADY in the lesson store; the queue gate
#:   exists to stop an *inferred* one writing live, not to write it a second time. (A self-model
#:   principle is a ``lesson_batch`` too, and :func:`branch_for` checks that branch first.)
#: * ``template_diff`` — applied AFTER the decision by
#:   ``handlers.learning._apply_accepted_template_diff``, which saves a new template VERSION. It
#:   is deliberately not an installer: the apply is async and reports its own ``applied`` result
#:   on the response.
NOTHING_TO_INSTALL = frozenset(
    {
        proposals.Kind.LESSON_BATCH.value,
        proposals.Kind.TEMPLATE_DIFF.value,
    }
)


def branch_for(data: dict[str, Any]) -> str:
    """Which branch owns installing the proposal record *data*, or ``""`` for none.

    Order is load-bearing and is the order the handler's dispatch already used:

    * the prompt-card branch is FIRST because it claims by TAG, and a card that mapped onto a
      ``template`` would otherwise fall through to a branch that cannot write it;
    * the self-model branch is checked before :data:`NOTHING_TO_INSTALL` because a promoted
      principle IS a ``lesson_batch`` and would otherwise be read as "nothing to write".

    Split out from :func:`install` so a caller can ask the question without performing the write:
    the route needs to know a kind is unsupported, and a predicate is cheaper to test than a
    write it has to undo.
    """
    from personalclaw.learning import project_context_review, self_model_observer, skill_promotion
    from personalclaw.packs import prompt_cards

    if not isinstance(data, dict):
        return ""
    if prompt_cards.is_prompt_card_proposal(data):
        return PROMPT_CARD
    if project_context_review.is_project_context_proposal(data):
        return PROJECT_CONTEXT
    if skill_promotion.is_skill_promotion_proposal(data):
        return SKILL
    if self_model_observer.is_self_model_proposal(data):
        return SELF_MODEL
    if str(data.get("kind") or "") in NOTHING_TO_INSTALL:
        return NOTHING
    return ""


def install(prop: Any, *, service: Any = None) -> str:
    """Install ONE accepted proposal. Returns the branch that claimed it.

    Called by :func:`~personalclaw.learning.proposals.accept` AFTER ``require_human`` — so this is
    always the human installing, the one actor §2.6 permits to write a self-model principle or a
    project-context change live.

    Raises :class:`~personalclaw.learning.proposals.NoProposalInstallerError` when no branch owns
    the proposal, which ``accept`` turns into a refusal that records no decision. Any other
    exception is a genuine install FAILURE and ``accept`` reports it the same way — either path
    leaves the proposal pending and retryable, which the injected-installer design could not.

    ``service`` is the memory service the self-model branch writes through. It is a parameter
    rather than something resolved here because the only live one is cached on the dashboard's
    state: building a second :class:`~personalclaw.memory_service.MemoryService` over the same
    files would give the process two handles on one store.
    """
    data = prop.to_dict() if hasattr(prop, "to_dict") else dict(prop)
    branch = branch_for(data)
    kind = str(data.get("kind") or "?")

    if branch == PROMPT_CARD:
        from personalclaw.packs import prompt_cards

        prompt_cards.install_accepted_prompt_card(data)
    elif branch == PROJECT_CONTEXT:
        from personalclaw.learning import project_context_review

        project_context_review.install_accepted_project_context(data)
    elif branch == SKILL:
        from personalclaw.learning import skill_promotion

        skill_promotion.install_accepted_skill(data)
    elif branch == SELF_MODEL:
        from personalclaw.learning import self_model_observer

        # A FALSE return means the principle was not written — no reachable memory store, or no
        # vector tier on this box. Refused rather than recorded: the handler used to call that
        # case a best-effort deferral, but a deferral that records an `accepted` decision
        # suppresses its own retry forever, which is the same bug in a smaller blast radius.
        if not self_model_observer.install_accepted_principle(service, data):
            raise proposals.AcceptError(
                "the self-model principle was not written (no memory store is reachable): "
                "nothing changed and the proposal is still pending"
            )
    elif branch != NOTHING:
        raise proposals.NoProposalInstallerError(
            f"nothing installs a {kind!r} proposal yet: nothing was changed and the proposal is "
            "still pending"
        )

    logger.debug("accept install: %s proposal took the %r branch", kind, branch)
    return branch


def installer_for(*, service: Any = None) -> Callable[[Any], None]:
    """The accept-time installer, bound to *service*.

    The shape ``accept`` expects — one positional proposal, return value ignored — so the store
    resolves this by default and no caller has to remember an argument.
    """

    def _install(prop: Any) -> None:
        install(prop, service=service)

    return _install
