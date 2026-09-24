"""WATCHED-SOURCES WS-8 — connector-pack app kind + the source-recipe directory.

The atom's ``done_when`` has four clauses and each is asserted as an OUTCOME, never as "the
call returned":

* **a connector pack installs and registers via KnowledgeTypeHandler** — a real app dir with
  an ``app.json`` and a three-line ``provider.py`` goes through the shipped handler and comes
  back out of ``list_provider_info`` as ``kind: external``;
* **its parse-only script receives an engine-fetched body on stdin and its JSON lines land as
  items** — driven through the real ``SourceEngine``, asserted by counting rows in
  ``knowledge.db``, not by reading the poll result;
* **bundled recipes surface in the create flow** — every shipped recipe resolves from a real
  URL and the resolved spec is put through the OWNING PROVIDER's ``validate_spec``, so a
  recipe the create flow would refuse on save cannot ship;
* **no socket outside net.fetch (SC#11, pack path)** — the negative is proved behaviourally
  against a REAL local listener: a pack script that tries to connect is stopped, the listener
  records zero connections, and the batch is discarded. ``test_the_socket_proof_is_not
  _vacuous`` runs the same fixture with the fence's denylist emptied and asserts the listener
  DOES get a connection, so the proof cannot pass by accident.

Every other escape route a parse script could take gets its own case: ``urllib.request``,
``ctypes`` (raw libc), ``subprocess``/``os`` (spawn a helper that networks), ``importlib``
(re-entry), and the ``object.__subclasses__()`` gadget that reaches ``os.system`` without any
import at all. And the fail-closed contract is asserted in all five of its shapes: garbage,
a partial line, a wrong-shaped object, a timeout, and an over-cap batch — each yielding ZERO
items, never a subset.

No test reaches the internet: ``fetch_fn`` is the provider's only byte seam and the socket
proof dials 127.0.0.1. Isolation: tmp_path db + PERSONALCLAW_HOME.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from personalclaw.apps.manifest import (
    PACK_ARG_TYPES,
    PACK_FETCH_METHODS,
    PACK_SECRET_HEADERS,
    AppManifest,
    PackSourceEntry,
)
from personalclaw.knowledge.source_engine import SourceEngine
from personalclaw.knowledge.source_recipes import (
    RECIPE_PROVIDERS,
    list_recipes,
    recipes_for_url,
    resolve_spec,
    validate_recipe,
)
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.knowledge_providers import pack_parse
from personalclaw.knowledge_providers.connector_pack import (
    ConnectorPackProvider,
    PackConfigError,
    render_fetch,
    validate_args,
)
from personalclaw.knowledge_providers.pack_parse import (
    DENIED_MODULES,
    SECRET_HEADERS,
    ParseFailure,
    harness_source,
    run_parse_script,
)

# Reached through the SDK facade on purpose: this is the exact import a pack's own
# ``provider.py`` writes, so exercising it here means the published boundary is what the
# tests drive rather than the core module behind it.
from personalclaw.sdk.knowledge import connector_pack_provider

FEED_URL = "https://api.example.com/repos/acme/widget/releases"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


@pytest.fixture()
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


# ── the fixture connector pack ──────────────────────────────────────────────────────

#: The pack's whole provider module. Three lines, and it imports core ONLY through
#: ``personalclaw.sdk.*`` — the boundary ``test_apps_import_boundary.py`` lints. That it can
#: be this small is the design: the pack contributes a parser, not a client.
PROVIDER_PY = """
from personalclaw.sdk.knowledge import connector_pack_provider


def create_provider(config=None):
    return connector_pack_provider(__file__, config)
"""

#: A real parse-only script: reads the engine-fetched body on stdin, its args on argv, emits
#: one JSON object per line. No network, no imports beyond text handling.
PARSE_RELEASES_PY = """
import json
import sys

args = json.loads(sys.argv[1])
payload = json.loads(sys.stdin.read())
for row in payload.get("releases", []):
    print(json.dumps({
        "guid": row["id"],
        "title": row["name"],
        "url": row["html_url"],
        "content": row.get("body", ""),
        "published_at": row.get("published_at", ""),
        "metadata": {"repo": args.get("repo", "")},
    }))
