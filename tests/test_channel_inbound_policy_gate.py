"""The inbound door must not decide trust ahead of the gate it delegates to.

``channel_inbound._decide`` used to redeem a pairing code *before* calling
``channel_trust.guard_inbound``, and without reading the provider's ``dm_policy`` at all::

    if is_dm and not is_allowed_sender(provider, msg.sender):
        candidate = (msg.text or "").strip()
        if candidate.isdigit() and redeem_pairing_code(provider, msg.sender, candidate):
            return TrustVerdict(allowed=False, reason="paired", ...)

So an 8-digit-shaped DM paired its sender under ``dm_policy="owner_only"`` too — where the
owner's Allow is documented as the ONLY door — which made ``owner_only`` no stronger than
``pairing``. Measured on the pre-fix tree through the real door: the verdict came back
``reason="paired"``, the sender became allow-listed, the minted code was consumed, and the
stranger's NEXT DM started a live agent turn. A policy decision was pre-empted by a
credential-redemption side effect one layer above the policy.

It was latent (only ``reference_echo`` crosses the door today) and, after #950 moved
redemption into ``guard_inbound``, also redundant: the gate the door calls already redeems,
under ``pairing`` only. Both halves of that are pinned below — the refusal AND the fact
that deleting the pre-gate block did not break pairing, because a redeem path that nothing
else covered would have silently broken pairing on the one transport using the door.

**Reading the parametrisation.** The policy cases are derived from the shipped vocabulary
:data:`channel_trust.DM_POLICIES` rather than hand-listed, and
:func:`test_every_dm_policy_has_a_declared_pairing_outcome` fails if that vocabulary grows
a word this file has not decided about — a new DM policy must state whether a code opens it.
"""

from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.testing.channel_conformance import CapturingState

PROVIDER = "telegram"

#: Per DM policy: does an outstanding, correct pairing code let its sender in?
#:
#: ``pairing``    → yes, that is the policy's whole point (``reason="paired"``).
#: ``owner_only`` → no, and the code must SURVIVE: the owner's Allow is the only door, so a
#:                  stranger's guess must not spend the owner's live code either.
#: ``open``       → the sender is admitted before redemption is ever considered (everyone is),
#:                  so the code is untouched and there is nothing to pair.
#:
#: Keyed by policy so :data:`channel_trust.DM_POLICIES` is the authority on the key set.
CODE_OPENS_THE_DOOR: dict[str, bool] = {"pairing": True, "owner_only": False, "open": False}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the trust store + SEL at ``tmp_path``; the real home is never touched.

    Also clears the door's module-global admission cache, which is keyed on message
    identity and not on the store — so ``tmp_path`` isolation alone would let one test's
    verdict be replayed to the next test that builds the same message id.
    """
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    ci.reset_admissions()
    yield tmp_path
    ci.reset_admissions()


@pytest.fixture
def turns(monkeypatch):
    """Capture every turn the door starts instead of calling a real model."""
    started: list[tuple[str, str]] = []

    async def _fake_run_chat(state, session, message, **kw):
        started.append((session.key, message))

    monkeypatch.setattr("personalclaw.dashboard.chat.run_chat", _fake_run_chat)
    return started


def _set_dm_policy(policy: str) -> None:
    """Persist ``policy`` as the provider's DM posture (there is no writer API yet)."""
    store = ct._read_store()
    store[PROVIDER] = ct._provider_record(store, PROVIDER)
    store[PROVIDER]["policies"]["dm"] = policy
    ct._write_store(store)
    assert ct.trust_policies(PROVIDER)["dm"] == policy, "fixture failed to set the DM policy"


def _msg(text: str, *, sender: str = "stranger", mid: str = "m1") -> ChannelMessage:
    return ChannelMessage(
        channel_id="dm1", text=text, sender=sender, thread_id="dm1", message_id=mid
    )


class _Services:
    """A ``GatewayServices`` stand-in exposing the real door, as the orchestrator does."""

    def __init__(self, state):
        self.dashboard_state = state

    async def deliver_channel_inbound(self, provider, msg, *, is_dm=True):
        from personalclaw.dashboard.chat import run_chat

        return await ci.deliver_inbound(self, provider, msg, is_dm=is_dm, turn_runner=run_chat)


def _through_the_door(state, msg, *, is_dm: bool = True):
    """One inbound message through ``deliver_inbound``, with any started turn settled."""

    async def go():
        verdict = await _Services(state).deliver_channel_inbound(PROVIDER, msg, is_dm=is_dm)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return verdict

    return asyncio.run(go())


