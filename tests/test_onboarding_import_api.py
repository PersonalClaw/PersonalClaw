"""``/api/onboarding/import`` — the onboarding step's two calls (PEP-5).

PEP-4 shipped the engine with no HTTP surface at all; this is the surface, and these
are the properties that make it safe to hand to a first-run screen. Every test drives
the REAL router (``TestClient`` over the registered app), a FIXTURE foreign root bound
through ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``, and a FIXTURE home bound through
``PERSONALCLAW_HOME`` — the developer's real ``~/.claude`` is never read and their real
``~/.personalclaw`` is never written. The fixture asserts both redirects BIND before any
test body runs, because an isolation lever that silently missed would turn this suite
into a scan of the machine it runs on.

The load-bearing tests, one per clause the atom names:

* ``test_fresh_home_scan_shows_the_source_with_nothing_already_imported`` — a fresh home
  with a fixture source shows the step something to offer.
* ``test_planted_secret_appears_nowhere_in_the_scan_response`` /
  ``test_planted_secret_never_reaches_the_home_through_the_route`` — the import completes
  without any secret appearing, over the wire OR on disk.
* ``test_reentry_marks_already_imported_items_existing`` /
  ``test_reimport_reports_existing_and_imports_nothing`` — re-entry shows already-imported
  items as ``existing``, and re-running writes nothing new.
* ``test_a_write_failure_is_reported_with_the_secret_redacted`` — a writer that raises is
  a 500 carrying the failure's own (screened) sentence, never a cheerful empty 200. A
  swallowed write is the defect class this endpoint exists to make impossible.
* ``test_the_scan_names_every_item_by_a_fingerprint_the_server_derives`` /
  ``test_a_pick_imports_exactly_those_items`` — the pick is item by item, by an id the scan
  mints and a re-scan reproduces, and the import writes exactly what was picked.
* ``test_a_pick_the_rescan_no_longer_finds_is_reported_not_dropped`` /
  ``test_content_in_the_request_is_ignored`` — ids travel, content never does: the import
  keeps only fingerprints its OWN re-scan found, reports the rest, and a body, path or
  payload riding along in the request changes nothing that lands.
* ``test_a_malformed_fingerprint_is_refused_before_anything_is_read`` /
  ``test_an_absent_or_empty_pick_is_refused_rather_than_importing_nothing`` — the pick is
  validated by shape before a scan runs, and "import nothing" is a refusal rather than a
  success with a zero in it.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.onboarding_import import (
    register_onboarding_import_routes,
)
from personalclaw.onboarding_import import ImportCategory, fingerprint_of

#: The planted credential. If this string reaches the wire or any byte under the home,
#: a test fails. Shaped like a real key so the redactors engage.
SECRET = "sk-ant-api03-PEP5PLANTEDSECRET00000000000000000000000000000AA"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def foreign(tmp_path: Path) -> Path:
    """A fixture ``~/.claude``: instructions and one MCP server, each carrying a secret."""
    root = tmp_path / "foreign" / ".claude"
    root.mkdir(parents=True)
    (root / "CLAUDE.md").write_text(
        f"# House rules\n\n- Always run the linter.\n- The key is {SECRET}.\n",
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "weather": {
                        "command": "npx",
                        "args": ["-y", "weather-mcp"],
                        "env": {"WEATHER_API_KEY": SECRET, "REGION": "eu"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def home(tmp_path: Path, foreign: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated home AND isolated foreign roots, with the binding asserted.

    ``PERSONALCLAW_HOME`` and the two source env vars are all read live on every call,
    which is what makes them the robust lever here (a ``config_dir`` attribute patch is
    not undoable once a consumer module has bound the name at import). The asserts are
    the point: an env var that failed to take effect would leave this suite scanning the
    developer's real machine while still passing.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.onboarding_import.sources import claude_code, codex

    h = tmp_path / "pclaw-home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    monkeypatch.setenv("PERSONALCLAW_SKIP_SKILL_SEED", "1")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(foreign))
    # A directory that does not exist: Codex must come back `present: false`, which is
    # also what proves "not installed" is distinguishable from "installed but empty".
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex-here"))

    assert config_dir() == h, "PERSONALCLAW_HOME did not bind — the real home is at risk"
    assert claude_code.resolve_root() == foreign, "CLAUDE_CONFIG_DIR did not bind"
    assert codex.resolve_root() == tmp_path / "no-codex-here", "CODEX_HOME did not bind"
    return h


@pytest.fixture
def make_client(home: Path):
    """A factory for a TestClient over an app carrying ONLY these two routes.

    A factory rather than a ready client because ``TestClient`` binds a cookie jar to
    the RUNNING loop, which does not exist yet while a sync fixture is being built.
    Every test therefore opens it inside its own ``async with``, the house pattern.
    """

    def _make() -> TestClient:
        app = web.Application()
        register_onboarding_import_routes(app)
        return TestClient(TestServer(app))

    return _make


def _bytes_under(root: Path) -> bytes:
    """Every byte of every file under ``root``, concatenated. For secret sweeps."""
    blob = b""
    for path in sorted(root.rglob("*")):
        if path.is_file():
            blob += path.read_bytes()
    return blob


# ── 1. the scan ───────────────────────────────────────────────────────────────


async def _scan(client) -> dict:
    resp = await client.get("/api/onboarding/import")
    assert resp.status == 200
    return await resp.json()


def _items(body: dict, source: str = "claude_code") -> list[dict]:
    return next(s for s in body["sources"] if s["source"] == source)["items"]


def _fingerprint(body: dict, category: str) -> str:
    return next(i["fingerprint"] for i in _items(body) if i["category"] == category)


@pytest.mark.asyncio
async def test_fresh_home_scan_shows_the_source_with_nothing_already_imported(make_client, home):
    async with make_client() as client:
        body = await _scan(client)

    by_name = {s["source"]: s for s in body["sources"]}
    claude = by_name["claude_code"]
    assert claude["detected"] is True
    assert claude["present"] is True
    assert claude["counts"]["instructions"] >= 1
    assert claude["counts"]["mcp_servers"] >= 1
    # A fresh home has imported nothing, so every item is on offer as new — with the place it
    # would land, which is what the step shows beside a conflict later.
    assert claude["items"], "the step would have nothing to render"
    assert [i["state"] for i in claude["items"]] == ["new"] * len(claude["items"])
    assert all(i["destination"] for i in claude["items"])
    # "not installed" is its own answer, not an error and not an empty detected source.
    assert by_name["codex"]["present"] is False
    assert by_name["codex"]["detected"] is False
    # The group vocabulary comes from the enum, so it cannot drift from the writers.
    assert body["categories"] == [c.value for c in ImportCategory]


@pytest.mark.asyncio
async def test_the_scan_names_every_item_by_a_fingerprint_the_server_derives(make_client):
    """The id a pick sends back is minted HERE, from source + category + key, and a second
    scan mints the same one — so an id taken from one scan still names the item in the next."""
    async with make_client() as client:
        first = await _scan(client)
        second = await _scan(client)

    items = _items(first)
    assert [i["fingerprint"] for i in items] == [i["fingerprint"] for i in _items(second)]
    for item in items:
        assert item["fingerprint"] == fingerprint_of(item["source"], item["category"], item["key"])
    assert len({i["fingerprint"] for i in items}) == len(items)


@pytest.mark.asyncio
async def test_each_item_says_what_was_withheld_from_it(make_client):
    """The MCP server loses its API key on the way over; the user sees it on THAT row, so they
    know which server needs its key entered again."""
    async with make_client() as client:
        body = await _scan(client)

    by_key = {i["key"]: i for i in _items(body)}
    assert by_key["weather"]["secrets_skipped"] == 1
    assert by_key["CLAUDE.md"]["redactions"] >= 1


@pytest.mark.asyncio
async def test_the_scan_shows_a_conflict_before_anything_is_imported(make_client, home):
    """The step must be able to say "you already have a different one" BEFORE the user picks,
    so the scan reads the destination — through the planner the writer uses — and writes
    nothing while doing it."""
    (home / "mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "mine"}}}), encoding="utf-8"
    )
    before = (home / "mcp.json").read_bytes()
    async with make_client() as client:
        body = await _scan(client)

    weather = next(i for i in _items(body) if i["key"] == "weather")
    assert weather["state"] == "conflict"
    assert "kept" in weather["detail"]
    assert weather["destination"] == "mcp.json#mcpServers.weather"
    assert (home / "mcp.json").read_bytes() == before


@pytest.mark.asyncio
async def test_planted_secret_appears_nowhere_in_the_scan_response(make_client):
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import")
        raw = await resp.text()
    assert SECRET not in raw
    # Vacuity: the response really did carry the file the secret was planted in.
    assert "CLAUDE.md" in raw or "instructions" in raw


@pytest.mark.asyncio
async def test_the_scan_writes_nothing_to_the_home(make_client, home):
    before = sorted(p.name for p in home.rglob("*"))
    async with make_client() as client:
        assert (await client.get("/api/onboarding/import")).status == 200
    assert sorted(p.name for p in home.rglob("*")) == before


# ── 2. the import ─────────────────────────────────────────────────────────────


async def _import(client, **body):
    resp = await client.post("/api/onboarding/import", json=body)
    return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_a_pick_imports_exactly_those_items(make_client, home):
    async with make_client() as client:
        scan = await _scan(client)
        weather = _fingerprint(scan, "mcp_servers")
        status, report = await _import(client, fingerprints=[weather])
    assert status == 200
    assert [(r["fingerprint"], r["outcome"]) for r in report["results"]] == [(weather, "imported")]
    # The MCP entry really landed in the user-owned override file…
    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert "weather" in mcp["mcpServers"]
    assert report["results"][0]["destination"]
    # …and the instructions the user left out did not, and the report says they were left out.
    assert not (home / "workspace" / "memory" / "imported").exists()
    left_out = {row["fingerprint"]: row["state"] for row in report["unselected"]}
    assert left_out == {_fingerprint(scan, "instructions"): "new"}
    assert report["missing"] == []


@pytest.mark.asyncio
async def test_a_pick_the_rescan_no_longer_finds_is_reported_not_dropped(
    make_client, home, foreign
):
    """The item was on the screen when the user picked it, and gone from the other tool by the
    time they pressed Import. The re-scan is the truth: nothing is imported for it, and the
    report NAMES it, so the step can say so instead of showing one success fewer."""
    async with make_client() as client:
        scan = await _scan(client)
        instructions, weather = _fingerprint(scan, "instructions"), _fingerprint(
            scan, "mcp_servers"
        )
        (foreign / "CLAUDE.md").unlink()
        status, report = await _import(client, fingerprints=[instructions, weather])

    assert status == 200
    assert [r["fingerprint"] for r in report["results"]] == [weather]
    assert report["missing"] == [instructions]
    assert not (home / "workspace" / "memory" / "imported").exists()


@pytest.mark.asyncio
async def test_a_well_formed_fingerprint_no_scan_ever_minted_imports_nothing(make_client, home):
    """Shape-valid but unknown: allowed past validation, refused by the allowlist — the re-scan
    — and reported, with a 200, because the request was well-formed and the answer is real."""
    stranger = "0123456789abcdef"
    async with make_client() as client:
        status, report = await _import(client, fingerprints=[stranger])
    assert status == 200
    assert report["results"] == []
    assert report["missing"] == [stranger]
    assert not (home / "mcp.json").exists()


@pytest.mark.asyncio
async def test_content_in_the_request_is_ignored(make_client, home, tmp_path):
    """The attack the id-only wire exists to defeat, attempted: the request carries the chosen
    fingerprint AND an item-shaped object naming another directory, a body and a payload, plus
    the old selection axes. None of it may reach the home — what lands is the server's OWN
    re-scan of the foreign root, byte for byte."""
    elsewhere = tmp_path / "not-a-source"
    elsewhere.mkdir()
    (elsewhere / "SKILL.md").write_text("---\nname: smuggled\n---\nEVIL-BODY\n", encoding="utf-8")
    async with make_client() as client:
        scan = await _scan(client)
        weather = _fingerprint(scan, "mcp_servers")
        status, report = await _import(
            client,
            fingerprints=[weather],
            items=[
                {
                    "fingerprint": weather,
                    "category": "skills",
                    "key": "smuggled",
                    "path": str(elsewhere),
                    "text": "EVIL-BODY",
                    "payload": {"command": "evil-command"},
                }
            ],
            sources=["codex"],
            categories=["skills"],
            payload={"command": "evil-command"},
        )

    assert status == 200
    assert [(r["key"], r["outcome"]) for r in report["results"]] == [("weather", "imported")]
    mcp = json.loads((home / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["weather"]["command"] == "npx"
    blob = _bytes_under(home)
    assert b"EVIL-BODY" not in blob and b"evil-command" not in blob and b"smuggled" not in blob
    assert not (home / "skills").exists()


@pytest.mark.asyncio
async def test_planted_secret_never_reaches_the_home_through_the_route(make_client, home):
    async with make_client() as client:
        scan = await _scan(client)
        status, report = await _import(
            client, fingerprints=[i["fingerprint"] for i in _items(scan)]
        )
    assert status == 200
    assert report["counts"]["imported"] >= 1
    assert SECRET.encode() not in _bytes_under(home)
    # The user is TOLD something was withheld — a count, never the value.
    assert report["secrets_skipped"] + report["redactions"] >= 1
    assert all(SECRET not in note for note in report["notes"])


@pytest.mark.asyncio
async def test_reentry_marks_already_imported_items_existing(make_client):
    """The atom's re-entry clause, over the wire: import, then scan again."""
    async with make_client() as client:
        scan = await _scan(client)
        status, _ = await _import(client, fingerprints=[_fingerprint(scan, "mcp_servers")])
        assert status == 200
        again = await _scan(client)

    mcp_items = [i for i in _items(again) if i["category"] == "mcp_servers"]
    other = [i for i in _items(again) if i["category"] != "mcp_servers"]
    assert mcp_items and all(i["state"] == "existing" for i in mcp_items)
    # Only what was imported is marked: a blanket "existing" would be just as wrong.
    assert other and all(i["state"] == "new" for i in other)