"""

RELEASE_BODY = json.dumps(
    {
        "releases": [
            {
                "id": "rel-1",
                "name": "widget 2.0.0",
                "html_url": "https://example.com/releases/2.0.0",
                "body": "Adds the thing.",
                "published_at": "2026-08-01T00:00:00Z",
            },
            {
                "id": "rel-2",
                "name": "widget 2.0.1",
                "html_url": "https://example.com/releases/2.0.1",
                "body": "Fixes the thing.",
                "published_at": "2026-08-09T00:00:00Z",
            },
        ]
    }
).encode()


def _manifest(**over) -> dict:
    base = {
        "name": "acme-connector",
        "version": "1.0.0",
        "displayName": "Acme Connector",
        "description": "Watches Acme releases.",
        "permissions": {"network": True},
        "provider": {
            "type": "knowledge",
            "implementation": "provider:create_provider",
            "capabilities": ["source"],
        },
        "sources": [
            {
                "name": "releases",
                "displayName": "Acme releases",
                "script": "scripts/parse_releases.py",
                "fetchSpec": {
                    "url": "https://api.example.com/repos/{{args.repo}}/releases",
                    "method": "GET",
                    "headers": {"Authorization": "Bearer {{secret:ACME_TOKEN}}"},
                    "accept": "application/json",
                },
                "argsSchema": {"repo": {"type": "string", "required": True}},
            }
        ],
    }
    base.update(over)
    return base


def _write_pack(tmp_path: Path, *, script: str = PARSE_RELEASES_PY, **over) -> Path:
    app_dir = tmp_path / "apps" / "acme-connector"
    (app_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (app_dir / "app.json").write_text(json.dumps(_manifest(**over), indent=2), encoding="utf-8")
    (app_dir / "provider.py").write_text(PROVIDER_PY, encoding="utf-8")
    (app_dir / "scripts" / "parse_releases.py").write_text(script, encoding="utf-8")
    return app_dir


class _Resp:
    """A recorded fetch response — the shape ``net.fetch`` returns."""

    def __init__(self, body=b"", *, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.url = FEED_URL


class _Fetcher:
    """A scripted fetch seam recording every request, so a test can assert the URL the
    manifest template rendered to and the headers the secret landed in."""

    def __init__(self, resp=None):
        self.resp = resp if resp is not None else _Resp(RELEASE_BODY)
        self.requests: list[str] = []
        self.headers: list[dict] = []
        self.methods: list[str] = []

    async def __call__(self, url, *, policy=None, method="GET", headers=None):
        self.requests.append(url)
        self.headers.append(dict(headers or {}))
        self.methods.append(method)
        return self.resp


class _FakeQueue:
    def __init__(self):
        self.enqueued: list[str] = []

    def enqueue(self, item_id: str) -> None:
        self.enqueued.append(item_id)

    def recover_pending(self) -> int:
        return 0


def _cfg(**over):
    from personalclaw.config.loader import SourcesConfig

    base = dict(
        enabled=True,
        poll_interval_default_secs=1,
        network_floor_secs=0,
        max_sources=100,
        max_items_per_poll=50,
        daily_request_budget=288,
    )
    base.update(over)
    return SourcesConfig(**base)


def _setup(store, app_dir, fetcher, *, spec=None, secret="tok-123"):
    sid = store.create_source(
        name="acme releases",
        provider="acme-connector",
        kind="pack",
        spec=spec or {"pack_source": "releases", "args": {"repo": "acme/widget"}},
        item_type="bookmark",
    )
    provider = ConnectorPackProvider(
        app_dir,
        store=store,
        fetch_fn=fetcher,
        secret_resolver=(lambda name: secret),
    )
    queue = _FakeQueue()
    engine = SourceEngine(store, queue, providers_lister=lambda: [provider], config_loader=_cfg)
    return sid, provider, engine, queue


async def _poll(engine, store, sid):
    return await engine.poll_source(store.get_source(sid), _cfg())


def _items(store, sid):
    return store.db.execute(
        "SELECT * FROM items WHERE source_id = ? ORDER BY guid", (sid,)
    ).fetchall()


# ── done_when 1: a pack installs and registers through KnowledgeTypeHandler ──────────


def test_a_connector_pack_registers_through_the_knowledge_type_handler(tmp_path, monkeypatch):
    """The shipped handler builds the pack's provider and it appears as ``kind: external``.

    Driven through ``KnowledgeTypeHandler`` + ``load_factory`` — the real enable path — rather
    than by constructing the provider directly, because what this clause is about is the
    manifest→factory→registry leg, not the class.
    """
    from personalclaw.knowledge_providers.registry import list_provider_info
    from personalclaw.providers.registry import KnowledgeTypeHandler, RegisteredProvider

    pack = _write_pack(tmp_path)
    # The ONE seam that resolves an installed app's dir — patched, not reimplemented, so the
    # module load below really is the shipped `load_factory` path.
    monkeypatch.setattr("personalclaw.providers.loader.app_dir", lambda name: pack)

    manifest = AppManifest.from_dict(json.loads((pack / "app.json").read_text()))
    assert manifest.validate() == []
    ext = RegisteredProvider(
        name="acme-connector", provider_config=manifest.provider, manifest=manifest
    )
    handler = KnowledgeTypeHandler()
    instance = handler.create(ext)
    try:
        handler.register(ext, instance)
        info = {p["name"]: p for p in list_provider_info()}
        assert "acme-connector" in info, "the pack's provider never reached the registry"
        assert info["acme-connector"]["kind"] == "external"
        assert info["acme-connector"]["display_name"] == "Acme Connector"
    finally:
        handler.deregister(ext, instance)
    assert "acme-connector" not in {p["name"] for p in list_provider_info()}


def test_the_pack_provider_is_poll_capable_so_the_engine_enrols_it(tmp_path, store):
    """Enrolment is duck-typed on the base class, so this is what puts the pack in the loop."""
    pack = _write_pack(tmp_path)
    _sid, provider, engine, _q = _setup(store, pack, _Fetcher())
    assert engine.enrolled_provider_names() == {"acme-connector"}
    assert provider.name == "acme-connector"


def test_the_pack_provider_module_is_importable_only_through_the_sdk(tmp_path):
    """The pack's own ``provider.py`` reaches core ONLY via ``personalclaw.sdk.*``.

    Asserted by parsing it the same way ``test_apps_import_boundary.py`` lints installed apps,
    because that lint skips in a standalone core clone and this fixture is the shape it would
    otherwise catch.
    """
    import ast

    pack = _write_pack(tmp_path)
    tree = ast.parse((pack / "provider.py").read_text(encoding="utf-8"))
    reached: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("personalclaw"):
            reached.append(node.module or "")
        if isinstance(node, ast.Import):
            reached += [a.name for a in node.names if a.name.startswith("personalclaw")]
    assert reached == ["personalclaw.sdk.knowledge"]


# ── done_when 2: engine-fetched body on stdin → JSON lines → items ──────────────────


@pytest.mark.asyncio
async def test_a_pack_parse_of_an_engine_fetched_body_lands_items(tmp_path, store):
    """The full leg: rendered URL → net.fetch seam → stdin → JSON lines → rows in the db."""
    pack = _write_pack(tmp_path)
    fetcher = _Fetcher(_Resp(RELEASE_BODY, headers={"ETag": 'W/"abc"'}))
    sid, _provider, engine, queue = _setup(store, pack, fetcher)

    assert await _poll(engine, store, sid) == 2
    rows = _items(store, sid)
    assert [r["guid"] for r in rows] == ["rel-1", "rel-2"]
    assert [r["title"] for r in rows] == ["widget 2.0.0", "widget 2.0.1"]
    assert len(queue.enqueued) == 2, "items must reach the ONE ingestion path"

    # The engine fetched, not the script: one request, at the URL the template rendered.
    assert fetcher.requests == ["https://api.example.com/repos/acme%2Fwidget/releases"]
    assert fetcher.methods == ["GET"]
    # The secret landed in a header and the conditional-GET plumbing rode along.
    assert fetcher.headers[0]["Authorization"] == "Bearer tok-123"
    assert fetcher.headers[0]["Accept"] == "application/json"

    # The validator was persisted, so the next poll is conditional.
    assert json.loads(store.get_source_cursor(sid)) == {"etag": 'W/"abc"'}


@pytest.mark.asyncio
async def test_a_second_poll_of_the_same_body_creates_no_duplicate_items(tmp_path, store):
    """The pack path inherits the ``UNIQUE(source_id, guid)`` gate like every other kind."""
    pack = _write_pack(tmp_path)
    fetcher = _Fetcher()
    sid, _p, engine, _q = _setup(store, pack, fetcher)
    assert await _poll(engine, store, sid) == 2
    assert await _poll(engine, store, sid) == 0
    assert len(_items(store, sid)) == 2


@pytest.mark.asyncio
async def test_a_304_returns_zero_items_and_keeps_the_validators(tmp_path, store):
    pack = _write_pack(tmp_path)
    sid, provider, _e, _q = _setup(store, pack, _Fetcher(_Resp(b"", status=304)))
    result = await provider.poll(sid, cursor='{"etag": "keep-me"}')
    assert result.items == []
    assert result.cursor == '{"etag": "keep-me"}'


@pytest.mark.asyncio
async def test_the_script_receives_its_declared_args_on_argv(tmp_path, store):
    """``args`` reach the script (the argv half of the argv/JSON-stdout contract)."""
    pack = _write_pack(tmp_path)
    sid, provider, _e, _q = _setup(store, pack, _Fetcher())
    result = await provider.poll(sid)
    assert [i.metadata["repo"] for i in result.items] == ["acme/widget", "acme/widget"]


# ── done_when 4 / SC#11: the script cannot open a socket ────────────────────────────

#: A pack script that tries to reach a REAL listener. Written so the only way it can emit an
#: item at all is by having connected — so "zero items" and "zero connections" are the same
#: claim measured at two ends.
SOCKET_PACK_PY = """
import json
import socket
import sys

