"""The editable-config registry: every config path a write may change, and how (`_EDITABLE_CONFIG`).

One entry per dotted path — its type, its bounds or values, its sanitizer and, for a field that is
part of the owner's security posture, the `SecurityControl` saying which direction loosens it (the
rules themselves are `edit_spec`'s). Every config write path validates against this one table: the
dashboard's PATCH (`dashboard/handlers/core.py`), `personalclaw config set` (`cli_config`), the
memory settings endpoint, pack imports, and an app's `permissions.config` declaration at install
time (`apps/manifest`).

It lives in the config layer, below all of them, so a caller that needs to know whether a setting
is editable never has to import the HTTP surface to find out. The inert-surface census
(`scripts/generate_inert_surface_baseline.py`) parses THIS file for the literal to find entries
with no backing field, and fails loudly if it cannot find it.
"""

from __future__ import annotations

import unicodedata

from personalclaw import self_update
from personalclaw.auth.lifetimes import MAX_LIFETIME_SECS
from personalclaw.config.edit_spec import (
    ConfigValueError,
    NotASecurityControl,
    SecurityControl,
    counted,
    in_dollars,
    loosens_egress,
    loosens_toward,
    loosens_when,
    loosens_when_added,
    loosens_when_changed,
    loosens_when_longer,
    loosens_when_raised,
    loosens_when_removed,
    loosens_when_shorter,
    named,
    shown,
)
from personalclaw.config.loader import (
    APPROVAL_TIMEOUT_MINUTES_MAX,
    APPROVAL_TIMEOUT_MINUTES_MIN,
    MEMORY_VAULT_MODES,
    PUSH_BACKENDS,
    _bot_name_disallowed,
)


# Allowed editable config paths and their validators
def _agent_values() -> set[str]:
    """Return allowed pool_agent values: empty string + all configured agent names."""
    from personalclaw.config.loader import AppConfig

    return {"", *AppConfig.load().agents}


def _context_engine_values() -> set[str]:
    """Allowed ``session.context_engine`` values — whatever is REGISTERED right now.

    A ``values_fn`` rather than a static enum because the registry is open: an app bundle
    registers its engine at startup, and a hardcoded list here would refuse the only name
    that bundle made valid. Today this resolves to ``{"default"}`` alone.
    """
    from personalclaw.context_engine import available_engines

    return set(available_engines())


def _bot_name_validator(value: str) -> str:
    """Refuse an assistant name holding a character a name may not contain; trim the rest.

    REFUSED, not stripped. This used to hand the value to the loader's sanitizer, which dropped
    what it disliked while the PATCH answered 200: `Chloé's Aide` was stored as `Chlos Aide`, and
    Settings showed "Saved" beside the name as typed. A stripped name is one nobody typed, and the
    caller has been told it succeeded — so the refusal names each character, and the user decides
    what the name becomes. Trimming surrounding whitespace changes no name, so that one is applied.

    Which characters are allowed is the loader's call (`_bot_name_disallowed`): one policy for this
    write side and for `load()`, which strips the same characters from a hand-edited file.
    """
    name = value.strip()
    refused = _bot_name_disallowed(name)
    if refused:
        # A glyph is shown quoted; an invisible character (control, format, space, mark) is shown
        # as its code point and name, since quoting it would print an empty pair of quotes.
        shown = ", ".join(
            (
                f"“{ch}”"
                if ch.isprintable() and not unicodedata.category(ch).startswith(("M", "Z"))
                else f"U+{ord(ch):04X} {unicodedata.name(ch, '')}".rstrip()
            )
            for ch in refused
        )
        raise ConfigValueError(
            f"{shown} can't be part of a name — use letters (any language), digits, spaces, "
            "apostrophes, hyphens, periods or underscores",
            f"agent.bot_name={value}",
        )
    return name


def _approval_channel_validator(value: str) -> str:
    """Refuse a "Send approvals to" channel this gateway has no channel app for; trim the rest.

    Checked against the LIVE channel registry, as ``session.context_engine`` is, so a name only
    becomes settable once its app has registered it. Empty is always allowed: it is the default
    order (the first connected channel that knows the owner). The refusal names what can be
    chosen, because "invalid value" gives the owner nothing to pick instead.
    """
    from personalclaw.channel_transports import WEBUI_TRANSPORT, list_transports

    name = value.strip()
    channels = sorted(n for n in list_transports() if n != WEBUI_TRANSPORT)
    if name and name not in channels:
        offer = ", ".join(channels) if channels else "none is set up"
        raise ConfigValueError(
            f"{name!r} is not a chat channel here (channels: {offer}); leave it empty to ask the "
            "first connected channel that knows you",
            f"agent.approval_channel={value}",
        )
    return name


#: Longer than any chat service's channel id, short enough that a pasted paragraph is refused.
WATCHED_CHANNEL_ID_MAX_LEN = 64


def _watched_channels_validator(ids: list[str]) -> list[str]:
    """Refuse an entry that is not one channel id; trim and de-duplicate the rest.

    A channel id is one word (``C0123456789``). A name with a space, a pasted link or an empty
    entry names no channel the inbox could read, and a list that quietly kept it would read as
    watched while nothing was, so the refusal names the entry. Surrounding whitespace changes no
    id, so that is trimmed, as a repeat is dropped.
    """
    kept: list[str] = []
    for raw in ids:
        channel = raw.strip()
        if (
            not channel
            or any(ch.isspace() for ch in channel)
            or "/" in channel
            or len(channel) > WATCHED_CHANNEL_ID_MAX_LEN
        ):
            raise ConfigValueError(
                f"{raw!r} is not a channel id: add each channel by its id alone, one word "
                "(for example C0123456789)",
                f"inbox.watched_channels={raw!r}",
            )
        if channel not in kept:
            kept.append(channel)
    return kept


def _version_pin_sanitizer(value: str) -> str:
    """Refuse a version pin that could never name a release; store the resolvers' spelling.

    The rule is `self_update.normalize_pin`'s, not a copy of it: `personalclaw update --to`
    writes the same field through `set_version_pin`, and two copies of "what a pin looks like"
    would let the CLI and Settings disagree about which pins are storable.
    """
    try:
        return self_update.normalize_pin(value)
    except ValueError as exc:
        raise ConfigValueError(str(exc), f"updates.pin={value}") from None


def _quiet_window_sanitizer(value: str) -> str:
    """Refuse a default quiet window the scheduler could not read; store it as typed.

    The rule is `triggers.calendar.parse_default_window`'s — the call the scheduler makes when it
    applies the default — looked up at call time, so a window that saves is a window that applies.
    Checking only "a string of at most 64 characters" let `10pm-7am` save and then hold nothing.
    """
    from personalclaw.triggers import calendar

    try:
        calendar.parse_default_window(value)
    except ValueError as exc:
        raise ConfigValueError(str(exc), f"workflows.default_quiet_windows={value}") from None
    return value.strip()


def _push_to_talk_chord_sanitizer(value: str) -> str:
    """Normalize a push-to-talk accelerator at the WRITE boundary.

    ``" Command + Shift + Space "`` and ``"Command+Shift+Space"`` are the same chord to
    a user and different strings to `globalShortcut.register`. Collapsing the spacing
    here means the value the shell is handed is byte-identical to the value stored and
    to the value the Settings control redisplays — otherwise a chord round-trips as
    "saved" while binding nothing.

    Empty stays empty: `load()` turns that into the shipped default, which is the one
    place that decision belongs.
    """
    return "+".join(part.strip() for part in value.split("+") if part.strip())


def _scratchpad_path_sanitizer(value: str) -> str:
    """Canonicalize the watched-scratchpad path at the WRITE boundary.

    `pathguard.canonicalize` is the same realpath+expanduser the trigger capability fence uses, so
    a stored path can never differ from the one a fence would compare — a config file holding
    ``~/notes/../.ssh/id_rsa`` while the runtime resolved something else is the split-brain this
    prevents. Empty stays empty: "" is how the feature is turned off.
    """
    if not value.strip():
        return ""
    from personalclaw.triggers.pathguard import canonicalize

    return canonicalize(value.strip())


