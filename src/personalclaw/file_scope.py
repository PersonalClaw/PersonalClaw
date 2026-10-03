"""Where the agent's file tools reach: one scope, read again at every call.

The native file tools (``read_file``, ``list_dir``, ``glob``, ``grep``, ``repo_map``,
``write_file``, ``edit_file``) and ``code_map`` work in three kinds of place:

* **the workspace**: the folder the session works in, and the extra folders the runtime gives a
  loop's worker (its engine files). Read and change.
* **the folders a session's work reads**: a workflow step's reach beyond the folder it works in
  (the tree its run's project is bound to, the folder its batch was started in). Read only, so a
  step working in an isolated copy cannot change the original through them; what a step may
  change stays its own folder's, and its tier's.
* **the allowed working directories** in Settings › Agent defaults
  (``agent.subagent_cwd_allowed_roots``). Read and change. A change meets the same approval, with
  its diff, as a change in the workspace: this module decides only WHERE a tool reaches.
* **the folders the owner added as knowledge sources** (a Watched Directory in Knowledge ›
  Sources). Read only, and only what that source itself takes in (``dir_source.resolve_in`` and
  ``dir_source.takes``, the rule its own scan uses: inside the folder once links are resolved, its
  file patterns, nothing hidden), because that is what the owner chose to share.

Everywhere else is refused. The answer depends only on the call's own arguments, the session's
folder and the owner's settings, so it can be given before anyone is asked to approve the call:
:func:`refusal` is that check, and the tools make the same one (:meth:`FileScope.resolve`).

The settings and the sources are read when a call is decided, never kept, so a folder the owner
takes out of the list, or a source paused or pointed elsewhere, is out of reach at the next call.
An entry that names the filesystem root or a system folder is not a place (``file_roots``'s rule
for a bound workspace), and a source whose folder its own provider would refuse to poll is not
one either (``DirSourceProvider.validate_spec``).

**PersonalClaw's own stores are not files to the agent.** The workspace in the home holds stores
of PersonalClaw's own beside the agent's files: the knowledge library's database and its stored
documents, the lexicon. Each is declared in the state inventory (``durability.inventory``, every
entry inside its ``workspace`` entry), and each is read and changed only through its own tools: a
raw read hands the model pages of a database, past the masking its tools apply, and a write breaks
the store. So the file tools refuse them and their listings leave them out
(:meth:`FileScope.own_store`), and a shell command that names one is refused before it is approved
(:func:`store_named_in`), with the tool to use instead.

Inside every place the checks the Files view makes still hold (``file_roots.Admission``):
symlinks and ``..`` are resolved first, so a link or a climb out of a place reaches nothing it
does not already reach; no credential location or secret file; and nothing in PersonalClaw's own
home except through a place inside it (``file_roots.within``). A path that starts with ``~/``
names the owner's home, as the owner writes it; ``~name`` stays a plain name. A path the tools show
in the home is written from ``~`` (:mod:`personalclaw.home_paths`), and either form opens it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.home_paths import from_home

logger = logging.getLogger(__name__)

#: The kinds of place.
WORKSPACE = "workspace"
ALLOWED = "allowed"
READS = "reads"
SOURCE = "source"

#: The native file tools that take a path: the argument naming it, whether the call changes the
#: file, and the path a call naming none is checked as (``None``: it is not checked here, since
#: the argument is required or the tool then works in the session's own folder).
PATH_TOOLS: dict[str, tuple[str, bool, str | None]] = {
    "read_file": ("path", False, None),
    "list_dir": ("path", False, "."),
    "glob": ("path", False, None),
    "grep": ("path", False, None),
    "repo_map": ("path", False, None),
    "write_file": ("path", True, None),
    "edit_file": ("path", True, None),
}

#: The pattern each searching tool takes, written relative to the folder it searches.
PATTERN_ARGS: dict[str, str] = {"glob": "pattern", "grep": "glob"}

#: Where the owner names the folders besides the workspace that the agent's file tools reach and a
#: subagent may work in.
ALLOWED_SETTING = "Settings › Agent defaults › Allowed working directories"
_REACH_HINT = (
    f"The user can add a folder in {ALLOWED_SETTING} (read and change), or add it as a "
    "knowledge source in Knowledge › Sources (read only)."
)


class OutOfScope(ValueError):
    """A path the file tools do not reach: the sentence saying why, and a hint saying what would."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class Place:
    """One folder the file tools reach: its real path, how it is named (as the owner wrote it,
    from ``~`` in the home), and its kind."""

    root: str
    shown: str
    kind: str
    name: str = ""
    spec: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def changes(self) -> bool:
        """Whether the file tools may change files here (a knowledge source and a folder the work
        only reads are read only)."""
        return self.kind in (WORKSPACE, ALLOWED)

    def takes(self, real: str) -> bool:
        """Whether a read may open *real*, a real path inside this place."""
        if self.kind != SOURCE:
            return True
        from personalclaw.knowledge_providers.dir_source import resolve_in, takes

        rel = resolve_in(self.root, real)
        return rel is not None and takes(self.spec, rel, is_dir=os.path.isdir(real))

    def patterns(self) -> str:
        """A knowledge source's file patterns, as a phrase (``*.md``)."""
        from personalclaw.knowledge_providers.dir_source import matchers

        return ", ".join(matchers(self.spec))


