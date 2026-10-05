"""Where the programs a test starts keep their files: a folder of the test's own, never ``HOME``.

A test's children run the developer's own programs, and they inherit the developer's ``HOME``: black
formatting a file keeps its cache in ``~/Library/Caches/black``, npm keeps its cache and writes a
debug log under ``~/.npm``, a mise shim (the developer's ``python3`` can be one) keeps lockfiles and
version lists in mise's cache and state folders, and a shell keeps its history in ``~/.zsh_history``
or ``~/.bash_history``. And the libraries PersonalClaw loads were told nothing in a test, though
every ``personalclaw`` command tells them (``personalclaw.library_env``): the code map's grammars
landed in the language pack's own folder in the user's cache, ``~/Library/Caches`` on a Mac.

So every test runs with each program's own setting pointed into its folder (:func:`program_env`),
and with ``library_env`` for its own home (``conftest._each_program_keeps_its_files_in_the_tests_
folder``); what runs before any test, a module's import, gets the same for a folder of the run's own
(:func:`for_collection`). The code map's grammars are the one exception, and they are not in
``HOME`` either: they are a download of tens of megabytes, so a run fetches them once, through the
egress guard as every download is, and every test reads them there (:func:`grammar_env`). They and
the default chat model's weight, the suite's two downloads, are kept in the run's downloads folder
(:func:`downloads`): pytest's own cache folder in the checkout, or the folder
:data:`DOWNLOADS_SETTING` names, where a run that finds them already fetches nothing.

What this does not move: a program that ignores its setting, a setting a shell's own startup file
overrides, and what a program only reads (its configuration, the tools it runs). A rustup proxy only
reads: with an existing rustup home it writes nothing, so that home stays where the developer keeps
it, and ``rustc`` keeps working in a test.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

#: Each program's own setting for a folder it writes, and that folder under a test's own folder.
PROGRAM_FOLDERS: dict[str, str] = {
    # black's cache of the files it formatted (``~/Library/Caches/black`` on a Mac).
    "BLACK_CACHE_DIR": "black",
    # npm's cache, which holds its debug logs (``_logs``) and the date it last asked the registry
    # whether a newer npm exists.
    "npm_config_cache": "npm",
    # mise's cache (lockfiles, version lists) and state (the configurations it has seen). Its
    # installs and its configuration stay where the developer keeps them, so a shim still runs.
    "MISE_CACHE_DIR": "mise/cache",
    "MISE_STATE_DIR": "mise/state",
    # A shell's history. zsh on a Mac names its own in its startup file, so a test that opens a
    # shell opens one that reads none of the developer's: ``/bin/sh``, with a ``HOME`` of its own.
    "HISTFILE": "shell-history",
}

#: Each program's own setting for something it would do by itself, and the value that stops it.
PROGRAM_VALUES: dict[str, str] = {
    # npm asks the registry whether a newer npm exists, and a cache of the test's own has never
    # recorded asking, so it would ask in every test that runs npm.
    "npm_config_update_notifier": "false",
}

#: The language pack's settings :func:`grammar_env` keeps for the whole run.
GRAMMAR_SETTINGS = ("TREE_SITTER_LANGUAGE_PACK_CACHE_DIR", "TREE_SITTER_LANGUAGE_PACK_MANIFEST_URL")

#: The setting that names the folder a run keeps the suite's downloads in, in place of pytest's
#: cache folder: what the folder holds is read, and what it lacks is fetched into it, once.
DOWNLOADS_SETTING = "PERSONALCLAW_TEST_DOWNLOADS"
#: The code map's grammars, in the downloads folder: the folder :func:`grammar_env` is given.
GRAMMARS = "tree-sitter-grammars"
#: The default chat model's weight, in the downloads folder: a home it is fetched into, where it
#: lands at the record's ``artifact`` (``test_bundled_model_gate.signed_off_weight``).
WEIGHT = "bundled-chat-model"

#: The run's own folder, for what runs before any test (a module's import).
BASE = Path(tempfile.mkdtemp(prefix="pclaw-tests-programs-"))


def program_env(folder: Path) -> dict[str, str]:
    """Each program's settings, its folders named under *folder*. Names only: each program makes
    its folder the first time it writes there."""
    return {
        **{name: str(folder / relative) for name, relative in PROGRAM_FOLDERS.items()},
        **PROGRAM_VALUES,
    }


def library_env_for(home: Path) -> dict[str, str]:
    """``personalclaw.library_env`` for *home*: asked while ``PERSONALCLAW_HOME`` names it, and the
    environment put back as it was."""
    from personalclaw.library_env import library_env

    chosen = os.environ.get("PERSONALCLAW_HOME")
    os.environ["PERSONALCLAW_HOME"] = str(home)
    try:
        return library_env()
    finally:
        if chosen is None:
            os.environ.pop("PERSONALCLAW_HOME", None)
        else:
            os.environ["PERSONALCLAW_HOME"] = chosen


def downloads(config: object, name: str) -> Path:
    """The run's folder for the download *name*, kept for the whole run and shared by every worker:
    in the folder :data:`DOWNLOADS_SETTING` names, else in pytest's own cache folder in the
    checkout, kept between runs, else (a run without that cache) in a folder of the run's own.
    Named only: nothing is made in a folder the setting names, which may be one a run only reads."""
    named = os.environ.get(DOWNLOADS_SETTING, "")
    if named:
        return Path(named).expanduser().resolve() / name
    cache = getattr(config, "cache", None)
    if cache is not None:
        return Path(cache.mkdir(name))
    return BASE / name


def grammar_env(folder: Path) -> dict[str, str]:
    """The language pack's settings for grammars kept in *folder* for the whole run: the ones a home
    there would have."""
    settings = library_env_for(folder / "home")
    return {name: settings[name] for name in GRAMMAR_SETTINGS}


def for_collection(base: Path, grammars: Path) -> Callable[[], None]:
    """Set every setting for *base*, the run's own folder, before anything is collected, with the
    grammars kept in *grammars*; return the call that puts the environment back and removes
    *base*."""
    settings = {
        **program_env(base / "programs"),
        **library_env_for(base / "home"),
        **grammar_env(grammars),
    }
    before = {name: os.environ.get(name) for name in settings}
    os.environ.update(settings)

    def undo() -> None:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(base, ignore_errors=True)

    return undo