port = int(json.loads(sys.argv[1])["port"])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
s.connect(("127.0.0.1", port))
s.sendall(b"x")
s.close()
print(json.dumps({"guid": "reached-the-network", "title": "reached the network"}))
"""


class _Listener:
    """A real loopback listener. Records accepted connections, so the assertion is about a
    socket that did or did not happen rather than about an exception's text."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.sock.settimeout(4)
        self.accepted: list[str] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.sock.getsockname()[1])

    def _serve(self) -> None:
        try:
            while True:
                conn, addr = self.sock.accept()
                self.accepted.append(str(addr))
                conn.close()
        except OSError:
            return

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


@pytest.fixture()
def listener():
    lis = _Listener()
    try:
        yield lis
    finally:
        lis.close()


def test_a_pack_script_that_tries_to_open_a_socket_reaches_nothing(tmp_path, listener):
    """SC#11 for the pack path, proved as an OUTCOME at both ends.

    The script's ONE job is to connect to a listener this test owns and then emit an item. It
    gets neither: the parse fails closed with the refusal code, and the listener — a real
    socket, not a mock — accepted nothing.
    """
    script = tmp_path / "sock.py"
    script.write_text(SOCKET_PACK_PY, encoding="utf-8")
    failure = None
    rows: list = []
    try:
        rows = run_parse_script(script, b"{}", {"port": listener.port}).rows
    except ParseFailure as exc:
        failure = exc
    # The OUTCOME first, deliberately: a `pytest.raises` block would have made the exception's
    # code the first thing checked, and then a mutation that let the connection through but
    # still raised somewhere else would red on the wrong line.
    assert listener.accepted == [], "a pack script reached the network"
    assert rows == []
    assert failure is not None and failure.code == ParseFailure.IMPORT_REFUSED
    assert "parse-only" in failure.detail


def test_the_socket_proof_is_not_vacuous(tmp_path, listener, monkeypatch):
    """The mutation check, in-tree: empty the fence's denylist and the SAME fixture connects.

    This is what makes the test above a proof rather than an observation that a script did not
    happen to network. ``DENIED_MODULES`` is the ONE live definition (the harness interpolates
    it), so emptying it here empties the rail that actually runs — there is no second copy in
    a docstring to mutate by mistake.
    """
    monkeypatch.setattr(pack_parse, "DENIED_MODULES", frozenset())
    script = tmp_path / "sock.py"
    script.write_text(SOCKET_PACK_PY, encoding="utf-8")
    result = run_parse_script(script, b"{}", {"port": listener.port})
    assert [r["guid"] for r in result.rows] == ["reached-the-network"]
    assert listener.accepted, "with the fence removed the listener must see the connection"


@pytest.mark.parametrize(
    "name,source",
    [
        ("urllib", "import json\nimport urllib.request\nprint(json.dumps({'guid': 'u'}))\n"),
        ("ctypes", "import json, ctypes\nprint(json.dumps({'guid': 'c'}))\n"),
        ("subprocess", "import json, subprocess\nprint(json.dumps({'guid': 's'}))\n"),
        ("os", "import json, os\nprint(json.dumps({'guid': 'o'}))\n"),
        ("asyncio", "import json, asyncio\nprint(json.dumps({'guid': 'a'}))\n"),
        (
            "importlib",
            "import json, importlib\n"
            "importlib.import_module('socket')\n"
            "print(json.dumps({'guid': 'i'}))\n",
        ),
    ],
)
def test_every_other_route_to_a_socket_is_refused(tmp_path, name, source):
    """Each escape a parse script could take instead of ``import socket``.

    Enumerated rather than trusted: the denylist's claim is that every in-process network path
    in CPython bottoms out at ``_socket``, ``_ctypes`` or a spawned child, and this is that
    claim exercised one route at a time.
    """
    script = tmp_path / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {})
    assert exc.value.code == ParseFailure.IMPORT_REFUSED, exc.value.detail


#: The classic no-import escape: walk ``object.__subclasses__()`` to a class defined in ``os``
#: and read ``os.system`` out of its ``__init__.__globals__``. An import fence alone does not
#: touch this, which is why the harness also neuters the loaded module's process calls.
GADGET_PACK_PY = """
import json

for cls in object.__subclasses__():
    glob = getattr(getattr(cls, "__init__", None), "__globals__", None) or {}
    if "system" in glob and "popen" in glob:
        glob["system"]("true")
        print(json.dumps({"guid": "spawned", "title": "spawned a process"}))
        break
else:
    print(json.dumps({"guid": "no-gadget", "title": "no gadget available"}))
"""


