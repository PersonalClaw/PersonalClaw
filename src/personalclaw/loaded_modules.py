"""Where the modules this process has loaded came from, read without running any of their code.

``sys.modules`` holds whatever the process's libraries put there, and a library may put anything
there: a module whose ``__getattr__`` answers every name with a new object that is truthy and not
iterable, a proxy that resolves itself the first time it is touched, an object that raises on every
attribute, a lazy loader. Asking such a module for its ``__path__`` or ``__file__`` the ordinary way
runs that code, and whatever it returns or raises lands in the scan that asked. One of them, in a
machine-learning library an app had loaded, was enough to make every app impossible to deactivate
or uninstall until the gateway restarted.

So a scan here reads what the import system stored in the module's own namespace when it loaded
it, and nothing else: no attribute lookup, no ``__getattr__``, no descriptor, no property, and no
iteration of anything but a plain list or tuple. A value of any other shape is skipped, never
trusted, and an object with no namespace of its own records nothing. The scan walks one copy of
``sys.modules`` taken at once, because another thread can import or remove a module while it runs.

Standard library only: :mod:`personalclaw.app_code` reads through this, and the registries that
call it sit below the app platform.
"""

from __future__ import annotations

import importlib._bootstrap_external
import sys
from typing import Any

#: A namespace package's ``__path__`` (a package folder with no ``__init__.py``). Iterating one
#: recalculates it: it reads the parent package's ``__path__``, which runs the parent's code or
#: raises once the parent has left ``sys.modules``, and asks the path finders again. What it found
#: last is read instead. ``None`` on an interpreter without it, where such a path is skipped.
_NAMESPACE_PATH: type | None = getattr(importlib._bootstrap_external, "_NamespacePath", None)


def snapshot() -> list[tuple[str, Any]]:
    """Every module in ``sys.modules`` as one copy taken at once, by name.

    An entry whose name is not a string is left out; its module is not one anything imports.
    """
    return [(name, module) for name, module in sys.modules.copy().items() if type(name) is str]


def recorded(obj: object, name: str) -> str | None:
    """The string the import system stored as *obj*'s *name* (``__file__``, ``__cached__``, a
    spec's ``origin``), or ``None`` when its namespace holds no string there."""
    value = _stored(obj, name)
    return value if type(value) is str else None


def locations(module: object) -> tuple[str, ...]:
    """Where *module* was loaded from: its file, its spec's origin, and the folders a package
    searches, in that order — each a path as the import system recorded it."""
    found = [recorded(module, "__file__"), recorded(_stored(module, "__spec__"), "origin")]
    return tuple(path for path in found if path) + _search_locations(_stored(module, "__path__"))


def forget(name: str, module: object) -> None:
    """Take *module* out of ``sys.modules`` if *name* still holds it: since the snapshot another
    thread may have removed it, or imported a new module under the name, which stays."""
    if sys.modules.get(name) is module:
        sys.modules.pop(name, None)


def _stored(obj: object, name: str) -> object:
    """What *obj*'s own namespace holds under *name*, read without running any of its code."""
    try:
        namespace = object.__getattribute__(obj, "__dict__")
    except Exception:  # noqa: BLE001 — an object with no namespace of its own stores nothing
        return None
    if type(namespace) is not dict:
        return None
    return dict.get(namespace, name)


def _search_locations(path: object) -> tuple[str, ...]:
    if _NAMESPACE_PATH is not None and type(path) is _NAMESPACE_PATH:
        path = _stored(path, "_path")
    if type(path) is list or type(path) is tuple:
        return tuple(entry for entry in path if type(entry) is str)
    return ()