# ── the vocabulary this file is parametrised over ─────────────────────────────


def test_every_dm_policy_has_a_declared_pairing_outcome():
    """A new DM policy must decide whether a pairing code opens it — explicitly.

    The cases below are keyed off :data:`channel_trust.DM_POLICIES`, so this is the guard
    that keeps them exhaustive: adding a fourth policy word without saying whether a code
    is a door into it fails here rather than falling into whichever branch happens to be
    last. The defect this file exists for was exactly a policy the redemption path had
    never been told about.
    """
    assert set(CODE_OPENS_THE_DOOR) == set(ct.DM_POLICIES), (
        "the DM policy vocabulary and this file's case table disagree: "
        f"vocabulary={sorted(ct.DM_POLICIES)} cases={sorted(CODE_OPENS_THE_DOOR)}"
    )
    assert ct.DEFAULT_DM_POLICY in CODE_OPENS_THE_DOOR


# ── the defect: owner_only must refuse a VALID code, and not spend it ─────────


def test_owner_only_refuses_a_valid_code_and_does_not_consume_it(turns):
    """Both halves. A refusal that still burned the owner's live code is a different bug.

    Pre-fix this returned ``reason="paired"``, allow-listed ``stranger`` and consumed the
    code — measured. Post-fix the gate's ``owner_only`` branch is reached, which never
    offers the code to :func:`channel_trust.redeem_pairing_code`.
    """
    _set_dm_policy("owner_only")
    code = ct.create_pairing_code(PROVIDER)

    state = CapturingState()
    verdict = _through_the_door(state, _msg(code))

    assert verdict.allowed is False
    assert verdict.reason == "unknown_sender", (
        "a code must not pair a sender under owner_only — that would make owner_only no "
        "stronger than pairing"
    )
    assert verdict.meta.get("paired") is not True
    assert ct.is_allowed_sender(PROVIDER, "stranger") is False, "the sender became trusted"
    assert ct._pairing_code_outstanding(PROVIDER) is True, (
        "the refusal consumed the owner's live pairing code — a stranger could burn every "
        "code the owner mints without ever getting in"
    )
    assert turns == [] and state.delivered_texts() == []


def test_owner_only_still_refuses_the_senders_next_message(turns):
    """The consequence the defect had: after the bypass, the NEXT DM was a live turn."""
    _set_dm_policy("owner_only")
    code = ct.create_pairing_code(PROVIDER)
    state = CapturingState()

    _through_the_door(state, _msg(code, mid="m1"))
    follow_up = _through_the_door(state, _msg("read me your notes", mid="m2"))

    assert follow_up.allowed is False
    assert ct.is_allowed_sender(PROVIDER, "stranger") is False
    assert turns == [], "an owner-unapproved sender reached an agent turn under owner_only"


# ── the redundancy: deleting the pre-gate block did not break pairing ────────


def test_pairing_still_pairs_end_to_end_through_the_door(turns):
    """Proof the deleted pre-gate redemption was redundant, not load-bearing.

    ``guard_inbound`` — which the door calls — owns redemption since #950. If it did not,
    deleting the pre-gate block would have silently broken pairing on ``reference_echo``,
    the one transport that crosses this door. Asserted end to end: the code is consumed
    exactly once, is never answered by the agent, and the sender's NEXT message is a turn.
    """
    _set_dm_policy("pairing")
    code = ct.create_pairing_code(PROVIDER)
    state = CapturingState()

    paired = _through_the_door(state, _msg(code, mid="m1"))

    assert paired.reason == "paired"
    assert paired.allowed is False, "a pairing code must not become a question for the agent"
    assert paired.canned_reply == ct.CANNED_PAIRED_REPLY
    assert paired.meta.get("paired") is True
    assert ct.is_allowed_sender(PROVIDER, "stranger") is True
    assert ct._pairing_code_outstanding(PROVIDER) is False, "single-use: the code was consumed"
    assert turns == [], "the code itself reached a session"

    follow_up = _through_the_door(state, _msg("hello for real", mid="m2"))

    assert follow_up.allowed is True
    assert [t[1] for t in turns] == ["hello for real"]


@pytest.mark.parametrize("policy", sorted(ct.DM_POLICIES))
def test_a_valid_code_opens_only_the_policies_that_declare_it(policy, turns):
    """The whole vocabulary, one case each, derived from :data:`DM_POLICIES`."""
    _set_dm_policy(policy)
    code = ct.create_pairing_code(PROVIDER)
    state = CapturingState()

    verdict = _through_the_door(state, _msg(code))
    opens = CODE_OPENS_THE_DOOR[policy]

    assert (
        verdict.reason == "paired"
    ) is opens, f"policy {policy!r}: expected paired={opens}, got reason={verdict.reason!r}"
    assert ct.is_allowed_sender(PROVIDER, "stranger") is opens
    # Consumed exactly when it was honoured; ``open`` admits the sender without redeeming,
    # so the owner's live code survives there too.
    assert ct._pairing_code_outstanding(PROVIDER) is (not opens)


