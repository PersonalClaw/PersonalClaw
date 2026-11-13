"""The apps' engines across a restore: which apps came back without an engine that fits, the
engines a replace hands back to their apps, the ones left in the backup, and the warning that says
to install one. An engine (``apps/<app>/venv``) is built for the machine it runs on, so a snapshot
leaves it out (``snapshot.derived_within``). Read by ``snapshot``'s restore."""

from __future__ import annotations

import sys
from pathlib import Path

from personalclaw.security import terminal_text


def engines_not_here(snap: Path) -> list[tuple[str, bool]]:
    """The apps this restore brought back whose engine is not installed here: ``(display name,
    whether the app has an engine here at all)``.

    An engine lives in the app's own ``venv/``, which a snapshot leaves out, so an app that declares
    one comes back without it and offers Install engine, unless this home had it. A merge keeps the
    live ``venv/`` and a replace hands it back to the app (:func:`keep_app_engines`); an engine
    kept that way was installed for the version this home had, so an app is named with ``True``
    when the version that came back does not match it, and Install engine then brings that
    environment up to date. An app whose engine is installed is not named, nor is an app the
    restore did not touch.
    """
    if not (snap / "apps").is_dir():
        return []
    try:
        from personalclaw.apps.manager import (
            APP_MANIFEST_FILENAME,
            APP_VENV_DIRNAME,
            app_dir,
            engine_not_installed,
        )
        from personalclaw.apps.manifest import AppManifest

        names: list[tuple[str, bool]] = []
        for tree in sorted((snap / "apps").iterdir()):
            if tree.name.startswith(".") or not (tree / APP_MANIFEST_FILENAME).is_file():
                continue
            if not engine_not_installed(tree.name):
                continue
            here = app_dir(tree.name)
            manifest = AppManifest.from_json_file(here / APP_MANIFEST_FILENAME)
            names.append((manifest.displayName or tree.name, (here / APP_VENV_DIRNAME).is_dir()))
        return names
    except Exception:  # noqa: BLE001 — a note after the restore must never fail the restore
        import logging

        logging.getLogger(__name__).debug("engine census after restore failed", exc_info=True)
        return []


def app_display_name(folder: Path) -> str:
    """The display name in the app manifest *folder* holds, else the folder's name."""
    from personalclaw.apps.manager import APP_MANIFEST_FILENAME
    from personalclaw.apps.manifest import AppManifest

    try:
        return AppManifest.from_json_file(folder / APP_MANIFEST_FILENAME).displayName or folder.name
    except Exception:  # noqa: BLE001 — a name for a sentence, never a reason to fail a restore
        return folder.name


def keep_app_engines(displaced: Path, restored: Path) -> list[str]:
    """Hand each app's engine back to the app of the same name the restore brought back.

    An engine (``apps/<app>/venv``) is built for this machine: capture leaves it out
    (``derived_within``) and a restore never plants one, so a replace has nothing to put in its
    place. Moving it into ``pre-restore-<ts>/`` with the rest of the app left gigabytes of working
    engine there while the restored app offered to install the same engine again. An update keeps
    an app's engine the same way (``apps.app_manager._carry_state``: a rename on one filesystem,
    whatever the size), and the app's own check (``SidecarInstall.installed``: the interpreter, and
    the packages the restored manifest declares) says whether it fits the version that came back.
    Everything else of the app, its ``data/`` included, stays displaced, so undoing the restore
    still has it. Returns the folder names of the apps whose engine stayed.
    """
    from personalclaw.apps.app_manager import _carry_state
    from personalclaw.apps.manager import APP_MANIFEST_FILENAME, APP_VENV_DIRNAME

    if not displaced.is_dir() or not restored.is_dir():
        return []
    kept: list[str] = []
    for old in sorted(displaced.iterdir()):
        new = restored / old.name
        if old.name.startswith(".") or not (new / APP_MANIFEST_FILENAME).is_file():
            continue
        if _carry_state(old, new, names=(APP_VENV_DIRNAME,)):
            kept.append(old.name)
    return kept


def engines_set_aside(displaced: Path) -> list[tuple[Path, int]]:
    """The engines still in the backup: those of apps the restore did not bring back, with their
    size. Each stays with its app, as removing an app takes its engine along, so moving the folder
    back undoes the restore whole; the backup is the user's to delete."""
    from personalclaw.apps.manager import APP_VENV_DIRNAME
    from personalclaw.durability.footprint import _tree_bytes

    if not displaced.is_dir():
        return []
    out: list[tuple[Path, int]] = []
    for old in sorted(displaced.iterdir()):
        venv = old / APP_VENV_DIRNAME
        if old.name.startswith(".") or venv.is_symlink() or not venv.is_dir():
            continue
        out.append((old, _tree_bytes(venv, skip_files=set(), skip_dirs=set())))
    return out


def say_engines_to_install(engines: list[tuple[str, bool]]) -> None:
    """For each app the restore brought back, by name, with whether it has an engine here that
    does not fit the version that came back, the warning on stderr saying to install one."""
    for name, has_one in engines:
        shown = terminal_text(str(name))
        if has_one:
            print(
                f"⚠️  {shown}'s engine here does not match the version the restore "
                "brought back. Install engine, on the app's card in Settings → Models & Providers → Providers, brings it up to date.",  # noqa: E501
                file=sys.stderr,
            )
            continue
        print(
            f"⚠️  {shown} has no engine here: a snapshot leaves engines out, since "
            "each one is built for the machine it runs on. Install it with Install engine, on the "
            "app's card in Settings → Models & Providers → Providers.",
            file=sys.stderr,
        )