def test_the_subclasses_gadget_cannot_reach_os_system(tmp_path):
    """Reflection reaches the os module dict; the harness has already emptied the shells.

    Asserted as "the batch is discarded", not "an exception mentioned os": the gadget FOUND
    ``system`` (so the route is genuinely open in a plain interpreter) and calling it raised
    the fence's own refusal, which is the only outcome that distinguishes a closed route from
    a lucky miss.
    """
    script = tmp_path / "gadget.py"
    script.write_text(GADGET_PACK_PY, encoding="utf-8")
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {})
    assert exc.value.code == ParseFailure.IMPORT_REFUSED
    assert "os.system" in exc.value.detail


def test_removing_the_fence_discards_the_whole_batch(tmp_path):
    """A script that tampers with the fence gets zero items — tamper detection, not trust."""
    script = tmp_path / "tamper.py"
    script.write_text(
        "import json, sys\nsys.meta_path.pop(0)\nprint(json.dumps({'guid': 't'}))\n",
        encoding="utf-8",
    )
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {})
    assert exc.value.code == ParseFailure.FENCE_TAMPERED


def test_a_parse_script_may_still_use_the_stdlib_it_needs(tmp_path):
    """The vacuity counterpart: the fence must not be "everything is refused".

    Without this, a fence that broke every import would pass every case above while making the
    whole feature unusable — and the failure would only show up on a real pack.
    """
    script = tmp_path / "stdlib.py"
    script.write_text(
        "import csv, datetime, hashlib, html.parser, io, json, re\n"
        "import xml.etree.ElementTree as ET\n"
        "from urllib.parse import urljoin\n"
        "row = next(csv.reader(io.StringIO('a,b')))\n"
        "print(json.dumps({\n"
        "    'guid': hashlib.sha256(b'x').hexdigest()[:8],\n"
        "    'title': ET.fromstring('<t>ok</t>').text + row[0],\n"
        "    'url': urljoin('https://a.example/', 'b'),\n"
        "    'published_at': datetime.date(2026, 8, 15).isoformat(),\n"
        "}))\n",
        encoding="utf-8",
    )
    result = run_parse_script(script, b"", {})
    assert len(result.rows) == 1
    assert result.rows[0]["title"] == "oka"
    assert result.rows[0]["url"] == "https://a.example/b"


def test_each_fence_mechanism_is_named_in_the_harness_and_none_is_redundant():
    """The fence is three mechanisms and the division of labour is MEASURED, not assumed.

    Under ``python -I`` exactly three denied names are pre-imported. That measurement is what
    decides which mechanism catches what — the finder covers everything absent, eviction covers
    the three, neutering covers reflection past eviction — and it is asserted here because a
    future Python that pre-imported ``socket`` would silently move ``import socket`` from the
    finder's jurisdiction into the eviction loop's, and the module docstring would be wrong.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-I", "-c", "import sys, json; print(json.dumps(sorted(sys.modules)))"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    preloaded = {
        name
        for name in json.loads(out)
        if any(
            ".".join(name.split(".")[: i + 1]) in DENIED_MODULES
            for i in range(len(name.split(".")))
        )
    }
    assert preloaded == {"os", "os.path", "posix"}, (
        f"the pre-imported denied set moved to {sorted(preloaded)}; re-derive which fence "
        f"mechanism covers which name before trusting the module docstring"
    )
    src = harness_source()
    # No fourth mechanism crept back in: wrapping `builtins.__import__` was deleted after a
    # mutation showed it reded nothing, and re-adding it would be an untested layer.
    assert "builtins.__import__ =" not in src


def test_evicting_the_preimported_modules_is_what_stops_the_spawn_route(tmp_path):
    """``import os`` is the ONE route the finder cannot see, so eviction has its own case.

    Without the eviction loop this import is served from the cache the interpreter populated at
    startup and no finder runs at all — which is exactly why the finder alone is not the fence.
    Asserted as an OUTCOME: the script's spawn would create a marker file, and the marker's
    absence is what says no process ran. (Dropping the eviction loop makes this marker APPEAR
    even though the batch is still discarded by the tamper check — so without the marker the
    test would have read as passing while a process really had been spawned.)
    """
    marker = tmp_path / "spawned.marker"
    script = tmp_path / "spawn.py"
    script.write_text(
        "import json, os\n"
        f"os.system('touch {marker}')\n"
        "print(json.dumps({'guid': 'spawned'}))\n",
        encoding="utf-8",
    )
    failure = None
    rows: list = []
    try:
        rows = run_parse_script(script, b"", {}).rows
    except ParseFailure as exc:
        failure = exc
    assert not marker.exists(), "a pack script spawned a process"
    assert rows == []
    assert failure is not None and failure.code == ParseFailure.IMPORT_REFUSED
    assert "'os'" in failure.detail


def test_the_spawn_really_carries_the_bounds_its_audit_entry_claims():
    """`test_spawn_ceiling_audit.py` CLASSIFIES this spawn site; it does not check the code.

    Measured: deleting the `spawn_shim_argv` line from `run_parse_script` leaves that audit — and
    every other test — green, because its `_CEILING_WRAPPED` map asserts that a site is *described*
    as ceiling-wrapped, not that it *is*. That is the declared-but-inert shape one layer up from
    the code, so the four bounds this atom promises are asserted against the AST of the function
    that must apply them.
    """
    import ast
    import inspect

    from personalclaw.knowledge_providers import pack_parse as mod

    tree = ast.parse(inspect.getsource(mod.run_parse_script))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    attr_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    kwargs = {
        kw.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg
    }
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert "spawn_shim_argv" in calls, "the tool resource ceiling is not applied"
    assert "PROFILE_TOOL" in names, "the ceiling is applied under some other profile"
    assert "build_child_env" in calls, "the child inherits an unfiltered environment"
    assert "wrap_argv" in calls, "the OS path sandbox is not applied"
    assert "timeout" in kwargs, "the spawn has no wall clock"
    assert "run" in attr_calls or "subprocess" in names, "no spawn found at all"


def test_the_harness_interpolates_the_live_denylist(tmp_path):
    """The shipped harness source really carries ``DENIED_MODULES`` — no hand-copied second
    list that could silently drift from (or outlive) the constant."""
    src = harness_source()
    for name in ("socket", "ctypes", "os", "subprocess"):
        assert repr(name) in src
    assert "__PC_DENIED_MODULES__" not in src


# ── fail closed on malformed output, in all five shapes ─────────────────────────────


def test_garbage_on_stdout_yields_zero_items_not_the_good_ones(tmp_path):
    """One bad line discards the batch. The two GOOD lines are the point of the assertion:
    a tolerant parser would have returned them and reported success."""
    script = tmp_path / "garbage.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'guid': 'a', 'title': 'first'}))\n"
        "print('progress: halfway')\n"
        "print(json.dumps({'guid': 'b', 'title': 'second'}))\n",
        encoding="utf-8",
    )
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {})
    assert exc.value.code == ParseFailure.MALFORMED
    assert "line 2" in exc.value.detail


def test_a_partial_line_yields_zero_items(tmp_path):
    """A script killed mid-write leaves a torn final line; the terminator makes that visible."""
    script = tmp_path / "partial.py"
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'guid': 'a', 'title': 'first'}))\n"
        'sys.stdout.write(\'{"guid": "b", "tit\')\n'
        "sys.stdout.flush()\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {})
    assert exc.value.code in (ParseFailure.MALFORMED, ParseFailure.INCOMPLETE)


#: The most determined shape of the fail-closed attack, and the one that motivates BOTH the
#: nonce and the ``sys.modules["__main__"]`` swap: read the terminator nonce out of the
#: harness's own globals, tamper with the fence, print rows, write a FORGED "intact"
#: terminator, then close stdout so the harness can never write its real one last.
FORGE_PACK_PY = """
import json
import sys

