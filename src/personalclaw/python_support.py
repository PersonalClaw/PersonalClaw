"""Whether an interpreter is one the installed PersonalClaw supports, and how to leave one it isn't.

The supported range is the installed distribution's own ``Requires-Python``, read from its
metadata, so nothing here states a version. pip refuses a Python outside that range; uv does not
enforce its upper bound, so a ``uv tool install`` that names no ``--python`` builds the tool
environment on whatever interpreter uv finds or downloads newest, and the release then runs on a
Python nothing tested it on. ``personalclaw doctor`` asks here and fails such an install.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from packaging.specifiers import InvalidSpecifier, SpecifierSet

DISTRIBUTION = "personalclaw"

#: What uv writes into every tool environment it builds (``uv tool install``), and nothing else
#: writes: the mark of an install ``uv tool upgrade`` can rebuild.
UV_TOOL_RECEIPT = "uv-receipt.toml"


@dataclass(frozen=True)
class PythonSupport:
    """One interpreter, judged against the installed release's ``Requires-Python``.

    ``supported`` is ``None`` when there was nothing to judge it against, and ``unknown`` then
    says why: that is a fact to report, never a pass.
    """

    version: str
    requires: str
    supported: bool | None
    unknown: str = ""


def python_support(version: str) -> PythonSupport:
    """Judge an interpreter's *version* (``3.13.14``) against the installed release's range.

    Only its release numbers are compared, as pip compares them, so a candidate of the first
    unsupported release (``3.14.0rc1`` under ``<3.14``) reads as that release.
    """
    release = re.match(r"\d+\.\d+(?:\.\d+)?", version)
    try:
        requires = (metadata.metadata(DISTRIBUTION).get("Requires-Python") or "").strip()
    except metadata.PackageNotFoundError:
        return PythonSupport(
            version, "", None, "no installed PersonalClaw metadata to check it against"
        )
    if not requires:
        return PythonSupport(
            version, "", None, "the installed PersonalClaw declares no Python range"
        )
    try:
        spec = SpecifierSet(requires)
    except InvalidSpecifier:
        return PythonSupport(
            version, requires, None, f"its Python range {requires!r} is unreadable"
        )
    if release is None:
        return PythonSupport(version, requires, None, "its version could not be read")
    return PythonSupport(version, requires, spec.contains(release.group(0), prereleases=True))


def environment_root() -> Path:
    """The environment the running interpreter belongs to."""
    return Path(sys.prefix)


def rebuild_command(requires: str) -> str:
    """The one command that rebuilds this install on a Python matching *requires*, or ``""``.

    Only a uv tool environment has one: ``uv tool upgrade --python`` rebuilds it on a matching
    interpreter (downloading one when none is found) and keeps what it was installed with, its
    extras and options included. A pip venv, a pipx install or a checkout has to be recreated by
    whoever made it.
    """
    if (environment_root() / UV_TOOL_RECEIPT).is_file():
        return f"uv tool upgrade --python '{requires}' {DISTRIBUTION}"
    return ""


def reinstall_command() -> str:
    """The one command that reinstalls this install with every dependency it declares, or ``""``.

    Only a uv tool environment has one: ``uv tool upgrade --reinstall`` reinstalls every package
    in it, on the Python and with the options it was installed with. A pip venv, a pipx install or
    a checkout is reinstalled by whoever made it.
    """
    if (environment_root() / UV_TOOL_RECEIPT).is_file():
        return f"uv tool upgrade --reinstall {DISTRIBUTION}"
    return ""
