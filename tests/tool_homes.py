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
egress guard as every download is, and every test reads them there (:func:`grammar_env`). The run
fetches them before its first test, never a test itself (:func:`fetch_grammars`,
:func:`refuse_grammar_fetches`). They and
the default chat model's weight, the suite's two downloads, are kept in the run's downloads folder
(:func:`downloads`): pytest's own cache folder in the checkout, or the folder
:data:`DOWNLOADS_SETTING` names, where a run that finds them already fetches nothing.

What this does not move: a program that ignores its setting, a setting a shell's own startup file
overrides, and what a program only reads (its configuration, the tools it runs). A rustup proxy only
reads: with an existing rustup home it writes nothing, so that home stays where the developer keeps
it, and ``rustc`` keeps working in a test.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Iterable
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


# ── The code map's grammars: fetched before the first test, never by a test ──────────────────
#
# The grammars come from the language pack's releases on GitHub. The first test that parsed code
# used to fetch them itself, inside its 30 s timeout, so a release download that answered 504 or
# hung turned the tests that parse code red on every change, whatever it touched. Now a test that
# needs a grammar asks for the :data:`GRAMMAR_FIXTURE` fixture, and a run that collected one fetches
# them once before its first test (:func:`fetch_grammars`). A test that reaches for them on the
# network is refused (:func:`refuse_grammar_fetches`), so none does.

#: The fixture a test that parses code with a grammar asks for (``tests/conftest.py``).
GRAMMAR_FIXTURE = "code_map_grammars"
#: How many times the run tries the fetch, and how long it waits before each try after the first:
#: GitHub's release downloads have answered 504 for minutes at a time.
GRAMMAR_FETCH_TRIES = 3
GRAMMAR_FETCH_WAITS_S = (10.0, 30.0)
#: How long one try may take, the 25 MB bundle included, before it is ended and tried again.
GRAMMAR_FETCH_TIMEOUT_S = 240.0
#: What a try runs: the code map's own fetch, through the egress guard, for one language. The
#: bundle holds every grammar for this machine, so the pack unpacks the others from it, offline.
_FETCH = (
    "import sys\n"
    "from personalclaw.codegraph import grammars\n"
    "try:\n"
    "    grammars.ensure('python')\n"
    "except grammars.GrammarUnavailable as exc:\n"
    "    print(exc, file=sys.stderr)\n"
    "    sys.exit(3)\n"
)


class GrammarFetch:
    """What this process's run did about the grammars: ``None`` while it has not fetched them
    (no test it collected asks), ``""`` once they are on this machine, else why they are not."""

    why_not: str | None = None


def asks_for_grammars(items: Iterable[object]) -> bool:
    """Whether any of the collected *items* asks for the grammars."""
    return any(GRAMMAR_FIXTURE in getattr(item, "fixturenames", ()) for item in items)


def fetch_grammars(
    base: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Fetch the grammars into the run's folder (the language pack's settings in ``os.environ``,
    :func:`for_collection`), trying :data:`GRAMMAR_FETCH_TRIES` times; ``""`` once they are there,
    else why not, in the code map's own words.

    Each try is a process of its own, outside every test's timeout, with a home of its own under
    *base*: the fetch reads that home's network settings and audits there, never the owner's. The
    workers of a run share the folder: one fetches while the others wait for it, and find what it
    unpacked (``grammars.ensure``)."""
    home = base / "grammar-fetch"
    env = {**os.environ, "PERSONALCLAW_HOME": str(home / "home"), "HOME": str(home / "user")}
    why = ""
    for attempt in range(GRAMMAR_FETCH_TRIES):
        if attempt:
            sleep(GRAMMAR_FETCH_WAITS_S[attempt - 1])
        try:
            done = run(
                [sys.executable, "-c", _FETCH],
                env=env,
                timeout=GRAMMAR_FETCH_TIMEOUT_S,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            why = f"the fetch took longer than {GRAMMAR_FETCH_TIMEOUT_S:.0f} s and was ended"
            continue
        if done.returncode == 0:
            return ""
        lines = [line for line in (done.stderr or "").splitlines() if line.strip()]
        why = lines[-1].strip() if lines else f"the fetch exited {done.returncode}"
    return (
        f"The run could not fetch the code map's grammars before its first test, in "
        f"{GRAMMAR_FETCH_TRIES} tries: {why}"
    )


def _on_this_computer(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def refuse_grammar_fetches(fetched: type[GrammarFetch] = GrammarFetch) -> Callable[[], None]:
    """Refuse every fetch of the grammars from beyond this computer, for the rest of this process,
    and return the call that lets them through again. A test of the fetch itself points it at a
    server on this computer, and is left to fetch from there."""
    from personalclaw.codegraph import grammars

    get = grammars._get

    def _get(url: str, **kwargs):
        if _on_this_computer(url):
            return get(url, **kwargs)
        why = fetched.why_not
        if why:
            raise grammars.GrammarUnavailable(
                f"{why} A test never fetches them itself: the run does, for each test that asks "
                f"for the `{GRAMMAR_FIXTURE}` fixture."
            )
        raise grammars.GrammarUnavailable(
            f"A test asked the network for the code map's grammars ({url}). A test never fetches "
            f"them: the run fetches them before its first test, for each test that asks for the "
            f"`{GRAMMAR_FIXTURE}` fixture (tests/conftest.py)."
        )

    grammars._get = _get

    def undo() -> None:
        grammars._get = get

    return undo