nonce = getattr(sys.modules.get("__main__"), "_NONCE", "")
sys.meta_path.pop(0)
print(json.dumps({"guid": "forged", "title": "forged a terminator"}))
sys.stdout.write("\\n__PC_PACK_END__" + nonce + json.dumps({"fence": "intact"}) + "\\n")
sys.stdout.flush()
sys.stdout.close()
"""


def test_a_script_cannot_forge_the_terminator_and_claim_an_intact_fence(tmp_path):
    """Three mechanisms are load-bearing here at once, and each was measured, not assumed.

    ``rfind`` alone is not enough: the harness normally writes the LAST terminator, but a script
    that closes stdout after forging one leaves its own as the last. What defeats that is the
    NONCE, which lives only in the harness's globals — and ``sys.modules["__main__"]`` is how a
    script would read those, which is why the harness swaps in an empty shell. Dropping either
    the nonce or the swap makes this batch land with a fake "intact" verdict, so this is the
    case that gives both of them teeth (and the only case that reaches ``INCOMPLETE``, since the
    timeout path raises before the terminator is ever parsed).
    """
    script = tmp_path / "forge.py"
    script.write_text(FORGE_PACK_PY, encoding="utf-8")
    rows: list = []
    failure = None
    try:
        rows = run_parse_script(script, b"", {}).rows
    except ParseFailure as exc:
        failure = exc
    assert rows == [], "a forged terminator got a batch accepted"
    assert failure is not None and failure.code == ParseFailure.INCOMPLETE


def test_a_missing_terminator_yields_zero_items(tmp_path):
    """The terminator is REQUIRED, so output that stops early can never read as complete.

    Simulated by shortening the timeout against a script that sleeps past it, because a
    process killed mid-run is exactly the shape a real crash produces.
    """
    script = tmp_path / "slow.py"
    script.write_text(
        "import json, time\nprint(json.dumps({'guid': 'a'}))\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {}, timeout_secs=2)
    assert exc.value.code == ParseFailure.TIMEOUT


@pytest.mark.asyncio
async def test_a_wrong_shaped_object_yields_zero_items(tmp_path, store):
    """A row with no guid, url or title cannot be de-duplicated, so the batch is refused —
    asserted through a real poll, where the alternative is an item re-created every poll."""
    pack = _write_pack(
        tmp_path,
        sources=_manifest()["sources"],
    )
    (pack / "scripts" / "parse_releases.py").write_text(
        "import json\n"
        "print(json.dumps({'guid': 'ok', 'title': 'fine'}))\n"
        "print(json.dumps({'content': 'nothing to key on'}))\n",
        encoding="utf-8",
    )
    sid, _p, engine, queue = _setup(store, pack, _Fetcher())
    assert await _poll(engine, store, sid) == 0
    assert _items(store, sid) == []
    assert queue.enqueued == []
    row = store.get_source(sid)
    assert row["health_status"] == "degraded"
    assert ParseFailure.BAD_SHAPE in (row["last_error_summary"] or "")


@pytest.mark.asyncio
async def test_a_row_with_a_non_http_url_yields_zero_items(tmp_path, store):
    """An emitted ``url`` is what a user clicks and what cross-source dedupe keys on, so a
    ``javascript:`` or ``file:`` row is refused rather than stored — and refused for the WHOLE
    batch, like every other shape failure."""
    pack = _write_pack(tmp_path, sources=_manifest()["sources"])
    (pack / "scripts" / "parse_releases.py").write_text(
        "import json\n"
        "print(json.dumps({'guid': 'ok', 'title': 'fine', 'url': 'https://a.example/x'}))\n"
        "print(json.dumps({'guid': 'bad', 'title': 'bad', 'url': 'javascript:alert(1)'}))\n",
        encoding="utf-8",
    )
    sid, _p, engine, queue = _setup(store, pack, _Fetcher())
    assert await _poll(engine, store, sid) == 0
    assert _items(store, sid) == []
    assert queue.enqueued == []
    assert ParseFailure.BAD_SHAPE in (store.get_source(sid)["last_error_summary"] or "")


@pytest.mark.asyncio
async def test_a_failed_parse_does_not_advance_the_cursor(tmp_path, store):
    """A batch we refused must be re-offered, not skipped past — otherwise a transient parser
    bug silently loses everything the feed published while it was broken."""
    pack = _write_pack(tmp_path, sources=_manifest()["sources"])
    (pack / "scripts" / "parse_releases.py").write_text("print('nonsense')\n", encoding="utf-8")
    sid, provider, _e, _q = _setup(
        store, pack, _Fetcher(_Resp(RELEASE_BODY, headers={"ETag": "e"}))
    )
    result = await provider.poll(sid, cursor='{"etag": "old"}')
    assert result.items == []
    assert result.cursor == '{"etag": "old"}'
    assert result.error.startswith(ParseFailure.MALFORMED)


def test_an_over_cap_batch_yields_zero_items(tmp_path, monkeypatch):
    monkeypatch.setattr(pack_parse, "MAX_ITEMS", 3)
    script = tmp_path / "many.py"
    script.write_text(
        "import json\nfor i in range(10):\n    print(json.dumps({'guid': str(i)}))\n",
        encoding="utf-8",
    )
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {})
    assert exc.value.code == ParseFailure.TOO_LARGE


def test_an_over_cap_output_size_yields_zero_items(tmp_path):
    script = tmp_path / "big.py"
    script.write_text(
        "import json\nprint(json.dumps({'guid': 'a', 'content': 'x' * 5000}))\n",
        encoding="utf-8",
    )
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"", {}, max_output_bytes=1000)
    assert exc.value.code == ParseFailure.TOO_LARGE


def test_a_body_over_the_input_cap_is_refused_before_the_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(pack_parse, "MAX_BODY_BYTES", 16)
    script = tmp_path / "never.py"
    script.write_text("raise AssertionError('must not run')\n", encoding="utf-8")
    with pytest.raises(ParseFailure) as exc:
        run_parse_script(script, b"x" * 64, {})
    assert exc.value.code == ParseFailure.TOO_LARGE


# ── manifest schema: the pack kind is coherent, not merely well-formed ──────────────


def test_a_wellformed_pack_manifest_validates_and_round_trips(tmp_path):
    manifest = AppManifest.from_dict(_manifest())
    assert manifest.validate() == []
    assert AppManifest.from_dict(manifest.to_dict()).to_dict() == manifest.to_dict()
    assert [s.name for s in manifest.sources] == ["releases"]
    assert manifest.pack_source("releases") is not None
    assert manifest.pack_source("nope") is None


def test_a_manifest_without_the_sources_block_round_trips_byte_identically():
    """The block is purely additive: an app that declares none serializes as it always did."""
    raw = {
        "name": "plain-app",
        "version": "1.0.0",
        "displayName": "Plain",
        "description": "no sources",
    }
    assert "sources" not in AppManifest.from_dict(raw).to_dict()


@pytest.mark.parametrize(
    "mutation,needle",
    [
        ({"script": "../escape.py"}, "path traversal"),
        ({"script": "/etc/passwd.py"}, "must be relative"),
        ({"script": "parse.sh"}, "must be a .py file"),
        ({"fetchSpec": {"url": "file:///etc/passwd"}}, "must be http(s)"),
        (
            {"fetchSpec": {"url": "https://a.example/?t={{secret:T}}"}},
            "must not put a secret in fetchSpec.url",
        ),
        ({"fetchSpec": {"url": "https://a.example/{{args.nope}}"}}, "undeclared arg"),
        ({"fetchSpec": {"url": "https://a.example/{{whatever}}"}}, "unknown placeholder"),
        ({"fetchSpec": {"url": "https://a.example/", "method": "POST"}}, "fetchSpec.method"),
        (
            {
                "fetchSpec": {
                    "url": "https://a.example/",
                    "headers": {"Authorization": "Bearer sk-live-literal"},
                }
            },
            "must reference a {{secret:KEY}}",
        ),
        ({"fetchSpec": {"url": "https://a.example/", "proxy": "socks5://x"}}, "unknown key"),
        ({"argsSchema": {"repo": {"type": "object"}}}, "argsSchema.repo.type"),
        ({"name": "Not Kebab"}, "kebab-case"),
    ],
)
def test_a_pack_source_entry_refuses_each_unsafe_declaration(mutation, needle):
    entry = _manifest()["sources"][0]
    entry.update(mutation)
    errors = AppManifest.from_dict(_manifest(sources=[entry])).validate()
    assert any(needle in e for e in errors), errors


def test_sources_without_a_knowledge_source_provider_is_refused():
    """A declared script nothing can drive is the inert-surface shape, refused at install."""
    raw = _manifest(provider={"type": "tool", "implementation": "m:f"})
    assert any(
        "requires a provider with type 'knowledge'" in e
        for e in AppManifest.from_dict(raw).validate()
    )
    raw = _manifest(
        provider={"type": "knowledge", "implementation": "m:f", "capabilities": ["search"]}
    )
    assert any("'source' capability" in e for e in AppManifest.from_dict(raw).validate())


def test_sources_without_the_network_permission_is_refused():
    """The fetch is core's, but it happens because the pack asked — so consent must say so."""
    raw = _manifest(permissions={})
    assert any("permissions.network" in e for e in AppManifest.from_dict(raw).validate())


