"""Where a chat's next turn may act: the readings its Execution Boundary control needs.

The control beside the composer shows, before a turn runs, the chat's permission mode, its working
folder, its memory mode, the sandbox its commands run in and the network its work reaches. The
first three come with the chat's own read (``GET /api/chat/sessions/{session}``). The last two no
other read serves a chat, so ``GET /api/chat/boundary`` reads them here:

* **Sandbox**: whether the commands the turn starts run in this computer's sandbox, and what it
  hides from them. The built-in agent's shell is wrapped at ``auto`` (``sandbox.wrap_argv``); an
  agent CLI is started in the tier and at the mode its runtime entry declares
  (``llm.acp_agent.options_sandbox`` and ``options_sandbox_mode``). A folder's Trust or Preview
  (``guardrails.project_trust``) holds back an automation's run and never a chat, so it is no part
  of this reading.
* **Reach**: the egress tier of the chat's run, read through ``guardrails.policy.
  profile_for_session``, the reader the egress guard and a command's launch ask.

Each reading carries a short label and the sentence that says it, written here, so the control
says what the gateway does and works nothing out in the browser. Nothing here changes anything:
the control changes the permission mode and the folder through the routes it always used.
"""

from __future__ import annotations

from aiohttp import web

from personalclaw.dashboard.state import DashboardState
from personalclaw.http_errors import json_error

#: What each egress tier lets the turn's work reach: its label and its sentence. The web tools are
#: held to the tier by the egress guard; a command, which no guard sees, keeps the network only
#: under ``all`` (``net.policy.no_network_for_commands``); the model a chat runs on is never
#: narrowed by a run's tier (``net.http_clients``).
_REACH: dict[str, tuple[str, str]] = {
    "all": (
        "Network on",
        "Its web tools reach public hosts, within your Network egress settings, and the commands "
        "it runs keep the network.",
    ),
    "registry": (
        "Package registries only",
        "Its web tools reach only package registries, and the commands it runs get no network.",
    ),
    "listed": (
        "Allowed hosts only",
        "Its web tools reach only your Allowed hosts, and the commands it runs get no network.",
    ),
    "off": (
        "No network",
        "Nothing it runs reaches the network. The model it runs on still answers.",
    ),
}


def reach_reading(run: str) -> dict[str, str]:
    """The network the work of *run* reaches: its egress tier, its label and its sentence.

    *run* is the chat's key, the one its tool calls are bound to, or ``""`` for a chat not
    started yet, which resolves as an attended run. A tier this table does not know reads as
    ``all`` does, as the guard reads it (``net.policy.egress_policy_for_tier``)."""
    from personalclaw.guardrails import policy

    tier = policy.profile_for_session(run).egress_tier
    label, said = _REACH.get(tier, _REACH["all"])
    return {"tier": tier, "label": label, "said": said}


def _hidden(level: str) -> str:
    """What the sandbox hides at *level*, as the sentence ends: the folders by name at the standard
    level a chat's commands get, and the level's own name otherwise."""
    if level != "standard":
        return f"at its {level} level, which hides your credential folders and files from them"
    from personalclaw.sandbox import _STANDARD_DIRS

    names = [f"~/{d}" for d in _STANDARD_DIRS]
    listed = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
    return f"{listed} are hidden from them"


def _on_this_computer(mode: str, *, subject: str) -> dict[str, object]:
    """The reading of a wrap at *mode* by this computer's sandbox, for *subject*, what it wraps
    (``"the commands it runs"``)."""
    from personalclaw import sandbox

    if sandbox.detect_backend(config_mode=mode) == "none":
        return {
            "confined": False,
            "label": "Not sandboxed",
            "said": (
                f"This computer has no sandbox PersonalClaw can use, so {subject} are not "
                "sandboxed: they run with your account's access."
            ),
        }
    level = sandbox.wrap_level(mode)
    hidden = f": {_hidden(level)}" if level == "standard" else f", {_hidden(level)}"
    return {
        "confined": True,
        "label": "Sandboxed",
        "said": f"{subject[0].upper()}{subject[1:]} are sandboxed on this computer{hidden}.",
    }


def _runtime_options(runtime: str) -> dict:
    """The options of *runtime*'s entry, or none for a runtime nothing registered here."""
    from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

    try:
        return dict(get_default_registry().get_entry(runtime).options or {})
    except ProviderResolutionError:
        return {}


def sandbox_reading(runtime: str) -> dict[str, object]:
    """Whether the commands a chat on *runtime* starts run sandboxed: ``confined``, a label and the
    sentence that says it.

    *runtime* is the chat's agent CLI (``acp:<cli>``), or ``""`` for the built-in agent, whose shell
    is wrapped at ``auto``. An agent CLI is read from its entry, as a session of it is started: a
    tier other than the host's runs it there; a mode of ``off`` is what an entry carries when its
    CLI sandboxes itself (``acp_bundles._register``'s ``self_sandboxing``), so this computer's
    sandbox does not wrap it; any other mode wraps the CLI and what it starts here."""
    if not runtime.startswith("acp"):
        return _on_this_computer("auto", subject="the commands it runs")
    from personalclaw.llm.acp_agent import options_sandbox, options_sandbox_mode

    options = _runtime_options(runtime)
    tier = options_sandbox(options)
    if tier != "none":
        from personalclaw.sandbox_providers import get_provider

        provider = get_provider(tier)
        name = (getattr(provider, "display_name", "") or tier) if provider else tier
        try:
            up = bool(provider and provider.available())
        except Exception:  # noqa: BLE001 - a probe that fails reads as down, as the picker reads it
            up = False
        if not up:
            return {
                "confined": False,
                "label": name,
                "said": f"Its agent CLI is set to run in the {name} sandbox, which is not "
                "available now.",
            }
        return {
            "confined": True,
            "label": name,
            "said": f"Its agent CLI runs in the {name} sandbox, with the commands it starts.",
        }
    mode = options_sandbox_mode(options)
    if mode == "off":
        return {
            "confined": True,
            "label": "The CLI's own sandbox",
            "said": (
                "This computer's sandbox does not wrap its agent CLI: its runtime says the CLI "
                "applies a sandbox of its own to the commands it starts."
            ),
        }
    return _on_this_computer(mode, subject="its agent CLI and the commands it starts")


async def api_chat_boundary(request: web.Request) -> web.Response:
    """GET /api/chat/boundary — the sandbox and the reach of a chat's next turn.

    ``session`` names an open chat, read for the agent it runs on and its own run; without it the
    reading is a new chat's, on the agent CLI ``runtime`` names (``acp:<cli>``) or, with none, the
    built-in agent. Answers ``{sandbox: {confined, label, said}, reach: {tier, label, said}}``. An
    unknown ``session`` is refused rather than read as a new chat, whose reading may differ."""
    state: DashboardState = request.app["state"]
    key = request.query.get("session", "")
    runtime = request.query.get("runtime", "")
    if key:
        session = state._sessions.get(key)
        if session is None:
            return json_error("session_not_found", status=404)
        runtime = str(getattr(session, "acp_provider", "") or "")
        run = session.key
    else:
        run = ""
    return web.json_response({"sandbox": sandbox_reading(runtime), "reach": reach_reading(run)})