def _inside(real: str, root: str) -> bool:
    return real == root or real.startswith(root.rstrip(os.sep) + os.sep)


def _plain(text: str, limit: int = 80) -> str:
    """Owner-written text (a source's name, a folder as she typed it) fit for one line."""
    from personalclaw.security import redact_for_display

    return redact_for_display(" ".join(str(text).split()))[:limit]


def _allowed_places() -> list[Place]:
    """The allowed working directories, read now. Unreadable settings add none (fail closed)."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.file_roots import control_character_in, is_system_root

    try:
        named = list(AppConfig.load().agent.subagent_cwd_allowed_roots or [])
    except Exception:  # noqa: BLE001 - the file tools stay in the workspace
        logger.warning("file scope: the allowed working directories are unreadable", exc_info=True)
        return []
    places = []
    for entry in named:
        text = str(entry or "").strip()
        expanded = os.path.expanduser(text)
        if not text or control_character_in(text) or not os.path.isabs(expanded):
            continue
        real = os.path.realpath(expanded)
        if is_system_root(real):
            logger.warning("file scope: %r is a system folder, so it is not a place", text)
            continue
        places.append(Place(real, from_home(text), ALLOWED))
    return places


def _source_places() -> list[Place]:
    """The watched folders of the knowledge sources that are on, read now. A library that cannot
    be read adds none (fail closed)."""
    from personalclaw.file_roots import is_system_root
    from personalclaw.knowledge.store import knowledge_db_path
    from personalclaw.knowledge_providers.dir_source import DirSourceProvider
    from personalclaw.triggers.pathguard import canonicalize

    try:
        if not knowledge_db_path(create=False).is_file():
            return []
        from personalclaw.knowledge import get_knowledge_store

        rows = get_knowledge_store().list_sources(enabled_only=True)
    except Exception:  # noqa: BLE001 - no folder is shared
        logger.warning("file scope: the knowledge sources are unreadable", exc_info=True)
        return []
    checker = DirSourceProvider(None)
    places = []
    for row in rows:
        raw = row.get("spec")
        spec: dict = raw if isinstance(raw, dict) else {}
        if row.get("provider") != checker.name or not checker.validate_spec(spec)[0]:
            continue
        real = canonicalize(str(spec.get("path") or ""))
        if not real or is_system_root(real):
            continue
        shown = from_home(str(spec["path"]).strip())
        places.append(Place(real, shown, SOURCE, str(row.get("name") or ""), spec))
    return places


class FileScope:
    """Where one call's file tools reach, from the session's folders (the first is where a
    relative path starts) and the owner's settings as they stand now."""

    def __init__(
        self,
        session_roots: Iterable[str | os.PathLike],
        *,
        reads: Iterable[str | os.PathLike] = (),
    ) -> None:
        from personalclaw.config.loader import resolve_config_dir
        from personalclaw.file_roots import is_system_root

        session = [os.path.realpath(str(r)) for r in session_roots if str(r)]
        self.base = session[0] if session else ""
        places = [Place(root, from_home(root), WORKSPACE) for root in dict.fromkeys(session)]
        # The folders this session's work reads: never a system folder, as for an allowed one.
        read_only = [os.path.realpath(str(r)) for r in reads if str(r)]
        read_places = [Place(r, from_home(r), READS) for r in read_only if not is_system_root(r)]
        for extra in _allowed_places() + read_places + _source_places():
            if not any(p.root == extra.root and p.changes >= extra.changes for p in places):
                places.append(extra)
        self.places: tuple[Place, ...] = tuple(places)
        self._home = os.path.realpath(str(resolve_config_dir()))
        self._stores = own_stores(self._home)
        self._admissions: dict[bool, Any] = {}

    def own_store(self, real: str) -> Any:
        """The inventory entry of PersonalClaw's own store that *real* is part of, or ``None``."""
        return next((entry for path, entry in self._stores if _inside(real, path)), None)

    def _admission(self, change: bool):
        if change not in self._admissions:
            from personalclaw.file_roots import Admission

            roots = [p.root for p in self.places if p.changes or not change]
            self._admissions[change] = Admission(roots)
        return self._admissions[change]

    def _containing(self, real: str, change: bool) -> list[Place]:
        return [p for p in self.places if (p.changes or not change) and _inside(real, p.root)]

    def resolve(self, raw: str, *, change: bool = False) -> str:
        """The real path *raw* names, when the file tools may read it (with *change*, change it).

        Raises :class:`OutOfScope` with the reason otherwise. A relative path starts at the
        session's folder."""
        from personalclaw.file_roots import control_character_in, within

        bad = control_character_in(raw)
        if bad:
            raise OutOfScope(
                f"path {raw!r} has a control character ({bad}) in it, which no file name may "
                "hold; write the path without it"
            )
        named = os.path.expanduser(raw) if raw == "~" or raw.startswith("~/") else raw
        if not os.path.isabs(named) and not self.base:
            raise OutOfScope(f"path {raw!r} is relative, and this session has no folder")
        real = os.path.realpath(os.path.join(self.base, named))
        places = self._containing(real, change)
        if not places:
            raise self._outside(raw, real, change)
        if not within(real, [p.root for p in places], home=self._home):
            raise OutOfScope(
                f"path {raw!r} is inside PersonalClaw's own home, which this tool does not reach"
            )
        if self._admission(change)(real) is None:
            raise OutOfScope(
                f"path {raw!r} is a credential or secret file, which this tool does not reach"
            )
        store = self.own_store(real)
        if store is not None:
            raise OutOfScope(*_store_refusal(f"path {raw!r}", store))
        if not any(p.takes(real) for p in places):
            source = places[0]
            raise OutOfScope(
                f"path {raw!r} is in the knowledge source {_plain(source.name)!r} "
                f"({_plain(source.shown)}), which shares only its {source.patterns()} files "
                "outside hidden folders",
                "Open a file the source takes in, or find it with knowledge_search.",
            )
        return real

    def _outside(self, raw: str, real: str, change: bool) -> OutOfScope:
        if not change:
            return OutOfScope(
                f"path {raw!r} is outside every folder the file tools reach: the workspace, the "
                "allowed working directories and the folders added as knowledge sources",
                _REACH_HINT,
            )
        held = self._containing(real, False)
        read_only = next((p for p in held if p.kind == READS), None)
        if read_only is not None:
            return OutOfScope(
                f"path {raw!r} is in {_plain(read_only.shown)}, a folder this work reads and "
                "never changes",
                "Make the change in this session's own folder, or tell the user what to change.",
            )
        source = next((p for p in held if p.kind == SOURCE), None)
        if source is not None:
            return OutOfScope(
                f"path {raw!r} is in the knowledge source {_plain(source.name)!r} "
                f"({_plain(source.shown)}), which the file tools read and never change",
                f"To change files there, the user adds the folder in {ALLOWED_SETTING}.",
            )
        return OutOfScope(
            f"path {raw!r} is outside every folder the file tools may change: the workspace and "
            "the allowed working directories",
            f"To change files in another folder, the user adds it in {ALLOWED_SETTING}.",
        )

    def admits(self, raw: str) -> str | None:
        """The real path *raw* names when a read may open it, else ``None``: what a listing, a
        search or a map asks of every entry, so it leaves out what the tools could not open."""
        real = self._admission(False)(raw)
        if real is None:
            return None
        if self.own_store(real) is not None:
            return None
        places = self._containing(real, False)
        return real if any(p.takes(real) for p in places) else None

    def root_of(self, real: str) -> str:
        """The folder a change to *real* is admitted through: the first place it may change it
        in, ``""`` for none."""
        return next((p.root for p in self._containing(real, True)), "")

    def shown(self, path: str | os.PathLike) -> str:
        """*path* as a result names it: relative to the session's folder inside it, else whole,
        from ``~`` in the home, so the next call can open it as written."""
        text = str(path)
        if self.base and _inside(text, self.base) and text != self.base:
            return os.path.relpath(text, self.base)
        return from_home(text)