def test_duplicate_source_names_are_refused():
    entry = _manifest()["sources"][0]
    assert any(
        "duplicate source name" in e
        for e in AppManifest.from_dict(_manifest(sources=[entry, dict(entry)])).validate()
    )


def test_the_secret_header_list_has_one_definition():
    """The manifest validates the rule and ``pack_parse`` renders it; two copies of the list
    would be one edit away from a header the schema guards and the renderer does not."""
    assert PACK_SECRET_HEADERS == SECRET_HEADERS


def test_the_declared_method_and_arg_vocabularies_are_read_only_and_scalar():
    """Both closed sets are asserted literally, because widening either is a real decision:
    a POST would be an unattended write, and a nested arg cannot go into a URL anyway."""
    assert PACK_FETCH_METHODS == frozenset({"GET", "HEAD"})
    assert PACK_ARG_TYPES == frozenset({"string", "integer", "boolean"})


# ── rendering: args, secrets, containment ──────────────────────────────────────────


def test_a_missing_required_arg_refuses_before_any_fetch():
    entry = PackSourceEntry.from_dict(_manifest()["sources"][0])
    with pytest.raises(PackConfigError, match="requires arg 'repo'"):
        validate_args(entry, {})


def test_an_undeclared_arg_refuses_rather_than_being_ignored():
    entry = PackSourceEntry.from_dict(_manifest()["sources"][0])
    with pytest.raises(PackConfigError, match="undeclared arg"):
        validate_args(entry, {"repo": "a/b", "token": "sneaky"})


