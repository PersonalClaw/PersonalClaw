"""Every route that takes a file's bytes from a request scans what it stores.

The content scan (``uploads.content_scan.scan_upload``) ran only when a resumable upload
completed, so a file sent in one request reached the agent and the library unread: a chat
attachment, a file uploaded to a folder, a Knowledge file under the chunk threshold, a file
dropped into a workflow run, a binary artifact's new bytes, a project archive and a backup import.

Derived from the source, as the whole-file census is
(``test_event_loop_whole_file_census.py``): every function in the package that takes a
``request`` and reads its body as bytes is found, wherever in it (or in a function nested in it)
the body is read:

* a multipart body (``request.multipart()``) or a form (``request.post()``);
* the raw body (``request.read()``, ``request.text()``) or its stream (``request.content``);
* a file sent inside a JSON body: a base64 decode in a function that reads ``request.json()``.

Each one is named below. :data:`SCANNED` store what they read, each with the function that hands
it to ``scan_upload``: the route itself, or for a part of a resumable upload the complete that
assembles it. The census checks that function calls it. :data:`NOT_SCANNED` read a body and do
not hand it to the scan, each with why: it stores no file the agent or the library reads. No
route is let off for storing pictures only: the scan reads an upload by its bytes, not by the kind
its name or its type claims, so a route that keeps a file hands it to the scan whatever it is. A
site in neither reds here, and so does a named one that is gone, so a new upload route has to say
which it is.

What the census cannot see: a body read through another name than ``request``, a file sent as a
JSON string field (the Files editor's own saves, which carry what was typed in the dashboard), and
a route that scans one file and stores another. The routes' own tests
(``test_every_upload_route_scans_what_it_stores.py``) drive the order: refused content is never
stored, and clean content is.
"""

from __future__ import annotations

import ast
import collections
import functools
import textwrap
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The calls on ``request`` that read its body as bytes.
_BODY_CALLS = frozenset({"multipart", "post", "read", "text"})
JSON_FILE = "a base64 file in request.json()"

#: The route hands what it read to the scan itself.
SELF = None

#: Routes that store what they read, by (file under ``src/personalclaw``, function): what they
#: store, and the function that hands it to ``scan_upload`` (:data:`SELF` for the route itself).
SCANNED: dict[tuple[str, str], tuple[str, tuple[str, str] | None]] = {
    ("dashboard/handlers/files.py", "api_upload_file"): ("a chat attachment", SELF),
    ("dashboard/handlers/files.py", "api_file_upload"): ("a file uploaded to a folder", SELF),
    ("dashboard/handlers/knowledge.py", "ingest_file"): ("a Knowledge file", SELF),
    ("dashboard/handlers/uploads.py", "api_uploads_part"): (
        "a part of a resumable upload, scanned whole when it completes",
        ("dashboard/handlers/uploads.py", "_complete"),
    ),
    ("workflows/handlers.py", "api_run_drop"): ("a file dropped into a workflow run", SELF),
    ("artifacts/handlers.py", "api_artifact_raw_write"): ("a binary artifact's new bytes", SELF),
    ("tasks/hierarchy_handlers.py", "_read_project_upload"): ("a project archive", SELF),
    ("dashboard/handlers/durability.py", "_read_upload_file"): ("a backup to import", SELF),
    ("dashboard/chat_handlers.py", "api_chat_screen_frame_pin"): ("a pinned screen frame", SELF),
}

#: Routes that read a body and do not hand it to the scan, by (file, function), with why.
NOT_SCANNED: dict[tuple[str, str], str] = {
    ("dashboard/handlers/core.py", "api_stt_transcribe"): (
        "a voice clip from the composer, transcribed and then removed: no file is kept"
    ),
    ("inbound/openai_dialect.py", "handle_transcriptions"): (
        "audio for the transcription API, transcribed and then removed: no file is kept"
    ),
    ("dashboard/handlers/apps.py", "api_app_proxy"): (
        "forwards the body to the app's own backend: the gateway keeps none of it"
    ),
    ("inbound/capture_proxy.py", "_handle"): (
        "a model request another agent sends through the capture proxy: a request, not a file"
    ),
    ("inbound/a2a.py", "handle_tasks"): "a task request, read as JSON",
    ("inbound/mcp_http.py", "handle_mcp"): "a JSON-RPC request",
    ("inbound/openai_dialect.py", "_read_json"): "a chat request, read as JSON",
    ("dashboard/handlers/trigger_runs.py", "api_trigger_fire"): (
        "a webhook's payload, fenced as untrusted text for the automation it fires"
    ),
    ("request_validation.py", "_has_bytes"): "asks whether a request carries a body at all",
    ("sdk/security.py", "require_proxy_signature._middleware"): (
        "reads a body to check the gateway's signature over it"
    ),
}


