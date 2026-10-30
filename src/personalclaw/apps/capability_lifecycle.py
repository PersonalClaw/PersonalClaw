"""What an installed app's capabilities become across its life: held off, renamed, retired (#3841).

Each provider an app declares is a capability, kept under ``<app>/<id>``
(``providers/registry.py``). Three things happen to one after it is installed:

* **Held off.** A manifest an older core installed may declare two providers of one type with no
  ids, which this core cannot tell apart (:meth:`AppManifest.capability_ids`). It registers
  none, so the app stays off and says why (:func:`hold_off_reason`), its files untouched, until an
  update names each one.
* **Renamed.** An update declares a capability's earlier id in ``renamedFrom``. Whatever is kept
  under ``<app>/<old>`` moves to ``<app>/<new>``, once: each store that keys by capability
  registers a mover (:func:`register_mover`; per-capability settings, secrets and switches add
  theirs, #3842 and #3843), and the installed record keeps the move, so running it again moves
  nothing. A move a mover failed is kept as pending, naming the movers that failed, and the
  gateway's next start runs those again (:func:`retry_pending_moves`) until none is left.
* **Retired.** An id an update drops stays behind in the installed record as a tombstone with its
  last label, the version that dropped it and whether the author declared it in
  ``retiredCapabilities``, and a read of it says what it was and when it went
  (:func:`retired_sentence`). A declared one is never declared again (:func:`update_refusal`).
  One dropped without saying so may come back in a later version, which clears its tombstone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.apps.manager import InstalledApp
    from personalclaw.apps.manifest import AppManifest

logger = logging.getLogger(__name__)

#: ``mover(app, old_id, new_id)``: carries what one store keeps under ``<app>/<old>`` to
#: ``<app>/<new>``.
Mover = Callable[[str, str, str], None]

_MOVERS: list[Mover] = []


def register_mover(mover: Mover) -> None:
    """Have *mover* carry its store's entries when an update renames a capability.

    A mover must be idempotent: a second run of the same move moves nothing and raises nothing.
    One that raises is run again at the gateway's next start (:func:`retry_pending_moves`), and an
    update interrupted after a mover ran can run it again. A pending move names the movers it
    still needs by :func:`mover_name`, so a mover registers at import, before the gateway starts
    its apps."""
    if mover not in _MOVERS:
        _MOVERS.append(mover)


def unregister_mover(mover: Mover) -> None:
    if mover in _MOVERS:
        _MOVERS.remove(mover)


def mover_name(mover: Mover) -> str:
    """The name a pending move records *mover* by: its module and qualified name."""
    return f"{getattr(mover, '__module__', '')}.{getattr(mover, '__qualname__', repr(mover))}"


def _run_movers(app: str, was: str, to: str, only: set[str] | None = None) -> list[str]:
    """Run each registered mover, or those *only* names, for one move; the names of those that
    raised."""
    failed = []
    for mover in list(_MOVERS):
        name = mover_name(mover)
        if only is not None and name not in only:
            continue
        try:
            mover(app, was, to)
        except Exception:  # noqa: BLE001 — one store's failure must not undo the update
            logger.exception("app %s: moving %s to %s failed (%s)", app, was, to, name)
            failed.append(name)
    return failed


def hold_off_reason(manifest: AppManifest) -> str:
    """Why this core keeps *manifest*'s app off because it cannot tell its capabilities apart, or
    ``""`` when it can."""
    try:
        manifest.capability_ids()
    except ValueError as exc:
        return f"{exc}. This core can't tell them apart, so the app stays off until an update does."
    return ""


def _labels(manifest: AppManifest) -> dict[str, str]:
    """Each capability id of *manifest* and the name a person reads for it."""
    try:
        ids = manifest.capability_ids()
    except ValueError:
        return {}
    return {cid: (p.label or cid) for cid, p in zip(ids, manifest.all_providers())}


def update_refusal(meta: InstalledApp | None, new: AppManifest) -> str:
    """The sentence an update to *new* is refused with because it declares an id an earlier
    version retired, or ``""``."""
    if meta is None:
        return ""
    for cid in _labels(new):
        tomb = meta.retiredCapabilities.get(cid)
        if tomb is not None and tomb.get("declared") is True:
            return (
                f"{new.name} {new.version} declares the capability id {cid!r}, which was removed "
                f"in {tomb.get('removedIn', '?')}: a retired id is never declared again"
            )
    return ""


def after_update(meta: InstalledApp, old: AppManifest | None, new: AppManifest) -> None:
    """Record on *meta* what the update from *old* to *new* did to the app's capabilities: each
    rename moved (once, or pending with the movers that failed), each dropped id kept as a
    tombstone, and each id dropped without saying so that is back cleared."""
    before = _labels(old) if old is not None else {}
    after = _labels(new)
    moved: set[str] = set()
    for cid, provider in zip(after, new.all_providers()):
        for was in provider.renamedFrom:
            if was not in before:
                continue
            moved.add(was)
            if was in meta.capabilityMoves:
                continue
            failed = _run_movers(new.name, was, cid)
            meta.capabilityMoves[was] = {"to": cid, "in": new.version}
            if failed:
                meta.capabilityMoves[was]["pending"] = failed
    _keep_tombstones(meta, new, before, after, moved)


def _keep_tombstones(
    meta: InstalledApp,
    new: AppManifest,
    before: dict[str, str],
    after: dict[str, str],
    moved: set[str],
) -> None:
    declared = set(new.retiredCapabilities)
    for cid in after:
        tomb = meta.retiredCapabilities.get(cid)
        if tomb is not None and tomb.get("declared") is not True:
            del meta.retiredCapabilities[cid]
            logger.info(
                "app %s: capability %s, dropped in %s without being retired, is back in %s",
                new.name,
                cid,
                tomb.get("removedIn", "?"),
                new.version,
            )
    for cid, label in before.items():
        if cid not in after and cid not in moved and cid not in meta.retiredCapabilities:
            tomb = {"label": label, "removedIn": new.version, "declared": False}
            meta.retiredCapabilities[cid] = tomb
    # Declared in this version, whether it drops the id now or an earlier one dropped it silently.
    for cid in declared & set(meta.retiredCapabilities):
        meta.retiredCapabilities[cid]["declared"] = True


def retry_pending_moves(meta: InstalledApp) -> bool:
    """Run again each mover a recorded move of *meta*'s is pending on, and mark the move done once
    none is left. A mover not registered now stays pending. Whether *meta* changed."""
    changed = False
    registered = {mover_name(m) for m in _MOVERS}
    for was, move in meta.capabilityMoves.items():
        pending = move.get("pending")
        if not isinstance(pending, list) or not pending:
            continue
        failed = _run_movers(meta.name, was, str(move.get("to", "")), set(pending))
        still = [n for n in pending if n in failed or n not in registered]
        if still == pending:
            continue
        changed = True
        if still:
            move["pending"] = still
        else:
            del move["pending"]
            logger.info("app %s: the move of %s to %s is done", meta.name, was, move.get("to"))
    return changed


def retired_sentence(app: str, capability: str) -> str:
    """What a read of *app*'s retired *capability* says, or ``""`` when it is not retired."""
    from personalclaw.apps.manager import _read_installed

    meta = _read_installed(app)
    tomb = meta.retiredCapabilities.get(capability) if meta is not None else None
    if meta is None or tomb is None:
        return ""
    who = meta.displayName or app
    return (
        f"{who} removed its “{tomb.get('label') or capability}” capability ({capability}) in "
        f"{tomb.get('removedIn', '?')}."
    )
