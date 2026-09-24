"""TARGET POLICY for desktop computer use (DESKTOP-COMPUTER-USE §3.3 `DCU-2`, §3.4 `DCU-5`).

Steps 2, 4 and 4b of the dispatch chain the plan fixes:

1. :func:`personalclaw.computer_use.enable_state.is_enabled` — the keystone
2. :func:`check_app`                                         — **here**
3. index freshness (TTL) + fingerprint re-walk
4. :func:`check_input_target`                                — **here**
4b. :func:`check_autonomy`                                   — **here** (the approval ladder)
5. ``gate.require_computer_use``                             — SEL audit
6. the platform driver

**This module DECIDES; the gate only RECORDS.** Every screen raises, and nothing downstream
is allowed to be the thing that says no. A policy that returned a verdict for step 5 to act
on would make the audit trail load-bearing for enforcement, and an audit sink that fails
open — the correct posture for an audit sink — would then fail the *policy* open too. So the
refusal is raised at the decision point, and the SEL records what already happened.

**Three screens, three questions.** :func:`check_app` answers *may this application be driven
at all* — an allowlist a human wrote, where the empty list means nothing may be driven.
:func:`check_input_target` answers *may keystrokes land in this specific element* — a
password field, or a field already holding something credential-shaped, is refused even
inside an allowlisted app. The second is not redundant: an allowlisted browser or mail client
contains login forms, and "the app is approved" was never a statement about every field in it.
:func:`check_autonomy` answers *may THIS CALLER drive it right now* — the first question here
that is about who is asking rather than about what they aimed at, and the one that consumes the
earned-autonomy ladder (``guardrails.rungs``) rather than the enable document alone.

**No implicit self-allowance, deliberately.** The plan calls the mechanism a "self-plus-
operator allowlist". PersonalClaw's own windows are NOT implicitly drivable here: the one
application where driving the desktop converts directly into *raising the agent's own
permissions* is PersonalClaw itself — its approval dialogs, its settings, its enable
surfaces. So "self" is an ordinary entry an operator lists like any other app if they want
it, and an empty allowlist refuses everything including PersonalClaw. The fail-closed
direction never needs an exemption; the widening would.

**One reader of the allowlist.** ``enable_state.allowed_apps()`` is called from exactly one
place in this module, through its owning module rather than a ``from``-imported alias, and its
result is never cached or copied into a constant.
:func:`~personalclaw.computer_use.enable_state.require_enabled` records what a second reader
costs: an earlier version of the keystone check read ``state.enabled`` beside ``is_enabled()``,
and forcing ``is_enabled`` to return True left every refusal test green. An allowlist with two
readers has the same failure available to it — and a module-level alias IS a second binding,
which is also why this module follows the package's documented
``from personalclaw.computer_use import enable_state`` convention instead.

**No second keystone reader either.** No screen here consults ``is_enabled()``. Step 1 owns
that decision, and a policy that re-checked it would be a second answer to a question already
answered — plus it would let a caller that skipped step 1 look gated when it is not.
:func:`check_autonomy` reads a DIFFERENT field of the same document
(``enable_state.unattended_tools()``), which is a third grant rather than a second answer.
"""

from __future__ import annotations

import re
from typing import NoReturn

from personalclaw.computer_use import enable_state
from personalclaw.errors import AgentError
from personalclaw.security import redact_credentials

#: The application named is not on the operator's allowlist, so it may not be driven.
ERR_APP_NOT_ALLOWED = "ERR_COMPUTER_USE_APP_NOT_ALLOWED"

#: The input destination is a secure/password field, holds credential-shaped text, or could
#: not be recognised well enough to rule those out. ONE code for the whole screen: an
#: unscreenable target is handled exactly as a password field is, and the ``what`` line
#: carries which of the two it was — the same code/detail split
#: :class:`~personalclaw.computer_use.enable_state.EnableState` uses for "off" versus "off
#: because your JSON has a typo".
ERR_SECURE_FIELD = "ERR_COMPUTER_USE_SECURE_FIELD"

#: A run with nobody watching asked to drive the desktop, and the operator never granted it.
#: `DCU-5`'s refusal, and the one screen here that is about WHO is calling rather than about
#: what they aimed at.
ERR_UNATTENDED_NOT_GRANTED = "ERR_COMPUTER_USE_UNATTENDED_NOT_GRANTED"

