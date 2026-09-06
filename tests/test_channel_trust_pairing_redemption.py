"""Pairing codes are redeemable at the ONE gate every channel crosses (#950).

``channel_trust`` shipped the whole pairing mechanism — an owner CLI that mints a code, a
hash-only store, TTL, single-use, constant-time compare — and then left
:func:`~personalclaw.channel_trust.redeem_pairing_code` reachable only from the platform's
inbound door, which **no shipping channel crossed**. Telegram and Discord call
``guard_inbound`` directly and never redeemed; Slack crosses no trust seam at all; only
email hand-rolled its own copy. So ``personalclaw pair <provider>`` minted codes that
nothing could spend and the canned "ask my owner for an 8-digit pairing code" reply was an
infinite loop.

The fix puts redemption inside :func:`~personalclaw.channel_trust.guard_inbound`, on the
same principle that already puts the untrusted-content fence there: a per-transport
obligation is a hope, not a property.

**Every case here carries a vacuity floor.** A refusal test that passes because the setup
never produced a redeemable code proves nothing, so each negative case also asserts the
positive it is the negation of. And because "a validator with no production call site is a
dead fix" is this project's rule, the last section asserts the redemption is reached from a
non-test caller rather than trusting that it is.
"""

from __future__ import annotations

import argparse
import ast
import inspect
from datetime import timedelta
from pathlib import Path

import pytest

from personalclaw import channel_trust as ct

