"""The source registry — the one list of tools we can import from.

A source is (name, display name, env var, default root, scan function, its two
conversation readers, and where it keeps its setup). Adding a tool is adding one module under
:mod:`~personalclaw.onboarding_import.sources` and one row here; nothing downstream
changes, which is why broader source coverage was explicitly not a v1 bar.

Each source's setup is a place outside the home (:mod:`personalclaw.outside_home`,
``setup:<name>``), read only for a press that names it or once the owner turned it on: the
readers of the machine's tools (:func:`~.engine.scan_all`,
:func:`~personalclaw.mcp_discovery.discover_importable_servers`) ask
:func:`~personalclaw.outside_home.readable` before they open anything of a tool's.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from personalclaw.onboarding_import.model import ImportItem, ScanResult
from personalclaw.onboarding_import.sources import claude_code, codex
from personalclaw.outside_home import SETUP_PREFIX


@dataclass(frozen=True)
class ImportSource:
    name: str
    display_name: str
    env_var: str
    default_root: str
    scan: Callable[..., ScanResult]
    resolve_root: Callable[[], Path]
    #: A conversation item's transcript, read in full when it is imported:
    #: ``(conversation, redactions)``, or ``None`` when the file holds no prompt.
    read_for_import: Callable[[ImportItem], tuple[dict[str, Any], int] | None]
    #: One conversation file read in full, so what the scan says of it is final.
    read_in_full: Callable[[Path], None]
    #: Where the tool keeps its setup on this machine, as Settings names the place. Worked out
    #: without opening or checking anything there.
    locations: Callable[[], tuple[Path, ...]]
    #: What else reading the setup opens, in words, ending in a space; ``""`` for nothing.
    place_note: str

    @property
    def place(self) -> str:
        """The tool's setup as a place outside the home: what a press names, and Settings
        turns on."""
        return SETUP_PREFIX + self.name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "env_var": self.env_var,
            "default_root": self.default_root,
        }


def _source(module) -> ImportSource:
    return ImportSource(
        name=module.NAME,
        display_name=module.DISPLAY_NAME,
        env_var=module.ENV_VAR,
        default_root=module.DEFAULT_ROOT,
        scan=module.scan,
        resolve_root=module.resolve_root,
        read_for_import=module.read_for_import,
        read_in_full=module.read_in_full,
        locations=module.locations,
        place_note=module.PLACE_NOTE,
    )


SOURCES: tuple[ImportSource, ...] = (_source(claude_code), _source(codex))

_BY_NAME: dict[str, ImportSource] = {src.name: src for src in SOURCES}


def list_sources() -> tuple[ImportSource, ...]:
    return SOURCES


def get_source(name: str) -> ImportSource:
    """Look up a source by name. Unknown names raise — never a silent no-op scan."""
    try:
        return _BY_NAME[name]
    except KeyError:
        known = ", ".join(sorted(_BY_NAME))
        raise KeyError(f"unknown import source {name!r} (known: {known})") from None


def looked_in(values: Iterable[object], *, field: str = "look_in") -> frozenset[str]:
    """The places a press asks to look in (``look_in``, or the request's ``field``), each one a
    source's setup.

    Raises ``ValueError`` naming the first that is not: a press that names something else asks
    for nothing these readers read, and saying so beats answering as though it had looked.
    """
    known = {src.place for src in SOURCES}
    asked: set[str] = set()
    for value in values:
        if not isinstance(value, str) or value not in known:
            raise ValueError(
                f"'{field}' names {value!r}, which is not a tool PersonalClaw can look in "
                f"(one of {', '.join(sorted(known))})."
            )
        asked.add(value)
    return frozenset(asked)
