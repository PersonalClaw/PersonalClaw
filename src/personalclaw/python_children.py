"""Children that need a Python interpreter, and the desktop app's refusal of them.

Wherever PersonalClaw runs from an interpreter it can start Python children of its own: an app's
own server and background worker, pip installing the packages an app declares, an app's engine in
a Python environment of its own, a connector pack's parse script, the Linux sandbox's launcher, a
bundle's own tests. The desktop app runs PersonalClaw from a frozen bundle
(:func:`personalclaw.self_update.is_frozen`), whose executable is the CLI and runs the three child
modules ``_frozen_child`` declares, and no other Python. Each of those children reached the CLI's
parser there and exited at once with a usage error, or failed with no word at all.

So :func:`available` is the one question every such child asks before anything of it starts, and
the desktop app refuses each one with one sentence (:func:`refusal`): what the desktop app cannot
run, and that the version installed with uv runs it. The refusal is said where the owner acts: an
app that needs any of them is shown as not available in the Store and the Library, and its
install, enable and start are refused (:func:`app_refusal`); the Linux sandbox refuses the
command, which never runs outside it.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from personalclaw.apps.manifest import AppManifest

#: The install that runs every Python child: the recommended route of
#: ``docs/guides/getting-started.md``, which ``docs/guides/desktop.md`` points at.
INSTALL_COMMAND = "uv tool install --python 3.13 personalclaw"


class NeedsInterpreter(RuntimeError):
    """A child that needs a Python interpreter, refused in the desktop app before it started.

    ``str()`` is the sentence the owner reads (:func:`refusal`)."""


def available() -> bool:
    """Whether this install can start a child that needs a Python interpreter.

    Every install can but the desktop app's frozen bundle, whose executable runs PersonalClaw's
    CLI and nothing else of Python's."""
    from personalclaw.self_update import is_frozen

    return not is_frozen()


def refusal(cannot: str) -> str:
    """The one sentence the desktop app refuses a Python child with.

    *cannot* is what it cannot do, said after "can't": ``start this app's own Python server``.
    The sentence ends with what the owner can do instead, which is the same for every child.
    """
    return f"The desktop app can't {cannot}; the version you install with `{INSTALL_COMMAND}` can."


def require(cannot: str) -> None:
    """Refuse *cannot* here, before anything of it starts, when this install has no interpreter.

    Raises:
        NeedsInterpreter: in the desktop app, with :func:`refusal`'s sentence.
    """
    if not available():
        raise NeedsInterpreter(refusal(cannot))


def app_refusal(manifest: AppManifest) -> str:
    """Why this install cannot run *manifest*'s app, as one sentence, or ``""`` when it can.

    An app needs a Python interpreter when it runs its own Python server, a background worker
    (always Python), an engine in a Python environment of its own (``execution: "sidecar"``),
    connector-pack parse scripts, or Python packages this install does not already carry. The
    packages are judged the way an install judges them (``app_python.unmet``), so an app whose
    packages the desktop app carries (the model SDKs it is built with) installs and runs there.
    ``""`` on every install that has an interpreter: none of this is a limit there.
    """
    if available():
        return ""
    needs = _what_needs_python(manifest)
    if not needs:
        return ""
    if len(needs) == 1:
        joined = needs[0]
    else:
        joined = f"{', '.join(needs[:-1])} or {needs[-1]}"
    return refusal(joined)


def _what_needs_python(manifest: AppManifest) -> list[str]:
    """What of *manifest*'s app runs as a Python child, each said as what the desktop app cannot
    do: the first names "this app", the rest say "its" or "it"."""
    from personalclaw.apps.backend_runtime import launcher_kind
    from personalclaw.apps.manifest import EXECUTION_SIDECAR
    from personalclaw.apps.worker_runtime import declared_workers

    parts: list[tuple[str, str]] = []
    backend = manifest.backend
    if backend.entryPoint and launcher_kind(backend.type, backend.entryPoint) == "python":
        parts.append(("start this app's own Python server", "start its own Python server"))
    if declared_workers(manifest):
        parts.append(
            ("start this app's Python background worker", "start its Python background worker")
        )
    if any(p.execution == EXECUTION_SIDECAR for p in manifest.all_providers()):
        engine = "engine in a Python environment of its own"
        parts.append((f"start this app's {engine}", f"start its {engine}"))
    if manifest.sources:
        parts.append(("run this app's Python parse scripts", "run its Python parse scripts"))
    missing = _missing_packages(manifest.dependencies.pythonDependencies)
    if missing:
        listed = ", ".join(missing)
        parts.append(
            (
                f"install the Python packages this app needs ({listed})",
                f"install the Python packages it needs ({listed})",
            )
        )
    return [first if i == 0 else later for i, (first, later) in enumerate(parts)]


def _missing_packages(requirements: list[str]) -> list[str]:
    """Which of *requirements* nothing this install carries satisfies, as declared."""
    if not requirements:
        return []
    from personalclaw.apps import app_python

    return list(_unmet(tuple(requirements), str(app_python.root())))


@functools.lru_cache(maxsize=512)
def _unmet(requirements: tuple[str, ...], folder: str) -> tuple[str, ...]:
    """``app_python.unmet`` for *requirements*, read once for each list and app packages
    *folder*. Asked only in the desktop app, by every Store card and Library row, where each read
    walks every package's metadata. Nothing there changes what is importable while it runs: it
    installs no package and collects none (``app_python.collect``), and the version installed
    with uv, which does, is not run on the same folder at once (``docs/guides/desktop.md``).
    *folder* keys each answer to the PersonalClaw folder it was read in."""
    from personalclaw.apps import app_python

    return tuple(app_python.unmet(list(requirements)))
