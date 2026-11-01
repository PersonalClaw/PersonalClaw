"""An app's own check of the settings a form is about to save, asked before anything is written.

A provider may declare ``settingsCheck: "<module>:<function>"`` in its manifest, a module of the
app's own, resolved as its ``implementation`` is. The function takes the settings that provider will
read once the save is made, every secret among them masked (``secret_fields.SECRET_MASK`` for one
that is set), and answers a sentence saying why they cannot be saved, or ``""``. Every route that
saves an app's settings asks :func:`refusal` before it writes, and refuses with the app's sentence:
Apps → Configure, Settings → Providers, a capability's own settings and a further account's. What
core refuses itself, such as an address with a password written into a plain-text setting
(``secret_refs.store``), it refuses whatever the check says.

A check is app code of unbounded cost, so it never runs on the event loop: it runs on a thread of
its own and is waited for :data:`CHECK_SECS`. A check that does not answer in time, cannot be
imported or raises is a refusal that says the check could not run, and nothing is saved that the
app was not asked about. A thread still running a check that did not answer is never started over:
the app's next save is refused until it ends. It runs whenever the app is installed, enabled or
not, since installing the app was the consent to its code.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import logging
import threading
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: How long a save waits for an app's check before it is refused as one that could not run.
CHECK_SECS = 10.0

#: The longest sentence of an app's that a refusal carries, so a check cannot fill a response.
_SENTENCE_LIMIT = 500

#: The app → its check still running from a save that stopped waiting for it.
_running: dict[str, concurrent.futures.Future[Any]] = {}
_lock = threading.Lock()


async def refusal(
    app: str,
    incoming: Mapping[str, Any],
    *,
    capability: str | None = None,
    account: str | None = None,
    whole: bool = False,
) -> str:
    """Why *app*'s checks refuse a save of *incoming*, or ``""`` when none does or it declares none.

    *capability* names the capability whose own settings the save writes, ``None`` for the
    account's settings (all of an app's that declares no account settings). *whole* says the save
    replaces those settings with *incoming*; otherwise *incoming* is merged over what is stored.
    Each provider the save reaches that declares a check is asked with the settings it will read.
    """
    from personalclaw.apps.app_manager import _manifest_of

    manifest = _manifest_of(app)
    if manifest is None or not isinstance(incoming, Mapping):
        return ""  # the route refuses a body that is not an object itself
    who = manifest.displayName or manifest.name
    for cid, provider in zip(manifest.capability_ids(), manifest.all_providers()):
        if not provider.settingsCheck or (capability is not None and cid != capability):
            continue
        view = _after_save(app, cid, incoming, capability, account, whole)
        sentence = await _ask(manifest.name, who, provider.settingsCheck, view)
        if sentence:
            return sentence
    return ""


def _after_save(
    app: str,
    cid: str,
    incoming: Mapping[str, Any],
    capability: str | None,
    account: str | None,
    whole: bool,
) -> dict[str, Any]:
    """The settings capability *cid* reads once the save is made: its account's, with its own over
    them, as ``ProviderSettings.load`` reads them, and the save's in the part it writes. A save of
    the account's settings changes nothing the capability's own set over them."""
    from personalclaw.providers.settings import load_stored_account, load_stored_capability

    shared = load_stored_account(app, account)
    own = load_stored_capability(app, cid, account)
    if capability is None:
        after = {**incoming, **own} if whole else {**shared, **incoming, **own}
    else:
        after = {**shared, **incoming} if whole else {**shared, **own, **incoming}
    return _masked(app, after, {**shared, **own})


def _masked(app: str, settings: Mapping[str, Any], stored: Mapping[str, Any]) -> dict[str, Any]:
    """*settings* with every secret that is set shown as the mask: a check is told a secret is
    there, never what it is. A secret the form sent back empty or masked keeps what is *stored*,
    as the save does (``secret_fields.preserve_unchanged_secrets``)."""
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.config import secret_refs

    out = dict(settings)
    for name in secret_refs.secret_fields(out, secret_refs.declared_app_fields(app)):
        value = out.get(name)
        if isinstance(value, str) and value.strip() or stored.get(name):
            out[name] = SECRET_MASK
    return out


async def _ask(app: str, who: str, entry: str, view: dict[str, Any]) -> str:
    """Run one check off the loop, bounded; its sentence, or the refusal that says it could not."""
    with _lock:
        still = _running.get(app)
        if still is not None and not still.done():
            return (
                f"{who}'s settings check from an earlier save is still running, so nothing was "
                "saved. Save again once it has ended."
            )
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        future.set_running_or_notify_cancel()  # running from here: no wait's end cancels it
        _running[app] = future

    def _run() -> None:
        try:
            future.set_result(_call(app, entry, view))
        except BaseException as exc:  # noqa: BLE001 - app code may raise anything, SystemExit too
            future.set_exception(exc)

    threading.Thread(target=_run, name=f"settings-check:{app}", daemon=True).start()
    try:
        answer = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), CHECK_SECS)
    except TimeoutError:
        logger.warning("app %s: settings check did not answer within %.0f s", app, CHECK_SECS)
        return (
            f"{who}'s settings check did not answer within {CHECK_SECS:.0f} s, so nothing was "
            "saved."
        )
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise  # the save itself was cancelled (its client left, or a shutdown): no answer
        logger.warning("app %s: settings check could not run (it was cancelled)", app)
        return f"{who}'s settings check could not run (CancelledError), so nothing was saved."
    except BaseException as exc:  # noqa: BLE001 - an import failure, or the check raising
        logger.warning("app %s: settings check could not run", app, exc_info=exc)
        return f"{who}'s settings check could not run ({type(exc).__name__}), so nothing was saved."
    if answer is None or answer == "":
        return ""
    if not isinstance(answer, str):
        return f"{who}'s settings check gave no answer PersonalClaw can read, so nothing was saved."
    return " ".join(answer.split())[:_SENTENCE_LIMIT]


def _call(app: str, entry: str, view: dict[str, Any]) -> Any:
    """Import *entry*'s module as the extension loader imports an app's, and call it on *view*."""
    from personalclaw.apps.manager import app_dir
    from personalclaw.apps.native_contract import (
        app_dir_on_path,
        bundle_module_file,
        load_bundle_module,
    )

    module_path, _, function = entry.rpartition(":")
    root = app_dir(app)
    if bundle_module_file(root, module_path) is not None:
        module = load_bundle_module(root, app, module_path)
    else:
        with app_dir_on_path(app, root):
            module = importlib.import_module(module_path)
    return getattr(module, function)(view)
