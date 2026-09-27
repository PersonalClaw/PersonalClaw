"""``/api/onboarding/import`` — the onboarding step's two calls.

PEP-4 shipped the engine with no HTTP surface at all; this is the surface, and these
are the properties that make it safe to hand to a first-run screen. Every test drives
the REAL router (``TestClient`` over the registered app), a FIXTURE foreign root bound
through ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``, and a FIXTURE home bound through
``PERSONALCLAW_HOME`` — the developer's real ``~/.claude`` is never read and their real
``~/.personalclaw`` is never written. The fixture asserts both redirects BIND before any
test body runs, because an isolation lever that silently missed would turn this suite
into a scan of the machine it runs on.

The load-bearing tests, one per clause the change names:

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
import asyncio
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
    # Claude Code's user-scope servers, in `.claude.json` — inside `$CLAUDE_CONFIG_DIR` when set.
    (root / ".claude.json").write_text(
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
        # The gateway's own body ceiling, not aiohttp's 1 MB default: a pick is 19 bytes an item,
        # and a months-long history is tens of thousands of items.
        from personalclaw.dashboard.server import _single_post_ceiling

        app = web.Application(client_max_size=_single_post_ceiling())
        register_onboarding_import_routes(app)
        return TestClient(TestServer(app))

    return _make


def _bytes_under(root: Path, *, but: Path | None = None) -> bytes:
    """Every byte of every file under ``root`` (bar ``but``), concatenated. For secret sweeps."""
    blob = b""
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != but:
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
    """What was left out is said on the row it was left out of. The MCP server loses nothing —
    its API key goes to the credential store, so no key needs entering again — and the key in
    CLAUDE.md's prose is redacted on CLAUDE.md's row."""
    async with make_client() as client:
        body = await _scan(client)

    by_key = {i["key"]: i for i in _items(body)}
    assert by_key["weather"]["secrets_skipped"] == 0
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


async def _finished(client) -> dict:
    """The import job once it has stopped running — waited for, so no write outlives the test's
    home."""
    for _ in range(500):
        resp = await client.get("/api/onboarding/import/job")
        assert resp.status == 200
        job = (await resp.json())["job"]
        if job["status"] != "running":
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("the import job never finished")


async def _import(client, **body):
    """Start an import and wait for it: ``(POST status, the finished job's report)``."""
    resp = await client.post("/api/onboarding/import", json=body)
    started = await resp.json()
    if resp.status != 202:
        return resp.status, started
    assert started["status"] in ("running", "done"), started
    job = await _finished(client)
    assert job["status"] == "done", job
    return resp.status, job["report"]


@pytest.mark.asyncio
async def test_a_pick_imports_exactly_those_items(make_client, home):
    async with make_client() as client:
        scan = await _scan(client)
        weather = _fingerprint(scan, "mcp_servers")
        status, report = await _import(client, fingerprints=[weather])
    assert status == 202
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

    assert status == 202
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
    assert status == 202
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

    assert status == 202
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
    from personalclaw.config.loader import env_path

    assert status == 202
    assert report["counts"]["imported"] >= 1
    # The server's key is in the credential store and in no other byte under the home.
    assert SECRET.encode() not in _bytes_under(home, but=env_path())
    assert SECRET.encode() in env_path().read_bytes()
    # The user is TOLD something was withheld — a count, never the value.
    assert report["secrets_skipped"] + report["redactions"] >= 1
    assert all(SECRET not in note for note in report["notes"])


@pytest.mark.asyncio
async def test_reentry_marks_already_imported_items_existing(make_client):
    """The change's re-entry clause, over the wire: import, then scan again."""
    async with make_client() as client:
        scan = await _scan(client)
        status, _ = await _import(client, fingerprints=[_fingerprint(scan, "mcp_servers")])
        assert status == 202
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
        assert (await _import(client, fingerprints=pick))[0] == 202
        (home / "mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        again = await _scan(client)
        status, report = await _import(client, fingerprints=pick)

    weather = next(i for i in _items(again) if i["category"] == "mcp_servers")
    assert weather["state"] == "new"
    assert (
        status == 202 and report["counts"]["imported"] == 1
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
    """A writer that raises must not become a finished job with a zero in it.

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
        assert resp.status == 202
        job = await _finished(client)
        raw = json.dumps(job)

    assert job["status"] == "failed"
    assert job["report"] is None, "a failed import does not pass for one with a report"
    assert "cannot write" in job["error"], "the failure's own words are the point"
    assert SECRET not in raw
    # And it says the retry is safe: every write is whole or absent.
    assert "again" in job["error"]


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

    from personalclaw.dashboard.handlers.onboarding_import import _MAX_CHOSEN

    monkeypatch.setattr("personalclaw.onboarding_import.scan_all", must_not_scan)
    pick = [f"{n:016x}" for n in range(_MAX_CHOSEN + 1)]
    async with make_client() as client:
        resp = await client.post("/api/onboarding/import", json={"fingerprints": pick})
        payload = await resp.json()
    assert resp.status == 400
    assert payload["error"]["code"] == "invalid_request"
    assert f"{_MAX_CHOSEN:,}" in payload["error"]["message"]


def test_the_largest_pick_is_one_a_months_long_history_fits_in():
    """The cap was 10,000, and a power user's Claude Code + Codex history is 12,161 items once the
    step ticks everything (measured on a synthetic one shaped like it): the step's own default
    choice was refused with a 400. The cap bounds a request, not a person's history."""
    from personalclaw.dashboard.handlers.onboarding_import import _MAX_CHOSEN

    assert _MAX_CHOSEN >= 50_000


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


def test_every_route_resolves_on_a_registered_app():
    app = web.Application()
    register_onboarding_import_routes(app)
    assert {(r.method, r.resource.canonical) for r in app.router.routes()} >= {
        ("GET", "/api/onboarding/import"),
        ("POST", "/api/onboarding/import"),
        ("GET", "/api/onboarding/import/job"),
        ("DELETE", "/api/onboarding/import/job"),
        ("GET", "/api/onboarding/import/stream"),
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


# ── 6. a months-long history: the step answers before every transcript is read ──────────────
#
# Measured on a synthetic power-user history shaped like a real one (12,005 conversation files,
# 5.3 GB): the GET took 41 s, because the scan read every transcript in full before answering,
# and ticking everything (12,161 items) was refused at the old 10,000 cap. So the GET LOOKS — it
# lists each conversation from the start of its file — a reading pass reads the rest in the
# background, and the import is a job with progress and a stop.


def _plant_transcript(foreign: Path, session: str, *, tool_output_lines: int = 3) -> Path:
    """A Claude Code transcript: a prompt, a reply, and tool output after them."""
    folder = foreign / "projects" / "-Users-ada-src-app"
    folder.mkdir(parents=True, exist_ok=True)
    base = {"cwd": "/Users/ada/src/app", "sessionId": session}
    lines = [
        {
            **base,
            "type": "user",
            "timestamp": "2026-08-01T10:00:00.000Z",
            "message": {"role": "user", "content": f"Why is {session} slow?"},
        },
        {
            **base,
            "type": "assistant",
            "timestamp": "2026-08-01T10:00:05.000Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Profiling."}]},
        },
    ] + [
        {
            **base,
            "type": "user",
            "timestamp": "2026-08-01T10:00:06.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 2000}],
            },
        }
        for _ in range(tool_output_lines)
    ]
    path = folder / f"{session}.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


