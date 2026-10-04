"""Where the agent's file tools reach: one scope, read again at every call.

The native file tools (``read_file``, ``list_dir``, ``glob``, ``grep``, ``repo_map``,
``write_file``, ``edit_file``) and ``code_map`` work in five kinds of place:

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
* **the skills library** in the home, where every skill the owner installs or imports lands. Read
  only, and only each installed skill's own folder: a folder below the library's top that holds a
  ``SKILL.md`` (as the loader finds a skill), once links are resolved, and nothing hidden, so the
  library's own records and an install's lock file stay out. A skill's ``SKILL.md`` is not among
  its files here: ``skill_invoke`` loads its instructions, the one place a body is read
  (``SkillsLoader.load_skill``: accepted refinements applied, the use counted). So a skill that
  names a file of its own ("use template.md") is followed as written, with no approval.

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
documents, the lexicon, and each working folder's memory database and learning log. Each is
declared in the state inventory (``durability.inventory``: every entry inside its ``workspace``
entry, and every partition an entry declares there, one per working folder), and each is read and
changed only through its own tools: a raw read hands the model pages of a database, past the
masking its tools apply, and a write breaks the store. So the file tools refuse them and their
listings leave them out (:meth:`FileScope.own_store`), and a shell command that names one is
refused before it is approved (:func:`store_named_in`), with the tool to use instead.

**Long-term memory is read and changed only by work that may.** The memory folders in the
workspace (the home's preferences.md, projects.md and daily history, and every working folder's
memory: ``memory.memory_folders``) are files the tools read and, with approval, change, and a
change to a memory document is its store's own write (``memory.write_document``). Work that may
change none of your memory (an Incognito or Temporary chat's, an app's not given your memory, a
turn someone other than you asked for) changes nothing there: the file tools refuse it with the
memory-write refusal, before anyone is asked (:func:`memory_kept_from_work`), and so does the shell
for a command that names a path there and does more than read it (:func:`memory_named_in`), whose
sandbox keeps the folders read-only. Work that may read none of your memory, a Temporary chat's or
an app's not given your memory, reads nothing there either: the file tools refuse a path there in
the words every memory read is refused in (``memory_reads.memory_read_refusal``) and leave the
folders out of their listings, the shell refuses a command that names a path there, and its sandbox
keeps the folders unreadable. The gate an agent CLI's own file and shell tools ask before a call
runs holds both, whatever would approve the call (:func:`memory_named_by_call`).

Inside every place the checks the Files view makes still hold (``file_roots.Admission``):
symlinks and ``..`` are resolved first, so a link or a climb out of a place reaches nothing it
does not already reach; no credential location or secret file; and nothing in PersonalClaw's own
home except through a place inside it (``file_roots.within``): a place that contains the home, a
worker's ``~``, says nothing about a path in it, so what the skills library shares is all a session
working there reads of the library. A path that starts with ``~/``
names the owner's home, as the owner writes it; ``~name`` stays a plain name. A path the tools show
in the home is written from ``~`` (:mod:`personalclaw.home_paths`), and either form opens it.
"""

from __future__ import annotations

import fnmatch
import json
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
SKILLS = "skills"

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
    """A path the file tools do not reach: the sentence saying why, a hint saying what would, and
    the control that refused it, as the call's audit row names it: ``file_scope``, the code of the
    memory-write refusal for a change to long-term memory (:func:`memory_kept_from_work`), or
    :data:`MEMORY_WITHHELD` for a read of it that the work may not make."""

    def __init__(self, message: str, hint: str = "", *, control: str = "file_scope") -> None:
        super().__init__(message)
        self.hint = hint
        self.control = control


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
        """Whether the file tools may change files here (a knowledge source, a folder the work
        only reads and the skills library are read only)."""
        return self.kind in (WORKSPACE, ALLOWED)

    def takes(self, real: str) -> bool:
        """Whether a read may open *real*, a real path inside this place."""
        if self.kind == SKILLS:
            return bool(_skill_of(self.root, real)) and not _is_instructions(real)
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