@pytest.mark.asyncio
async def test_an_imported_item_the_user_since_deleted_is_offered_again(make_client, home):
    """The state is read off the DESTINATION, not the import ledger. The ledger still remembers
    writing the server after the user removed it from `mcp.json`, so a ledger-derived flag called
    it "already imported" while importing it would in fact bring it back."""
    async with make_client() as client:
        pick = [_fingerprint(await _scan(client), "mcp_servers")]
        assert (await _import(client, fingerprints=pick))[0] == 200
        (home / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        again = await _scan(client)
        status, report = await _import(client, fingerprints=pick)

    weather = next(i for i in _items(again) if i["category"] == "mcp_servers")
    assert weather["state"] == "new"
    assert (
        status == 200 and report["counts"]["imported"] == 1
    ), "and importing it does bring it back"


@pytest.mark.asyncio
async def test_reimport_reports_existing_and_imports_nothing(make_client):
    async with make_client() as client:
        pick = [_fingerprint(await _scan(client), "mcp_servers")]
        first = (await _import(client, fingerprints=pick))[1]
        second = (await _import(client, fingerprints=pick))[1]
    assert first["counts"]["imported"] >= 1
    assert second["counts"]["imported"] == 0
    assert second["counts"]["existing"] == first["counts"]["imported"]


# ── 3. failures are reported, never swallowed ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_write_failure_is_reported_with_the_secret_redacted(make_client, monkeypatch):
    """A writer that raises must not become a 200 with a zero in it.

    The exception carries the planted secret on purpose: a path or a value from a
    foreign root can itself look like a credential, so the sentence a user reads is
    screened at the one boundary where an exception becomes a string.
    """

    def boom(*_a, **_kw):
        raise OSError(f"cannot write /tmp/x?key={SECRET}")

    monkeypatch.setattr("personalclaw.onboarding_import.run_import", boom)
    async with make_client() as client:
        pick = [_fingerprint(await _scan(client), "mcp_servers")]
        resp = await client.post("/api/onboarding/import", json={"fingerprints": pick})
        assert resp.status == 500
        raw = await resp.text()
        body = json.loads(raw)

    assert body["error"]["code"] == "onboarding_import_failed"
    assert "cannot write" in body["error"]["message"], "the failure's own words are the point"
    assert SECRET not in raw
    # And it says the retry is safe, because the ledger recorded whatever landed.
    assert "again" in body["error"]["message"]


@pytest.mark.asyncio
async def test_a_scan_failure_is_reported_rather_than_rendering_an_empty_step(
    make_client, monkeypatch
):
    def boom(*_a, **_kw):
        raise OSError("the foreign root is unreadable")

    monkeypatch.setattr("personalclaw.onboarding_import.scan_all", boom)
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import")
        body = await resp.json()
    assert resp.status == 500
    assert body["error"]["code"] == "onboarding_import_failed"
    assert "unreadable" in body["error"]["message"]


# ── 4. the pick is validated, not trusted ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "../../../../etc/passwd",
        "/Users/someone/.ssh",
        "ABCDEF0123456789",  # the right length, the wrong alphabet
        "0123456789abcde",  # one short
        "0123456789abcdef0",  # one long
        "0123456789abcdef\n",
    ],
)
async def test_a_malformed_fingerprint_is_refused_before_anything_is_read(
    make_client, home, monkeypatch, bad
):
    """Refused by SHAPE, before a scan runs — the scan is not even called — and the refusal
    does not echo the caller's string back: a count, the expected shape, nothing else."""

    def must_not_scan(*_a, **_kw):
        raise AssertionError("a malformed pick reached the scanner")

    monkeypatch.setattr("personalclaw.onboarding_import.scan_all", must_not_scan)
    async with make_client() as client:
        resp = await client.post(
            "/api/onboarding/import", json={"fingerprints": ["0123456789abcdef", bad]}
        )
        body = await resp.json()
    assert resp.status == 400
    assert body["error"]["code"] == "bad_request"
    assert "1 of the 2 entries" in body["error"]["message"]
    assert bad.strip() not in body["error"]["message"]
    assert not (home / "mcp.json").exists(), "a refused request must write nothing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [{}, {"fingerprints": []}, {"sources": ["claude_code"], "categories": ["mcp_servers"]}],
)
async def test_an_absent_or_empty_pick_is_refused_rather_than_importing_nothing(make_client, body):
    """An empty or absent pick is a request for no work; answering `0 imported` would look like
    a successful import that simply found nothing. The retired two-axis body is one of these:
    it names no item, so it is refused rather than read as "everything"."""
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json=body)
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert "fingerprints" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_a_pick_larger_than_any_real_setup_is_refused(make_client, monkeypatch):
    def must_not_scan(*_a, **_kw):
        raise AssertionError("an oversized pick reached the scanner")

    monkeypatch.setattr("personalclaw.onboarding_import.scan_all", must_not_scan)
    pick = [f"{n:016x}" for n in range(10_001)]
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json={"fingerprints": pick})
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert "10,000" in payload["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [
        ({"fingerprints": "0123456789abcdef"}, "bad_request"),
        ({"fingerprints": [1234567890123456]}, "bad_request"),
        ({"fingerprints": {"0123456789abcdef": {"text": "x"}}}, "bad_request"),
        ([], "invalid_body"),
    ],
)
async def test_a_malformed_body_is_a_400_not_a_500(make_client, body, code):
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json=body)
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == code