#: The ``SafetyProfile.approval`` postures under which the ladder's ASK can actually be
#: answered, so no standing grant is needed.
#:
#: Read off the one profile field that describes how much a run may decide alone — the same
#: field :func:`~personalclaw.guardrails.policy.rung_ceiling_for_profile` reads, and with the
#: same meanings it documents:
#:
#: * ``ask`` — a human is watching this run and sees the result as it lands, so the approval
#:   prompt the tool layer already raises IS the ``one_tap`` ask. Nothing to add here.
#: * ``auto`` — the operator pre-approved this posture out loud (the dashboard trust toggle,
#:   ``--approval yolo``, an explicit ``approval_mode``). The ask was answered in advance.
#: * ``hook_based`` — the UNATTENDED posture: no human to ask and nobody watching. The ask
#:   cannot be answered at all, so the only thing that can license the drive is a grant the
#:   operator wrote before the run started.
#:
#: Resolved through :func:`~personalclaw.guardrails.policy.profile_for_session`, which is
#: already ceiling-intersected — so an operator who pins ``{"scopes": {"approval": {"value":
#: "hook_based"}}}`` in ``governance/ceiling.json`` makes EVERY desktop drive on the machine
#: grant-required, interactive ones included. That is the ceiling doing its job, not a bug.
_ASKABLE_APPROVALS = frozenset({"ask", "auto"})

#: The key an operator adds to the enable document to list drivable applications. Named once
#: so the refusal's FIX line and the reader agree; the document's shape is owned by
#: :mod:`~personalclaw.computer_use.enable_state`, whose module docstring uses
#: ``{"enabled": true, "apps": ["Mail"]}`` as the canonical "on, for Mail" example.
ALLOWLIST_KEY = "apps"

#: AX roles that are a text destination at all. An allowlist, not a denylist: an ``AXButton``,
#: an ``AXMenuItem`` and a role this build has never heard of are all refused, because "type
#: into it" is not a defined operation on any of them.
_EDITABLE_ROLES = frozenset(
    {
        "AXTextField",
        "AXTextArea",
        "AXComboBox",
        "AXSearchField",
    }
)

#: Subroles accepted on an editable role. Absent or empty is the ordinary case. Also an
#: allowlist: ``AXSecureTextField`` is the password one, but naming only IT would leave every
#: subrole a future toolkit invents silently passing.
_ALLOWED_SUBROLES = frozenset({"", "AXSearchField"})

#: The secure subrole, named so the refusal can say "password field" instead of "unrecognised
#: subrole". It is refused by :data:`_ALLOWED_SUBROLES` regardless; this only buys the message.
_SECURE_SUBROLES = frozenset({"AXSecureTextField"})

#: Keys read for the element's role/subrole. Both the snapshot spelling and the raw AX
#: attribute name, so a driver that forwards the AX dictionary unchanged is screened
#: identically to one that maps it first.
_ROLE_KEYS = ("role", "AXRole")
_SUBROLE_KEYS = ("subrole", "AXSubrole")

#: Keys whose text NAMES the field to a human. A password field in a web view or an Electron
#: app very often has no secure subrole at all — it is an ``AXTextField`` whose title is
#: "Password" — so the subrole check alone would miss the commonest shape on a real desktop.
_LABEL_KEYS = (
    "label",
    "title",
    "placeholder",
    "description",
    "help",
    "AXTitle",
    "AXDescription",
    "AXPlaceholderValue",
    "AXHelp",
)

#: Keys carrying the element's EXISTING text.
_VALUE_KEYS = ("value", "AXValue")

#: Field-naming vocabulary: terms that identify a destination as secret-bearing. Word-bounded
#: rather than substring-matched — an unbounded "pin" matches "Pinned" and "Shipping", and a
#: screen that refuses ordinary fields is a screen the first annoyed operator turns off.
#:
#: This is deliberately NOT a second definition of "sensitive text". It classifies the FIELD;
#: :func:`~personalclaw.security.redact_credentials` classifies the CONTENT, and that function
#: is this repo's single definition of a credential-shaped string. The two answer different
#: questions, so reusing one for the other would be the drift, not the fix.
_SECRET_FIELD_TERMS = (
    "password",
    "passwd",
    "passphrase",
    "passcode",
    "pin",
    "secret",
    "api key",
    "api_key",
    "apikey",
    "token",
    "credential",
    "credentials",
    "private key",
    "security code",
    "cvv",
    "cvc",
    "otp",
    "2fa",
    "mfa",
    "verification code",
    "recovery code",
    "seed phrase",
    "master key",
)