def _skill_of(library: str, real: str) -> str:
    """The installed skill whose own folder holds *real*, a real path inside the skills *library*,
    by its name (``imported/claude_code/incident-writeup``): the nearest folder above it, itself
    for a folder, that holds a ``SKILL.md`` and is not the library's top, as the loader finds a
    skill (``skills.loader.iter_skill_files``). ``""`` for none, and for a path with a hidden part
    inside the library: the library's own records (use counts, refinements, proposals, drafts) and
    an install's lock file are PersonalClaw's, not the skill's."""
    rel = os.path.relpath(real, library)
    if any(part.startswith(".") for part in rel.split(os.sep)):  # the library itself is "."
        return ""
    folder = real if os.path.isdir(real) else os.path.dirname(real)
    while folder != library and _inside(folder, library):
        if os.path.isfile(os.path.join(folder, "SKILL.md")):
            return os.path.relpath(folder, library).replace(os.sep, "/")
        folder = os.path.dirname(folder)
    return ""


def _is_instructions(real: str) -> bool:
    """Whether *real* is a skill's ``SKILL.md``, which ``skill_invoke`` loads."""
    from personalclaw.skills.loader import is_instructions_file

    return is_instructions_file(os.path.basename(real))


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


def _library_places(home: str) -> list[Place]:
    """The skills library in *home*, as a place when it exists; what it shares is each installed
    skill's own folder (:meth:`Place.takes`). A library that is a link to the filesystem root or a
    system folder is no place."""
    from personalclaw.file_roots import is_system_root
    from personalclaw.skills.loader import SKILLS_DIR_NAME

    library = os.path.realpath(os.path.join(home, SKILLS_DIR_NAME))
    if not os.path.isdir(library) or is_system_root(library):
        return []
    return [Place(library, from_home(library), SKILLS)]


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

        self._home = os.path.realpath(str(resolve_config_dir()))
        session = [os.path.realpath(str(r)) for r in session_roots if str(r)]
        self.base = session[0] if session else ""
        places = [Place(root, from_home(root), WORKSPACE) for root in dict.fromkeys(session)]
        # The folders this session's work reads: never a system folder, as for an allowed one.
        read_only = [os.path.realpath(str(r)) for r in reads if str(r)]
        read_places = [Place(r, from_home(r), READS) for r in read_only if not is_system_root(r)]
        extras = _allowed_places() + read_places + _source_places() + _library_places(self._home)
        for extra in extras:
            if not any(p.root == extra.root and p.changes >= extra.changes for p in places):
                places.append(extra)
        self.places: tuple[Place, ...] = tuple(places)
        self._stores = own_stores(self._home)
        self._admissions: dict[bool, Any] = {}
        from personalclaw import memory

        self._memory = tuple(os.path.realpath(folder) for folder in memory.memory_folders())
        # Why this call's work may read none of your memory, asked once, when a path in the
        # memory folders is first met (most calls never meet one).
        self._withheld: str | None = None

    def own_store(self, real: str) -> Any:
        """The inventory entry of PersonalClaw's own store that *real* is part of, or ``None``."""
        return next((store.entry for store in self._stores if store.holds(real)), None)

    def memory_withheld(self, real: str) -> str:
        """Why *real*, a real path in the memory folders, is not read for the work this call is
        made for (``memory_reads.memory_read_refusal``: a Temporary chat's, an app's not given your
        memory); ``""`` when the work may read it, and for a path outside the memory folders."""
        if not any(_inside(real, folder) for folder in self._memory):
            return ""
        if self._withheld is None:
            from personalclaw import memory_reads

            self._withheld = memory_reads.memory_read_refusal()
        return self._withheld

    def _admission(self, change: bool):
        if change not in self._admissions:
            from personalclaw.file_roots import Admission

            roots = [p.root for p in self.places if p.changes or not change]
            self._admissions[change] = Admission(roots)
        return self._admissions[change]

    def _containing(self, real: str, change: bool) -> list[Place]:
        return [p for p in self.places if (p.changes or not change) and _inside(real, p.root)]

    def _reaching(self, real: str, places: list[Place]) -> list[Place]:
        """Those of *places* that reach *real*: for a path inside the home, only the places inside
        it too (``file_roots.within``). A place that contains the home answers nothing for a path
        in it, neither whether it may be read nor what of it is shared."""
        from personalclaw.file_roots import within

        return [p for p in places if within(real, [p.root], home=self._home)]

    def resolve(self, raw: str, *, change: bool = False) -> str:
        """The real path *raw* names, when the file tools may read it (with *change*, change it).

        Raises :class:`OutOfScope` with the reason otherwise. A relative path starts at the
        session's folder."""
        from personalclaw.file_roots import control_character_in

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
        places = self._reaching(real, places)
        if not places:
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
        if change and (kept := memory_kept_from_work(f"path {raw!r}", real)) is not None:
            raise kept
        if not change and (why := self.memory_withheld(real)):
            raise _memory_withheld(f"path {raw!r}", why)
        if not any(p.takes(real) for p in places):
            raise _not_shared(raw, real, places[0])
        return real

    def _outside(self, raw: str, real: str, change: bool) -> OutOfScope:
        if not change:
            return OutOfScope(
                f"path {raw!r} is outside every folder the file tools reach: the workspace, the "
                "allowed working directories, the folders added as knowledge sources and each "
                "installed skill's own folder",
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
        library = next((p for p in held if p.kind == SKILLS), None)
        if library is not None:
            return OutOfScope(
                f"path {raw!r} is in the skills library ({_plain(library.shown)}), which the file "
                "tools read and never change",
                "To change a skill, tell the user what to change; skill_promote proposes a new "
                "one for them to accept.",
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
        if self.own_store(real) is not None or self.memory_withheld(real):
            return None
        places = self._reaching(real, self._containing(real, False))
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


@dataclass(frozen=True)
class OwnStore:
    """One of PersonalClaw's own stores in the workspace (:func:`own_stores`): the real folder or
    file its path names up to its first wildcard, the parts of the path below that (``*`` standing
    for any one folder, as a partition's ``_ext/*/learning.db`` names each working folder's), and
    its inventory entry."""

    root: str
    rest: tuple[str, ...]
    entry: Any

    @property
    def name(self) -> str:
        """The last part of its path, the file name a database is opened by."""
        return self.rest[-1] if self.rest else os.path.basename(self.root)

    def holds(self, real: str) -> bool:
        """Whether *real*, a real path, is this store or inside it."""
        if not _inside(real, self.root):
            return False
        parts = os.path.relpath(real, self.root).split(os.sep) if real != self.root else []
        return len(parts) >= len(self.rest) and all(
            fnmatch.fnmatchcase(part, want) for part, want in zip(parts, self.rest)
        )


def own_stores(home: str) -> list[OwnStore]:
    """PersonalClaw's own stores inside the agent workspace in *home*: every path the state
    inventory declares inside its ``workspace`` entry, an entry's own and each of its partitions
    (``StateEntry.partitions``: the memory database and the learning log every working folder
    keeps), a database with the files SQLite keeps beside it."""
    from personalclaw.command_paths import is_glob
    from personalclaw.durability.inventory import KIND_SQLITE, all_entries, by_id

    workspace = by_id("workspace")
    if workspace is None:
        return []
    stores = []
    for entry in all_entries():
        suffixes = _SQLITE_SIBLINGS if entry.kind == KIND_SQLITE else ("",)
        for path in (entry.path, *entry.partitions):
            if not path.startswith(workspace.path + "/"):
                continue
            parts = path.split("/")
            # Up to its first wildcard the path names a real place; the rest is matched by part.
            fixed = next((i for i, part in enumerate(parts) if is_glob(part)), len(parts))
            for suffix in suffixes:
                named = [*parts[:-1], parts[-1] + suffix]
                root = os.path.realpath(os.path.join(home, *named[:fixed]))
                stores.append(OwnStore(root, tuple(named[fixed:]), entry))
    return stores


def _store_refusal(subject: str, entry: Any) -> tuple[str, str]:
    """The sentence and the hint for a call whose *subject* is part of PersonalClaw's own store
    *entry*."""
    from personalclaw.durability.inventory import DOMAIN_KNOWLEDGE, DOMAIN_MEMORY

    if entry.domain == DOMAIN_KNOWLEDGE:
        hint = (
            "Search the knowledge library with knowledge_search, and open an item with "
            "knowledge_get."
        )
    elif entry.domain == DOMAIN_MEMORY:
        hint = "Recall what memory holds with memory_recall."
    else:
        hint = "Use the tool made for it, or tell the user what you need from it."
    return (
        f"{subject} is part of PersonalClaw's own {entry.domain} store ({entry.help}), which "
        "only its own tools read and change",
        hint,
    )


def _not_shared(raw: str, real: str, place: Place) -> OutOfScope:
    """Why a read of *real*, inside *place*, is refused by what the place shares
    (:meth:`Place.takes`): a skill's instructions, the skills library beside a skill's own
    folder, or a file a knowledge source does not take in."""
    if place.kind == SKILLS:
        skill = _skill_of(place.root, real)
        if skill and _is_instructions(real):
            name = _plain(skill, 200)
            return OutOfScope(
                f"path {raw!r} holds the instructions of the skill {name!r}, which skill_invoke "
                "loads",
                f"Load them with skill_invoke(name={name!r}); read_file opens the files the skill "
                "keeps beside them.",
            )
        return OutOfScope(
            f"path {raw!r} is in the skills library ({_plain(place.shown)}), where the file "
            "tools reach only the files each installed skill ships in its own folder",
            "Find a skill with skill_search; skill_invoke loads it and names the folder its files "
            "are in.",
        )
    return OutOfScope(
        f"path {raw!r} is in the knowledge source {_plain(place.name)!r} "
        f"({_plain(place.shown)}), which shares only its {place.patterns()} files "
        "outside hidden folders",
        "Open a file the source takes in, or find it with knowledge_search.",
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
    names = {store.name for store in stores if store.entry.kind == KIND_SQLITE}
    for word, path in named_paths(command, cwd=cwd, home=home, names=names):
        real = os.path.realpath(path)
        entry = next((store.entry for store in stores if store.holds(real)), None)
        if entry is not None:
            sentence, hint = _store_refusal(repr(word), entry)
            return f"Blocked: {sentence}. {hint}"
    return ""


#: What the agent is told to do about a change to long-term memory its work may not make.
_MEMORY_KEPT_HINT = (
    "Nothing was written. Tell the user it was not saved, and why; do not try to save it "
    "another way."
)


def memory_kept_from_work(subject: str, real: str | os.PathLike[str]) -> OutOfScope | None:
    """Why the current work may not change *real*, the path *subject* names, or ``None``.

    *real* is in long-term memory (``memory.in_memory_folders``: the home's memory folder, or a
    working folder's memory) and the work may change none of it (``memory_writes``: an Incognito
    or Temporary chat's, an app's not given your memory, a turn someone other than you asked for).
    Refused in the memory-write refusal's own sentence and under its code, so the call is recorded
    as that refusal."""
    from personalclaw import memory

    return _memory_kept(subject) if memory.in_memory_folders(real) else None


def _memory_kept(subject: str) -> OutOfScope | None:
    """The refusal a change to long-term memory, which *subject* names, gets in the current work,
    or ``None`` when the work may make it."""
    from personalclaw import memory_writes

    refused = memory_writes.memory_write_refusal()
    if refused is None:
        return None
    return OutOfScope(
        f"{subject} is part of long-term memory. {refused.reason}",
        _MEMORY_KEPT_HINT,
        control=refused.code,
    )


#: The control a read of long-term memory the work may not make is refused by, as the call's audit
#: row names it.
MEMORY_WITHHELD = "memory_withheld"

#: What the agent is told to do about long-term memory its work may not read.
_MEMORY_WITHHELD_HINT = (
    "Nothing was read from it. Answer without it, and do not try to read it another way."
)


def _memory_withheld(subject: str, why: str) -> OutOfScope:
    """The refusal of a read of long-term memory, which *subject* names, in work that may read none
    of it, *why* being the reason the memory read path gives (``memory_reads.Reach.refusal``)."""
    return OutOfScope(
        f"{subject} is part of long-term memory. {why}",
        _MEMORY_WITHHELD_HINT,
        control=MEMORY_WITHHELD,
    )


def held_from_reads(raw: str) -> OutOfScope | None:
    """Why a read of the file *raw* names is refused for what the file is, wherever it is: part
    of one of PersonalClaw's own stores (:func:`own_stores`), or of long-term memory in work that
    may read none of it (``memory_reads.memory_read_refusal``). ``None`` when neither holds.

    For a tool that reads a file the agent names past the file tools (an artifact's
    ``content_file``): whatever else that tool allows, it hands the model nothing the file tools
    hold back for what it is."""
    from personalclaw import memory, memory_reads
    from personalclaw.config.loader import resolve_config_dir

    real = os.path.realpath(os.path.expanduser(raw))
    home = os.path.realpath(str(resolve_config_dir()))
    entry = next((store.entry for store in own_stores(home) if store.holds(real)), None)
    if entry is not None:
        return OutOfScope(*_store_refusal(f"path {raw!r}", entry))
    if memory.in_memory_folders(real) and (why := memory_reads.memory_read_refusal()):
        return _memory_withheld(f"path {raw!r}", why)
    return None


def memory_named_in(
    command: str, *, cwd: str | os.PathLike[str] | None = None
) -> OutOfScope | None:
    """Why a shell *command* that names a path in long-term memory is refused, or ``None``: one
    that does more than read it (``task_modes.is_read_only_bash``) in work that may change none of
    it (:func:`memory_kept_from_work`), and any one in work that may read none of it
    (``memory_reads.memory_read_refusal``), decided by the one screen of long-term memory
    (:func:`memory_screen`). A command that only reads runs where the work reads your memory: an
    Incognito chat reads its memory.

    Every path the command names is read as its shell would find it (``command_paths.named_paths``,
    from *cwd*). Defence in depth, which says why before anyone is asked: the sandbox keeps the
    memory folders read-only to such work's commands, and unreadable to the commands of work that
    may read none of them, whatever their text says (``sandbox``)."""
    from personalclaw.command_paths import named_paths
    from personalclaw.task_modes import is_read_only_bash

    named = ((word, str(path)) for word, path in named_paths(command, cwd=cwd))
    screened = memory_screen(named, reads=is_read_only_bash(command))
    return screened[1] if screened is not None else None


def memory_screen(
    named: Iterable[tuple[str, str]], *, reads: bool
) -> tuple[str, OutOfScope] | None:
    """The one screen of long-term memory for a call or a command that names paths: the first of
    *named* (each the word that names a path, and the path) in the memory folders, and why the call
    is refused. One that does more than read it (*reads* false) is refused in work that may change
    none of it (:func:`memory_kept_from_work`), and any one in work that may read none of it
    (``memory_reads.memory_read_refusal``). ``None`` when the work may do what the call does
    there, or it names no path there. The work is asked only once a path there is named: most
    calls name none.

    The shell's (:func:`memory_named_in`) and the gate an agent CLI's own tools ask
    (:func:`memory_named_by_call`)."""
    from personalclaw import memory, memory_reads

    for word, path in named:
        if not memory.in_memory_folders(path):
            continue
        subject = f"Blocked: {word!r}"
        if not reads and (kept := _memory_kept(subject)) is not None:
            return word, kept
        why = memory_reads.memory_read_refusal()
        return (word, _memory_withheld(subject, why)) if why else None
    return None


#: The keys a call's input names the file it works on by: PersonalClaw's own file tools' ``path``,
#: and those the file tools an agent CLI brings use (``file_path``, ``notebook_path``, …).
_FILE_KEYS = (
    "path",
    "file_path",
    "filePath",
    "notebook_path",
    "target_file",
    "file",
    "filename",
    "destination",
    "new_path",
)


def memory_named_by_call(
    title: str, tool_input: Any, command: str, *, cwd: str | os.PathLike[str] | None = None
) -> tuple[str, OutOfScope] | None:
    """The path a call put to an approval gate names in long-term memory and why the call is
    refused (:func:`memory_screen`), or ``None``: a change there in work that may change none of
    your memory, and a read too in work that may read none of it.

    An agent CLI brings file and shell tools of its own, which PersonalClaw's file tools' checks
    never see: the gate the CLI asks before a call runs (``acp.permission_authority.
    screen_tool_call``) has only the call's title and input. A call that runs a command (*command*,
    or a title in the ``Running: `` form) is read as the shell's is (:func:`memory_named_in`); any
    other is read for the paths its title names, its own description of what it does. Both are read
    for the paths under the keys the input names a file by (:data:`_FILE_KEYS`), from *cwd* (the
    workspace in the home when ``None``). A call reads only when its command only reads, or when
    its title is the form the gate takes a read in (``Reading ``, as the deny-list reads one).
    Deny-only, so reading more can only refuse more.

    A call to one of PersonalClaw's own tools (``acp.mcp_servers.names_core_tool``) is left to
    that tool: it changes memory only through the stores, which refuse the change for such work,
    and it holds back from what it reads what the file tools hold back (:func:`held_from_reads`)."""
    from personalclaw.acp.mcp_servers import names_core_tool
    from personalclaw.command_paths import named_paths
    from personalclaw.task_modes import is_read_only_bash

    if names_core_tool(title, tool_input):
        return None
    shown = str(title or "")
    run = command or (shown.removeprefix("Running: ") if shown.startswith("Running: ") else "")
    named = [(word, str(path)) for word, path in named_paths(run or shown, cwd=cwd)]
    named.extend(_files_in_input(tool_input, cwd=cwd))
    return memory_screen(
        named, reads=is_read_only_bash(run) if run else shown.startswith("Reading ")
    )


def _files_in_input(
    tool_input: Any, *, cwd: str | os.PathLike[str] | None
) -> list[tuple[str, str]]:
    """The files a call's input names under :data:`_FILE_KEYS`, each as written and as the path it
    names: a relative one from *cwd*, the workspace in the home when ``None``. The input is the
    parsed arguments, or their JSON text, as an agent CLI's permission request carries them."""
    args = tool_input
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return []
    if not isinstance(args, Mapping):
        return []
    from personalclaw.config.loader import memory_root

    base = Path(cwd) if cwd is not None else memory_root()
    named: list[tuple[str, str]] = []
    for key in _FILE_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(os.path.expanduser(value.strip()))
            named.append((value, str(path if path.is_absolute() else base / path)))
    return named


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
    them: a question about plans otherwise searched the notes and missed the calendar. The skills
    library is left out: it is in every home, so naming it would cost every turn, and a skill's
    folder is named where the skill is loaded (``skill_invoke``)."""
    beyond = [p for p in scope.places if p.kind not in (WORKSPACE, SKILLS)]
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
