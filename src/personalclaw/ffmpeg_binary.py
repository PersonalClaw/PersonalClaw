"""Where PersonalClaw finds ffmpeg and ffprobe: an absolute path, handed to whatever runs them.

ffmpeg cuts a long recording into parts for speech-to-text, takes the sound and frames out of a
video for the knowledge base, joins a spoken reply's sentences, and decodes recordings for speaker
diarization. It is not a dependency, so each of those asks :func:`find_ffmpeg` and, when it is not
there, says so in the words :func:`ffmpeg_not_found` gives.

It is looked for in these folders and nowhere else, in order:

1. the folders on the ``PATH`` this process started with (:func:`personalclaw.env.startup_path`);
2. :data:`FALLBACK_DIRS`, where package managers install it, for a gateway that a service manager
   started with a short ``PATH``.

Only absolute folders count: an empty or relative ``PATH`` entry names whatever folder the process
happens to be working in. And the process's own ``PATH`` is never changed to find it. That
``PATH`` is every child's, so a folder added to it so that one program resolves would make every
other program in that folder resolve for each tool server, hook and script started after it.
"""

from __future__ import annotations

import os
import sys

#: Where package managers put ffmpeg, searched after the startup ``PATH``: a user install, then
#: Homebrew on Apple silicon, then Homebrew on Intel macs and most hand-built installs. Named in
#: :func:`ffmpeg_not_found`, so a missing ffmpeg says where it was looked for. ``~`` is the home
#: of the moment the lookup runs.
FALLBACK_DIRS: tuple[str, ...] = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")


def _folders() -> list[str]:
    """The folders looked in, in order. A fallback under ``~`` is in the owner's own home, where
    they installed ffmpeg themselves: read only to find that one program."""
    from personalclaw.env import startup_path

    home = os.path.expanduser("~")
    fallbacks = [os.path.join(home, d[2:]) if d.startswith("~/") else d for d in FALLBACK_DIRS]
    return [*(startup_path() or "").split(os.pathsep), *fallbacks]


def _find(name: str) -> str | None:
    for folder in _folders():
        if not os.path.isabs(folder):
            continue
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_ffmpeg() -> str | None:
    """The absolute path of the ffmpeg this process runs, or ``None`` when there is none."""
    return _find("ffmpeg")


def find_ffprobe() -> str | None:
    """The absolute path of the ffprobe this process runs, or ``None`` when there is none."""
    return _find("ffprobe")


def install_hint(platform: str | None = None) -> str:
    """The command that installs ffmpeg on *platform* (this one by default).

    A fault line hands back a command that works where it is read, and ``brew`` works on exactly
    one of the three platforms this ships to: inside the published Linux container the doctor
    once said ``brew install ffmpeg``, on a machine with no brew and no way to get one."""
    plat = sys.platform if platform is None else platform
    if plat == "darwin":
        return "brew install ffmpeg"
    if plat.startswith("win"):
        return "winget install ffmpeg"
    return "apt install ffmpeg (or your distribution's package manager)"


def _listed(items: tuple[str, ...]) -> str:
    if len(items) < 2:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def ffmpeg_not_found() -> str:
    """Why nothing ran ffmpeg, in the words the owner reads where they act: where it was looked
    for, and what to do."""
    where = "the folders on the PATH the gateway started with"
    if FALLBACK_DIRS:
        where += f", then {_listed(FALLBACK_DIRS)}"
    return (
        f"ffmpeg isn't installed where PersonalClaw looks for it: {where}. Install it with "
        f"{install_hint()} and try again, or start the gateway with the folder that holds it on "
        "its PATH."
    )