async def _conversations(client) -> tuple[dict, list[dict]]:
    body = await _scan(client)
    return body, [i for i in _items(body) if i["category"] == "conversations"]


@pytest.mark.asyncio
async def test_the_first_answer_arrives_before_the_transcripts_are_read_in_full(
    make_client, foreign, monkeypatch
):
    """The GET answers from the START of each transcript and says it has not read the rest —
    with the reading of the rest held back here, so the answer provably did not wait for it —
    then, once the rest is read, answers again with every count final."""
    import threading
    from dataclasses import replace

    from personalclaw.onboarding_import import registry
    from personalclaw.onboarding_import.sources import claude_code

    for n in range(3):
        _plant_transcript(foreign, f"s{n}")
    release = threading.Event()

    def held_back(path: Path) -> None:
        release.wait(10)
        claude_code.read_in_full(path)

    source = registry.get_source("claude_code")
    monkeypatch.setitem(registry._BY_NAME, "claude_code", replace(source, read_in_full=held_back))
    async with make_client() as client:
        first, conversations = await _conversations(client)
        assert len(conversations) == 3
        assert all(c["provisional"] for c in conversations)
        assert all(c["note"].startswith("Not read in full yet.") for c in conversations)
        assert {c["title"] for c in conversations} == {f"Why is s{n} slow?" for n in range(3)}
        claude = next(s for s in first["sources"] if s["source"] == "claude_code")
        assert claude["reading"] == {"read": 0, "of": 3}
        assert first["reading"]["running"] is True

        release.set()
        for _ in range(250):
            reading = (await _scan(client))["reading"]
            if not reading["running"]:
                break
            await asyncio.sleep(0.02)
        final, conversations = await _conversations(client)

    assert not any(c["provisional"] for c in conversations)
    assert {c["note"] for c in conversations} == {
        "2 messages. Tool calls come over by name; their output does not."
    }
    claude = next(s for s in final["sources"] if s["source"] == "claude_code")
    assert claude["reading"] == {"read": 3, "of": 3}