#: The files SQLite keeps beside a database, which hold its pages too.
_SQLITE_SIBLINGS = ("", "-wal", "-shm", "-journal")


def own_stores(home: str) -> list[tuple[str, Any]]:
    """PersonalClaw's own stores inside the agent workspace in *home*, as ``(real path, inventory
    entry)``: every entry the state inventory declares inside its ``workspace`` entry, a database
    with the files SQLite keeps beside it."""
    from personalclaw.durability.inventory import KIND_SQLITE, all_entries, by_id

    workspace = by_id("workspace")
    if workspace is None:
        return []
    stores = []
    for entry in all_entries():
        if not entry.path.startswith(workspace.path + "/"):
            continue
        suffixes = _SQLITE_SIBLINGS if entry.kind == KIND_SQLITE else ("",)
        for suffix in suffixes:
            stores.append((os.path.realpath(os.path.join(home, entry.path + suffix)), entry))
    return stores


def _store_refusal(subject: str, entry: Any) -> tuple[str, str]:
    """The sentence and the hint for a call whose *subject* is part of PersonalClaw's own store
    *entry*."""
    from personalclaw.durability.inventory import DOMAIN_KNOWLEDGE

    hint = (
        "Search the knowledge library with knowledge_search, and open an item with knowledge_get."
        if entry.domain == DOMAIN_KNOWLEDGE
        else "Use the tool made for it, or tell the user what you need from it."
    )
    return (
        f"{subject} is part of PersonalClaw's own {entry.domain} store ({entry.help}), which "
        "only its own tools read and change",
        hint,
    )


