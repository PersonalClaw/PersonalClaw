"""Each capability of an app has its own switch and its own status (#3843).

An app's ``enabled`` is the master switch: an app that is off runs nothing. An app that is on runs
the capabilities switched on in its installed record (``InstalledApp.capabilities``,
``{id: {"on": true|false}}``):

* **At install** each capability is switched as its provider's ``startsOn`` says
  (:func:`initial`).
* **After that the switch is the owner's** (:func:`switch`, the ``…/capabilities/{id}/on`` and
  ``…/off`` routes). An update never changes it: a capability it renames keeps its switch, one it
  drops leaves the record, and one it adds starts off (:func:`after_update`).
* **A record written before the switches** has none, and its app runs every capability it
  declares (:func:`is_on`); the first switch or update writes them all down. A record whose
  switches are there but cannot be read runs none of them.

A capability that is off is not built (``app_runtime``), so it reads no settings and starts
nothing, and switching one builds or tears down that one alone.

What a capability is doing comes from one place, :func:`capability_status`: the requested state
(its switch) beside the effective one (``off``, ``needs_setup``, ``starting``, ``running``,
``failed``, ``unavailable``) and the sentence that says why. ``GET /api/apps``, its summary line
(:func:`summary`) and the switch routes' answers all read it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from personalclaw.apps.manager import InstalledApp
    from personalclaw.apps.manifest import AppManifest
    from personalclaw.providers.registry import RegisteredProvider

logger = logging.getLogger(__name__)

OFF = "off"
NEEDS_SETUP = "needs_setup"
STARTING = "starting"
RUNNING = "running"
FAILED = "failed"
UNAVAILABLE = "unavailable"
STATES = frozenset({OFF, NEEDS_SETUP, STARTING, RUNNING, FAILED, UNAVAILABLE})


@dataclass(frozen=True)
class CapabilityStatus:
    """What one capability was asked to do (``on``) and what it is doing (``state``), with the
    sentence that says why when it is not running as asked."""

    on: bool
    state: str
    reason: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {"on": self.on, "status": self.state, "reason": self.reason}


class SwitchRefused(Exception):
    """A switch :func:`switch` did not make: ``status`` is the HTTP answer, ``native`` says it
    was a native app's switch off (answered as the app's own disable is), and the message is the
    sentence to show."""

    def __init__(self, message: str, *, status: int, native: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.native = native


def _ids(manifest: AppManifest) -> list[str]:
    """*manifest*'s capability ids, or none when they cannot be told apart (the app is held off,
    ``capability_lifecycle.hold_off_reason``)."""
    try:
        return manifest.capability_ids()
    except ValueError:
        return []


def initial(manifest: AppManifest) -> dict[str, dict[str, bool]]:
    """The switches an install writes: each capability as its provider's ``startsOn`` says."""
    return {
        cid: {"on": provider.startsOn}
        for cid, provider in zip(_ids(manifest), manifest.all_providers())
    }


def starts_on(manifest: AppManifest, capability: str) -> bool:
    """Whether *capability* is on once *manifest*'s app is installed, as :func:`initial` writes
    it. ``""`` names the app's own: on with the app."""
    return not capability or initial(manifest).get(capability, {}).get("on") is True


def is_on(meta: InstalledApp | None, capability: str) -> bool:
    """Whether *capability*'s switch is on in *meta*. A record with no switches (written before
    them) runs every capability; one with switches runs only those it switches on, so a map that
    could not be read, or names no switch for it, runs nothing."""
    if meta is None or meta.capabilities is None:
        return True
    return meta.capabilities.get(capability, {}).get("on") is True


def readable(meta: InstalledApp | None, capability: str) -> bool:
    """Whether *capability* has a switch *meta* can read. A record written before the switches
    has none to read and runs all, so it reads as readable; one with switches whose mark for
    *capability* is missing or could not be read does not: that capability stays off, and says
    so (:func:`unreadable_sentence`)."""
    return meta is None or meta.capabilities is None or capability in meta.capabilities


def unreadable_sentence(app_label: str) -> str:
    """What a capability whose switch cannot be read says about itself."""
    return (
        f"{app_label}'s capability switches couldn't be read, so they stay off until you "
        "switch them on."
    )