PROVIDER = "telegram"
SENDER = "15216999"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Point the entity-settings store + SEL at tmp_path (real home is never touched)."""
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    yield tmp_path


class _State:
    """A DashboardState stand-in that records the owner notifications raised."""

    def __init__(self) -> None:
        self.notes: list[dict] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append({"kind": kind, "title": title, "body": body, "meta": meta or {}})


def _dm(text: str, sender: str = SENDER, provider: str = PROVIDER, state=None):
    """One inbound DM through the gate, shaped exactly as a shipping transport calls it."""
    return ct.guard_inbound(
        state if state is not None else _State(),
        provider,
        sender,
        sender_name="Stranger",
        channel_id="c1",
        is_dm=True,
        text=text,
    )


def _set_dm_policy(provider: str, policy: str) -> None:
    store = ct._read_store()
    rec = ct._provider_record(store, provider)
    rec["policies"]["dm"] = policy
    store[provider] = rec
    ct._write_store(store)


def _sel_rows():
    from personalclaw.sel import sel

    return [(e.get("operation"), e.get("outcome")) for e in sel().recent(200)]


# ── the round trip: mint → redeem at the gate → sender converses ─────────────


def test_full_round_trip_mint_redeem_then_converse():
    """The bug, end to end: a minted code sent as a DM pairs the sender at the gate.

    Vacuity floor: the SAME call with ordinary text is denied both before and after, so
    ``allowed=True`` in step 4 cannot be an artifact of an ``open`` policy or of the gate
    admitting everyone.
    """
    # Floor: the gate is genuinely fail-closed for this sender before any code exists.
    before = _dm("hello?")
    assert before.allowed is False, "vacuity floor: the gate must deny an unpaired sender"
    assert before.reason == "unknown_sender"
    assert before.canned_reply == ct.CANNED_PAIRING_REPLY
    assert ct.trust_policies(PROVIDER)["dm"] == "pairing", (
        "vacuity floor: this suite must run under the default 'pairing' policy — "
        "under 'open' every assertion below would pass for the wrong reason"
    )

    code = ct.create_pairing_code(PROVIDER)

    # The redemption itself, at the gate a shipping transport already calls.
    v = _dm(code)
    assert v.allowed is False, "the code message is spent on pairing, not answered"
    assert v.reason == "paired"
    assert v.canned_reply == ct.CANNED_PAIRED_REPLY
    assert v.meta.get("paired") is True
    assert ct.is_allowed_sender(PROVIDER, SENDER) is True, (
        "THE BUG: a code minted by `personalclaw pair` must be redeemable through the "
        "one gate every transport crosses"
    )

    # The code is consumed, and provenance records that pairing (not the owner) did it.
    projection = ct.provider_trust(PROVIDER)
    assert projection["pairing_active"] is False
    assert [s["via"] for s in projection["allowed_senders"]] == ["pairing"]

    # And the NEXT message is a real turn — the conversation proceeds.
    after = _dm("what's on my calendar?")
    assert after.allowed is True and after.reason == "allowed"

    # Audit: the successful pairing is on the SEL as sender_paired/pairing.
    assert ("sender_paired", "pairing") in _sel_rows()


def test_round_trip_from_the_real_owner_cli():
    """Drive the REAL ``personalclaw pair <provider>`` code path, not a store call.

    The issue's repro starts at the CLI, so the test does too: whatever
    :func:`~personalclaw.cli_commands._pair` prints to the owner's terminal must be
    spendable at the gate. Vacuity floor: the printed code is parsed out of stdout and
    asserted to be a real 8-digit code before it is spent, and a *different* 8-digit
    string is asserted NOT to pair — so the pass cannot come from the gate accepting any
    digits at all.
    """
    import io
    from contextlib import redirect_stdout

    from personalclaw.cli_commands import _pair

    buf = io.StringIO()
    with redirect_stdout(buf):
        _pair(argparse.Namespace(provider=PROVIDER))
    out = buf.getvalue()

    codes = [w for w in out.replace(":", " ").split() if w.isdigit() and len(w) == 8]
    assert (
        len(codes) == 1
    ), f"vacuity floor: the CLI must print exactly one 8-digit code; got {out!r}"
    code = codes[0]
    assert code not in str(ct._read_store()), "the plaintext code must never reach the store"

    # Floor: a wrong 8-digit code does NOT pair, so success below is about THIS code.
    wrong = f"{(int(code) + 1) % 10**8:08d}"
    assert _dm(wrong, sender="other-sender").reason == "unknown_sender"
    assert ct.is_allowed_sender(PROVIDER, "other-sender") is False

    assert _dm(code).reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, SENDER) is True


# ── refusals, each with its vacuity floor ────────────────────────────────────


def test_expired_code_is_refused_at_the_gate(monkeypatch):
    """A code past its TTL does not pair. Floor: the same code pairs before expiry."""
    # Floor — the very same code, unexpired, DOES pair (so the refusal is about the TTL).
    floor_code = ct.create_pairing_code(PROVIDER)
    assert _dm(floor_code, sender="floor-sender").reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, "floor-sender") is True

    code = ct.create_pairing_code(PROVIDER)
    real_now = ct._now()
    monkeypatch.setattr(
        ct, "_now", lambda: real_now + timedelta(seconds=ct.PAIRING_CODE_TTL_SECS + 5)
    )
    v = _dm(code)
    assert v.allowed is False
    assert v.reason == "unknown_sender", "an expired code falls through to the normal denial"
    assert v.canned_reply == ct.CANNED_PAIRING_REPLY
    assert ct.is_allowed_sender(PROVIDER, SENDER) is False
    assert ("sender_denied", "expired_code") in _sel_rows()


def test_wrong_code_is_refused_at_the_gate():
    """Wrong digits do not pair, and do not burn the live code.

    Floor: the RIGHT code still pairs afterwards, which proves the refusal was about the
    value and not about there being no active code to redeem.
    """
    code = ct.create_pairing_code(PROVIDER)
    wrong = f"{(int(code) + 1) % 10**8:08d}"

    v = _dm(wrong)
    assert v.allowed is False
    assert v.reason == "unknown_sender"
    assert ct.is_allowed_sender(PROVIDER, SENDER) is False
    assert ("sender_denied", "wrong_code") in _sel_rows()

    # Floor: the real code is untouched and still works.
    assert ct.provider_trust(PROVIDER)["pairing_active"] is True
    assert _dm(code).reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, SENDER) is True


def test_code_is_single_use_at_the_gate():
    """One code pairs exactly one sender. Floor: the FIRST redemption succeeded."""
    code = ct.create_pairing_code(PROVIDER)

    first = _dm(code, sender="first-sender")
    assert first.reason == "paired", "vacuity floor: the first redemption must succeed"
    assert ct.is_allowed_sender(PROVIDER, "first-sender") is True

    second = _dm(code, sender="second-sender")
    assert second.allowed is False
    assert second.reason == "unknown_sender"
    assert second.canned_reply == ct.CANNED_PAIRING_REPLY
    assert (
        ct.is_allowed_sender(PROVIDER, "second-sender") is False
    ), "a redeemed code MUST NOT admit a second sender"
    # The spent code left nothing outstanding, so the second sender is not even a
    # redemption attempt — they take the ordinary unknown-sender path.
    assert ct.provider_trust(PROVIDER)["pairing_active"] is False
    assert ("sender_denied", "unknown_sender") in _sel_rows()
    assert ("sender_denied", "no_active_code") not in _sel_rows()


def test_a_new_code_kills_the_previous_one_at_the_gate():
    """Only the newest code is live. Floor: the newest one does pair."""
    old = ct.create_pairing_code(PROVIDER)
    new = ct.create_pairing_code(PROVIDER)

    assert _dm(old, sender="old-sender").reason == "unknown_sender"
    assert ct.is_allowed_sender(PROVIDER, "old-sender") is False
    # Floor: the replacement genuinely works.
    assert _dm(new, sender="new-sender").reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, "new-sender") is True


# ── the gate must not become a wider door ────────────────────────────────────


def test_owner_only_policy_is_not_bypassable_by_a_code():
    """Under ``owner_only`` a code is NOT a second door — only the owner's Allow admits.

    Floor: the identical code + sender DO pair under ``pairing``, so the refusal is
    attributable to the policy and not to a broken code.
    """
    _set_dm_policy(PROVIDER, "owner_only")
    code = ct.create_pairing_code(PROVIDER)
    v = _dm(code)
    assert v.allowed is False
    assert v.reason == "unknown_sender"
    assert v.canned_reply == "", "owner_only stays silent in-channel"
    assert (
        ct.is_allowed_sender(PROVIDER, SENDER) is False
    ), "a pairing code MUST NOT widen owner_only into pairing"
    # The code is NOT consumed either — refusing is not the same as spending it.
    assert ct.provider_trust(PROVIDER)["pairing_active"] is True

    # Floor: flip the policy back and the very same code pairs the very same sender.
    _set_dm_policy(PROVIDER, "pairing")
    assert _dm(code).reason == "paired"
    assert ct.is_allowed_sender(PROVIDER, SENDER) is True


def test_an_already_allowed_senders_digits_are_a_normal_message():
    """An approved sender typing 8 digits sends a MESSAGE; their code is not spent.

    Floor: the outstanding code is still redeemable by someone else afterwards.
    """
    ct.allow_sender(PROVIDER, SENDER, name="Alice", via="owner")
    code = ct.create_pairing_code(PROVIDER)

    v = _dm(code)
    assert v.allowed is True and v.reason == "allowed"
    assert (
        ct.provider_trust(PROVIDER)["pairing_active"] is True
    ), "an allowed sender's message must never consume the outstanding code"
    # Floor: the code was genuinely live and still is.
    assert _dm(code, sender="someone-else").reason == "paired"


def test_open_policy_does_not_consume_the_code():
    """Under ``open`` everyone is admitted, so nothing is a code. Floor: it stays live."""
    _set_dm_policy(PROVIDER, "open")
    code = ct.create_pairing_code(PROVIDER)
    assert _dm(code).allowed is True
    assert ct.provider_trust(PROVIDER)["pairing_active"] is True
    # Floor: the untouched code still pairs once the policy tightens.
    _set_dm_policy(PROVIDER, "pairing")
    assert _dm(code, sender="later-sender").reason == "paired"


def test_group_messages_never_redeem():
    """A group/room message is not a pairing channel. Floor: the DM path does redeem."""
    ct.track(PROVIDER, "room1", name="Room")
    code = ct.create_pairing_code(PROVIDER)
    v = ct.guard_inbound(_State(), PROVIDER, SENDER, channel_id="room1", is_dm=False, text=code)
    assert (
        ct.is_allowed_sender(PROVIDER, SENDER) is False
    ), "shouting a code into a shared room must not pair the shouter"
    assert v.reason == "tracked_channel"
    assert ct.provider_trust(PROVIDER)["pairing_active"] is True
    # Floor: the same code in a DM pairs.
    assert _dm(code).reason == "paired"


@pytest.mark.parametrize(
    "text",
    [
        "1234567",  # too short
        "123456789",  # too long
        "1234 5678",  # spaced
        "code: 12345678",  # embedded, not the whole message
        "abcdefgh",  # not digits
        "١٢٣٤٥٦٧٨",  # non-ASCII digits: isdigit() is True but can never match a hash
        "",  # empty
    ],
)
def test_non_code_shaped_text_is_not_treated_as_a_code(text):
    """Only the exact minted shape is a redemption ATTEMPT.

    Denial alone is too weak an assertion: text that merely fails to match would be denied
    anyway. What the shape check buys is that impossible text never reaches
    ``redeem_pairing_code`` at all — so it writes no ``wrong_code`` audit row, and a
    stranger cannot spam arbitrary digit strings into the SEL while a code is outstanding.

    Floor: the correctly-shaped code pairs in the same store state, so these refusals are
    about the *shape* and not about redemption being switched off.
    """
    code = ct.create_pairing_code(PROVIDER)
    v = _dm(text)
    assert v.reason == "unknown_sender"
    assert ct.is_allowed_sender(PROVIDER, SENDER) is False
    assert ct.provider_trust(PROVIDER)["pairing_active"] is True
    assert ("sender_denied", "wrong_code") not in _sel_rows(), (
        f"{text!r} cannot possibly be a pairing code, so it must not be offered to "
        "redeem_pairing_code — that is a free audit-row write for any stranger"
    )
    # Floor: a correctly-shaped code IS offered, and does pair.
    assert _dm(code, sender="shape-floor").reason == "paired"


def test_no_outstanding_code_means_no_redemption_attempt_and_no_audit_flood():
    """Digit-spam with no code minted writes no per-message audit rows.

    An unpaired stranger must not be able to drive an unbounded stream of SEL rows by
    sending digit strings — that is the same flood the renotify window prevents on the
    notification side. Floor: with a code outstanding, a wrong guess DOES get audited, so
    this is about the no-code case rather than auditing being broken.
    """
    for _ in range(5):
        _dm("12345678")
    assert ("sender_denied", "no_active_code") not in _sel_rows()

    # Floor: once a code exists, a wrong attempt is audited.
    ct.create_pairing_code(PROVIDER)
    _dm("00000000", sender="guesser")
    assert ("sender_denied", "wrong_code") in _sel_rows()


def test_first_contact_still_notifies_the_owner_exactly_once():
    """The unknown-sender flow is unchanged: pairing did not displace the Allow/Deny path.

    Floor: the notification's meta still carries what the Allow button needs.
    """
    state = _State()
    _dm("hello?", state=state)
    _dm("hello again?", state=state)
    actionable = [n for n in state.notes if n["meta"].get("actions")]
    assert len(actionable) == 1, "a chatty stranger must not flood the owner"
    assert sorted(actionable[0]["meta"]["actions"]) == ["allow", "deny"]
    assert actionable[0]["meta"]["provider"] == PROVIDER
    assert actionable[0]["meta"]["sender_id"] == SENDER


# ── the fix is not dead code: it has a non-test production call site ─────────


def _src_root() -> Path:
    import personalclaw

    return Path(personalclaw.__file__).parent


def test_the_gate_reaches_redemption():
    """``guard_inbound`` itself calls ``redeem_pairing_code`` — asserted on its AST.

    A grep would match the docstring; walking the function body cannot.
    """
    tree = ast.parse(inspect.getsource(ct.guard_inbound))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "redeem_pairing_code" in called, (
        "guard_inbound MUST reach redeem_pairing_code — that call IS the fix for #950. "
        f"Calls found: {sorted(called)}"
    )


def test_redemption_has_a_non_test_production_caller():
    """The gate that redeems is reached from shipped, non-test code.

    This project's rule is that a validator with no production call site is a dead fix, and
    #950 is exactly that failure mode: ``redeem_pairing_code`` existed, was correct, and was
    reachable only from a reference transport. So the call site is asserted, not assumed:
    some module under ``src/personalclaw/`` that is neither ``channel_trust`` itself, nor a
    test-support module, nor a bare re-export must call ``guard_inbound``.
    """
    root = _src_root()
    callers: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == "channel_trust.py" or rel.startswith("testing/") or rel.startswith("sdk/"):
            continue  # the definition site, test-support, and pure re-export surfaces
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "guard_inbound"
            ):
                callers.append(rel)
                break

    assert callers, (
        "no production module calls guard_inbound, so the pairing redemption inside it is "
        "unreachable — the #950 failure mode reintroduced one layer up"
    )
    assert "channel_inbound.py" in callers, (
        "the platform's inbound door must still route through the gate; production "
        f"callers found: {callers}"
    )


def test_the_paired_reply_is_deliberately_not_on_the_sdk_surface():
    """``CANNED_PAIRED_REPLY`` is core-only until an app actually reads it.

    Re-exporting it through ``personalclaw.sdk.channel`` was the first thing I tried, and the
    inert-surface gate rejected it: ``sdk_export:CANNED_PAIRED_REPLY`` rose 106 -> 107 with no
    reader anywhere. No app needs it, because a transport renders ``verdict.canned_reply``
    generically and never names the constant. So the export is left out, and this pins that
    on purpose rather than leaving the next person to rediscover the gate. Add it in the same
    change as its first reader.
    """
    from personalclaw.sdk import channel as sdk_channel

    assert sdk_channel.CANNED_PAIRING_REPLY is ct.CANNED_PAIRING_REPLY, (
        "floor: the pairing-needed half IS on the surface (an app reads it), so a failure "
        "here means the surface moved rather than the assertion below being vacuous"
    )
    assert not hasattr(sdk_channel, "CANNED_PAIRED_REPLY"), (
        "CANNED_PAIRED_REPLY is on the SDK surface with no reader — the inert-surface gate "
        "will fail. Export it together with the app code that consumes it."
    )


def test_the_paired_reply_has_exactly_one_definition():
    """``channel_inbound`` must not carry a second, divergent 'paired' confirmation.

    ``channel_trust`` owns both halves of the pairing conversation
    (:data:`CANNED_PAIRING_REPLY` / :data:`CANNED_PAIRED_REPLY`) so every channel says the
    same thing. ``channel_inbound.PAIRED_REPLY`` predates this and is now a duplicate
    literal; this pins the two together so they cannot drift while it is collapsed. Delete
    ``channel_inbound.PAIRED_REPLY`` (and its now-redundant pre-gate redemption, which also
    bypasses the ``owner_only`` policy) and this assertion goes away with it.
    """
    from personalclaw import channel_inbound as ci

    legacy = getattr(ci, "PAIRED_REPLY", None)
    if legacy is None:
        return  # collapsed onto channel_trust — nothing left to drift
    assert legacy == ct.CANNED_PAIRED_REPLY, (
        "channel_inbound.PAIRED_REPLY has drifted from channel_trust.CANNED_PAIRED_REPLY. "
        "There must be one paired-confirmation string: import it from channel_trust."
    )
