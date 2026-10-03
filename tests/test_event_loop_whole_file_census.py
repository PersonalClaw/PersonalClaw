"""Nothing new on the event loop copies, moves or hashes a whole file.

Measured: completing a resumable upload copied every part into one file and hashed it on the
event loop, and a knowledge upload sent in one request was moved and hashed there too. On the
loop, assembling a 512 MB upload stopped every other request for up to 0.34 s and hashing it for
0.22 s, and a 2 GB one for as long as copying 2 GB takes; the gateway answered nothing meanwhile.
Work like that runs in a worker thread (a file's reads and writes and a hash leave the interpreter
lock), or in a child process when it holds the lock (the upload content scan).

Derived from the source, as the model-wait rail is
(``test_nothing_waits_on_a_model_on_the_event_loop.py``): every ``async def`` in the package is
read, and the census finds the whole-file work in it:

* a whole-file call of the standard library: ``shutil.copy``, ``copy2``, ``copyfile``,
  ``copyfileobj``, ``copytree`` and ``move`` (between disks a move is a copy), and
  ``hashlib.file_digest``;
* a loop that reads a file a chunk at a time and writes or hashes each chunk;
* a hash of a whole read (``hashlib.sha256(path.read_bytes())``);
* a loop that writes a request's body to a file as it arrives, with a plain ``write`` (on a busy
  disk one such write held the loop for 0.1 s: an upload's bytes go through
  ``uploads.spool.Spool``);
* a call to one of the package's own functions that does any of the first three, found the same
  way and followed through the functions that call it (resolved through imports, ``self`` and the
  method names the package defines once).

Each must sit inside the arguments of ``asyncio.to_thread`` / ``run_in_executor``, or in a function
handed to one of them. :data:`STILL_ON_THE_LOOP` names, with what the work is, the ones that are
still on the loop; a site not named reds here, and so does a named one that is gone, so the list
only shrinks. Not followed: the shared atomic writer (``atomic_write.py``), which writes what its
caller already holds in memory, and copies only when a rename is refused.

What the census cannot see: a call it cannot resolve (a method name the package defines twice, a
function passed around as a value) and whole-file work spelled another way.
"""

from __future__ import annotations

import ast
import collections
import functools
import textwrap
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: Whole-file calls of the standard library, by module and function.
_STDLIB = frozenset(
    {("shutil", name) for name in ("copy", "copy2", "copyfile", "copyfileobj", "copytree", "move")}
    | {("hashlib", "file_digest")}
)
_HAND_OFF = frozenset({"to_thread", "run_in_executor"})
_READS = frozenset({"read", "readinto", "read1", "read_chunk"})
_SINKS = frozenset({"write", "update"})
#: The shared writer's module, not followed (see the module docstring).
_NOT_FOLLOWED = "personalclaw.atomic_write."

COPY_LOOP = "a chunked copy or hash"
STREAM_LOOP = "a request body written as it arrives"

_VOICE_TAKE = "copies one spoken take of a voice profile (synthesized speech)"
_PACK_FILES = "hashes the files of a pack (its skills, workflows and agents)"
_SECOND_OPINION = "hashes the workspace files a second opinion claims to have changed"
_WORKSPACE_BASELINE = (
    "hashes the workspace's files (each under 8 MB, at most 20,000) before a second opinion"
)