_SECRET_FIELD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in _SECRET_FIELD_TERMS) + r")\b",
    re.IGNORECASE,
)


class ComputerUsePolicyRefusal(Exception):
    """A target policy screen refused this call — fail CLOSED, like the keystone does.

    Carries an :class:`AgentError` so every surface (tool result, exception text, HTTP
    envelope) renders the same WHAT/WHY/FIX. Raised rather than returned for the reason
    :class:`~personalclaw.computer_use.enable_state.ComputerUseDisabled` states: a
    computer-use tool that quietly does nothing reads to a model as "the click landed", and it
    then reasons forward from a desktop state that never changed.

    ONE class for both screens, discriminated by :attr:`AgentError.code`. Two classes would
    invite a handler that catches one and forgets the other, and nothing in the dispatch chain
    treats "wrong app" differently from "wrong field" — both end the call. A caller that needs
    to tell them apart branches on the code, which is what the codes are for.
    """

    def __init__(self, error: AgentError) -> None:
        super().__init__(error.render())
        self.error = error


def app_not_allowed_error(app: str, *, tool: str, allowed: tuple[str, ...]) -> AgentError:
    """The WHAT/WHY/FIX for a non-allowlisted application.

    Public for the same reason
    :func:`~personalclaw.computer_use.enable_state.disabled_error` is: a surface that renders
    an envelope instead of raising needs the identical three lines. Two spellings of this
    message is how a refusal ends up telling an operator to edit a setting that does not
    exist — and here the FIX names an out-of-band file, which is precisely the sentence that
    must never drift into "open Settings".
    """
    path = enable_state.enable_file_path()
    shown = repr(app) if isinstance(app, str) else f"{app!r} (not a string)"
    census = (
        "the allowlist is EMPTY, so no application may be driven"
        if not allowed
        else f"the allowlist holds {', '.join(repr(a) for a in allowed)}"
    )
    return AgentError(
        code=ERR_APP_NOT_ALLOWED,
        what=f"{tool} refused: {shown} is not an application this machine may drive — {census}.",
        why=(
            "Driving an application posts real clicks and keystrokes into it, so which apps "
            "are reachable is a human's decision, not the agent's. The list is an allowlist "
            "and the match is exact: a near-miss, a different capitalisation, or a longer "
            "name that merely contains an allowed one is a DIFFERENT application, and "
            "granting it would grant strictly more than the operator wrote. PersonalClaw "
            "itself is not implicitly included — its own approval and settings windows are "
            "the one target where driving the desktop raises the agent's own permissions."
        ),
        fix=(
            f'A human must add the exact application name to the "{ALLOWLIST_KEY}" list in '
            f"{path} (or in the file {enable_state.ENABLE_PATH_ENV} points at, which is the "
            "version this process cannot rewrite), then restart PersonalClaw so the document is "
            "re-read at boot. There is no in-band path: no tool call, prompt, or setting edits "
            "this list, including this one."
        ),
        suggestions=allowed,
    )


def input_target_error(*, tool: str, detail: str) -> AgentError:
    """The WHAT/WHY/FIX for a refused input destination.

    ``detail`` is the reason this specific target failed ("it is a secure text field", "no
    recognised role"), carried into the WHAT the way
    :attr:`~personalclaw.computer_use.enable_state.EnableState.detail` is. ONE constructor for
    every reason this screen refuses, so the WHY and FIX have exactly one spelling: "refused"
    and "refused because the element dictionary was malformed" are very different problems to
    be handed, but they are the same *policy*, and splitting the message is how the two answers
    drift apart.
    """
    return AgentError(
        code=ERR_SECURE_FIELD,
        what=f"{tool} refused: this input destination is not safe to type into — {detail}.",
        why=(
            "Typing into a password field, or into a field already holding something "
            "credential-shaped, either exposes a secret or overwrites one — and a secure "
            "field's contents are exactly what no automated caller should be authoring. An "
            "element this build cannot positively recognise as an ordinary editable text "
            "destination is refused for the same reason: a screen that only recognises the "
            "shapes it was shown is a screen with a hole, so unknown means no. Being inside "
            "an allowlisted application does not change this — approved apps contain login "
            "forms."
        ),
        fix=(
            "Target an ordinary text field instead: re-snapshot the window and choose an "
            "element whose role is one of "
            f"{', '.join(sorted(_EDITABLE_ROLES))}, with no secure subrole and no secret-"
            "bearing label. A credential a human must enter is entered BY that human — there "
            "is no allowlist, setting, or flag that opens a secure field to this tool. The "
            f"drivable-app list itself lives in {enable_state.enable_file_path()}, which only a "
            "human edits."
        ),
    )