def store_named_in(command: str, *, cwd: str | os.PathLike | None = None) -> str:
    """Why a shell *command* that names one of PersonalClaw's own stores is refused, or ``""``.

    Every path the command names, read as its shell would find it (``command_paths.named_paths``:
    ``~`` written out, a relative one against *cwd* and each folder a ``cd`` moves to, the
    workspace in the home when None), is checked against :func:`own_stores`. Defence in depth,
    like ``owner_only.named_in``: a command can build a path no reading of its text sees."""
    from personalclaw.command_paths import named_paths
    from personalclaw.config.loader import resolve_config_dir
    from personalclaw.durability.inventory import KIND_SQLITE

    home = os.path.realpath(str(resolve_config_dir()))
    stores = own_stores(home)
    # A database's own file names count bare (`grep -a x knowledge.db`); a folder needs a path.
    names = {os.path.basename(path) for path, entry in stores if entry.kind == KIND_SQLITE}
    for word, path in named_paths(command, cwd=cwd, home=home, names=names):
        real = os.path.realpath(path)
        entry = next((e for root, e in stores if _inside(real, root)), None)
        if entry is not None:
            sentence, hint = _store_refusal(repr(word), entry)
            return f"Blocked: {sentence}. {hint}"
    return ""


def pattern_refusal(arg: str, pattern: str) -> OutOfScope | None:
    """Why a ``glob``/``grep`` pattern is refused as a whole, or ``None``: an absolute or ``~``
    pattern, or one with a ``..`` segment, names a place by itself rather than files inside the
    folder searched. Each match is checked as well; this is the refusal that says why, instead of
    an empty result."""
    parts = pattern.replace("\\", "/").split("/")
    if not (pattern.startswith(("/", "~", "\\")) or Path(pattern).is_absolute() or ".." in parts):
        return None
    return OutOfScope(
        f"{arg} {pattern!r} leaves the folder searched: a pattern is relative to it (the "
        "workspace, or the folder given as `path`) and cannot climb out of it (no absolute "
        "path, `~` or `..`)",
        f"Write {arg} relative to the folder searched, e.g. '**/*.md', and give the folder as "
        "`path`.",
    )


