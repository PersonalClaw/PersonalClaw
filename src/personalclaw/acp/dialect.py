"""ACP protocol dialects — per-CLI handshake/permission divergences.

The Agent Client Protocol (ACP) is shared across agent CLIs, but each CLI
diverges in a handful of concrete ways: the ``protocolVersion`` value/type, how
the model is selected, whether/how the agent is activated, and the shape of
permission options. :class:`AcpClient` stays 100% vendor-neutral by delegating
exactly those points to an :class:`ACPDialect` strategy supplied by the caller
(a removable per-CLI provider bundle). Core never names a specific CLI.

The :class:`DefaultDialect` here encodes the protocol shape PersonalClaw's
``AcpClient`` originally hard-coded (date-string ``protocolVersion``, agent
activated via ``session/set_mode`` with ``modeId=<agent>``, model via
``session/set_model``). Bundles ship
their own subclasses (e.g. an int-``protocolVersion`` / ``set_config_option``
dialect for Claude Code and Codex).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Protocol method names + outcome constants live in acp.types (kept here as a
# local import inside methods would be circular-safe, but these are plain
# strings shared with the client, so import at module top is fine — types has
# no upward deps).
from personalclaw.acp.types import (
    METHOD_PROMPT,
    METHOD_SET_MODE,
    METHOD_SET_MODEL,
    OPTION_ALLOW_ALWAYS,
    OPTION_ALLOW_ONCE,
    OUTCOME_CANCELLED,
    OUTCOME_SELECTED,
)


@dataclass(frozen=True)
class AcpRequest:
    """A JSON-RPC request a dialect asks the client to send: (method, params)."""

    method: str
    params: dict


@dataclass
class DiscoveryResult:
    """Vendor-neutral normalization of a backend's ``session/new`` discovery
    surface, produced by :meth:`ACPDialect.normalize_discovery`.

    Kept free of any ``agents.provider`` import (the dialect layer must not
    depend upward) — :class:`AcpAgentProvider` maps these plain entries to
    ``DiscoveredAgent``. ``agents`` entries are dicts:
      ``{id, label, description, provider_agent, reasoning_effort, use_runtime_prefix}``
    where ``label`` is the full picker name when ``use_runtime_prefix`` is False
    (persona-style agents) or a parenthetical suffix when True (empty label = the
    runtime's base agent). ``models`` are selectable model-override ids.
    ``permission_modes`` are the backend's NATIVE permission mode values (claude's
    5; empty for the default dialect) — raw capability data the trust-rung layer
    maps onto PersonalClaw rungs.

    ``supported_efforts`` are the backend-declared reasoning-effort options (from
    ``configOptions.effort``), surfaced VERBATIM as ``{value, label}`` — PClaw does
    NOT invent or translate an effort scale. Empty = the runtime has no effort axis.
    Effort is a per-turn session setting (``set_effort_request``), NOT an agent
    identity, so effort levels are no longer exploded into separate agents."""

    agents: list[dict]
    models: list[str]
    permission_modes: list[str]
    supported_efforts: list[dict] = field(default_factory=list)


# ── which refusal a Deny sends ───────────────────────────────────────────────
#: Words in an option's id or name saying that choosing it ENDS the agent's turn rather than
#: declining one call: ``cancel``, "No, and stop". Matched as word prefixes ("cancelled").
_TURN_ENDING_WORDS = ("cancel", "abort", "interrupt", "stop", "halt", "terminat")
#: Words saying the agent goes on without the call: "No, continue without running it".
_CONTINUING_WORDS = ("continu", "proceed", "skip")
#: Words that make an option the agent gave no spec ``kind`` a refusal.
_REFUSAL_WORDS = ("reject", "deny", "denied", "declin", "refus")
#: Words that keep an option with no spec ``kind`` from ever being read as a refusal.
_ALLOWING_WORDS = ("allow", "approv", "accept", "yes", "grant")


def _words(text: str) -> list[str]:
    """Lower-case words of an option id or name: ``rejectOnce`` and ``reject_once`` alike."""
    return re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower())


def _says(words: list[str], stems: tuple[str, ...]) -> bool:
    return any(word.startswith(stem) for word in words for stem in stems)


def is_refusal_option(option: dict[str, str]) -> bool:
    """Whether a Deny may answer with *option*.

    The spec ``kind`` decides when the agent gave one: a ``reject_*`` kind (or a kind that says
    deny/decline) is a refusal, and every other kind is not — an ``allow_*`` option is never
    sent for a Deny, and a ``cancel`` kind says what the ``cancelled`` outcome already says.
    An option with no kind is a refusal when its id or name says so and nothing in it allows.
    """
    kind = (option.get("kind") or "").lower()
    if kind:
        return kind.startswith("reject") or _says(_words(kind), _REFUSAL_WORDS)
    words = _words(f"{option.get('id') or ''} {option.get('label') or ''}")
    return _says(words, _REFUSAL_WORDS) and not _says(words, _ALLOWING_WORDS)


def refusal_rank(option: dict[str, str]) -> tuple[bool, bool, bool]:
    """Sort key over the refusals an agent offered: the smallest is the one a Deny sends.

    The protocol gives a refusal two semantics, both in its ``kind``: that it IS one, and
    whether the agent should remember it (``reject_always``). It has none for whether the
    agent's turn goes on, which is the difference a person pressing Deny cares about, so that
    is read off the option's own id and name, in this order:

    1. A refusal that does not end the turn before one that does (``cancel``, ``abort``,
       ``interrupt``, ``stop``, ``halt``, ``terminate``). An option that also says it
       continues ("continue", "proceed", "skip") does not end the turn.
    2. Once before always: a Deny is about this call, and an "always" option asks the agent
       to remember a rule nobody decided.
    3. A refusal that says it continues before one that says nothing either way.

    An option this cannot read sorts as a plain decline that lets the agent continue, ahead
    of every option that says it ends the turn. Ties keep the agent's own order.
    """
    words = _words(f"{option.get('id') or ''} {option.get('label') or ''}")
    continues = _says(words, _CONTINUING_WORDS)
    ends_turn = not continues and _says(words, _TURN_ENDING_WORDS)
    remembered = "always" in (option.get("kind") or option.get("id") or "").lower()
    return (ends_turn, remembered, not continues)


class ACPDialect:
    """Strategy for the per-CLI ACP protocol divergences.

    A dialect owns ONLY the points where ACP agents differ. Everything else
    (spawn, framing, event loop, the neutral event mapping) stays in the
    vendor-neutral client. Subclasses override only what differs from
    :class:`DefaultDialect`.
    """

    #: Short identifier, e.g. "default", "claude", "codex". For logging only.
    name: str = "default"

    #: Capability gate — whether this backend actually SERVICES multiple sessions
    #: concurrently on ONE process (interleaved session/prompt), vs. internally
    #: serializing them. Default **False** (safe): the client keeps one-session-per-
    #: process until a backend is PROVEN concurrent by a live 2-session spike. A True
    #: here opts a backend into the FrameRouter demux + per-connection session pooling.
    #: Never assume True — an un-verified backend that actually serializes would deadlock
    #: the second session's dispatcher.
    supports_concurrent_sessions: bool = False

    #: Whether this backend SERVICES a `session/prompt` that arrives while a turn is
    #: already generating — i.e. whether mid-turn steering reaches the running answer.
    #: Default **False** (safe), same discipline as `supports_concurrent_sessions`: no
    #: dialect is assumed capable until a live spike proves the interleaved prompt is
    #: serviced rather than queued-or-clobbered. A False here routes a mid-turn message
    #: to the normal queue, which is visible (a `queue_push` event) instead of silent.
    supports_mid_turn_prompt: bool = False

    #: Whether this backend puts its question tool's questions to the client over ACP's form
    #: elicitation (``acp/elicitation.py``) once the client advertises it. Default **False**: the
    #: capability is advertised only to a backend whose bridge was read, because a backend that
    #: asks a form PersonalClaw cannot read is answered ``cancel``, and that is all it gets.
    asks_through_elicitation: bool = False

    # ── mid-turn steering ──
    def mid_turn_prompt_request(self, *, session_id: str, text: str) -> AcpRequest | None:
        """The request that delivers a STEER into the turn already generating, or
        ``None`` when this dialect cannot deliver one.

        The verb is dialect-owned for the same reason every other ``*_request`` here is:
        core ACP's only mid-turn-shaped frame is a second ``session/prompt``, but a
        backend is free to expose a dedicated extension, and nothing outside this class
        may assume which. ``None`` is the honest answer for a dialect that does not
        declare :attr:`supports_mid_turn_prompt` — and the caller MUST treat it as "this
        steer was NOT delivered" rather than substituting a next-turn prompt, because a
        steer that lands after the answer is finished is a different message.

        Gated on the flag HERE, not only at the call site: the flag and the frame are one
        decision, so a subclass that overrides one without the other cannot produce a
        dialect that declares mid-turn support and then silently builds nothing."""
        if not self.supports_mid_turn_prompt:
            return None
        body = (text or "").strip()
        if not body:
            return None
        from personalclaw.acp import translate

        return AcpRequest(
            METHOD_PROMPT,
            {"sessionId": session_id, "prompt": translate.encode_prompt_content(body)},
        )

    # ── handshake ──
    def protocol_version(self) -> object:
        """Value sent as ``initialize.protocolVersion`` (date-string or int)."""
        return "2025-08-22"

    def client_info(self, *, client_name: str, client_version: str) -> dict:
        """The ``initialize.clientInfo`` block."""
        return {"name": client_name, "version": client_version}

    def client_capabilities(self, *, attended: bool) -> dict:
        """The ``initialize.clientCapabilities`` block, ``{}`` when it has nothing to say.

        Form elicitation only for an ATTENDED session of a backend that asks through it: an
        unattended one has nobody to answer, and the backend then keeps its question tool off
        (Claude Code's adapter lists ``AskUserQuestion`` as disallowed without it), so its model
        is not offered a tool that could only be cancelled."""
        if attended and self.asks_through_elicitation:
            from personalclaw.acp.elicitation import FORM_CAPABILITY

            return dict(FORM_CAPABILITY)
        return {}

    def activate_agent_request(self, *, session_id: str, agent: str) -> AcpRequest | None:
        """Request that activates/selects the agent, or ``None`` if the dialect
        activates purely via the launch argv (no protocol message).

        Empty ``agent`` → ``None``: ACP has no global default agent (it is a
        per-session choice), so an unselected agent means "use the CLI's own
        built-in default" — NOT a fabricated name. Sending an arbitrary modeId
        the backend doesn't define errors (default dialect: ``Mode '<name>' not found``)."""
        if not agent:
            return None
        return AcpRequest(METHOD_SET_MODE, {"sessionId": session_id, "modeId": agent})

    def set_model_request(
        self, *, session_id: str, model: str, default_model: str
    ) -> AcpRequest | None:
        """Request that sets the model, or ``None`` when the dialect has no
        model verb / the model is the agent default (``model == default_model``)."""
        if model and model != default_model:
            return AcpRequest(METHOD_SET_MODEL, {"sessionId": session_id, "modelId": model})
        return None

    def set_mode_request(self, *, session_id: str, mode: str) -> AcpRequest | None:
        """Request that sets the session's permission/operating MODE, or ``None``.

        This is distinct from :meth:`activate_agent_request` (which selects the
        *agent*). Some adapters expose a separate permission-mode axis via
        ``session/set_config_option`` (``configId="mode"``) — and they do NOT share
        a vocabulary on it. Measured off live ``session/new`` snapshots: claude-code
        offers ``auto``/``default``/``acceptEdits``/``plan``/``dontAsk``/
        ``bypassPermissions``, codex offers ``read-only``/``agent``/
        ``agent-full-access``. So a value is never forwarded verbatim — see
        :meth:`ZedAdapterDialect.native_mode`. The default dialect has NO separate
        mode axis — its ``set_mode`` already *is* agent activation — so it returns
        ``None`` and the client skips the step (kiro: the host gate is the only
        gate). Empty ``mode`` → ``None`` (use agent default).

        MUST be issued AFTER the model is set: adapters recompute the available
        modes for the active model and clamp an out-of-range current mode."""
        return None

    def set_effort_request(self, *, session_id: str, effort: str) -> AcpRequest | None:
        """Request that sets the per-turn reasoning EFFORT, or ``None``. The value
        is the backend's own declared effort option (see
        :attr:`DiscoveryResult.supported_efforts`) — no PClaw translation. The
        default dialect has no separate effort axis and returns ``None``."""
        return None

    # ── discovery ──
    def normalize_discovery(self, session_new: dict) -> "DiscoveryResult":
        """Normalize a ``session/new`` response into vendor-neutral discovery data.

        Default-dialect shape: ``modes.availableModes`` ARE selectable agents (each
        a persona activated via ``session/set_mode`` with its ``id`` as the
        modeId), and ``models.availableModels`` are model overrides. There is no
        separate permission-mode axis (``set_mode`` is agent activation), so
        ``permission_modes`` is empty. Subclasses whose ``availableModes`` mean
        something else (Zed adapters: permission modes, not agents) override this.
        """
        modes = (session_new.get("modes") or {}).get("availableModes", []) or []
        agents = [
            {
                "id": str(m.get("id", "")),
                "label": str(m.get("name") or m.get("id", "")),
                "description": str(m.get("description", "")),
                "provider_agent": str(m.get("id", "")),
                "reasoning_effort": "",
                "use_runtime_prefix": False,
            }
            for m in modes
            if isinstance(m, dict) and m.get("id")
        ]
        models_raw = (session_new.get("models") or {}).get("availableModels", []) or []
        models = [
            str(m.get("modelId") or m.get("id") or "")
            for m in models_raw
            if isinstance(m, dict) and (m.get("modelId") or m.get("id"))
        ]
        return DiscoveryResult(agents=agents, models=models, permission_modes=[])

    # ── permission options ──
    def parse_permission_options(self, raw_options: list[dict]) -> list[dict[str, str]]:
        """Normalise inbound ``session/request_permission`` options to the
        neutral ``[{"id", "label", "kind"}]`` shape the host gate consumes.

        The public ACP spec keys an option ``optionId`` + ``name``; the shape this host
        first spoke keys it ``id`` + ``label``. Both are read, for every dialect: an option
        dropped for its key spelling leaves a Deny nothing to answer with but
        ``cancelled``, which ends the agent's turn, and leaves an approval guessing at a
        literal ``allow_once``.

        ``kind`` is the ACP ``PermissionOptionKind`` (``allow_once`` /
        ``allow_always`` / ``reject_once`` / ``reject_always``) when the agent
        supplies it; the host uses it to select the right ``optionId`` to echo
        back (the ``id`` is agent-defined and is NOT assumed to be a well-known
        constant — claude-code-acp's is not the literal ``allow_once``, which is what
        made fs_write approvals silently fail; see :meth:`AcpClient.approve_tool`)."""
        opts = [
            {
                "id": o.get("optionId") or o.get("id", ""),
                "label": o.get("name") or o.get("label", ""),
                "kind": o.get("kind", ""),
            }
            for o in raw_options
        ]
        return [o for o in opts if o["id"]]

    def default_permission_options(self) -> list[dict[str, str]]:
        """Fallback options when the request carries none."""
        return [
            {"id": OPTION_ALLOW_ONCE, "label": "Allow once", "kind": OPTION_ALLOW_ONCE},
            {"id": OPTION_ALLOW_ALWAYS, "label": "Allow always", "kind": OPTION_ALLOW_ALWAYS},
        ]

    def select_allow_option_id(self, offered: list[dict[str, str]]) -> str:
        """Pick the ``optionId`` to echo back when approving, from the options
        the agent actually offered.

        The agent's ``optionId`` values are agent-defined — they are NOT
        guaranteed to be the well-known ``allow_once`` / ``allow_always``
        constants (claude-code-acp uses different ids). Selection is therefore
        driven by the spec-defined ``kind`` classifier, falling back to the
        literal id and then to the first non-reject option. An approval answers
        one call, so the once option wins over the always option. Returns ``""``
        only when nothing approvable was offered (caller falls back to the default).
        """
        if not offered:
            return ""

        def _is_allow(opt: dict[str, str]) -> bool:
            k = (opt.get("kind") or "").lower()
            i = (opt.get("id") or "").lower()
            return k.startswith("allow") or i.startswith("allow")

        # Scope once/always buckets to allow options only — "reject_once" also
        # contains "once" but must never be selected as an approval.
        allow_any = [o for o in offered if _is_allow(o)]
        once = [o for o in allow_any if "once" in (o.get("kind") or o.get("id") or "").lower()]
        always = [o for o in allow_any if "always" in (o.get("kind") or o.get("id") or "").lower()]

        for bucket in (once, always, allow_any):
            if bucket:
                return bucket[0]["id"]
        # No clearly-allow option — fall back to the first non-reject option.
        for o in offered:
            ki = (o.get("kind") or o.get("id") or "").lower()
            if not ki.startswith("reject") and "cancel" not in ki and "deny" not in ki:
                return o["id"]
        return ""

    def select_reject_option_id(self, offered: list[dict[str, str]]) -> str:
        """Pick the ``optionId`` to echo back when DENYING, from the options the agent
        actually offered. Mirror of :meth:`select_allow_option_id`.

        A Deny means *decline this call and carry on*: the agent should answer without it,
        and say what it could not check. ``cancelled`` is not that — in ACP it means *the
        prompt turn was cancelled before the user responded*, so it ends the agent's turn
        — and neither is a reject option that ends the turn. Agents offer both kinds under
        the same spec kind: an agent can list a ``reject_once`` "No, continue without
        running it" beside a ``reject_once`` "No, and tell me what to do differently" that
        stops its turn, in either order. The spec kind is the only semantics the protocol
        provides, so it decides what a refusal IS; which refusal is chosen is read off the
        option's own id and name by :func:`refusal_rank`. Returns ``""`` when the agent
        offered no refusal at all, which is the only case where ``cancelled`` is sent.

        When every refusal it offered ends its turn, the one sent ends it; the session then
        asks the agent to carry on without the call (``AcpSession``'s carry-on), and the
        approval card says so before the Deny (:meth:`deny_ends_turn`).
        """
        refusals = [o for o in offered if is_refusal_option(o)]
        if not refusals:
            return ""
        return str(min(refusals, key=refusal_rank).get("id") or "")

    def deny_ends_turn(self, offered: list[dict[str, str]]) -> bool:
        """Whether a Deny of a request offering *offered* ends the agent's turn, said before it
        is pressed: the refusal :meth:`select_reject_option_id` picks says it ends the turn
        (:func:`refusal_rank`), or the agent offered no refusal and the answer is ``cancelled``.
        An agent asking for an escalation can offer only its turn-ending refusal."""
        chosen = self.select_reject_option_id(offered)
        if not chosen:
            return True
        return refusal_rank(next(o for o in offered if o.get("id") == chosen))[0]

    def approve_outcome(self, option_id: str) -> dict:
        """The ``outcome`` payload for an approved tool (selected option)."""
        return {"outcome": {"outcome": OUTCOME_SELECTED, "optionId": option_id}}

    def reject_outcome(self, option_id: str = "") -> dict:
        """The ``outcome`` payload for a DENIED tool.

        ``selected`` + the agent's own reject option when it offered one (see
        :meth:`select_reject_option_id`); ``cancelled`` only when it offered none, because
        ``cancelled`` claims the whole turn was abandoned rather than that one tool was
        refused.
        """
        if option_id:
            return {"outcome": {"outcome": OUTCOME_SELECTED, "optionId": option_id}}
        return {"outcome": {"outcome": OUTCOME_CANCELLED}}

    # ── process hygiene ──
    def child_process_names(self) -> tuple[str, ...]:
        """Extra binary basenames this dialect's CLI may spawn, contributed to
        the orphan-cleanup allowlist so they aren't leaked."""
        return ()


class DefaultDialect(ACPDialect):
    """The baseline ACP protocol shape, used when no dialect is supplied.

    Date-string ``protocolVersion``, ``session/set_mode`` for agent
    activation, ``session/set_model`` for the model. A bundle whose CLI speaks
    this shape selects it with ``dialect="default"``."""

    name = "default"

    # A CLI speaking this dialect was PROVEN concurrent by a live 2-session
    # spike: two sessions on one process, interleaved session/update frames.
    # The Zed adapters (ClaudeCode/Codex) stay at the base False until their own spike.
    supports_concurrent_sessions = True


# ``session/set_config_option`` — used by the Zed ACP adapters (claude/codex)
# to set the model, instead of the default dialect's ``session/set_model``.
METHOD_SET_CONFIG_OPTION = "session/set_config_option"


class ZedAdapterDialect(ACPDialect):
    """Shared shape for the Zed-maintained ACP adapters (``claude-code-acp``,
    ``codex-acp``): integer ``protocolVersion`` (1), model set via
    ``session/set_config_option`` rather than ``session/set_model``, and NO
    ``session/set_mode`` agent-activation step (the adapter binds the agent at
    spawn).
    """

    name = "zed"

    def protocol_version(self) -> object:
        return 1

    def activate_agent_request(self, *, session_id: str, agent: str) -> AcpRequest | None:
        # Zed adapters select the agent at launch — no set_mode message.
        return None

    def set_model_request(
        self, *, session_id: str, model: str, default_model: str
    ) -> AcpRequest | None:
        if model and model != default_model:
            return AcpRequest(
                METHOD_SET_CONFIG_OPTION,
                {"sessionId": session_id, "configId": "model", "value": model},
            )
        return None

    def native_mode(self, mode: str) -> str:
        """Translate a HOST-canonical permission mode into THIS adapter's own
        ``configId="mode"`` vocabulary. ``""`` = this adapter has no equivalent,
        so send nothing rather than a value it will reject.

        The canonical vocabulary is claude-code's (``default`` / ``acceptEdits`` /
        ``plan`` / ``dontAsk`` / ``bypassPermissions``), which is what
        ``permission_authority`` speaks, so the base translation is identity.
        A subclass whose CLI names the same axis differently overrides this."""
        return mode

    def set_mode_request(self, *, session_id: str, mode: str) -> AcpRequest | None:
        """Set the Zed-adapter permission mode via ``session/set_config_option``
        (``configId="mode"``), translated into the adapter's own vocabulary by
        :meth:`native_mode` first.

        Forwarding the host value VERBATIM is what this used to do, on the belief
        that "the adapter validates against the model's available modes and
        rejects unknown ones, so let the adapter be the authority". Measured on a
        live adapter, that belief is false in the way that matters: codex-acp does
        not clamp an out-of-vocabulary value, it answers ``-32602 Invalid params``
        and *keeps its own default-allow mode*. The reply was dropped on the floor
        (see :meth:`AcpClient._watch_dialect_reply`), so the host's rule "never leave the
        CLI in its own default-allow mode" silently did not hold for codex at all.
        The host therefore owns the translation, and an untranslatable mode is
        skipped instead of sent.

        Empty/untranslatable ``mode`` → ``None`` (keep the adapter's default)."""
        native = self.native_mode(mode) if mode else ""
        if native:
            return AcpRequest(
                METHOD_SET_CONFIG_OPTION,
                {"sessionId": session_id, "configId": "mode", "value": native},
            )
        return None

    def set_effort_request(self, *, session_id: str, effort: str) -> AcpRequest | None:
        """Set the per-turn reasoning effort via ``session/set_config_option``
        (``configId="effort"``). The value is the backend's OWN declared effort
        option (surfaced verbatim as supported_efforts) — no PClaw translation.
        Empty ``effort`` → ``None`` (keep the adapter's default). MUST follow
        :meth:`set_model_request` (effort granularity can be model-dependent)."""
        if effort:
            return AcpRequest(
                METHOD_SET_CONFIG_OPTION,
                {"sessionId": session_id, "configId": "effort", "value": effort},
            )
        return None

    def normalize_discovery(self, session_new: dict) -> "DiscoveryResult":
        """Zed adapters expose NO named sub-agents. Their ``availableModes`` are
        permission modes (not agents), and selectable axes live in
        ``configOptions``. Per the axis-mapping model:
          * ``configOptions.effort`` → agents (one per level), with the
            ``default`` level FOLDED into the runtime's base agent (empty label);
          * ``configOptions.model`` → model overrides;
          * ``configOptions.mode`` → the backend's native permission modes (raw
            capability for the trust-rung mapping).
        If a backend omits ``effort`` entirely, a single base agent is still
        surfaced so the runtime is selectable."""
        cfg = {
            str(o.get("id")): o
            for o in (session_new.get("configOptions") or [])
            if isinstance(o, dict) and o.get("id")
        }

        def _values(opt_id: str) -> list[dict]:
            opt = cfg.get(opt_id) or {}
            return [o for o in (opt.get("options") or []) if isinstance(o, dict)]

        models = [str(o.get("value")) for o in _values("model") if o.get("value")]
        permission_modes = [str(o.get("value")) for o in _values("mode") if o.get("value")]

        # Effort is a per-turn SETTING, not an agent identity: surface the backend's
        # declared effort options VERBATIM as supported_efforts (the composer's
        # ReasoningPill populates from these + applies them via set_effort_request).
        # Exactly ONE base agent per runtime — no effort-agent explosion. The
        # "default" level is the empty selection ("" = backend default), so it isn't
        # listed as a pickable value.
        supported_efforts: list[dict] = []
        for o in _values("effort"):
            value = str(o.get("value", "")).strip()
            if not value or value == "default":
                continue
            supported_efforts.append(
                {
                    "value": value,
                    "label": str(o.get("name") or value).strip(),
                }
            )
        agents: list[dict] = [
            {
                "id": "",
                "label": "",
                "description": "",
                "provider_agent": "",
                "reasoning_effort": "",
                "use_runtime_prefix": True,
            }
        ]
        return DiscoveryResult(
            agents=agents,
            models=models,
            permission_modes=permission_modes,
            supported_efforts=supported_efforts,
        )


class ClaudeCodeDialect(ZedAdapterDialect):
    """`@zed-industries/claude-code-acp` driving the Claude Code CLI."""

    name = "claude"

    #: The adapter enables Claude Code's ``AskUserQuestion`` only for a client that advertises
    #: form elicitation, and then asks each of its questions over ``elicitation/create``.
    asks_through_elicitation = True

    def child_process_names(self) -> tuple[str, ...]:
        return ("claude",)


class CodexDialect(ZedAdapterDialect):
    """`@zed-industries/codex-acp` driving the OpenAI Codex CLI."""

    name = "codex"

    #: codex-acp's ``configId="mode"`` vocabulary is NOT claude-code's. Measured off a
    #: live ``session/new`` snapshot, its ``options`` are exactly::
    #:
    #:   read-only          "Requires approval to edit files and run commands."
    #:   agent              "Read and edit files, and run commands."          ← its DEFAULT
    #:   agent-full-access  "…edit files outside this workspace and run commands
    #:                       with network access."
    #:
    #: so the canonical ``default`` was answered ``-32602 Invalid params`` and every
    #: codex session stayed on ``agent`` — the self-approving mode the host must leave.
    #: ``read-only`` is codex's most-restrictive mode and the only one that makes the
    #: host the permission authority, so that is where the canonical restrictive mode
    #: lands. ``plan`` maps there too: codex's planning switch is a DIFFERENT axis
    #: (``configId="collaboration_mode"``, values ``default``/``plan``), and on the
    #: permission axis "plan" means "change nothing without asking" — which is
    #: ``read-only``. The auto-approve modes map to the nearest codex equivalent so
    #: the unattended path still widens to something codex accepts.
    #: Keyed on ``permission_authority.canonical_mode`` form, because the unattended
    #: path forwards the caller's auto-approve mode VERBATIM — any of that module's
    #: aliases (``yolo``, ``fullAuto``, ``accept-edits``…) can arrive here, not just the
    #: five claude-code spellings.
    _NATIVE_MODES = {
        # host-authority / behavioural → codex's most restrictive
        "default": "read-only",
        "plan": "read-only",
        # "auto-approve edits, still gate the rest" → codex's workspace-scoped mode
        "acceptedits": "agent",
        "acceptall": "agent",
        "acceptalledits": "agent",
        "dontask": "agent",
        "neverask": "agent",
        # "do not gate anything" → codex's unsandboxed mode. Anything narrower would
        # still escalate network + out-of-workspace writes (measured), so an unattended
        # run pointed at `agent` would wedge on exactly the calls an unattended run must make.
        "bypasspermissions": "agent-full-access",
        "bypass": "agent-full-access",
        "yolo": "agent-full-access",
        "allowall": "agent-full-access",
        "dangerfullaccess": "agent-full-access",
        "fullauto": "agent-full-access",
        "autoapprove": "agent-full-access",
        "auto": "agent-full-access",
    }

    def native_mode(self, mode: str) -> str:
        """Canonical → codex vocabulary, fail-closed.

        An unrecognised mode becomes ``read-only``, not ``agent`` and not "send
        nothing": both of those leave codex on its self-approving default, which is
        the failure this method exists to remove. The restrictive answer is the only
        safe one for a value the host cannot interpret."""
        from personalclaw.acp.permission_authority import canonical_mode

        return self._NATIVE_MODES.get(canonical_mode(mode), "read-only")

    def child_process_names(self) -> tuple[str, ...]:
        return ("codex",)


# Registry of dialects bundles select by id (the ``<cli>`` of ``acp:<cli>``).
# Core ships these neutral shapes; a bundle picks one via options["dialect"].
_DIALECTS: dict[str, type[ACPDialect]] = {
    "default": DefaultDialect,
    "claude-code": ClaudeCodeDialect,
    "codex": CodexDialect,
}


def get_dialect(name: str | None) -> ACPDialect:
    """Resolve a dialect by id (``acp:<cli>`` suffix). Unknown/empty → default."""
    return _DIALECTS.get((name or "").strip(), DefaultDialect)()