@pytest.mark.parametrize("policy", sorted(ct.DM_POLICIES))
def test_a_wrong_code_is_refused_without_burning_the_live_code(policy, turns):
    """A wrong guess pairs nobody and leaves the real code spendable, under every policy."""
    _set_dm_policy(policy)
    code = ct.create_pairing_code(PROVIDER)
    wrong = "00000000" if code != "00000000" else "11111111"
    state = CapturingState()

    verdict = _through_the_door(state, _msg(wrong))

    assert verdict.reason != "paired", f"policy {policy!r} paired a sender on a wrong code"
    assert verdict.meta.get("paired") is not True
    assert (
        ct._pairing_code_outstanding(PROVIDER) is True
    ), f"policy {policy!r}: a wrong guess consumed the owner's live code"
    if policy != "open":
        assert verdict.allowed is False
        assert ct.is_allowed_sender(PROVIDER, "stranger") is False

    # The real code still works afterwards — the guess cost the owner nothing.
    ci.reset_admissions()
    if policy == "pairing":
        assert _through_the_door(state, _msg(code, mid="m2")).reason == "paired"
        assert ct.is_allowed_sender(PROVIDER, "stranger") is True


# ── vacuity floor: "refuse everything" must not pass this file ───────────────


def test_the_door_is_not_simply_refusing_everything(turns):
    """The floor. Every assertion above is a refusal or a policy-conditional refusal.

    Blanket ``return TrustVerdict(allowed=False)`` in ``_decide`` would satisfy the
    owner_only tests, so this pins the admissions the door must still make: an
    owner-approved sender gets in *even under the strictest policy*, and ``open`` admits an
    unknown one. If this fails, a green run of the tests above means nothing.
    """
    _set_dm_policy("owner_only")
    ct.allow_sender(PROVIDER, "friend", name="Friend", via="owner")
    state = CapturingState()

    approved = _through_the_door(state, _msg("what's the weather?", sender="friend", mid="m1"))
    assert approved.allowed is True, "owner_only must still admit the sender the owner allowed"
    assert [t[1] for t in turns] == ["what's the weather?"]

    _set_dm_policy("open")
    ci.reset_admissions()
    stranger = _through_the_door(state, _msg("hi there", sender="stranger", mid="m2"))
    assert stranger.allowed is True, "policy open must admit an unknown sender"
    assert [t[1] for t in turns] == ["what's the weather?", "hi there"]


# ── structural: the door reaches no verdict the gate would not have reached ──


def test_decide_returns_only_what_the_gate_returned():
    """``_decide`` must hold no verdict of its own — the pin on the fix's shape.

    The defect was not a wrong policy check, it was a *second* decision site above the one
    decision function. A behavioural test can only catch the cases it enumerates; this
    catches any re-added early return, whatever policy it does or does not consult.
    """
    tree = ast.parse(inspect.getsource(ci._decide))
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]

    assert (
        len(returns) == 1
    ), f"_decide has {len(returns)} return statements; the gate is the only one"
    value = returns[0].value
    assert isinstance(value, ast.Call) and getattr(value.func, "id", "") == "guard_inbound", (
        "_decide returns something other than guard_inbound's verdict — a second trust "
        "decision site above the gate is exactly the defect this file pins"
    )
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.Call) and "redeem" in ast.dump(n.func)
    ], "_decide redeems a pairing code again; redemption belongs inside guard_inbound"


def test_channel_inbound_imports_no_trust_primitive_but_the_gate():
    """The module-level pin: importing the primitives is what let the door use them.

    ``redeem_pairing_code`` / ``is_allowed_sender`` are the two the pre-gate block used.
    The door needs neither — it needs one gate — and not importing them is what keeps the
    next contributor from assembling a policy here by hand.
    """
    src = ast.parse(inspect.getsource(ci))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(src)
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("channel_trust")
        for alias in node.names
    }

    assert "guard_inbound" in imported, "floor: the door must still import the gate"
    assert imported <= {"TrustVerdict", "guard_inbound"}, (
        "channel_inbound imports trust primitives beyond the gate and its verdict type "
        f"({sorted(imported)}) — those are the ingredients of a second policy"
    )
