"""SDK: the contract for an app's ``availability()`` hook, and the cheap check it is built from.

A provider app may export, from its provider module::

    def availability() -> tuple[bool, str]:
        ...  # (True, "") — or (False, "what the user has to do about it")

"can this provider run on THIS machine?". Core runs it in a separate probe process and never
inside the gateway (``providers/availability.py``), kills it at a deadline, and caches the
answer. Every card in Settings → Providers still waits on it, so the hook must be CHEAP:

* **Answer from metadata. Never import the library you are checking for.** Importing a package
  runs its whole initialisation, and for the ML stacks that is the entire cost: a hook that
  ran ``import sentence_transformers`` to learn it was installed took 171.8 s cold. Use
  :func:`modules_installed` — it locates a module without executing it.
* No network, no model load, no subprocess beyond a PATH lookup (``shutil.which``).
* Be deterministic: the same machine gives the same answer, because the answer is cached and
  only re-measured when the user presses "Check again" or it goes stale.

The reason is shown verbatim on the card when the answer is ``False`` — it is product copy, so
it should say what is missing and how to get it.
"""

from __future__ import annotations

import importlib.util


def missing_modules(*modules: str) -> list[str]:
    """The named modules this interpreter could NOT import — found without importing them.

    A top-level name (``"sentence_transformers"``) is located by the path finders and nothing
    runs. A dotted name (``"pyannote.audio"``) imports its PARENT package to find the child,
    but not the child itself — name the top-level module where you can.
    """
    missing: list[str] = []
    for name in modules:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):  # a missing parent package / a spec-less module
            found = False
        if not found:
            missing.append(name)
    return missing


def modules_installed(*modules: str) -> bool:
    """True when every named module is importable here — without importing any of them."""
    return not missing_modules(*modules)


__all__ = ["missing_modules", "modules_installed"]