def check_app(app: str, *, tool: str) -> None:
    """Refuse unless *app* is on the operator's allowlist. Raises; returns None when allowed.

    Step 2 of the dispatch chain, before the index walk and before any driver call — the
    cheapest possible place to learn an app is off-limits, and the only place where "we already
    walked its windows" cannot have happened yet.

    Matching is EXACT on the stripped string. Not case-folded and not a substring: this is a
    policy comparison against names a human typed, not a fuzzy resolver. Two distinct
    applications can differ only in case, ``TextEditor`` is not ``TextEdit``, and every
    loosening here grants an app the operator did not name. When the operator's spelling is
    what is wrong, the refusal's ``suggestions`` carry the exact accepted strings, so the
    strict comparison costs a corrected retry rather than a mystery.

    An empty or non-string *app* refuses like any other miss: an unnamed target is an unknown
    one, and ``allowed_apps()`` is still consulted first so the refusal can list what WOULD have
    been accepted.
    """
    allowed = enable_state.allowed_apps()
    candidate = app.strip() if isinstance(app, str) else ""
    if not candidate or not any(candidate == entry.strip() for entry in allowed):
        raise ComputerUsePolicyRefusal(app_not_allowed_error(app, tool=tool, allowed=allowed))


def _screened_text(target: dict, key: str, *, tool: str) -> str:
    """The stripped string at *key*, refusing when it is present but not a string.

    Malformed is refused here rather than coerced with ``str()``: a driver that hands a list
    where a role goes has produced a target this build does not understand, and screening the
    repr of it would be screening our own formatting instead of the element.
    """
    raw = target[key]
    if not isinstance(raw, str):
        raise ComputerUsePolicyRefusal(
            input_target_error(
                tool=tool,
                detail=(
                    f"the element's {key!r} is a {type(raw).__name__}, not a string, so the "
                    "target is malformed"
                ),
            )
        )
    return raw.strip()