@pytest.mark.asyncio
async def test_unparseable_json_is_a_400(make_client):
    async with make_client() as client:
        resp = await client.post(
            "/api/onboarding/import",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == "invalid_json"


# ── 5. the routes are MOUNTED, not merely defined ─────────────────────────────


def test_both_routes_resolve_on_a_registered_app():
    app = web.Application()
    register_onboarding_import_routes(app)
    assert {(r.method, r.resource.canonical) for r in app.router.routes()} >= {
        ("GET", "/api/onboarding/import"),
        ("POST", "/api/onboarding/import"),
    }


def _registrars_called_by_the_gateway() -> set[str]:
    """Every ``register_*(app)`` call the gateway's app builder makes, from the AST.

    Static, because booting ``start_dashboard`` has heavy security-critical startup
    side effects (extension load, binding migration) — the same reason
    ``test_api_manifest_drift`` audits routes from the AST rather than a live table.
    A registrar that is defined but never CALLED is a route that exists in a module and
    on no running server, which is the failure this guard exists for.
    """
    import personalclaw.dashboard.server as server_mod

    tree = ast.parse(Path(server_mod.__file__).read_text(encoding="utf-8"))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id.startswith("register_")
    }


def test_the_gateway_builder_mounts_the_import_routes():
    called = _registrars_called_by_the_gateway()
    # Vacuity: the walk can find a registrar (a long-mounted neighbour) and does not
    # invent one — so a green here means the call is really in the builder.
    assert "register_pack_routes" in called
    assert "register_nothing_at_all_routes" not in called
    assert "register_onboarding_import_routes" in called