def switched_on(manifest: AppManifest) -> list[str]:
    """The ids of *manifest*'s capabilities that are switched on, from its installed record."""
    from personalclaw.apps.manager import _read_installed

    meta = _read_installed(manifest.name)
    return [cid for cid in _ids(manifest) if is_on(meta, cid)]


def after_update(meta: InstalledApp, before: list[str] | None, new: AppManifest) -> None:
    """Keep the owner's switches across an update from capabilities *before* to *new*'s: each
    capability *new* still declares keeps its switch, one it renames (``renamedFrom``) keeps the
    old id's, one it drops leaves the record, and one it adds starts off. A record written before
    the switches had every capability on, and is written down that way first. With *before*
    unknown (``None``: the old manifest could not be read), the record's own switches are the
    earlier capabilities."""
    after = _ids(new)
    if not after:
        return
    if before is None:
        if meta.capabilities is None:
            return  # nothing to tell an added capability by: it runs as it did
        before = list(meta.capabilities)
    was = {cid: is_on(meta, cid) for cid in before if readable(meta, cid)}
    unread = set(before) - set(was)
    out: dict[str, dict[str, bool]] = {}
    for cid, provider in zip(after, new.all_providers()):
        if cid in was:
            out[cid] = {"on": was[cid]}
            continue
        if cid in unread or unread.intersection(provider.renamedFrom):
            continue  # its switch could not be read: it stays without one, off, and says so
        inherited = [was[old] for old in provider.renamedFrom if old in was]
        out[cid] = {"on": inherited[0] if inherited else False}
    meta.capabilities = out


def switch(
    name: str, capability: str, on: bool, *, caller: str = "app_manager"
) -> CapabilityStatus:
    """Switch *name*'s *capability* on or off, and answer its status.

    The switch is written to the installed record first, then that one capability is built or
    torn down while the app runs; an app that is off keeps the switch and builds nothing until
    it is switched on. A native app is always on, so none of its capabilities is switched off.

    Raises :class:`SwitchRefused` for an app that is not installed, a capability it does not
    declare, an app whose capabilities cannot be told apart, and a native app's switch off."""
    from personalclaw.acp.cli_resolve import adapter_installs_allowed
    from personalclaw.apps import app_manager, capability_lifecycle
    from personalclaw.apps.manager import _now_iso, _read_installed, _write_installed, record_lock
    from personalclaw.providers.registry import get_provider_registry

    # The record is read under the lock every writer of it holds, so the app's own switch read
    # here is the one in force: a disable that runs first leaves this one building nothing.
    with record_lock(name):
        meta = _read_installed(name)
        manifest = app_manager._manifest_of(name)
        if meta is None or manifest is None or manifest.name != name:
            raise SwitchRefused(f"app {name!r} not installed", status=404)
        held = capability_lifecycle.hold_off_reason(manifest)
        if held:
            raise SwitchRefused(held, status=409)
        ids = _ids(manifest)
        if capability not in ids:
            raise SwitchRefused(f"{name!r} has no capability {capability!r}", status=404)
        if not on and app_manager.is_native(name):
            who = manifest.displayName or name
            raise SwitchRefused(
                f"{who} is a native app: it ships with PersonalClaw and is always on, so it "
                "can't be turned off or removed.",
                status=409,
                native=True,
            )
        # Every switch as it reads, the one asked for set; one that could not be read stays
        # without a switch, off, until it is switched itself.
        meta.capabilities = {
            cid: {"on": is_on(meta, cid)} for cid in ids if readable(meta, cid)
        } | {capability: {"on": on}}
        meta.updatedAt = _now_iso()
        _write_installed(name, meta)
        registry = get_provider_registry()
        if not on:
            registry.disable(name, capability)
            rec = registry.get(name, capability)
            if rec is not None:
                rec.error, rec.needs_setup = "", False
        elif meta.enabled and not app_manager.enable_refusal(name):
            # Switching a capability on is the moment the owner said yes to it, as enabling the
            # app is: its provider may install the agent adapter it needs here.
            with adapter_installs_allowed():
                registry.enable(name, capability)
        app_manager._audit(
            "capability_on" if on else "capability_off",
            "ok",
            name,
            caller=caller,
            detail=capability,
        )
        return capability_status(
            name, capability, on=on, app_enabled=meta.enabled, app_label=meta.displayName or name
        )