@pytest.mark.asyncio
async def test_the_import_is_a_job_that_brings_a_looked_at_conversation_over_whole(
    make_client, home, foreign
):
    """A conversation picked from a LOOKING scan is read in full by the import itself: the chat
    it becomes carries every message, and the report counts it."""
    from personalclaw.history import ConversationLog

    _plant_transcript(foreign, "s1")
    async with make_client() as client:
        _body, conversations = await _conversations(client)
        status, report = await _import(client, fingerprints=[conversations[0]["fingerprint"]])
    assert status == 202
    assert [r["outcome"] for r in report["results"]] == ["imported"]
    messages = ConversationLog().read_messages("dashboard_claude-code-s1")
    assert [m["role"] for m in messages] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_one_import_runs_at_a_time(make_client, monkeypatch):
    """A second POST while an import runs is refused and names the running job — so a double
    click, or a second tab, watches the one import rather than racing it."""
    import threading

    import personalclaw.onboarding_import as onboarding_import

    release = threading.Event()
    real = onboarding_import.run_import

    def slow(*args, **kwargs):
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(onboarding_import, "run_import", slow)
    async with make_client() as client:
        pick = [_fingerprint(await _scan(client), "mcp_servers")]
        first = await client.post("/api/onboarding/import", json={"fingerprints": pick})
        second = await client.post("/api/onboarding/import", json={"fingerprints": pick})
        refused = await second.json()
        release.set()
        job = await _finished(client)
    assert (first.status, second.status) == (202, 409)
    assert refused["error"]["code"] == "import_running"
    assert refused["job"]["id"] == job["id"]
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_a_stop_takes_effect_between_two_items_and_the_rest_are_named(
    make_client, home, monkeypatch
):
    """A stop asked for while the first of two items is being written lets that one land, writes
    nothing more, and reports the other as not reached — which importing again brings over."""
    import threading

    from personalclaw.onboarding_import import writers

    writing = threading.Event()
    release = threading.Event()
    real = writers.write_item

    def gated(item):
        writing.set()
        release.wait(10)
        return real(item)

    monkeypatch.setattr(writers, "write_item", gated)
    async with make_client() as client:
        scan = await _scan(client)
        pick = [_fingerprint(scan, "instructions"), _fingerprint(scan, "mcp_servers")]
        assert (
            await client.post("/api/onboarding/import", json={"fingerprints": pick})
        ).status == 202
        assert await asyncio.to_thread(writing.wait, 10)
        stopping = await client.delete("/api/onboarding/import/job")
        assert stopping.status == 202
        assert (await stopping.json())["stopping"] is True
        release.set()
        job = await _finished(client)
        assert not (home / "mcp.json").exists(), "nothing is written past a stop"
        monkeypatch.setattr(writers, "write_item", real)
        again = await _import(client, fingerprints=pick)

    assert job["status"] == "stopped"
    report = job["report"]
    assert [(r["category"], r["outcome"]) for r in report["results"]] == [
        ("instructions", "imported")
    ]
    assert report["not_reached"] == [_fingerprint(scan, "mcp_servers")]
    outcomes = {r["category"]: r["outcome"] for r in again[1]["results"]}
    assert outcomes == {"instructions": "existing", "mcp_servers": "imported"}


@pytest.mark.asyncio
async def test_the_job_route_answers_only_for_an_import_this_gateway_ran(make_client):
    """After a restart there is no job to read: the step says the import stopped, and the next
    scan shows what it wrote as already here."""
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import/job")
        body = await resp.json()
        stop = await client.delete("/api/onboarding/import/job")
    # A first visit asks this too, so "none" is an answer, not an error a browser logs.
    assert (resp.status, body) == (200, {"job": None})
    assert stop.status == 409


@pytest.mark.asyncio
async def test_the_stream_sends_where_the_reading_and_the_import_have_got_to(make_client):
    async with make_client() as client:
        resp = await client.get("/api/onboarding/import/stream")
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        frame = (await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 5)).decode()
        resp.close()
    event, data = frame.strip().split("\n", 1)
    assert event == "event: status"
    status = json.loads(data.removeprefix("data: "))
    assert status["job"] is None
    assert set(status["reading"]) == {"running", "read", "of"}