def check_input_target(target: dict, *, tool: str) -> None:
    """Refuse a secure/password field or sensitive text destination before any type/set-value.

    Step 4 of the dispatch chain: after the index is known fresh (so the element screened is
    the element that will be typed into, not a stale row describing something the user has
    since replaced) and before the driver.

    **What is screened.** *target* is one element as the snapshot publishes it: a flat mapping
    carrying at least a role, optionally a subrole, human-facing label text, and the element's
    existing value. Both spellings of every key are read — ``role`` and ``AXRole``, ``value``
    and ``AXValue`` — so a driver that forwards the raw AX dictionary is screened identically
    to one that maps it first. EVERY present spelling must pass, not the first one found: a
    target carrying ``role="AXTextField"`` beside ``AXRole="AXButton"`` describes two different
    elements, and screening whichever came first would let the caller choose which claim gets
    enforced. Reading more keys therefore only ever produces more refusals, never fewer.

    Unknown keys are IGNORED here, the deliberate opposite of the enable document's unknown-key
    refusal. That document is an operator's *instruction*, where an unenforced key means
    granting something other than what was asked; this dictionary is a *description* of an
    element, and a real AX element carries dozens of attributes this screen has no opinion
    about.

    **Unknown means no**, per shape:

    * not a mapping, or an empty one → refused; there is nothing to screen.
    * no role key at all → refused; an unidentified destination is an unknown one.
    * a role outside :data:`_EDITABLE_ROLES` → refused; "type into it" is undefined on a
      button, and on a role this build has never seen it is unknowable.
    * a subrole outside :data:`_ALLOWED_SUBROLES` → refused, with ``AXSecureTextField`` named
      in the message rather than reported as merely unrecognised.
    * any screened key present with a non-string value → refused as malformed.
    * a label/title/placeholder naming a secret (:data:`_SECRET_FIELD_TERMS`) → refused. This
      catches the commonest real password field: the web-view ``AXTextField`` titled
      "Password" that carries no secure subrole at all.
    * an existing value :func:`~personalclaw.security.redact_credentials` would redact →
      refused. That function is this codebase's ONE definition of credential-shaped text, so a
      string the system redacts on the way OUT is one this tool will not type INTO.

    **What is NOT screened, and why not here.** The text the caller is about to write is not
    part of the target and is not inspected: the contract takes a destination, and a screen
    that hopefully probed for a pending-text key the driver might not have named would report
    "clean" on text it never read — the exact hole the unknown-means-no rule above exists to
    close. Screening the outgoing string is a separate input with a separate parameter, not a
    guess inside this one.
    """

    def refuse(detail: str) -> NoReturn:
        raise ComputerUsePolicyRefusal(input_target_error(tool=tool, detail=detail))

    if not isinstance(target, dict):
        refuse(f"the target is a {type(target).__name__}, not an element dictionary")
    if not target:
        refuse("the target is an empty element dictionary, so there is nothing to screen")

    role_keys = [key for key in _ROLE_KEYS if key in target]
    if not role_keys:
        refuse("the element declares no role, so this build cannot tell what it would type into")
    for key in role_keys:
        role = _screened_text(target, key, tool=tool)
        if not role:
            refuse(f"the element's {key} is empty, so its kind is unknown")
        if role not in _EDITABLE_ROLES:
            refuse(
                f"the element's {key} is {role!r}, which is not one of the editable text roles "
                f"this build recognises ({', '.join(sorted(_EDITABLE_ROLES))})"
            )

    for key in (key for key in _SUBROLE_KEYS if key in target):
        subrole = _screened_text(target, key, tool=tool)
        if subrole in _SECURE_SUBROLES:
            refuse(f"it is a secure text field ({subrole}) — a password or other secret entry")
        if subrole not in _ALLOWED_SUBROLES:
            refuse(
                f"the element's {key} is {subrole!r}, which this build does not recognise as an "
                "ordinary editable destination"
            )

    for key in _LABEL_KEYS:
        if key not in target:
            continue
        label = _screened_text(target, key, tool=tool)
        if label and _SECRET_FIELD_RE.search(label):
            refuse(
                f"its {key} names a secret-bearing field ({label!r}), so it is treated as a "
                "password entry even though it carries no secure subrole"
            )

    for key in _VALUE_KEYS:
        if key not in target:
            continue
        existing = _screened_text(target, key, tool=tool)
        if existing and redact_credentials(existing)[1]:
            refuse(
                "the element already holds credential-shaped text, so typing here would "
                "overwrite or expose a secret"
            )


def unattended_not_granted_error(
    *, tool: str, profile_name: str, granted: tuple[str, ...]
) -> AgentError:
    """The WHAT/WHY/FIX for an unattended drive the operator never granted.

    Public for the reason its siblings are: a surface that renders an envelope instead of
    raising needs the identical three lines, and two spellings of a message whose FIX names an
    out-of-band file is precisely how "edit this file" drifts into "open Settings".
    """
    path = enable_state.enable_file_path()
    census = (
        "no tool is granted to unattended runs"
        if not granted
        else f"the unattended grant holds {', '.join(repr(t) for t in granted)}"
    )
    return AgentError(
        code=ERR_UNATTENDED_NOT_GRANTED,
        what=(
            f"{tool} refused: nothing is watching this run (the {profile_name!r} safety "
            f"profile), and driving the desktop unattended was not granted — {census}."
        ),
        why=(
            "This capability asks before it acts, and an unattended run has nobody to ask. "
            "There is also nothing to undo afterwards: a press, a keystroke or a scroll in "
            "somebody else's application cannot be reversed by this build, so a run that acted "
            "silently would leave real effects and no trace a person would notice. The "
            "operator arming the machine and the operator agreeing that a cron fire, a channel "
            "message or an inbound caller may drive it while they are asleep are two different "
            "decisions, and only the second one licenses this."
        ),
        fix=(
            f'A human must add "{tool}" to the "{enable_state.UNATTENDED_KEY}" list in {path} '
            f"(or in the file {enable_state.ENABLE_PATH_ENV} points at), then restart "
            "PersonalClaw so the document is re-read at boot. Names are matched exactly and "
            "must be tools this build declares. There is no in-band path: no tool call, "
            "prompt, or setting grants this, including this one. To run it right now instead, "
            "drive it from a session a human is present in — an interactive run gets the "
            "approval prompt rather than this refusal."
        ),
        suggestions=granted,
    )