def status_of(name: str, capability: str) -> CapabilityStatus:
    """*name*'s *capability*'s status, its switch read from the installed record."""
    from personalclaw.apps.manager import _read_installed

    meta = _read_installed(name)
    return capability_status(
        name,
        capability,
        on=is_on(meta, capability),
        app_enabled=bool(meta and meta.enabled),
        app_label=(meta.displayName if meta is not None else "") or name,
        readable=readable(meta, capability),
    )


def capability_status(
    name: str,
    capability: str,
    *,
    on: bool,
    app_enabled: bool,
    app_label: str,
    readable: bool = True,
) -> CapabilityStatus:
    """The one reading of what *name*'s *capability* is doing, given its switch (*on*, and
    whether it could be *readable* at all) and the app's (*app_enabled*; *app_label* is the name
    the app goes by): every surface that says it reads this.

    ``off`` while either switch is off (the app's says so, and so does a switch that could not
    be read); then, from its record in the
    provider registry, ``running`` once built, ``needs_setup`` while an account field it requires
    is empty, ``failed`` with the sentence its build failed with, and ``starting`` until its
    build has run. ``unavailable`` wherever the availability check found it cannot run on this
    machine, with its reason."""
    from personalclaw.apps import app_manager
    from personalclaw.providers.registry import get_provider_registry

    if not app_enabled:
        return CapabilityStatus(on, OFF, f"{app_label} is switched off.")
    if not readable:
        return CapabilityStatus(False, OFF, unreadable_sentence(app_label))
    if not on:
        return CapabilityStatus(False, OFF)
    rec = get_provider_registry().get(name, capability)
    if rec is None:
        return CapabilityStatus(
            True, FAILED, app_manager.enable_refusal(name) or "It is not loaded."
        )
    unavailable = _unavailable(rec)
    if unavailable:
        return CapabilityStatus(True, UNAVAILABLE, unavailable)
    if rec.enabled:
        return CapabilityStatus(True, RUNNING)
    if rec.needs_setup:
        return CapabilityStatus(True, NEEDS_SETUP, rec.error)
    if rec.error:
        return CapabilityStatus(True, FAILED, rec.error)
    return CapabilityStatus(True, STARTING)


def _unavailable(rec: RegisteredProvider) -> str:
    """The reason the availability check last gave for *rec* not running on this machine, or
    ``""``. Read as measured: a status read schedules no check."""
    from personalclaw.providers.availability import UNAVAILABLE as MEASURED_UNAVAILABLE
    from personalclaw.providers.availability import get_availability_board

    answer = get_availability_board().peek(rec.name, rec.provider_config.implementation)
    if answer is None or answer.state != MEASURED_UNAVAILABLE:
        return ""
    return answer.reason


#: Each state's words after the capabilities' labels: one capability, then several.
_PHRASES = {
    OFF: ("is off", "are off"),
    STARTING: ("is starting", "are starting"),
    NEEDS_SETUP: ("needs setup", "need setup"),
    FAILED: ("failed", "failed"),
    UNAVAILABLE: ("is unavailable", "are unavailable"),
}


def summary(app_label: str, rows: list[tuple[str, CapabilityStatus]], *, app_enabled: bool) -> str:
    """The app's one line about its capabilities, from each one's label and status:
    ``2 of 3 capabilities running · Inbox failed: the token was refused``. Capabilities in one
    state for one reason are named together, so a reason about the whole app (its switches
    could not be read) is said once."""
    if not app_enabled:
        return f"{app_label} is switched off."
    running = sum(1 for _, status in rows if status.state == RUNNING)
    noun = "capability" if len(rows) == 1 else "capabilities"
    parts = [f"{running} of {len(rows)} {noun} running"]
    groups: dict[tuple[str, str], list[str]] = {}
    for label, status in rows:
        if status.state != RUNNING:
            groups.setdefault((status.state, status.reason), []).append(label)
    for (state, reason), labels in groups.items():
        named = labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"
        phrase = f"{named} {_PHRASES[state][len(labels) > 1]}"
        parts.append(f"{phrase}: {reason}" if reason else phrase)
    return " · ".join(parts)