#: Async functions that still do whole-file work on the event loop, by (file under
#: ``src/personalclaw``, function, the call), with what the work is. It only ever shrinks.
STILL_ON_THE_LOOP: dict[tuple[str, str, str], str] = {
    # Start-up, before the gateway serves anything.
    ("cli_server.py", "_gateway", "_boot_config"): "copies config.json aside before migrating it",
    ("gateway.py", "GatewayOrchestrator.run", "_init_services"): "archives retired workflow files",
    ("dashboard/server.py", "start_dashboard", "load_all_extensions"): "starts the installed apps",
    # Evals, each run in a process of its own and never by the gateway.
    ("evals/child.py", "_run", "seed_fixture_home"): "seeds an eval cell's own fixture home",
    ("evals/bakeoff.py", "run_bakeoff", "compute_pin_for_subject"): (
        "hashes the prompt pack an eval run is pinned to"
    ),
    ("evals/judge_bench.py", "run_judge_bench", "compute_pin_for_subject"): (
        "hashes the prompt pack an eval run is pinned to"
    ),
    ("evals/studies.py", "run_study", "persist"): "hashes the prompts a study is pinned to",
    # The agent's own file tools and the work they leave.
    ("agents/native/builtin_tools.py", "NativeBuiltinToolProvider.invoke", "_read_gate_refusal"): (
        "hashes the file an agent's write would replace, to refuse a write over an unread version"
    ),
    (
        "agents/native/builtin_tools.py",
        "NativeBuiltinToolProvider.invoke",
        "_read_gate_observe_write",
    ): "hashes the file an agent's write replaced, to record the version it wrote",
    (
        "agents/native/builtin_tools.py",
        "NativeBuiltinToolProvider.preflight",
        "_read_gate_refusal",
    ): "hashes the file an agent's write would replace, to refuse a write over an unread version",
    ("dashboard/chat_file_rewind.py", "api_chat_session_rewind", "resume_incomplete_rewind"): (
        "restores the files of a rewind that stopped part-way"
    ),
    ("dashboard/chat_file_rewind.py", "api_chat_session_rewind", "preview_rewind"): (
        "hashes the files a chat's later turns changed"
    ),
    ("dashboard/chat_file_rewind.py", "api_chat_session_rewind", "apply_rewind"): (
        "hashes and restores the files a chat's later turns changed"
    ),
    (
        "dashboard/chat_file_rewind.py",
        "api_chat_session_rewind_preview",
        "resume_incomplete_rewind",
    ): "restores the files of a rewind that stopped part-way",
    ("dashboard/chat_file_rewind.py", "api_chat_session_rewind_preview", "preview_rewind"): (
        "hashes the files a chat's later turns changed, to say what a rewind restores"
    ),
    ("planning/runner.py", "run_planner_pass", "reclaim_misplaced"): (
        "moves a walkthrough file the planner wrote into the workspace to the loop's folder"
    ),
    ("workflows/provisioning.py", "provision", "preserve"): (
        "copies the files a workflow's settings keep into its isolated worktree"
    ),
    ("proposer/backends.py", "RunnerProposerBackend.prepare", "snapshot_workspace"): (
        _WORKSPACE_BASELINE
    ),
    ("proposer/backends.py", "SubagentProposerBackend.prepare", "snapshot_workspace"): (
        _WORKSPACE_BASELINE
    ),
    ("proposer/service.py", "run_second_opinion", "build_brief"): _WORKSPACE_BASELINE,
    ("proposer/backends.py", "RunnerProposerBackend.collect", "normalise"): _SECOND_OPINION,
    ("proposer/backends.py", "SubagentProposerBackend.collect", "normalise"): _SECOND_OPINION,
    # A small file of the product's own: its size is bounded by what it is.
    ("dashboard/chat_voice.py", "api_voice_synthesize", "_record_generation"): _VOICE_TAKE,
    ("dashboard/handlers/voice_profiles.py", "api_voice_profile_lock", "lock_profile"): (
        _VOICE_TAKE
    ),
    ("apps/native/bundled-chat/provider.py", "download_weight", "_install_licence_texts"): (
        "copies a downloaded model's licence texts beside it"
    ),
    ("dashboard/handlers/apps.py", "api_app_get", "ui_revision"): (
        "hashes the entry files of an app's interface, for its revision"
    ),
    ("dashboard/handlers/apps.py", "api_apps_list", "ui_revision"): (
        "hashes the entry files of each app's interface, for its revision"
    ),
    ("dashboard/handlers/skills.py", "api_skill_verify", "verify_skill_integrity"): (
        "hashes a skill's files to check them against its install record"
    ),
    ("dashboard/handlers/skills.py", "api_skills_list", "verify_skill_integrity"): (
        "hashes each listed skill's files to check them against its install record"
    ),
    ("dashboard/handlers/packs.py", "api_pack_bundled_install", "import_pack"): _PACK_FILES,
    ("dashboard/handlers/packs.py", "api_pack_one_link", "import_onelink"): _PACK_FILES,
    ("dashboard/handlers/packs.py", "api_pack_update", "apply_update"): _PACK_FILES,
    ("dashboard/handlers/packs.py", "api_pack_update", "plan_update"): _PACK_FILES,
    ("dashboard/handlers/packs.py", "api_pack_uninstall", "apply_uninstall"): _PACK_FILES,
    ("dashboard/handlers/packs.py", "api_pack_uninstall", "plan_uninstall"): _PACK_FILES,
}