def _transport_named(name: object) -> str:
    """A sync transport as the Backups page names it — its app's display name, ``Folder Sync`` —
    in the consent that sends the home through it. A name no installed transport answers to is
    shown as it is: sync stays idle on it, and the owner should see exactly what was typed."""
    if not isinstance(name, str) or not name.strip():
        return "None"
    from personalclaw.sync_transports.registry import get_transport

    transport = get_transport(name)
    return (getattr(transport, "display_name", "") or name) if transport else f"“{name}”"


def _sync_switch(on: object) -> str:
    """The "Sync this instance" switch as its consent shows it: turned on, it names the transport
    the home leaves through, or says none is chosen yet — "the configured transport" named none."""
    if on is not True:
        return shown(on)
    from personalclaw.config.loader import AppConfig

    transport = AppConfig.load().durability.sync_transport
    if not transport.strip():
        return "On, with no transport chosen yet"
    return f"On, through {_transport_named(transport)}"


_AUTONOMY_OFFER_ONLY = (
    "decides when a promotion is OFFERED; granting one is still the owner's click on the "
    "owner-only POST /api/autonomy/grant"
)


def _external_surface(label: str) -> dict:
    """The spec for one inbound surface's kill switch — a security control in its own right,
    because a surface that is on accepts requests from any client holding its token."""
    return {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            f"The external {label} surface turns on: a client holding one of its tokens can "
            "reach this gateway through it.",
        ),
    }


