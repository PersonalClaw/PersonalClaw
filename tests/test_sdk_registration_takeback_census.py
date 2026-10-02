"""Every registration the SDK publishes takes back what an app's code registered.

An installed app's code runs inside the gateway and registers things into core's process-wide
registries through the SDK: a model type, a catalog, a sidecar runner, a skills marketplace.
Unloading the app (a disable, each uninstall rung, the start of an update) takes each of them
back, and it can only because each registry records how at the moment it stores the entry
(``personalclaw.app_code.keep``). The skills registry did not, so an uninstalled app's
marketplace stayed listed and searchable, still running the app's code, until a restart.

So every ``register*`` function or method the SDK publishes (``sdk/signatures.json``) records its
take-back, or hands the entry to one that does, or is named below with the reason it records none.
"""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

SIGNATURES = (
    Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "sdk" / "signatures.json"
)

#: A published registration that hands the entry to another registration, which records it.
DELEGATES = {
    "personalclaw.trust_mode.register_on_disable": (
        "personalclaw.trust_mode._TrustMode.register_on_disable"
    ),
}

_CORE_SLOT = (
    "one slot the dashboard's own startup fills (DashboardState wires the session manager), "
    "not a registry an app contributes to"
)

#: A published registration that records no take-back, and why that is right.
NOT_AN_APP_CONTRIBUTION = {
    "personalclaw.sdk.model.ProviderRegistry.register_entry": (
        "an entry is an instance's configuration, and core's own config sync writes entries "
        "lazily from whichever call first needs one, an app's call among them, so the stack "
        "does not say whose an entry is; what an app's code contributes there is the type "
        "(register_type), which is taken back, and an entry whose type is gone does not build"
    ),
    "personalclaw.sdk.channel.SessionManager.register_child_stopper": _CORE_SLOT,
    "personalclaw.sdk.channel.SessionManager.register_turn_stop_hook": _CORE_SLOT,
    "personalclaw.sdk.channel.SessionManager.register_dashboard_sessions": _CORE_SLOT,
}


def _resolve(path: str) -> Any:
    """The object a dotted path names: a module attribute, or a class's member."""
    module_path, _, attr = path.rpartition(".")
    try:
        return getattr(importlib.import_module(module_path), attr)
    except ModuleNotFoundError:
        owner_path, _, cls = module_path.rpartition(".")
        return getattr(getattr(importlib.import_module(owner_path), cls), attr)


def _published_registrations() -> dict[str, Callable[..., Any]]:
    """Every ``register*`` callable the SDK publishes, by the path a reader would look it up at.

    A function, a method of a published class, and a function of a published module. An alias
    is left out: what it names is published under its own path.
    """
    found: dict[str, Callable[..., Any]] = {}
    for path, sig in json.loads(SIGNATURES.read_text(encoding="utf-8")).items():
        kind = sig.get("kind")
        if kind == "function" and path.rpartition(".")[2].startswith("register"):
            found[path] = _resolve(path)
        elif kind == "class":
            cls = _resolve(path)
            for member in sig.get("members", {}):
                if member.startswith("register"):
                    found[f"{path}.{member}"] = getattr(cls, member)
        elif kind == "module":
            module = importlib.import_module(sig["module"])
            for name, value in vars(module).items():
                if (
                    name.startswith("register")
                    and callable(value)
                    and getattr(value, "__module__", None) == module.__name__
                ):
                    found[f"{module.__name__}.{name}"] = value
    return found


def _records_take_back(fn: Callable[..., Any]) -> bool:
    return "app_code.keep(" in inspect.getsource(inspect.unwrap(fn))


def _unrecorded(found: dict[str, Callable[..., Any]]) -> list[str]:
    """The published registrations that take nothing back and are not named above."""
    missing = []
    for path, fn in sorted(found.items()):
        if path in NOT_AN_APP_CONTRIBUTION:
            continue
        if not _records_take_back(_resolve(DELEGATES[path]) if path in DELEGATES else fn):
            missing.append(path)
    return missing


def test_every_published_registration_takes_back_what_an_apps_code_registered():
    found = _published_registrations()
    assert len(found) >= 12, f"vacuity floor: the census read {sorted(found)}"
    assert "personalclaw.sdk.skill.SkillsRegistry.register" in found
    missing = _unrecorded(found)
    assert missing == [], (
        f"these SDK registrations keep what a removed app registered until a restart: {missing}. "
        "Record how to take the entry back where it is stored (app_code.keep), or name it in "
        "NOT_AN_APP_CONTRIBUTION with the reason an app's entry there does not outlive the app."
    )


def test_the_census_reports_a_registration_that_takes_nothing_back():
    def register_widget(widget: object) -> None:
        """A registry that stores the entry and records nothing."""

    assert _unrecorded({"personalclaw.sdk.example.register_widget": register_widget}) == [
        "personalclaw.sdk.example.register_widget"
    ]


def test_every_name_the_census_excuses_or_follows_is_still_published():
    found = _published_registrations()
    stale = sorted((set(NOT_AN_APP_CONTRIBUTION) | set(DELEGATES)) - set(found))
    assert stale == [], f"no longer published, so not the census's to excuse or follow: {stale}"
    for target in DELEGATES.values():
        assert _records_take_back(_resolve(target)), f"{target} takes nothing back"