#: Where the whole-file work of an upload and the other large-file paths is handed to a worker
#: thread: the census must see each of them do so, or it is not reading what it should.
HANDED_OFF: frozenset[tuple[str, str]] = frozenset(
    {
        ("uploads/store.py", "UploadStore.assemble"),
        ("dashboard/handlers/uploads.py", "_finalize_target"),
        ("knowledge/file_items.py", "store_file_item"),
        # The copy a file nobody uploaded is scanned and kept as (a dropped file, a watched
        # folder's), and the move of a watched folder's copy into the library's files.
        ("knowledge/file_items.py", "_check"),
        ("knowledge/file_items.py", "_keep"),
        ("dashboard/handlers/files.py", "api_file_move"),
        ("dashboard/handlers/evals.py", "api_evals_retrieval_card"),
        ("action_providers/selfqa_evidence_provider.py", "SelfQaEvidenceActionProvider.execute"),
        ("voice_reply.py", "stitch_wavs"),
    }
)


def _name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


class _Census:
    """The census over modules given as ``{path under the package: source}``."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.trees = {rel: ast.parse(text) for rel, text in sources.items()}
        self.module = {rel: self._module_name(rel) for rel in sources}
        self.defs: dict[str, ast.AST] = {}
        self.by_name: dict[str, list[str]] = collections.defaultdict(list)
        self.owner: dict[int, tuple[str, str, str | None]] = {}  # id(def) → (rel, qual, class)
        self.imports: dict[str, dict[str, str]] = {}
        self.awaited: dict[str, set[int]] = {}
        for rel, tree in self.trees.items():
            self._index(rel, tree)
        self.whole_file = self._whole_file_defs()

    @staticmethod
    def _module_name(rel: str) -> str:
        parts = ["personalclaw", *Path(rel).with_suffix("").parts]
        return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)

    def _index(self, rel: str, tree: ast.Module) -> None:
        module = self.module[rel]
        parents: dict[int, ast.AST] = {}
        functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        names: dict[str, str] = {}
        awaited: set[int] = set()
        for node in ast.walk(tree):  # one pass: parents, functions, imports, awaited calls
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    head = alias.name.split(".")[0]
                    names[alias.asname or head] = alias.name if alias.asname else head
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
            elif isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                awaited.add(id(node.value))
        self.imports[rel] = names
        self.awaited[rel] = awaited
        for fn in functions:
            chain, up = [], parents.get(id(fn))
            in_class = isinstance(up, ast.ClassDef)
            while up is not None:
                if isinstance(up, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    chain.append(up.name)
                up = parents.get(id(up))
            qual = ".".join([*reversed(chain), fn.name])
            self.defs[f"{module}.{qual}"] = fn
            self.by_name[fn.name].append(f"{module}.{qual}")
            cls = f"{module}." + ".".join(reversed(chain)) if in_class else None
            self.owner[id(fn)] = (rel, qual, cls)

    def resolve(self, func: ast.expr, fn: ast.AST) -> str | None:
        """The qualified name of the package function *func* names, inside *fn*, or None."""
        rel, _qual, cls = self.owner[id(fn)]
        module, names = self.module[rel], self.imports[rel]
        if isinstance(func, ast.Name):
            for candidate in (f"{module}.{func.id}", names.get(func.id, "")):
                if candidate in self.defs:
                    return candidate
            return None
        if not isinstance(func, ast.Attribute):
            return None
        if isinstance(func.value, ast.Name):
            if func.value.id in ("self", "cls") and cls and f"{cls}.{func.attr}" in self.defs:
                return f"{cls}.{func.attr}"
            base = names.get(func.value.id)
            if base and f"{base}.{func.attr}" in self.defs:
                return f"{base}.{func.attr}"
        candidates = self.by_name.get(func.attr, [])
        return candidates[0] if len(candidates) == 1 else None

    def whole_file_call(self, call: ast.Call, rel: str) -> str:
        """``module.function`` when *call* is the standard library's whole-file work: a copy, a
        move or a file digest, or a hash of a whole read; else ``""``."""
        func = call.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
            return ""
        spelled = f"{func.value.id}.{func.attr}"
        if (func.value.id, func.attr) in _STDLIB:
            return spelled
        if func.value.id == "hashlib" and any(
            isinstance(inner, ast.Call)
            and _name(inner.func) in ("read", "read_bytes")
            and id(inner) not in self.awaited[rel]
            for arg in call.args
            for inner in ast.walk(arg)
        ):
            return spelled
        return ""

    def loops(self, fn: ast.AST, rel: str) -> dict[int, str]:
        """The whole-file loops in *fn*: id(loop) → :data:`COPY_LOOP` or :data:`STREAM_LOOP`."""
        awaited, found = self.awaited[rel], {}
        for loop in ast.walk(fn):
            if not isinstance(loop, (ast.For, ast.While, ast.AsyncFor)):
                continue
            calls = [c for c in ast.walk(loop) if isinstance(c, ast.Call)]
            reads = [
                c for c in calls if _name(c.func) in _READS and isinstance(c.func, ast.Attribute)
            ]
            sinks = [
                c for c in calls if _name(c.func) in _SINKS and isinstance(c.func, ast.Attribute)
            ]
            if not reads or not sinks:
                continue
            if any(id(c) not in awaited for c in reads):
                found[id(loop)] = COPY_LOOP
            elif isinstance(fn, ast.AsyncFunctionDef) and any(
                _name(c.func) == "write" and id(c) not in awaited for c in sinks
            ):
                found[id(loop)] = STREAM_LOOP
        return found

    def _whole_file_defs(self) -> set[str]:
        whole: set[str] = set()
        callees: dict[str, set[str]] = {}
        for qual, fn in self.defs.items():
            if not isinstance(fn, ast.FunctionDef) or qual.startswith(_NOT_FOLLOWED):
                continue
            rel = self.owner[id(fn)][0]
            nodes = list(ast.walk(fn))
            calls = [c for c in nodes if isinstance(c, ast.Call)]
            has_loop = any(isinstance(n, (ast.For, ast.While)) for n in nodes)
            if any(self.whole_file_call(c, rel) for c in calls) or (
                has_loop and COPY_LOOP in self.loops(fn, rel).values()
            ):
                whole.add(qual)
            callees[qual] = {t for c in calls if (t := self.resolve(c.func, fn))}
        grew = True
        while grew:
            grew = False
            for qual, called in callees.items():
                if qual not in whole and called & whole:
                    whole.add(qual)
                    grew = True
        return whole

    def sites(self) -> tuple[dict[tuple[str, str, str], list[int]], set[tuple[str, str]]]:
        """``(on the loop: (file, function, the call) → lines, functions that hand some off)``."""
        on_loop: dict[tuple[str, str, str], list[int]] = collections.defaultdict(list)
        handing: set[tuple[str, str]] = set()
        for rel, tree in self.trees.items():
            # Each node is read once, from the outermost ``async def`` around it: ``ast.walk``
            # reaches an ``async def`` before the ones nested in it.
            read: set[int] = set()
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.AsyncFunctionDef):
                    continue
                off, handed = set(), set()
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and _name(node.func) in _HAND_OFF:
                        off.update(id(n) for n in ast.walk(node))
                        handed.update(_name(a) for a in node.args if _name(a))
                for node in ast.walk(fn):
                    if isinstance(node, ast.FunctionDef) and node.name in handed:
                        off.update(id(n) for n in ast.walk(node))
                loops = self.loops(fn, rel)
                qual = self.owner[id(fn)][1]
                for node in ast.walk(fn):
                    if id(node) in read:
                        continue
                    read.add(id(node))
                    what = ""
                    if isinstance(node, ast.Call):
                        what = self.whole_file_call(node, rel)
                        if not what and self.resolve(node.func, fn) in self.whole_file:
                            what = _name(node.func)
                    elif id(node) in loops:
                        what = loops[id(node)]
                    if not what:
                        continue
                    if id(node) in off:
                        handing.add((rel, qual))
                    else:
                        on_loop[(rel, qual, what)].append(node.lineno)
                # A function or method handed over by reference: ``to_thread(self._concat, …)``.
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and _name(node.func) in _HAND_OFF:
                        for arg in node.args:
                            target = self.resolve(arg, fn) if isinstance(arg, ast.expr) else None
                            if target in self.whole_file or (
                                isinstance(arg, ast.Attribute)
                                and isinstance(arg.value, ast.Name)
                                and (arg.value.id, arg.attr) in _STDLIB
                            ):
                                handing.add((rel, qual))
        return dict(on_loop), handing


@functools.lru_cache(maxsize=1)
def _package_census() -> _Census:
    return _Census(
        {
            path.relative_to(_PACKAGE).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(_PACKAGE.rglob("*.py"))
        }
    )


def test_nothing_new_on_the_event_loop_copies_moves_or_hashes_a_whole_file():
    """🔴 Red before: the upload paths' assembly, moves and hashes, the Files move, the retrieval
    card's database hashes, the self-check evidence seal, a spoken reply's copy, and every route
    that wrote an upload's body to disk as it arrived, all on the event loop."""
    on_loop, _handing = _package_census().sites()

    new = {key: lines for key, lines in on_loop.items() if key not in STILL_ON_THE_LOOP}
    gone = sorted(key for key in STILL_ON_THE_LOOP if key not in on_loop)
    listed = "\n".join(
        f"  {f}:{lines} {fn}: {what}" for (f, fn, what), lines in sorted(new.items())
    )
    assert not new, (
        "whole-file work on the event loop: hand it to asyncio.to_thread (a child process when it "
        f"holds the interpreter lock):\n{listed}"
    )
    assert not gone, f"no longer on the loop, so take these out of STILL_ON_THE_LOOP: {gone}"