def test_an_arg_is_percent_encoded_into_the_url_but_not_into_a_header():
    """A repo name with a slash must not silently become an extra path segment."""
    raw = _manifest()["sources"][0]
    raw["fetchSpec"]["headers"]["X-Repo"] = "{{args.repo}}"
    entry = PackSourceEntry.from_dict(raw)
    url, method, headers = render_fetch(
        entry, {"repo": "acme/widget"}, secret_resolver=lambda n: "t"
    )
    assert url.endswith("/repos/acme%2Fwidget/releases")
    assert headers["X-Repo"] == "acme/widget"
    assert method == "GET"


def test_a_missing_credential_refuses_rather_than_sending_a_blank_header():
    """An empty ``Authorization`` is a 401 nobody can diagnose, or a request that succeeded
    against an endpoint that did not need the token — both worse than a named refusal."""
    entry = PackSourceEntry.from_dict(_manifest()["sources"][0])

    def _missing(name: str) -> str:
        raise PackConfigError(f"credential {name!r} is not configured")

    with pytest.raises(PackConfigError, match="ACME_TOKEN"):
        render_fetch(entry, {"repo": "a/b"}, secret_resolver=_missing)


def test_the_default_credential_resolver_refuses_a_configured_but_valueless_secret(
    tmp_path, monkeypatch
):
    """The shipped resolver, not an injected fake. A descriptor with no value anywhere in the
    chain must refuse by name — the injected-resolver tests above prove the propagation, and
    this proves the thing that actually runs in production."""
    from personalclaw.knowledge_providers.connector_pack import _default_secret

    home = tmp_path / "creds-home"
    home.mkdir()
    (home / "credentials.json").write_text(
        json.dumps({"ACME_TOKEN": {"type": "static_token"}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        "personalclaw.config.loader.config_dir", lambda *a, **k: home, raising=False
    )
    with pytest.raises(PackConfigError, match="configured but has no value"):
        _default_secret("ACME_TOKEN")
    with pytest.raises(PackConfigError, match="is not configured"):
        _default_secret("NEVER_DECLARED")


def test_a_secret_in_a_rendered_url_is_refused_even_from_an_edited_manifest():
    """The schema refuses it at install; the renderer refuses it again, so a hand-edited
    installed manifest cannot put a token where the egress audit will log it."""
    raw = _manifest()["sources"][0]
    raw["fetchSpec"]["url"] = "https://a.example/?t={{secret:ACME_TOKEN}}"
    entry = PackSourceEntry.from_dict(raw)
    with pytest.raises(PackConfigError, match="secret in its URL"):
        render_fetch(entry, {"repo": "a/b"}, secret_resolver=lambda n: "t")


def test_a_script_symlinked_out_of_the_app_dir_is_refused(tmp_path, store):
    """The schema guards the STRING (no ``..``, no leading ``/``); this guards the PATH."""
    pack = _write_pack(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("print('{}')\n", encoding="utf-8")
    link = pack / "scripts" / "escape.py"
    link.symlink_to(outside)
    raw = _manifest()["sources"][0]
    raw["script"] = "scripts/escape.py"
    (pack / "app.json").write_text(json.dumps(_manifest(sources=[raw])), encoding="utf-8")
    provider = ConnectorPackProvider(pack, store=store, secret_resolver=lambda n: "t")
    with pytest.raises(PackConfigError, match="outside the app dir"):
        provider.script_path(provider.resolve_entry("releases"))


@pytest.mark.asyncio
async def test_a_spec_naming_an_undeclared_pack_source_refuses_before_fetching(tmp_path, store):
    pack = _write_pack(tmp_path)
    fetcher = _Fetcher()
    sid, provider, _e, _q = _setup(store, pack, fetcher, spec={"pack_source": "ghost", "args": {}})
    result = await provider.poll(sid)
    assert result.items == []
    assert "declares no source 'ghost'" in result.error
    assert fetcher.requests == [], "a bad spec must be refused before the fetch seam"


@pytest.mark.asyncio
async def test_an_unknown_spec_key_is_refused(tmp_path, store):
    """The spec is deliberately two keys: a spec that could override the URL would make the
    reviewed-at-install manifest advisory."""
    pack = _write_pack(tmp_path)
    fetcher = _Fetcher()
    sid, provider, _e, _q = _setup(
        store,
        pack,
        fetcher,
        spec={"pack_source": "releases", "args": {"repo": "a/b"}, "url": "https://evil.example"},
    )
    result = await provider.poll(sid)
    assert "unknown key(s) ['url']" in result.error
    assert fetcher.requests == []


@pytest.mark.asyncio
async def test_a_manifest_that_became_invalid_after_install_stops_polling(tmp_path, store):
    """An installed manifest is a file an update or a hand-edit can change, and it decides both
    a fetch target and which script runs — so it is re-validated per poll, not trusted.

    The mutation chosen is an inline-literal ``Authorization`` header, DELIBERATELY: a bad
    ``method`` or a ``..`` in the script path is caught a second time by ``render_fetch`` and
    ``script_path``, so using one of those would have let this test pass with the poll-time
    ``entry.validate()`` deleted. A committed credential is the case only the schema catches.
    """
    pack = _write_pack(tmp_path)
    fetcher = _Fetcher()
    sid, provider, _e, _q = _setup(store, pack, fetcher)
    assert (await provider.poll(sid)).items
    raw = _manifest()["sources"][0]
    raw["fetchSpec"]["headers"] = {"Authorization": "Bearer sk-live-committed"}
    (pack / "app.json").write_text(json.dumps(_manifest(sources=[raw])), encoding="utf-8")
    result = await provider.poll(sid)
    assert result.items == []
    assert "must reference a {{secret:KEY}}" in result.error
    assert len(fetcher.requests) == 1, "the second poll must not have fetched"


def test_the_factory_accepts_a_file_inside_the_pack(tmp_path, store):
    """``connector_pack_provider(__file__, config)`` is the documented three-line call."""
    pack = _write_pack(tmp_path)
    provider = connector_pack_provider(pack / "provider.py", {"parse_timeout_secs": 5}, store=store)
    assert provider.name == "acme-connector"
    assert provider._timeout_secs == 5


# ── done_when 3: bundled recipes surface in the create flow ─────────────────────────


def test_the_bundled_recipe_directory_is_not_empty():
    """A wheel missing the ``package-data`` line ships an empty directory and every pasted URL
    silently looks uncovered — a product regression with no error, so the count is asserted."""
    recipes = list_recipes()
    assert len(recipes) >= 5
    assert len({r.id for r in recipes}) == len(recipes)


def test_every_bundled_recipe_is_valid_and_targets_a_known_provider():
    from personalclaw.knowledge.source_recipes import recipes_dir

    files = sorted(recipes_dir().glob("*.json"))
    assert files, "no recipe files found — the bundled directory is missing"
    for path in files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert validate_recipe(raw, where=path.name) == [], path.name
        assert raw["provider"] in RECIPE_PROVIDERS


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/astral-sh/uv", "github-releases"),
        ("https://github.com/astral-sh/uv/releases", "github-releases"),
        ("https://github.com/trending", "github-trending"),
        ("https://news.ycombinator.com", "hacker-news"),
        ("https://pypi.org/project/aiohttp/", "pypi-releases"),
        ("https://www.reddit.com/r/rust", "reddit-subreddit"),
        ("https://example.substack.com", "substack-newsletter"),
        ("https://code.visualstudio.com/updates", "changelog-page"),
    ],
)
def test_a_pasted_url_finds_the_recipe_that_covers_it(url, expected):
    """The §7.2 workflow: before anyone tunes a selector, check whether the site is covered."""
    matched = [m.recipe.id for m in recipes_for_url(url)]
    assert expected in matched, f"{url} matched {matched}"


def test_an_uncovered_url_matches_nothing_rather_than_guessing():
    """The vacuity floor. A recipe whose pattern matched everything would make every match
    above pass while the directory told the user nothing true."""
    assert recipes_for_url("https://example.com/") == []
    assert recipes_for_url("") == []


def test_a_matched_recipe_arrives_with_its_spec_already_resolved():
    """Capture groups do the work, so the create flow saves what it was shown."""
    (match,) = [m for m in recipes_for_url("https://github.com/astral-sh/uv")]
    assert match.groups == {"owner": "astral-sh", "repo": "uv"}
    assert match.spec["url"] == "https://github.com/astral-sh/uv/releases.atom"
    assert "{{" not in json.dumps(match.spec)


def test_a_recipe_with_an_unfillable_placeholder_is_skipped_not_half_resolved():
    """A spec with a hole in its URL is a fetch of the wrong thing."""
    from personalclaw.knowledge.source_recipes import SourceRecipe

    recipe = SourceRecipe(
        id="x", display_name="X", provider="watched-feed", spec={"url": "https://a/{{owner}}"}
    )
    with pytest.raises(KeyError):
        resolve_spec(recipe, {})


def test_a_recipe_declaring_a_placeholder_no_pattern_captures_is_invalid():
    """The static rail behind the case above: the recipe file itself is refused."""
    errors = validate_recipe(
        {
            "id": "broken",
            "displayName": "Broken",
            "provider": "watched-feed",
            "matchPatterns": ["^https://a\\.example/$"],
            "spec": {"url": "https://a.example/{{owner}}"},
        }
    )
    assert any("no matchPatterns capture group" in e for e in errors)


def test_every_bundled_recipe_spec_is_accepted_by_its_own_provider():
    """A recipe the create flow would refuse on save is a broken recipe.

    This is the clause that makes "surfaces in the create flow" mean something: each shipped
    recipe is resolved against a real URL and the result goes through the same
    ``validate_spec`` the provider runs at save time AND at poll time.
    """
    from personalclaw.knowledge_providers.feed_source import FeedSourceProvider
    from personalclaw.knowledge_providers.web_source import WebSourceProvider

    samples = {
        "github-releases": "https://github.com/astral-sh/uv",
        "github-trending": "https://github.com/trending",
        "hacker-news": "https://news.ycombinator.com",
        "pypi-releases": "https://pypi.org/project/aiohttp/",
        "reddit-subreddit": "https://www.reddit.com/r/rust",
        "substack-newsletter": "https://example.substack.com",
        "changelog-page": "https://code.visualstudio.com/updates",
    }
    providers = {
        "watched-feed": FeedSourceProvider(None),
        "watched-page": WebSourceProvider(None),
    }
    checked = 0
    for recipe in list_recipes():
        url = samples.get(recipe.id)
        assert url, f"recipe {recipe.id} has no sample URL in this test — add one"
        matches = [m for m in recipes_for_url(url) if m.recipe.id == recipe.id]
        assert matches, f"recipe {recipe.id} did not match its own sample URL {url}"
        ok, err = providers[recipe.provider].validate_spec(matches[0].spec)
        assert ok, f"{recipe.id}: {err}"
        checked += 1
    assert checked == len(list_recipes())


@pytest.mark.asyncio
async def test_the_recipe_directory_is_reachable_over_http():
    """ "Surfaces in the create flow" is only true if something can read it. WS-9 owns the UI;
    this is the route that UI consumes, driven through the real handler."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.knowledge import list_source_recipes

    resp = await list_source_recipes(
        make_mocked_request("GET", "/api/knowledge/source-recipes", app=web.Application())
    )
    payload = json.loads(resp.body)
    assert len(payload["recipes"]) == len(list_recipes())
    assert "matches" not in payload

    resp = await list_source_recipes(
        make_mocked_request(
            "GET",
            "/api/knowledge/source-recipes?url=https://github.com/astral-sh/uv",
            app=web.Application(),
        )
    )
    payload = json.loads(resp.body)
    assert [m["id"] for m in payload["matches"]] == ["github-releases"]
    assert payload["matches"][0]["spec"]["url"].endswith("/astral-sh/uv/releases.atom")
    assert payload["matches"][0]["kind"] == "feed"


def test_the_recipe_route_is_registered_on_the_knowledge_router():
    """The handler existing is not the same as the route existing (the shape this repo's own
    inert-surface census keeps catching), so the registration is asserted separately."""
    import inspect

    from personalclaw.dashboard.handlers import knowledge as handlers

    src = inspect.getsource(handlers.setup_knowledge_routes)
    assert '"/api/knowledge/source-recipes", list_source_recipes' in src


def test_the_denylist_names_every_root_the_docstring_claims():
    """The module's claim is that the socket set bottoms out at three roots; if one of those
    names ever leaves the constant, this reds rather than the property quietly weakening."""
    for root in ("_socket", "_ctypes", "ctypes", "os", "subprocess", "_posixsubprocess"):
        assert root in DENIED_MODULES