def _is_request(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "request"


class _Census:
    """The census over modules given as ``{path under the package: source}``."""

    def __init__(self, sources: dict[str, str]) -> None:
        self.trees = {rel: ast.parse(text) for rel, text in sources.items()}
        self.defs: dict[tuple[str, str], ast.AST] = {}
        #: (file, function) → how it reads a request's body.
        self.sites: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
        for rel, tree in self.trees.items():
            self._read(rel, tree)

    def _read(self, rel: str, tree: ast.Module) -> None:
        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[id(child)] = node
        quals: dict[int, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain, up = [fn.name], parents.get(id(fn))
                while up is not None:
                    if isinstance(up, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        chain.append(up.name)
                    up = parents.get(id(up))
                quals[id(fn)] = ".".join(reversed(chain))
                self.defs[(rel, quals[id(fn)])] = fn

        def owner(node: ast.AST) -> str | None:
            """The innermost function around *node* that takes a ``request``."""
            up = parents.get(id(node))
            while up is not None:
                if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = [*up.args.posonlyargs, *up.args.args, *up.args.kwonlyargs]
                    if any(a.arg == "request" for a in params):
                        return quals[id(up)]
                up = parents.get(id(up))
            return None

        reads_json: set[str] = set()
        decodes: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if _is_request(node.func.value) and attr in _BODY_CALLS | {"json"}:
                    if (fn := owner(node)) is not None:
                        if attr == "json":
                            reads_json.add(fn)
                        else:
                            self.sites[(rel, fn)].add(f"request.{attr}()")
                elif attr.endswith("b64decode") and (fn := owner(node)) is not None:
                    decodes.add(fn)
            elif isinstance(node, ast.Attribute) and node.attr == "content":
                if _is_request(node.value) and (fn := owner(node)) is not None:
                    self.sites[(rel, fn)].add("request.content")
        for fn in reads_json & decodes:
            self.sites[(rel, fn)].add(JSON_FILE)

    def calls_the_scan(self, site: tuple[str, str]) -> bool:
        fn = self.defs.get(site)
        return fn is not None and any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "scan_upload")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "scan_upload")
            )
            for node in ast.walk(fn)
        )


@functools.lru_cache(maxsize=1)
def _package_census() -> _Census:
    return _Census(
        {
            path.relative_to(_PACKAGE).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(_PACKAGE.rglob("*.py"))
        }
    )


def test_every_route_that_reads_an_uploaded_body_is_named():
    """A route added later that reads an uploaded body reds here until it says whether it scans
    what it stores, so the scan's coverage cannot fall behind the routes again."""
    census = _package_census()

    named = SCANNED.keys() | NOT_SCANNED.keys()
    new = {site: sorted(how) for site, how in census.sites.items() if site not in named}
    gone = sorted(site for site in named if site not in census.sites)
    listed = "\n".join(f"  {f} {fn}: {', '.join(how)}" for (f, fn), how in sorted(new.items()))
    assert not new, (
        "a route reads an uploaded body: hand what it stores to uploads.content_scan.scan_upload "
        "before anything is made from it and name it in SCANNED, or name it in NOT_SCANNED with "
        f"why it is not scanned:\n{listed}"
    )
    assert not gone, f"no longer read a request's body, so take these out of the census: {gone}"
    assert not SCANNED.keys() & NOT_SCANNED.keys()


def test_every_route_that_stores_an_upload_hands_it_to_the_scan():
    """🔴 Red before: of the routes that store an uploaded file, only the resumable complete
    scanned it."""
    census = _package_census()

    unscanned = sorted(
        f"{f} {fn} ({what}): scanned in {scanner or (f, fn)}"
        for (f, fn), (what, scanner) in SCANNED.items()
        if not census.calls_the_scan(scanner or (f, fn))
    )
    assert not unscanned, "these never call scan_upload:\n  " + "\n  ".join(unscanned)


def test_the_census_sees_the_upload_routes():
    """The positive control: the census reads every shape of body it names somewhere in the
    package, so a census that stopped matching cannot pass by finding nothing."""
    census = _package_census()

    shapes = set().union(*census.sites.values())
    assert shapes >= {"request.multipart()", "request.read()", "request.content", JSON_FILE}
    assert len(census.sites) >= 19, sorted(census.sites)


_SHAPES = textwrap.dedent("""
    import base64


    async def keeps_a_multipart_file(request):
        reader = await request.multipart()
        part = await reader.next()
        with open("kept", "wb") as fh:
            fh.write(await part.read())


    async def keeps_and_scans(request):
        reader = await request.multipart()
        await scan_upload(path, "document", surface="files")


    async def keeps_a_file_sent_in_json(request):
        body = await request.json()
        return base64.b64decode(body["file"])


    async def reads_json_only(request):
        return await request.json()


    async def streams_its_body(request):
        async for chunk in request.content.iter_chunked(1024):
            pass


    async def reads_through_a_closure(request):
        async def inner():
            return await request.read()

        return await inner()


    def reads_a_file(path):
        return open(path).read()
""")


def test_the_census_finds_each_shape_of_body_and_which_one_scans():
    """The falsification: source written to hold each shape."""
    census = _Census({"shapes.py": _SHAPES})

    assert dict(census.sites) == {
        ("shapes.py", "keeps_a_multipart_file"): {"request.multipart()"},
        ("shapes.py", "keeps_and_scans"): {"request.multipart()"},
        ("shapes.py", "keeps_a_file_sent_in_json"): {JSON_FILE},
        ("shapes.py", "streams_its_body"): {"request.content"},
        ("shapes.py", "reads_through_a_closure"): {"request.read()"},
    }
    assert census.calls_the_scan(("shapes.py", "keeps_and_scans"))
    assert not census.calls_the_scan(("shapes.py", "keeps_a_multipart_file"))