def test_the_census_sees_the_large_file_paths_hand_their_work_off():
    """The positive control: the census reads the upload paths, and sees each of them hand its
    whole-file work to a worker thread. A census that stopped matching would see nothing on the
    loop anywhere and pass."""
    on_loop, handing = _package_census().sites()

    assert HANDED_OFF <= handing, f"not seen handing off: {sorted(HANDED_OFF - handing)}"
    assert len(on_loop) >= 30, f"the census found only {len(on_loop)} sites on the loop"


_SHAPES = textwrap.dedent("""
    import asyncio
    import hashlib
    import shutil


    def _digest(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()


    def _keep(src, dest):
        shutil.move(src, dest)
        return _digest(dest)


    def _filed(src, dest):
        return _keep(src, dest)


    async def files_an_upload(src, dest):
        return _filed(src, dest)


    async def copies(src, dest):
        shutil.copy(src, dest)


    async def hashes_a_read(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()


    async def writes_a_body_as_it_arrives(field, fh):
        while chunk := await field.read_chunk():
            fh.write(chunk)


    async def hands_it_all_off(src, dest, field, spool):
        await asyncio.to_thread(_filed, src, dest)
        await asyncio.to_thread(shutil.copy, src, dest)

        def work():
            return _keep(src, dest)

        await asyncio.get_running_loop().run_in_executor(None, work)
        while chunk := await field.read_chunk():
            await spool.write(chunk)
""")


def test_the_census_finds_each_shape_on_the_loop_and_none_handed_off():
    """The falsification: source written to hold each shape, on the loop and handed off."""
    on_loop, handing = _Census({"shapes.py": _SHAPES}).sites()

    assert sorted(on_loop) == [
        ("shapes.py", "copies", "shutil.copy"),
        ("shapes.py", "files_an_upload", "_filed"),
        ("shapes.py", "hashes_a_read", "hashlib.sha256"),
        ("shapes.py", "writes_a_body_as_it_arrives", STREAM_LOOP),
    ], on_loop
    assert handing == {("shapes.py", "hands_it_all_off")}