def check_autonomy(tool: str, *, caller_identity: str = "") -> None:
    """Refuse an unattended drive without the operator's standing grant (`DCU-5`).

    Step 4b of the dispatch chain — the LAST screen before the approved audit row, deliberately
    after ``check_app`` and ``check_input_target`` so a refusal is still recorded against the
    app and element it was aimed at. A refusal with no target is a worse audit row than one
    with, and the ordering costs nothing: every screen before it already refused the calls that
    should never reach any of them.

    **Two reads, and neither is a new vocabulary.**

    1. :func:`~personalclaw.guardrails.rungs.route_action_type` on
       :data:`~personalclaw.guardrails.rungs.COMPUTER_USE_DRIVE` — the earned-autonomy ladder's
       own answer. The declaration ceilings at ``one_tap`` ("asks first"), which is *why* an
       unattended run needs a standing grant at all, and the route it returns is what
       :func:`~personalclaw.guardrails.rungs.announce_withheld` turns into the durable inbox row
       the clause's "and notifies" names. The rung, its user-facing label and this key all come
       from :mod:`~personalclaw.guardrails.autonomy`; nothing here mints a second ladder.
    2. ``profile_for_session(...).approval`` — whether anybody is there to answer that ask.
       See :data:`_ASKABLE_APPROVALS` for the three postures and what each means.

    The ladder cannot make read 2 for us, and that is a property of the ladder rather than an
    omission here: ``rung_ceiling_for_profile`` narrows an unattended run to ``auto_with_undo``,
    which is ABOVE ``one_tap``, so the composed rung is ``one_tap`` for an interactive run and
    ``one_tap`` for a cron fire alike. One rung, two consequences — asked-and-answered versus
    asked-with-nobody-home — and the seam is where that difference is known.

    Raises :class:`ComputerUsePolicyRefusal` so the whole chain keeps one exception type and one
    rendered envelope; the code discriminates. Returns None when the drive may proceed.
    """
    from personalclaw.guardrails import policy as guardrails_policy
    from personalclaw.guardrails import rungs

    profile = guardrails_policy.profile_for_session(caller_identity)
    if profile.approval in _ASKABLE_APPROVALS:
        return
    granted = enable_state.unattended_tools()
    if tool in granted:
        return
    error = unattended_not_granted_error(tool=tool, profile_name=profile.name, granted=granted)
    # 🔴 MEASURED: without this, `route_action_type` answers `draft_only`/``ROUTE_DRAFT``, not
    # `one_tap`/``ROUTE_ASK``. `resolve_rung` fails closed for a DECLARED key with no
    # registration, and the gateway registers the core declarations from the action-provider
    # seam — a path a computer-use dispatch does not travel. The refusal would still refuse, so
    # nothing here would look broken; it would just file a "here is what it would have done"
    # proposal instead of the "decide" request the clause asks for, and say the wrong sentence.
    # ``route_provider_action`` calls this for the same reason, in the same position.
    rungs.ensure_core_action_types()
    route = rungs.route_action_type(rungs.COMPUTER_USE_DRIVE, session_key=caller_identity)
    # Deduped per TOOL rather than per attempt: a trigger that fires every thirty seconds must
    # not stack a hundred identical rows, and a held drive is one standing request however many
    # times it was tried. Per tool rather than per action type because "let it snapshot" and
    # "let it click" are different grants a person answers differently.
    rungs.announce_withheld(
        route,
        title=f"A background run wanted to drive {tool.removeprefix('computer_')}",
        body=error.what,
        refs={"tool": tool, "caller": caller_identity},
        dedup_key=f"computer_use_hold:{tool}",
    )
    raise ComputerUsePolicyRefusal(error)
