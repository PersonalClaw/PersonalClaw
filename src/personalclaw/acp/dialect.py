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
``session/set_model``, permission options keyed ``id``/``label``). Bundles ship
their own subclasses (e.g. an int-``protocolVersion`` / ``set_config_option``
dialect for Claude Code and Codex).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Protocol method names + outcome constants live in acp.types (kept here as a
# local import inside methods would be circular-safe, but these are plain
# strings shared with the client, so import at module top is fine — types has
# no upward deps).
from personalclaw.acp.types import (
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
    (task #33) maps onto PersonalClaw rungs.

    ``supported_efforts`` are the backend-declared reasoning-effort options (from
    ``configOptions.effort``), surfaced VERBATIM as ``{value, label}`` — PClaw does
    NOT invent or translate an effort scale. Empty = the runtime has no effort axis.
    Effort is a per-turn session setting (``set_effort_request``), NOT an agent
    identity, so effort levels are no longer exploded into separate agents."""

    agents: list[dict]
    models: list[str]
    permission_modes: list[str]
    supported_efforts: list[dict] = field(default_factory=list)


class ACPDialect:
    """Strategy for the per-CLI ACP protocol divergences.

    A dialect owns ONLY the points where ACP agents differ. Everything else
    (spawn, framing, event loop, the neutral event mapping) stays in the
    vendor-neutral client. Subclasses override only what differs from
    :class:`DefaultDialect`.
    """

    #: Short identifier, e.g. "default", "claude", "codex". For logging only.
    name: str = "default"

    #: P9 capability gate — whether this backend actually SERVICES multiple sessions
    #: concurrently on ONE process (interleaved session/prompt), vs. internally
    #: serializing them. Default **False** (safe): the client keeps one-session-per-
    #: process until a backend is PROVEN concurrent by a live 2-session spike. A True
    #: here opts a backend into the FrameRouter demux + per-connection session pooling.
    #: Never assume True — an un-verified backend that actually serializes would deadlock
    #: the second session's dispatcher.
    supports_concurrent_sessions: bool = False

    # ── handshake ──
    def protocol_version(self) -> object:
        """Value sent as ``initialize.protocolVersion`` (date-string or int)."""
        return "2025-08-22"

    def client_info(self, *, client_name: str, client_version: str) -> dict:
        """The ``initialize.clientInfo`` block."""
        return {"name": client_name, "version": client_version}

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
        *agent*). Some adapters expose a separate permission-mode axis — Claude
        Code / Codex offer ``default`` / ``acceptEdits`` / ``plan`` / ``dontAsk``
        / ``bypassPermissions`` via ``session/set_config_option`` (``configId =
        "mode"``). The default dialect has NO separate mode axis — its
        ``set_mode`` already *is* agent activation — so it returns ``None`` and
        the client skips the step. Empty ``mode`` → ``None`` (use agent default).

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

        ``kind`` is the ACP ``PermissionOptionKind`` (``allow_once`` /
        ``allow_always`` / ``reject_once`` / ``reject_always``) when the agent
        supplies it; the host uses it to select the right ``optionId`` to echo
        back on approval (the ``id`` is agent-defined and is NOT assumed to be a
        well-known constant — see :meth:`AcpClient.approve_tool`)."""
        opts = [
            {"id": o.get("id", ""), "label": o.get("label", ""), "kind": o.get("kind", "")}
            for o in raw_options
        ]
        return [o for o in opts if o["id"]]

    def default_permission_options(self) -> list[dict[str, str]]:
        """Fallback options when the request carries none."""
        return [
            {"id": OPTION_ALLOW_ONCE, "label": "Allow once", "kind": OPTION_ALLOW_ONCE},
            {"id": OPTION_ALLOW_ALWAYS, "label": "Allow always", "kind": OPTION_ALLOW_ALWAYS},
        ]

    def select_allow_option_id(
        self, offered: list[dict[str, str]], *, prefer_always: bool = False
    ) -> str:
        """Pick the ``optionId`` to echo back when approving, from the options
        the agent actually offered.

        The agent's ``optionId`` values are agent-defined — they are NOT
        guaranteed to be the well-known ``allow_once`` / ``allow_always``
        constants (claude-code-acp uses different ids). Selection is therefore
        driven by the spec-defined ``kind`` classifier, falling back to the
        literal id and then to the first non-reject option. Returns ``""`` only
        when nothing approvable was offered (caller falls back to the default).
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

        order = (always, once, allow_any) if prefer_always else (once, always, allow_any)
        for bucket in order:
            if bucket:
                return bucket[0]["id"]
        # No clearly-allow option — fall back to the first non-reject option.
        for o in offered:
            ki = (o.get("kind") or o.get("id") or "").lower()
            if not ki.startswith("reject") and "cancel" not in ki and "deny" not in ki:
                return o["id"]
        return ""

    def approve_outcome(self, option_id: str) -> dict:
        """The ``outcome`` payload for an approved tool (selected option)."""
        return {"outcome": {"outcome": OUTCOME_SELECTED, "optionId": option_id}}

    def reject_outcome(self) -> dict:
        """The ``outcome`` payload for a rejected tool."""
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

    # A CLI speaking this dialect was PROVEN concurrent by the P9 live 2-session
    # spike (2026-07-06): two sessions on one process, interleaved session/update frames.
    # The Zed adapters (ClaudeCode/Codex) stay at the base False until their own spike.
    supports_concurrent_sessions = True


# ``session/set_config_option`` — used by the Zed ACP adapters (claude/codex)
# to set the model, instead of the default dialect's ``session/set_model``.
METHOD_SET_CONFIG_OPTION = "session/set_config_option"


class ZedAdapterDialect(ACPDialect):
    """Shared shape for the Zed-maintained ACP adapters (``claude-code-acp``,
    ``codex-acp``): integer ``protocolVersion`` (1), model set via
    ``session/set_config_option`` rather than ``session/set_model``, NO
    ``session/set_mode`` agent-activation step (the adapter binds the agent at
    spawn), and permission options keyed ``optionId``/``name`` per the public
    ACP spec.
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

    def set_mode_request(self, *, session_id: str, mode: str) -> AcpRequest | None:
        """Set the Zed-adapter permission mode via ``session/set_config_option``
        (``configId="mode"``). The adapter validates the value against the
        model's currently-available modes (``default`` / ``acceptEdits`` /
        ``plan`` / ``dontAsk`` / ``bypassPermissions``) and rejects unknown ones,
        so we forward the value verbatim and let the adapter be the authority.
        Empty ``mode`` → ``None`` (keep the adapter's default)."""
        if mode:
            return AcpRequest(
                METHOD_SET_CONFIG_OPTION,
                {"sessionId": session_id, "configId": "mode", "value": mode},
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

    def parse_permission_options(self, raw_options: list[dict]) -> list[dict[str, str]]:
        # Public ACP spec: options carry ``optionId`` + ``name`` + ``kind`` (vs
        # the default dialect's ``id``/``label``). Accept either so a spec tweak can't silently
        # drop. ``kind`` is the agent-independent allow/reject classifier the
        # host uses to pick which ``optionId`` to echo back (claude-code-acp's
        # ``optionId`` is NOT the literal ``allow_once`` — it must be read off
        # the offered option, not assumed; this is what made fs_write approvals
        # silently fail).
        opts = [
            {
                "id": o.get("optionId") or o.get("id", ""),
                "label": o.get("name") or o.get("label", ""),
                "kind": o.get("kind", ""),
            }
            for o in raw_options
        ]
        return [o for o in opts if o["id"]]


class ClaudeCodeDialect(ZedAdapterDialect):
    """`@zed-industries/claude-code-acp` driving the Claude Code CLI."""

    name = "claude"

    def child_process_names(self) -> tuple[str, ...]:
        return ("claude",)


class CodexDialect(ZedAdapterDialect):
    """`@zed-industries/codex-acp` driving the OpenAI Codex CLI."""

    name = "codex"

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