#: 🔴 A `"security"` key makes a field part of the owner's security posture — the ONE list of
#: security-sensitive config fields is the set of entries here holding a `SecurityControl`.
#: Being on it means an app-scoped caller can never write the field, and the owner's write that
#: LOOSENS it needs `"confirm": true` (both decided in `config/edit_spec.py`, which also says
#: why). Every field in `edit_spec.SECURITY_SECTIONS` must declare one or the other, and
#: `tests/test_security_posture_rail.py` enumerates the list and drives both refusals for each.
#: The `consent` sentence is shown in a consent dialog, so it must be true of the looser value.
_EDITABLE_CONFIG: dict[str, dict] = {
    "agent.approval_mode": {
        "type": "enum",
        "values": ["auto", "interactive", "trust_reads"],
        "security": SecurityControl(
            loosens_toward("interactive", "trust_reads", "auto"),
            "A looser approval mode lets tool calls run without asking you first. 'Trust "
            "reads' lets a chat run a read-only shell command without asking. 'Auto' lets an "
            "agent no chat started (a trigger's Invoke Agent agent, a subagent started outside a "
            "chat) approve every tool call it makes, file changes and shell commands included, "
            "without asking you; chats still ask.",
            shows=named(
                {"interactive": "Ask each time", "trust_reads": "Trust reads", "auto": "Auto"}
            ),
        ),
    },
    "agent.yolo": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "Turning YOLO on skips every tool-approval confirmation, for every session, until "
            "it is turned off.",
        ),
    },
    # Not a SecurityControl: a longer wait keeps asking and a shorter one denies sooner —
    # either way the call runs only on an explicit approval, and an unanswered one is refused.
    "agent.approval_timeout_minutes": {
        "type": "int",
        "min": APPROVAL_TIMEOUT_MINUTES_MIN,
        "max": APPROVAL_TIMEOUT_MINUTES_MAX,
    },
    # "Send approvals to". Not a SecurityControl: whichever channel asks, the call runs only on
    # the owner's explicit answer, and each channel refuses a press from anyone but its owner.
    "agent.approval_channel": {
        "type": "str",
        "max_len": 64,
        "sanitize": _approval_channel_validator,
    },
    "agent.soft_stop_budget_secs": {"type": "float", "min": 0.5, "max": 60.0},
    "agent.max_subagents": {"type": "int", "min": 0, "max": 16},
    "agent.subagent_max_turns": {"type": "int", "min": 1, "max": 200},
    "agent.subagent_timeout_secs": {"type": "int", "min": 60, "max": 7200},
    "agent.spawn_min_memory_gb": {"type": "float", "min": 0.0, "max": 64.0},
    "agent.subagent_cwd_allowed_roots": {
        "type": "str_list",
        "max_items": 20,
        "security": SecurityControl(
            loosens_when_added(),
            "The agent's file tools may read files in the added folders without asking, and "
            "change them under the same approval as the workspace's files (a room member as "
            "its own tier allows). Subagents may be started in them.",
        ),
    },
    # Resource ceilings for agent-influenced child
    # processes, delivered post-exec by the ceiling shim. 0 disables an individual
    # limit. session_host (ACP) is exempt from the NOFILE cap by profile, not config.
    "sandbox.nofile": {
        "type": "int",
        "min": 0,
        "max": 1_048_576,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0),
            "A process the agent starts may hold more open files — 0 removes the limit.",
        ),
    },
    "sandbox.max_pids": {
        "type": "int",
        "min": 0,
        "max": 100_000,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0),
            "A process the agent starts may start more processes — 0 removes the limit.",
        ),
    },
    "sandbox.max_rss_mb": {
        "type": "int",
        "min": 0,
        "max": 1_048_576,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0),
            "A process the agent starts may use more memory — 0 removes the limit.",
            shows=counted("MB"),
        ),
    },
    # Opt into the Linux-only second enforcement tier (a transient systemd user
    # scope carrying TasksMax/MemoryMax derived from the ceilings above, so they bound the
    # whole child subtree). Editable because it is the one knob that changes HOW the
    # ceilings are delivered; it is a no-op where systemd user scopes are unavailable.
    "sandbox.cgroup_scopes": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(False),
            "Processes the agent starts stop being wrapped in a cgroup scope, so their limits "
            "no longer cover the processes they start in turn.",
        ),
    },
    # The declared-needs seam for the child-env allowlist. Names only; the
    # credential floor still refuses a sensitive name at spawn, so a write here cannot
    # hand a hook the gateway's AWS session.
    "sandbox.env_passthrough": {
        "type": "str_list",
        "max_items": 40,
        "security": SecurityControl(
            loosens_when_added(),
            "Processes the agent starts inherit the added environment variables (names on "
            "the credential floor are still withheld).",
        ),
    },
    # Bounds on the turn-bound file checkpoint store. Only the
    # BOUNDS are editable: which files may never be copied is a code-level floor
    # (turn_checkpoints.NEVER_CAPTURE_GLOBS) with no config field, so no PATCH can widen it.
    "checkpoints.enabled": {"type": "bool"},
    "checkpoints.max_mb": {"type": "int", "min": 0, "max": 100_000},
    "checkpoints.max_turns": {"type": "int", "min": 1, "max": 1_000},
    "checkpoints.max_file_mb": {"type": "int", "min": 0, "max": 10_000},
    "security.denied_commands": {
        "type": "str_list",
        "max_items": 100,
        "each_regex": True,
        "security": SecurityControl(
            loosens_when_removed(),
            "The agent may run commands the removed pattern used to block (the built-in "
            "denylist still applies).",
        ),
    },
    "security.egress": {
        "type": "egress",
        "security": SecurityControl(
            loosens_egress,
            "The agent's outbound fetches, scrapes and webhooks can reach an address the egress "
            "guard blocked before — a private or LAN address, or a host you had denied — and its "
            "shell commands, and the programs apps start, reach a host you add without asking.",
        ),
    },
    # Flipping this changes where NEW credentials are written; it deliberately does
    # NOT move the secrets already in `.env` — that is the separate, snapshot-backed,
    # consented `credentials_to_keychain` action (`/api/security/credentials/migrate`). A
    # PATCH that silently rewrote the credential store would be an unconfirmed data move.
    "security.credential_keychain": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(False),
            # True with or without a keychain answering: where none does they already go to
            # .env, and turning the request off keeps them there once one does.
            "New credentials will be saved to the .env file (mode 0600), and not to the OS "
            "keychain even where one is available.",
        ),
    },
    # The per-server elicitation grant. A `str_list` and not a bool, because the
    # whole point is that consent is per SERVER: one boolean here would be the global
    # "MCP can interrupt me" switch, which must not exist. `max_items` is generous rather than
    # tight — it bounds the list, and a home with 60 configured MCP servers is a real
    # shape, so a low cap would refuse a legitimate grant.
    "security.mcp_elicitation_servers": {
        "type": "str_list",
        "max_items": 100,
        "security": SecurityControl(
            loosens_when_added(),
            "The added MCP server will be able to interrupt its own tool calls to ask you "
            "questions.",
        ),
    },
    # Per place, like the elicitation grant: each id is one folder or sign-in outside the
    # home (`outside_home.places()`), so allowing one never allows the others.
    "security.outside_home": {
        "type": "str_list",
        "max_items": 50,
        "security": SecurityControl(
            loosens_when_added(),
            "PersonalClaw will read the place you added, which is outside its own home.",
        ),
    },
    # The runtime-editable guardrail subset. Incident is
    # NOT here — it's its own endpoint. Budgets/breaker/scan are
    # plain scalars edited via Settings.
    "guardrails.budgets.max_tokens_per_run": {
        "type": "int",
        "min": 0,
        "max": 100_000_000,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0),
            "A single run may spend more tokens — 0 removes the limit.",
            shows=counted("tokens"),
        ),
    },
    "guardrails.budgets.max_tokens_per_day": {
        "type": "int",
        "min": 0,
        "max": 1_000_000_000,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0),
            "The agent may spend more tokens per day — 0 removes the limit.",
            shows=counted("tokens"),
        ),
    },
    "guardrails.budgets.max_dollars_per_day": {
        "type": "float",
        "min": 0.0,
        "max": 100_000.0,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0),
            "The agent may spend more money per day — 0 removes the limit.",
            shows=in_dollars,
        ),
    },
    "guardrails.breaker.failure_threshold": {
        "type": "int",
        "min": 1,
        "max": 100,
        "security": NotASecurityControl(
            "fails a model PROVIDER over fast: it decides reliability, not what the agent may do"
        ),
    },
    "guardrails.breaker.recovery_secs": {
        "type": "float",
        "min": 0.0,
        "max": 3600.0,
        "security": NotASecurityControl(
            "fails a model PROVIDER over fast: it decides reliability, not what the agent may do"
        ),
    },
    # The TOOL-loop breaker's abort ceiling — a different breaker
    # from the two rows above, which fail a model PROVIDER fast. `min: 1` mirrors
    # `load()`'s floor: at 0 the `>` comparison aborts a run on its first failed call.
    "guardrails.loop_breaker.circuit_threshold": {
        "type": "int",
        "min": 1,
        "max": 1000,
        "security": SecurityControl(
            loosens_when_raised(),
            "A run may fail more tool calls in a row before it is stopped.",
        ),
    },
    "guardrails.scan_mode": {
        "type": "enum",
        "values": ["warn", "redact", "block"],
        "security": SecurityControl(
            loosens_toward("block", "redact", "warn"),
            "Secrets or personal data found in a prompt bound for a remote model provider get "
            "less protection — 'redact' replaces them, 'warn' only logs them.",
            shows=named({"block": "Block", "redact": "Redact", "warn": "Warn"}),
        ),
    },
    # Model routing — the runtime-editable
    # subset: the master switch plus the tuning numbers a user reaches for after watching
    # what routing actually did. Per-use-case mode/pin are NOT here: they live in
    # use_case_settings/{uc}.json + routing_policy.json, beside the other bindings state.
    "routing.enabled": {"type": "bool"},
    "routing.local_timeout_secs": {"type": "float", "min": 0.0, "max": 600.0},
    "routing.min_samples": {"type": "int", "min": 1, "max": 10_000},
    "routing.hysteresis": {"type": "float", "min": 0.0, "max": 1.0},
    "routing.cloud_quality_margin": {"type": "float", "min": 0.0, "max": 1.0},
    "routing.energy_sampling": {"type": "bool"},
    "routing.reproposal_cooldown_days": {"type": "int", "min": 0, "max": 365},
    # Earned-autonomy thresholds. Runtime-editable because these are the knobs a
    # user reaches for after seeing what the ladder actually proposed. Bounded on both
    # sides: `clean_approvals` floors at 1 (a bar of zero would offer a promotion to a
    # type with no record), and every ceiling keeps a typo from budgeting a decade.
    #
    # None of the five is a security control: they decide when a promotion is OFFERED. Granting
    # one is still the owner's click on `POST /api/autonomy/grant`, which is owner-only.
    "guardrails.autonomy.clean_approvals": {
        "type": "int",
        "min": 1,
        "max": 1000,
        "security": NotASecurityControl(_AUTONOMY_OFFER_ONLY),
    },
    "guardrails.autonomy.min_days": {
        "type": "int",
        "min": 0,
        "max": 365,
        "security": NotASecurityControl(_AUTONOMY_OFFER_ONLY),
    },
    "guardrails.autonomy.max_rejections": {
        "type": "int",
        "min": 0,
        "max": 100,
        "security": NotASecurityControl(_AUTONOMY_OFFER_ONLY),
    },
    "guardrails.autonomy.cooldown_days": {
        "type": "int",
        "min": 0,
        "max": 365,
        "security": NotASecurityControl(_AUTONOMY_OFFER_ONLY),
    },
    "guardrails.autonomy.evidence_window_days": {
        "type": "int",
        "min": 1,
        "max": 365,
        "security": NotASecurityControl(_AUTONOMY_OFFER_ONLY),
    },
    # The hands-free voice loop. All six are convenience
    # knobs (comfort, not safety): the phrase lists drive frontend gating, the
    # booleans switch echo filtering, mute-during-playback, pre-speech cleaning
    # and the voice-origin disclaimer.
    "voice.confirmation_phrases": {"type": "str_list", "max_items": 20},
    "voice.exit_phrases": {"type": "str_list", "max_items": 20},
    "voice.echo_filter_enabled": {"type": "bool"},
    "voice.duplex_mute_enabled": {"type": "bool"},
    "voice.clean_for_speech_enabled": {"type": "bool"},
    "voice.voice_disclaimer_enabled": {"type": "bool"},
    # The desktop push-to-talk chord. An Electron accelerator string, so
    # `max_len` and a whitespace normalise are all this boundary can usefully check:
    # whether the chord is BINDABLE is a question only the shell can answer (another
    # app may already own it), and it answers with a reason the Settings control
    # renders. Normalising here keeps the stored value byte-identical to what the
    # shell is asked to bind.
    "voice.push_to_talk_chord": {
        "type": "str",
        "max_len": 64,
        "sanitize": _push_to_talk_chord_sanitizer,
    },
    "resilience.doctor_enabled": {"type": "bool"},
    "resilience.degraded_indicator": {"type": "bool"},
    "resilience.mid_turn_policy": {
        "type": "enum",
        "values": ["queue", "steer", "cancel_and_replace"],
    },
    "resilience.cancel_replace_min_interval_secs": {"type": "float", "min": 0.0, "max": 60.0},
    "resilience.remediation.enabled": {"type": "bool"},
    "resilience.remediation.target_score": {"type": "int", "min": 0, "max": 100},
    "resilience.remediation.max_cost_usd": {"type": "float", "min": 0.0, "max": 100.0},
    "resilience.remediation.idle_minutes_healthy": {"type": "int", "min": 1, "max": 1440},
    "resilience.remediation.tick_minutes_degraded": {"type": "int", "min": 1, "max": 1440},
    # The scheduled-backup contract. Runtime-editable
    # because these are the knobs a user reaches for after seeing what the schedule
    # actually produced (the snapshot list shows keep-vs-prune before anything is
    # deleted). Retention caps are bounded, not unbounded: 0 disables a tier, and the
    # ceilings keep a typo from budgeting a decade of archives.
    "durability.auto_backup": {"type": "bool"},
    "durability.keep_daily": {"type": "int", "min": 0, "max": 365},
    "durability.keep_weekly": {"type": "int", "min": 0, "max": 260},
    "durability.keep_monthly": {"type": "int", "min": 0, "max": 120},
    "durability.restore_drills": {"type": "bool"},
    "durability.time_travel": {"type": "bool"},
    # Sync knobs. sync_enabled is fail-closed in load(); the
    # transport is a free-text provider name (validated against installed transports at
    # cycle time, not here — an unknown name simply leaves sync idle).
    #
    # All three sync fields decide where a copy of the whole home goes, so all three are
    # security controls: an app that could point sync at a transport it ships, switch it on and
    # turn encryption off would receive the home in the clear.
    "durability.sync_enabled": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "Your home starts syncing off this machine through the configured transport.",
            shows=_sync_switch,
        ),
    },
    "durability.sync_transport": {
        "type": "str",
        "max_len": 64,
        "security": SecurityControl(
            loosens_when_changed(),
            "Sync will send your home through a different transport.",
            shows=_transport_named,
        ),
    },
    "durability.sync_stale_after_secs": {"type": "int", "min": 30, "max": 86400},
    # The encryption tri-state. `values` is closed, so an out-of-scale value is
    # REFUSED at the boundary rather than silently landing as the safe default: a user who
    # mistypes a security control should be told, not quietly overruled. (load() still
    # falls back to "auto" for a hand-edited file, which this endpoint never produces.)
    "durability.sync_encrypt": {
        "type": "str",
        "max_len": 8,
        "values": ("auto", "on", "off"),
        "security": SecurityControl(
            loosens_toward("on", "auto", "off"),
            "Synced data may leave this machine unencrypted — 'auto' leaves a private git "
            "repository readable, and 'off' leaves every transport readable.",
            shows=named(
                {
                    "on": "Always encrypt",
                    "auto": "Automatic (per transport)",
                    "off": "Never encrypt",
                }
            ),
        ),
    },
    # The runtime-editable evals subset. These are the
    # knobs a user reaches for from Settings; each is a plain scalar. Deliberately
    # EXCLUDED: `evals.bakeoff_capture_enabled` — a privacy-sensitive input-capture
    # flag, off by default and SEL-audited, kept out of the one-click PATCH allowlist
    # (mirroring `inbound.mcp.allow_remote`); flipping it is a deliberate config edit.
    "evals.enabled": {"type": "bool"},
    "evals.study_default_k": {"type": "int", "min": 1, "max": 50},
    "evals.judge_agreement_floor": {"type": "float", "min": 0.0, "max": 1.0},
    "evals.ablation_cadence_days": {"type": "int", "min": 1, "max": 365},
    "evals.default_budget_usd": {"type": "float", "min": 0.0, "max": 1000.0},
    # #2680 — the `Provider:model` the paired evals (gate/ablation/skills-bench) score
    # against. Free-text on purpose: WHETHER the ref resolves is a question only the home
    # can answer (`providers[]` + the model's own availability), and it answers with a
    # reason the run records — an unresolvable ref makes a run REFUSE to score, which is
    # strictly more legible than this boundary guessing at a pool. `.strip()` mirrors
    # load(), so the file matches what runtime reads. Carries no secret: the ref names a
    # `providers[]` entry, and the cell's key still arrives by env-var NAME (cell_provider).
    "evals.benchmark_model_ref": {"type": "str", "max_len": 128, "sanitize": lambda v: v.strip()},
    # The runtime-editable triage subset.
    # `auto_execute_enabled` IS here on purpose: it is the one-click revoke, so
    # a user who dislikes what the digest did must be able to switch acting off from
    # the surface that showed them. Its blast radius is bounded elsewhere (the frozen
    # capability set, the per-run cap, the guardrails budget floor), not by hiding it.
    "proactive.triage_enabled": {"type": "bool"},
    "proactive.digest_schedule": {"type": "str", "max_len": 64},
    "proactive.auto_execute_enabled": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "The triage digest may act on items by itself, up to its per-run cap, without "
            "asking you first.",
        ),
    },
    "proactive.max_auto_actions_per_run": {
        "type": "int",
        "min": 0,
        "max": 50,
        "security": SecurityControl(
            loosens_when_raised(),
            "The triage digest may take more actions by itself in each run.",
        ),
    },
    "proactive.classifier_gate_enabled": {"type": "bool"},
    "proactive.decision_default_horizon_days": {"type": "int", "min": 1, "max": 3650},
    "tools.projection_rules": {"type": "projection_rules"},
    # Background compression feature flags (runtime-editable).
    "tools.bg_compress_enabled": {"type": "bool"},
    "tools.bg_compress_idle_days": {"type": "float", "min": 0.0, "max": 365.0},
    # Dynamic tool-group activation (runtime-editable). Takes
    # effect for sessions created after the change (activation state is per-runtime).
    "tools.groups_enabled": {"type": "bool"},
    # The runtime-editable subset of the inbound access seam.
    # The kill switches are here so turning a surface OFF takes effect on the next
    # request without a restart (that is the whole point of a kill switch).
    #
    # 🔴 Deliberately NOT PATCH-editable, and asserted as REFUSALS in
    # `test_external_access_seam.py` rather than merely absent from this dict:
    #   · `external_access.public_url` — the public URL is the security boundary for
    #     every non-loopback peer check (`inbound/auth.peer_allowed` compares the
    #     request Host to it). A boundary that moves on one PATCH is not a boundary.
    #   · `external_access.<surface>.allow_remote` — widening a network surface from
    #     loopback to the world is a deliberate config-file edit.
    #   · the per-surface TOKENS — they are not in `config.json` at all (they live in
    #     the credential store via `save_credential`), so there is no path here even in
    #     principle; token lifecycle is `personalclaw inbound token create <surface>`.
    "external_access.enabled": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "External access turns on: every surface that is switched on accepts requests "
            "from clients holding one of its tokens.",
        ),
    },
    "external_access.openai.enabled": _external_surface("OpenAI-compatible API"),
    "external_access.mcp.enabled": _external_surface("MCP"),
    "external_access.a2a.enabled": _external_surface("agent-to-agent (A2A)"),
    "external_access.capture.enabled": _external_surface("capture"),
    "external_access.bridge.enabled": _external_surface("bridge"),
    "external_access.rate_rps": {
        "type": "float",
        "min": 0.01,
        "max": 1000.0,
        "security": SecurityControl(
            loosens_when_raised(),
            "External clients may send more requests per second before they are limited.",
        ),
    },
    "external_access.rate_burst": {
        "type": "int",
        "min": 1,
        "max": 10000,
        "security": SecurityControl(
            loosens_when_raised(),
            "External clients may send a larger burst of requests before they are limited.",
        ),
    },
    "external_access.rate_concurrent": {
        "type": "int",
        "min": 1,
        "max": 256,
        "security": SecurityControl(
            loosens_when_raised(),
            "External clients may keep more requests running at once.",
        ),
    },
    "external_access.auto_disable_after_breaches": {
        "type": "int",
        "min": 0,
        "max": 10000,
        "security": SecurityControl(
            loosens_when_raised(unlimited=0, unlimited_reads="Never"),
            "An external surface tolerates more breaches before it switches itself off — 0 "
            "means it never does.",
        ),
    },
    # The capture surface's two operator knobs. Both are
    # runtime-editable: the pruner reads retention on each curator tick and the
    # streaming client reads the allow-list per forward, so neither needs a restart.
    # This is the ONE writable spelling (#2950): `load()` resolves the effective
    # window from this nested key whenever it is present, and a fresh config.json
    # always ships it — so the legacy flat `external_access.capture_retention_days`
    # was dead on arrival as a PATCH target: it wrote the file, `load()` never read
    # it back, and the panel reported 200 having changed nothing. The flat field
    # stays on the dataclass as a read-only mirror (older external readers may still
    # look at it); it is deliberately absent from this allowlist so there is exactly
    # one name to write.
    "external_access.capture.retention_days": {
        "type": "int",
        "min": 0,
        "max": 3650,
        "security": NotASecurityControl(
            "how long captured traffic is kept: it changes what is stored, not who may connect"
        ),
    },
    # Operator-visible by requirement: upstream forwarding must be guarded
    # against an explicit host list rather than hand-rolled unguarded egress, which
    # means the operator has to be able to SEE and edit the list.
    "external_access.capture.upstream_allowlist": {
        "type": "str_list",
        "max_items": 40,
        "security": SecurityControl(
            loosens_when_added(),
            "The capture surface may forward requests to the added upstream hosts.",
        ),
    },
    # Entity linking. Runtime-editable: turning it off
    # stops new links immediately (existing links are kept, so re-enabling doesn't
    # need a backfill).
    "memory.graph_enabled": {"type": "bool"},
    # The push reflex. Both runtime-editable: the reflex
    # reads them per turn, so a change takes effect on the next message with no restart.
    "memory.push_context": {"type": "bool"},
    "memory.push_min_confidence": {"type": "float", "min": 0.0, "max": 1.0},
    # The confidence a LEARNED semantic fact needs before memory keeps it. Its
    # only control used to be a field on the Vector Memory app that nothing read; this is the
    # value the store actually applies, read live, so a change takes effect on the next write.
    "memory.semantic_confidence_threshold": {"type": "float", "min": 0.0, "max": 1.0},
    # The topology block and the holder axis. Both
    # runtime-editable: the block is read per new session and the axis per write, so a
    # change takes effect without a restart. Turning the axis OFF stops NEW attribution
    # and stops rendering it; already-attributed rows keep their holder, so flipping it
    # back on does not need a backfill.
    "memory.graph_topology_in_context": {"type": "bool"},
    "memory.holder_attribution": {"type": "bool"},
    # The Slots block budget. Runtime-editable because
    # it is read per session build, so a change takes effect on the next new session. The
    # bounds mirror `memory_slots.SLOTS_BLOCK_MIN/HARD_MAX_CHARS`: the consumer clamps to the
    # same range, so a value that got past this allowlist by another route still cannot widen
    # the always-injected block. The per-slot caps are NOT here — which individual register is
    # full is a per-class judgment fixed in code, not a number to tune from Settings.
    "memory.slot_size_cap": {"type": "int", "min": 200, "max": 4000},
    # The retention / behaviour / vault fields that `PUT /api/memory/settings` writes.
    # Declared HERE rather than validated a second time in that handler: they are editable
    # config fields by definition (the Memory panel writes them), and an endpoint carrying
    # its own private copy of the rules is how `graph_enabled` ended up with two answers —
    # this allowlist demanded a real boolean while the PUT ran `bool(body[flag])`, so
    # `{"graph_enabled": "false"}` turned the feature ON. Bounds are stated, not clamped:
    # a request that "succeeded" having stored something else is the one outcome a caller
    # can never notice. The two floors (0.5h idle, 7d retention) are the ones the PUT
    # already enforced by clamping; the ceilings are new and deliberately generous —
    # they exist so an accidental extra digit is a 400 rather than "retention: never".
    "memory.history_idle_hours": {"type": "float", "min": 0.5, "max": 8760.0},
    "memory.history_max_days": {"type": "int", "min": 7, "max": 3650},
    "memory.l1_manifest": {"type": "bool"},
    "memory.active_recall": {"type": "bool"},
    "memory.proactive_commitments": {"type": "bool"},
    # The one-time "legacy memory has been migrated" marker. Editable because the Memory
    # panel clears it to re-offer the migration; a bool, so it cannot be set by typing a
    # word that happens to be truthy.
    "memory.migrated": {"type": "bool"},
    # The vault. `vault_mode` is a closed three-value enum
    # (an unrecognised value must be a 400, never a silent fall back to "off", or mirroring
    # stops while the setting looks saved). `vault_path` normalises empty → the default at
    # the write boundary so config.json matches what load() reads back.
    "memory.vault_mode": {"type": "enum", "values": list(MEMORY_VAULT_MODES)},
    "memory.vault_path": {
        "type": "str",
        "max_len": 256,
        "sanitize": lambda v: v.strip() or "memory-vault",
    },
    "feedback.enabled": {"type": "bool"},
    "feedback.retire_threshold": {"type": "float", "min": 0.1, "max": 0.9},
    "feedback.min_n": {"type": "int", "min": 3, "max": 50},
    "feedback.window_days": {"type": "int", "min": 7, "max": 365},
    # Suggest-first specialist routing (runtime-editable).
    # The watched scratchpad. "" = off, which is
    # the default; canonicalized on write so the stored path is the one the fence compares.
    "planning.scratchpad_path": {
        "type": "str",
        "max_len": 512,
        "sanitize": _scratchpad_path_sanitizer,
    },
    "agents_routing.enabled": {"type": "bool"},
    "agents_routing.min_confidence": {"type": "float", "min": 0.3, "max": 0.95},
    "agents_routing.cooldown_hours": {"type": "float", "min": 0.0, "max": 720.0},
    "agent.orchestrator_skill": {"type": "bool"},
    "agent.acp_concurrent_sessions": {"type": "bool"},
    # Require verified adapter provenance for an
    # UNATTENDED spawn onto an external runner. Runtime-editable because it is a
    # posture choice, not a floor: which provenance counts as verified is code
    # (agents/runners.verify_adapter), so a PATCH here can only turn the requirement
    # on or off, never widen what "verified" means.
    "agent.unattended_requires_verified_adapter": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(False),
            "Unattended runs may start on an external runner whose adapter has not been "
            "verified.",
        ),
    },
    # How long a runner's measured health evidence counts as current. Bounded
    # below at a minute (a shorter window would mark every row overdue between two
    # clicks) and above at a day.
    "agent.runner_health_check_secs": {"type": "int", "min": 60, "max": 86_400},
    "agent.runner_idle_release_secs": {"type": "int", "min": 60, "max": 86_400},
    "agent.durable_sessions": {"type": "bool"},
    # The prompt-cache switch (default ON). Off collapses
    # the provider's declared cache mode to NONE, which is the byte-identical no-marker
    # path an undeclared provider already takes. It does NOT revert the wire
    # ordering repairs, which are unconditional.
    "agent.prompt_cache_enabled": {"type": "bool"},
    # The assistant's display name — consumed by the prompt engine ({{bot_name}}
    # template var + ContextBuilder). Validated at the write boundary (≤50 chars; a
    # character a name may not contain is refused by name, never dropped) so the FILE
    # holds exactly what the user typed — load() strips the same characters from a
    # hand-edited file, defense in depth.
    "agent.bot_name": {"type": "str", "max_len": 50, "sanitize": _bot_name_validator},
    "agent.log_level": {"type": "enum", "values": ["DEBUG", "INFO", "WARNING", "ERROR"]},
    # Self-QA companion. All four fields are editable,
    # not just the two toggles: a companion you can enable but cannot point at a repo is
    # enabled and inert, which reads as broken. `max_scenarios_per_fire` is clamped to the same
    # [1, 20] window ``AppConfig.load()`` applies, so the file and the dashboard agree.
    "agent.self_qa.enabled": {"type": "bool"},
    "agent.self_qa.watched_repo": {"type": "str", "max_len": 512},
    "agent.self_qa.fix_branch_enabled": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "On every confirmed failure, the companion will open a pclaw/selfqa-<sha> branch "
            "in your watched repository carrying a proposed diff — with no further "
            "confirmation.",
        ),
    },
    "agent.self_qa.max_scenarios_per_fire": {"type": "int", "min": 1, "max": 20},
    "session.timeout_secs": {"type": "int", "min": 0, "max": 86400},
    "session.autocompact_pct": {"type": "float", "min": 5.0, "max": 90.0},
    "session.pool_size": {"type": "int", "min": 0, "max": 10},
    "session.pool_agent": {"type": "str", "values_fn": _agent_values},
    "session.pool_ttl_secs": {"type": "int", "min": 0, "max": 7200},
    # 0 = off; the ceiling is generous on purpose (a year) since "archive rarely"
    # is a legitimate preference and archiving is non-destructive.
    "session.auto_archive_days": {"type": "int", "min": 0, "max": 3650},
    # #1783 — the installer's write path. Validated against the LIVE registry, so a name
    # only becomes settable once something has registered it. Takes effect at the next
    # gateway start (`_context_engine_startup`): swapping the assembly engine under
    # running sessions would change the prompt shape mid-conversation.
    "session.context_engine": {"type": "str", "values_fn": _context_engine_values},
    # The release-tracking config block. All six are
    # runtime-editable from Settings > Updates. `channel`/`auto` are closed enums
    # so an out-of-range value is REFUSED at the boundary (a mistyped channel should be
    # told, not silently overruled); `check_interval_hours` states the same [1, 168] window
    # `load()` clamps, so the file and the dashboard agree. `pin` is refused unless it is a
    # release VERSION (`self_update.normalize_pin`): the resolvers match it exactly against
    # the release tags, so a line, a range or a typo can never name a release, and storing
    # one silently stopped every update. `last_version` is written by the updater itself.
    # `updates.auto` (off | staged) is the opt-in unattended-apply gate that RETIRED the
    # legacy top-level `auto_update` bool: "off" = notify-only, "staged" = apply at
    # the next safe point (holds while work is in flight, lands on the resolved tag).
    "updates.channel": {"type": "enum", "values": ["stable", "beta", "nightly"]},
    "updates.pin": {"type": "str", "max_len": 64, "sanitize": _version_pin_sanitizer},
    "updates.auto": {"type": "enum", "values": ["off", "staged"]},
    "updates.check_enabled": {"type": "bool"},
    "updates.check_interval_hours": {"type": "int", "min": 1, "max": 168},
    "updates.last_version": {"type": "str", "max_len": 64},
    "dashboard.mcp_probe_timeout_secs": {"type": "int", "min": 5, "max": 120},
    # The screen-context master switch (default OFF). Runtime-editable because
    # it is the consent knob: a user turns it on for one working session and off again,
    # and having to restart the gateway to withdraw that permission would be the wrong
    # shape for a privacy control. Flipping it off takes effect on the NEXT frame POST
    # (the route re-reads config per request) and on the next drain, so a slot staged
    # while it was on is dropped rather than delivered.
    "dashboard.screen_share_enabled": {"type": "bool"},
    # The in-place document editor's master switch (default OFF). Runtime-editable
    # for the same reason as the flag above: it is a consent knob for a LOSSY path, so
    # withdrawing it must not need a restart. `PUT /api/artifacts/{slug}/model` re-reads it
    # per request, so flipping it off closes the write immediately — the UI hiding the
    # editor is the second layer, not the only one.
    "dashboard.document_editing": {"type": "bool"},
    # Opt-in tmux-backed terminal persistence (survives a gateway restart). Read as a
    # raw dict from config.json by handlers/terminal.py::_get_config — a 3-part nested path.
    "dashboard.terminal.persist": {"type": "bool"},
    # The loop JUDGE's model axis, independent of the `loops` axis the
    # graded worker rides. Runtime-editable because it is the knob a user reaches for
    # after reading a verdict they disagree with (pin the strongest model to judgment).
    # The closed set is the chat-family axes only: a non-chat capability (stt, embedding,
    # image_gen…) cannot render a verdict, so offering it would be a footgun. `loops` IS
    # offered — collapsing judge onto the worker's binding is a legitimate, explicit
    # choice; it just stops being the silent default.
    "loops.judge_use_case": {
        "type": "enum",
        "values": ["reasoning", "chat", "code_tools", "background", "orchestration", "loops"],
    },
    # How many consecutive no-progress cycles stall a loop. Runtime-editable
    # because it is the knob a user reaches for after a loop either nagged too early or
    # burned cycles before asking for direction. Floor of 2 is structural (the
    # worker-independent signals compare findings BETWEEN cycles); the ceiling keeps a
    # typo from parking the detector past any realistic cycle budget.
    "loops.stagnation_window": {"type": "int", "min": 2, "max": 50},
    # The SDLC post-gate check-work hook. Off by default; on, a
    # passing stage gate additionally re-derives checks from the stage's own claims.
    "loops.check_work_stages": {"type": "bool"},
    "loops.worktree_sparse": {"type": "bool"},
    "inbox.engagement_ranking_enabled": {"type": "bool"},
    # "Sort new messages": read before every sorting call, so no restart is needed.
    "inbox.sort_messages": {"type": "bool"},
    "inbox.engagement_half_life_days": {"type": "float", "min": 0.0, "max": 365.0},
    # "Poll the drop folder": the built-in drop folder's switch alone (an installed inbox
    # app is polled while it is enabled). Read at every poll, so no restart is needed.
    "inbox.enabled": {"type": "bool"},
    # The channels a channel app's inbox source reads (Settings → Inbox shows the list while a
    # polled source says it reads one). Read at every poll, so no restart is needed.
    "inbox.watched_channels": {
        "type": "str_list",
        "max_items": 50,
        "sanitize": _watched_channels_validator,
    },
    # Runtime-editable because all three are knobs the human reaches for
    # while a room is running: killing the feature, or capping a deliberation that is
    # spending more than it is worth. The budget floor is 1, not 0 — a room reads 0 as
    # "inherit this default", so a 0 default would resolve to an unbounded loop.
    "rooms.enabled": {"type": "bool"},
    "rooms.round_budget": {"type": "int", "min": 1, "max": 100},
    "rooms.max_members": {"type": "int", "min": 1, "max": 32},
    # Runtime-editable: these are the knobs a user reaches for
    # WHILE something is going wrong — capping concurrency because a fan-out is starving
    # the box, or shortening a stall timeout because a node is wedged. Requiring a
    # restart to change them would mean restarting mid-run to fix a run.
    "workflows.enabled": {"type": "bool"},
    "workflows.self_schedule_max_outstanding": {"type": "int", "min": 0, "max": 200},
    # No `workflows.max_concurrent_nodes`: the two per-lane caps below are the live partition,
    # and the bare total that claimed to be "partitioned across typed lanes" was read by
    # nothing (#465). Cap a lane, not a total that no lane consults.
    "workflows.default_node_timeout_total_secs": {"type": "int", "min": 0, "max": 86400},
    "workflows.default_node_timeout_stall_secs": {"type": "int", "min": 0, "max": 86400},
    "workflows.retention_per_def": {"type": "int", "min": 1, "max": 10000},
    "workflows.max_concurrent_llm_nodes": {"type": "int", "min": 1, "max": 32},
    "workflows.max_concurrent_io_nodes": {"type": "int", "min": 1, "max": 32},
    "workflows.model_tier_reasoning": {"type": "str", "max_len": 32},
    "workflows.model_tier_standard": {"type": "str", "max_len": 32},
    "workflows.model_tier_fast": {"type": "str", "max_len": 32},
    # The T4 embedding tie-break floor. Live-editable — the dial an owner turns when the
    # matcher composes too readily or ignores a genuine semantic near-match, tuned while watching.
    "workflows.match_threshold": {"type": "float", "min": 0.0, "max": 1.0},
    # All four are live-editable: each changes how much the system does on
    # its own, which is exactly the class of setting an owner reaches for mid-session rather than
    # after a restart.
    #
    # The bounds are the ones the code already enforces, restated here so the API refuses out-of-
    # range values instead of storing one the runtime silently clamps — a stored value that does not
    # match the behaviour is worse than a rejection, because the user reads the stored one.
    "workflows.surface_mode_default": {"type": "enum", "values": ["off", "passive", "suggest"]},
    "workflows.max_materialized_per_foreach": {"type": "int", "min": 1, "max": 500},
    # 0 = never expires (an author writing `0` means "wait for me"), and the upper bound is 30 days:
    # a gate held longer than that is an abandoned run, not a patient one.
    "workflows.confirmation_ttl_secs": {"type": "int", "min": 0, "max": 30 * 24 * 3600},
    # Capped at MAX_LEASE_SECS (1h) — the ceiling `pool.Lease.expires_at` clamps to. Accepting a
    # larger number here would store a week-long lease that the runtime silently shortens.
    "workflows.lease_ttl_secs": {"type": "int", "min": 30, "max": 3600},
    # Quiet-window and duty-gate defaults. Strings rather than enums: a quiet window is an
    # `HH:MM-HH:MM` range and a duty gate is a provider name an app can supply, so neither has a
    # closed value set.
    # The window is still checked, by the parser the scheduler reads it with
    # (`_quiet_window_sanitizer`), so a value the scheduler cannot read is refused here rather than
    # saved and then silently applied as no default.
    "workflows.default_quiet_windows": {
        "type": "str",
        "max_len": 64,
        "sanitize": _quiet_window_sanitizer,
    },
    "workflows.duty_gate_default": {"type": "str", "max_len": 64},
    # Both live-editable, and each for a concrete reason: the
    # default mode is what a user changes after watching a run touch their real tree, and the
    # teardown switch is what they reach for when a teardown command is itself the problem — both
    # mid-session decisions. `container` is in the enum because it is in `workspace.Mode`; it
    # degrades to an isolated scratch dir until an image or build is given, so accepting the word
    # here never promises a runtime the engine does not have.
    "workflows.workspace_default_mode": {
        "type": "enum",
        "values": ["scratch", "worktree", "in_place", "container"],
    },
    "workflows.workspace_teardown_on_expiry": {"type": "bool"},
    # The learning subsystem's own switch. Live-editable because every reader re-loads the config
    # (the gate per decision, the Learning routes per request), and because it needs a way back
    # on: while it is off the Learning page says so and offers "Turn learning on", which is this
    # PATCH. Without the row the only way back was hand-editing `config.json`.
    "learning.enabled": {"type": "bool"},
    # Learning-loop capture: the knobs worth changing without a restart. The
    # evidence floor and the session-score threshold are how an owner tunes how
    # eagerly the system learns, and staging can be turned off if the log is
    # unwanted — so all three are live-editable.
    "learning.min_evidence": {"type": "int", "min": 1, "max": 20},
    # The lesson injection floor. Live-editable for the same reason as the
    # evidence floor beside it — this is the knob for "why is it still doing that" /
    # "why did it stop doing that", and a user chasing either answer should be able to
    # move the gate and re-read the Memory studio without restarting the gateway.
    "learning.min_lesson_confidence": {"type": "float", "min": 0.0, "max": 1.0},
    "learning.staging_enabled": {"type": "bool"},
    # The self-model gate. Live-editable because it is the one learning path
    # that acts on what WORKED rather than on corrections — a user who finds that presumptuous
    # should be able to stop it without a restart.
    "learning.self_model_enabled": {"type": "bool"},
    "learning.propose_quota_per_run": {"type": "int", "min": 1, "max": 25},
    "learning.curator_enabled": {"type": "bool"},
    # The replay harness's switch and its ceiling. Both live-editable, and the ceiling
    # deliberately allows 0 — that is not a disabled range value, it is the OFF position the
    # harness reads as "do not run", so a user who wants to stop replay spend mid-pass can set
    # it to 0 without also having to find the boolean. The 25.0 cap is a guard against a typo
    # turning a $2 ceiling into a $200 one on a background tick nobody is watching.
    "learning.replay_enabled": {"type": "bool"},
    "learning.replay_max_dollars": {"type": "float", "min": 0.0, "max": 25.0},
    "learning.context_budget_tokens": {"type": "int", "min": 500, "max": 100000},
    # Learn from terminal workflow-run failures. Live-editable because a user
    # who finds run-end lesson proposals noisy should be able to silence them without a
    # restart, the same as every other learning-eagerness knob above.
    "learning.run_end_enabled": {"type": "bool"},
    # Grade accepted changes against their predictions and auto-file HARMFUL
    # reverts. Live-editable for the same reason — a user who does not want the flywheel
    # measuring its own accepted proposals should be able to stop it without a restart.
    "learning.attribution_enabled": {"type": "bool"},
    # The periodic identity report's cadence, and its ONLY switch (`off` is a member, not
    # a sibling bool). Live-editable because the reconciler CONVERGES it — `reconcile_digest_cron`'s
    # contract — so changing it on the Learning page re-arms the trigger without the user knowing
    # one exists. The `values` list is asserted equal to `learning_report.IDENTITY_REPORT_CADENCES`
    # by `test_identity_report_schedule.py`, so this copy cannot drift from the vocabulary.
    "learning.identity_report_cadence": {
        "type": "enum",
        "values": ["monthly", "weekly", "off"],
    },
    # The write-semantics knobs worth changing without a restart.
    # `require_citations` is here deliberately — an owner mid-research may need to store an
    # unsourced note and should not have to restart the gateway to do it.
    # No `knowledge.idempotent_persist`: content-derived write identity is an invariant, not a
    # switch — see `KnowledgeConfig`'s docstring. It was allowlisted and read by nothing (#465).
    "knowledge.require_citations": {"type": "bool"},
    "knowledge.report_budget_chars": {"type": "int", "min": 1000, "max": 500000},
    "knowledge.max_mentions_per_claim": {"type": "int", "min": 1, "max": 200},
    # The long-run + maintenance cadences. Runtime-editable because the right value depends on
    # what a store is being used for, and finding it means adjusting and watching — which a
    # restart per attempt makes nobody do.
    "knowledge.synthesis_window": {"type": "int", "min": 1, "max": 200},
    # The anti-starvation window. Floor 60s rather than 1: below a minute this stops
    # being a staleness window and becomes "run every tick", which defeats the coalescing
    # the watermark exists for. `maintenance.max_staleness_secs()` enforces its own floor
    # too, because config.json is hand-editable and this bound only guards the PATCH path.
    "knowledge.maintenance_max_staleness_secs": {"type": "int", "min": 60, "max": 86400},
    # The projection gate. PATCH-editable rather than a dedicated PUT (which is what
    # `memory.vault_mode` has) because there is nothing to do at the moment of the flip: the
    # projection is a maintenance pass, so turning the mode on arms the pass and the next tick
    # writes the vault. `off` is the shipped default and stays reachable, so a user who
    # changes their mind stops the writer with the same control that started it.
    #
    # `two_way` is a strictly larger grant than `mirror` — it is what makes an edited file
    # win over the database — and both are refused by anything except this validated write,
    # because a mode outside the tuple resolves to `off` in `load()`.
    "knowledge.vault_mode": {"type": "enum", "values": ["off", "mirror", "two_way"]},
    "knowledge.vault_path": {"type": "str", "max_len": 512},
    # The similarity-edge shape. Runtime-editable because the right floor depends on the
    # embedding model and on what a store holds, and finding it means changing the value and
    # LOOKING at the edges it produced — a restart per attempt means nobody tunes it at all.
    # The floor's own floor is 0.05, not 0.0: this path REJECTS out-of-bounds rather than
    # clamping, and a 0.0 accepted here would be silently replaced by the shipped default on
    # the next load (a PATCH that reports success and changes nothing). Refusing it says so.
    # The ceiling is 1.0 because the value is a cosine similarity and nothing above 1.0 is
    # satisfiable. Note these three are validated INDEPENDENTLY: a degree cap set below the
    # top-K is accepted by both entries and only the pass can see that they disagree.
    "knowledge.similarity_min_score": {"type": "float", "min": 0.05, "max": 1.0},
    "knowledge.similarity_top_k": {"type": "int", "min": 1, "max": 64},
    "knowledge.similarity_degree_cap": {"type": "int", "min": 1, "max": 512},
    # The ceiling is generous because the real limit is the PROVIDER's and core cannot
    # know it — an oversized batch is discovered by bisection and costs a few extra calls, not
    # a failed import, so the bound here only needs to stop a typo, not to be correct.
    "knowledge.embed_batch_size": {"type": "int", "min": 1, "max": 2048},
    "knowledge.embed_retry_budget": {"type": "int", "min": 1, "max": 10},
    "knowledge.consolidate_min_cluster": {"type": "int", "min": 2, "max": 100},
    "knowledge.consolidate_min_hours": {"type": "int", "min": 0, "max": 720},
    "knowledge.session_brief_max_tokens": {"type": "int", "min": 0, "max": 8000},
    # The artifact→knowledge mirror's master switch. Live-editable deliberately — the
    # listener reads it per artifact save, so turning it on backfills and turning it off stops
    # new indexing without a restart. Nothing already indexed is removed by turning it off;
    # that would delete search state on a settings toggle.
    "knowledge.auto_ingest_artifacts": {"type": "bool"},
    # The relevance-reranker stage. Off by default; the retrieval bench's `rerank`
    # arm (`personalclaw retrieval-eval`) is how an operator decides whether to flip it.
    "knowledge.rerank_enabled": {"type": "bool"},
    "knowledge.rerank_candidates": {"type": "int", "min": 1, "max": 200},
    # #1783 — the chat-injection fetch budget. Both floors are 1, matching `load()`'s own
    # floor: this path REJECTS out of range instead of clamping, and a 0 accepted here would
    # be replaced by the shipped default on the next load — a PATCH that reports success and
    # offers no cards. The token ceiling is 32000 because that is
    # `_CONTEXT_MAX_TOKENS_CEILING`, which `search-for-context` applies to the configured
    # value as well as to a `?max_tokens=` override, so anything larger is unreachable.
    "knowledge.fetch_top_n": {"type": "int", "min": 1, "max": 50},
    "knowledge.fetch_max_tokens": {"type": "int", "min": 1, "max": 32000},
    # The owner-login knobs. Runtime-editable so turning login on
    # or off, or loosening a lockout you tripped, takes effect on the next request without
    # a restart. The PASSWORD is deliberately NOT here and never will be: a credential is
    # not a setting, it goes through `personalclaw auth set-password` / the enroll flow so
    # the plaintext never rides in a PATCH body that lands in a request log. `public_url`
    # is likewise excluded — widening a network surface should be a deliberate file edit.
    #
    # Which way LOOSENS `login_enabled` is not the obvious way. Login "ADDS a second issuer of
    # the same session token" (`AuthConfig`): turning it ON is a new way in that anyone who can
    # reach the page may try, so ON is the consented direction. Turning it OFF removes that way
    # in; the token link keeps working, and the Account panel's dialog for OFF is a lockout
    # warning for the owner, not a security consent.
    "auth.login_enabled": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "Password sign-in becomes a way in: anyone who can reach this dashboard can try a "
            "username and password, up to the lockout limit.",
        ),
    },
    "auth.require_totp": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(False),
            "Signing in with just a username and password will be enough — the authenticator "
            "code will no longer be required.",
        ),
    },
    "auth.session_ttl": {
        "type": "duration",
        # A sign-in lasts at most 90 days (`personalclaw.auth.lifetimes`); longer is refused
        # with the sentence saying so, never stored and quietly applied as something else.
        "max_secs": MAX_LIFETIME_SECS,
        "security": SecurityControl(
            loosens_when_longer(),
            "A signed-in browser stays signed in longer before it must sign in again.",
        ),
    },
    "auth.lockout_threshold": {
        "type": "int",
        "min": 1,
        "max": 100,
        "security": SecurityControl(
            loosens_when_raised(),
            "More wrong passwords are allowed before sign-in is locked.",
        ),
    },
    "auth.lockout_window": {
        "type": "duration",
        "security": SecurityControl(
            loosens_when_shorter(),
            "A lockout ends sooner and failed attempts are forgotten sooner, so more guesses "
            "fit into the same time.",
        ),
    },
    # Platform-legibility toggles (Discover tips, context adapters).
    # discover_tips gates the propose-don't-write Discover section + hub;
    # context_adapters gates writing adapter files into opted-in project workspaces.
    "legibility.discover_tips": {"type": "bool"},
    "legibility.context_adapters": {"type": "bool"},
    # Ambient surfaces — the composable home + generative-UI knobs.
    # `surfaces_max_layer` and `tray_enabled` were allowlisted here with no reader anywhere
    # (issue #3490) and are gone: the layer ceiling is `surface_layers.py`'s process latch,
    # and menu-bar presence is the Electron shell's, reported through `desktop_registry`.
    "ambient.tiles_enabled": {"type": "bool"},
    "ambient.max_tiles": {"type": "int", "min": 1, "max": 48},
    "ambient.default_refresh_ttl_secs": {"type": "int", "min": 30, "max": 86400},
    "ambient.genui_enabled": {"type": "bool"},
    # Companion apps — LAN discovery advertisement + the friendly
    # instance name a client shows. discovery_enabled is off by default; toggling it here
    # is the opt-in to announcing this gateway on the local network.
    "companion.discovery_enabled": {"type": "bool"},
    "companion.instance_name": {"type": "str", "max_len": 64},
    # The connector toggle for the `user_browser` execution target.
    # Editable here so the Settings control has a write path; there is deliberately no knob for
    # the `gateway` target, which needs no permission to drive this machine's own profile.
    "browse.user_browser_enabled": {
        "type": "bool",
        "security": SecurityControl(
            loosens_when(True),
            "The agent may drive your own browser through the connector, with the sites you "
            "are signed in to.",
        ),
    },
    # Mobile push — which transport carries a CONTENT-FREE
    # {kind,item_id} ping to the phone. WHETHER a notification pushes at all is the notification
    # rules matrix, not this; these two only pick the pipe. The enum values are read from
    # the loader so the write path cannot drift from the field's own declared choices.
    "mobile.push_backend": {"type": "enum", "values": list(PUSH_BACKENDS)},
    "mobile.ntfy_topic_url": {"type": "https_url", "max_len": 512},
    # Local models — the memory-pressure warning threshold the
    # loaded-models bar reads, and the crashed-sidecar respawn budget. Both are advisory
    # knobs on the user's own machine: the threshold blocks nothing, and the restart bound
    # only decides when the runner stops retrying a genuinely broken install.
    "local_models.pressure_warn_pct": {"type": "int", "min": 1, "max": 100},
    "local_models.sidecar_restart_max": {"type": "int", "min": 0, "max": 20},
    # The fit budget's OS/runtime reserve and the browse filter's default. The
    # reserve is bounded at 64 GB — the same window the loader clamps to — so a UI edit
    # cannot make every model read as unrunnable; out-of-range values are REJECTED here
    # rather than clamped, so a PATCH that "succeeded" never means a different number
    # than the one the user typed. Neither knob blocks a download or a load.
    "local_models.memory_reserve_gb": {"type": "float", "min": 0.0, "max": 64.0},
    "local_models.hide_unrunnable_models": {"type": "bool"},
    # The HF-token whoami cache TTL (0 = re-check every read) and the bound on a model's
    # Test in Settings → Models. Both bounded to the same windows the loader clamps to, so a UI
    # edit is rejected rather than silently clamped and can never mean a different number than
    # typed.
    "local_models.whoami_ttl_s": {"type": "int", "min": 0, "max": 86400},
    "local_models.selftest_timeout_s": {"type": "int", "min": 5, "max": 600},
    # Background work (Settings → Models → Background): how long a background task may run and
    # write, and how long a reply waits for a busy local model. The windows are the loader's
    # clamps, so a write is refused rather than stored as another number. Not security
    # controls: a longer limit only waits longer, and the spend ceilings still bound the cost.
    "background.call_timeout_secs": {"type": "float", "min": 30.0, "max": 3600.0},
    "background.max_output_tokens": {"type": "int", "min": 512, "max": 65536},
    "background.busy_model_wait_secs": {"type": "float", "min": 0.0, "max": 300.0},
    # Watched sources — the poll engine's runtime knobs. The
    # network floor is bounded at 300s (the rate floor) so a UI edit cannot make
    # the engine poll a third party abusively. `daily_request_budget` was allowlisted here
    # with nothing counting requests against it (issue #3490) and is gone; the allowance that
    # IS enforced is the per-source, per-poll `budget.max_requests` on the source row.
    "sources.enabled": {"type": "bool"},
    "sources.poll_interval_default_secs": {"type": "int", "min": 300, "max": 604800},
    "sources.network_floor_secs": {"type": "int", "min": 300, "max": 604800},
    "sources.max_sources": {"type": "int", "min": 1, "max": 1000},
    "sources.max_items_per_poll": {"type": "int", "min": 1, "max": 1000},
    # Packs — the runtime-editable subset: the fingerprint toggle and the
    # skill-catalog list are the knobs a user reaches for from Settings. No credential rides
    # either (a connector credential goes to the credential store, never a config field).
    # `packs.connector_catalog_url` was allowlisted here for a catalog refresh nothing
    # implements (issue #3490) and is gone.
    "packs.fingerprint_enabled": {"type": "bool"},
    "packs.skill_catalogs": {"type": "skill_catalogs"},
    # Apps — whether the curated registry ships as a default Store
    # source. Editable because it is the operator's opt-out for a shipped NETWORK source; it
    # only gates SEEDING, so a PATCH cannot retract a row already in app-sources.json (the
    # Store's remove control does that, and that removal persists).
    "apps.registry_source_enabled": {"type": "bool"},
    # Whether the BUNDLED default git source is listed (#2528). Unlike the registry flag this
    # is a live filter, not a seed switch: the bundled tuple is folded into every read of
    # list_git_sources(), so there is no row to remove and this PATCH is the only way to turn
    # it off. Editable for exactly that reason — an unremovable default that reaches the
    # network before the user has configured anything needs an off switch to point at.
    "apps.bundled_source_enabled": {"type": "bool"},
}