def refusal(
    tool_name: str,
    arguments: Any,
    *,
    cwd: str | os.PathLike | None,
    extra_roots: Iterable[str | os.PathLike] = (),
    reads: Iterable[str | os.PathLike] = (),
) -> OutOfScope | None:
    """Why a native file tool's call is refused (its sentence, and the hint the tool gives with
    it), or ``None`` when it is in scope.

    Decided from the call's arguments, the session's folder (*cwd*, the *extra_roots* a worker is
    given and the folders its work *reads*) and the owner's settings alone, so a caller can ask it
    before an approval is asked for: a call this refuses would be refused by the tool after any
    approval. ``None`` as well for a tool that names no path here, for a missing required argument
    (the tool's own error says so) and for a session with no folder (the tool decides)."""
    shape = PATH_TOOLS.get(tool_name)
    if shape is None or not cwd or not isinstance(arguments, dict):
        return None
    arg, change, default = shape
    pattern_arg = PATTERN_ARGS.get(tool_name)
    if pattern_arg and (
        leaves := pattern_refusal(pattern_arg, str(arguments.get(pattern_arg) or ""))
    ):
        return leaves
    raw = arguments.get(arg)
    if raw in (None, ""):
        if default is None:
            return None
        raw = default
    try:
        FileScope([cwd, *extra_roots], reads=reads).resolve(str(raw), change=change)
    except OutOfScope as refused:
        return refused
    return None


def file_places_note(tool_index: Mapping[str, Any]) -> str:
    """The note a native turn carries about where its file tools reach (:func:`places_note`), from
    the platform provider serving ``read_file`` in *tool_index*, or ``""`` for a session with no
    file tools."""
    file_scope = getattr(tool_index.get("read_file"), "file_scope", None)
    if not callable(file_scope):
        return ""
    return places_note(
        file_scope(),
        knowledge="knowledge_search" in tool_index,
        calendars="calendar_events" in tool_index,
    )


#: The calendars the note names at most; the tool reads every one.
_CALENDARS_NAMED = 8


def places_note(scope: FileScope, *, knowledge: bool = False, calendars: bool = False) -> str:
    """What the model is told, each turn, about the places beyond the workspace: ``""`` when
    there are none. *knowledge*: the session has the knowledge tools, which then come first, since
    the library indexes every knowledge source's notes. *calendars*: it has ``calendar_events``,
    so the calendar files in these places are named, by name and kind, with the tool that reads
    them: a question about plans otherwise searched the notes and missed the calendar."""
    beyond = [p for p in scope.places if p.kind != WORKSPACE]
    if not beyond:
        return ""
    sources = [p for p in beyond if not p.changes]
    lines = ["[file places] The user's own files beyond the workspace."]
    if knowledge and sources:
        lines.append(
            "Their notes are in the knowledge library: to find one by what it says, call "
            "knowledge_search first, and knowledge_get returns a note's full text and its path."
        )
    if calendars:
        from personalclaw import calendar_files

        # Fails open: the note is built for every model request, and a search that could not run
        # leaves the calendars unnamed for this turn rather than costing the turn.
        try:
            found = calendar_files.find(scope)
        except Exception:  # noqa: BLE001 - the folders are still named below
            logger.warning("file places: the calendars could not be looked for", exc_info=True)
            found = []
        if found:
            lines.append(
                "Their calendars are files in the folders below: for a question about their plans "
                "or schedule (what is on a day, when or where something is), call calendar_events, "
                "which reads them, repeating events included, in the user's time zone."
            )
            for ref in found[:_CALENDARS_NAMED]:
                lines.append(f"- {_plain(ref.name)!r}: {calendar_files.KIND}, {_plain(ref.shown)}")
            if len(found) > _CALENDARS_NAMED:
                lines.append(f"- and {len(found) - _CALENDARS_NAMED} more calendar files")
    lines.append(
        "read_file, list_dir, glob and grep reach these folders with no approval: name a file by "
        "its absolute or ~ path, and give glob or grep the folder as `path`."
    )
    for place in beyond:
        if place.changes:
            lines.append(
                f"- {_plain(place.shown)}: an allowed working directory. write_file and "
                "edit_file change files here too, with the same approval as in the workspace."
            )
        elif place.kind == READS:
            lines.append(f"- {_plain(place.shown)}: a folder this work reads, read only.")
        else:
            lines.append(
                f"- {_plain(place.shown)}: the knowledge source {_plain(place.name)!r}, read "
                f"only: its {place.patterns()} files."
            )
    lines.append(
        "Read and search the user's files with these tools rather than bash: a shell command may "
        "wait for the user's approval, and PersonalClaw's own databases are not files to read."
    )
    return "\n".join(lines)
