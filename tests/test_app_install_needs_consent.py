"""Every install waits for consent — and consent is to exactly the bytes the owner reviewed.

Measured on the real image (72 Store apps): a Store card installed with NO consent screen
whenever the scanner raised no warning — 50 of 65 installs. Growth Tracker (API reach into
projects, tasks and knowledge, an agent grant, a daily cron) and Research Lab (an hourly
background agent) installed in one click; their triggers appeared in ``triggers.json``,
enabled, and the user never saw them. The cause sat on both sides of the wire: the dialog
opened only on a scanner warning, and ``POST /api/apps`` committed a clean bundle on a bare
request. These pin the server half, because it is the half every client reaches — a
script, an app holding ``/api/apps``, or a future surface that forgets the dialog.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import app_manager, backend_runtime, manager
from personalclaw.dashboard.handlers.apps import register_app_routes
from personalclaw.triggers.store import TriggerStore


@asynccontextmanager
async def _client(tmp_path: Path):
    from personalclaw import inbox as _inbox
    from personalclaw.apps import catalog as _catalog
    from personalclaw.providers import entity_routes as _er

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
        patch.object(_catalog, "config_dir", return_value=tmp_path),
        patch.object(_er, "config_dir", return_value=tmp_path),
        patch.object(_inbox, "config_dir", return_value=tmp_path),
    ):
        backend_runtime._supervisor = backend_runtime.BackendSupervisor()
        app = web.Application()
        register_app_routes(app)
        async with TestClient(TestServer(app)) as client:
            try:
                yield client
            finally:
                backend_runtime.get_backend_supervisor().stop_all()


def _app(
    tmp_path: Path,
    *,
    subdir: str = "src",
    version: str = "1.0.0",
    permissions: dict | None = None,
    crons: list | None = None,
    files: dict[str, str] | None = None,
) -> str:
    """A clean-scanning app shaped like the ones that installed unseen: a daily agent job
    and the ``cron`` permission that switches it on."""
    d = tmp_path / subdir / "digest"
    d.mkdir(parents=True)
    manifest = {
        "name": "digest",
        "version": version,
        "displayName": "Daily Digest",
        "description": "summarises your day",
        "permissions": {"cron": True, "network": False} if permissions is None else permissions,
        "crons": (
            [{"name": "daily-digest", "cron_expr": "3 18 * * *", "message": "summarise my day"}]
            if crons is None
            else crons
        ),
    }
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, body in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body, encoding="utf-8")
    return str(d)


def _app_triggers(home: Path) -> list:
    return [
        row.trigger
        for row in TriggerStore(base_dir=home).load()
        if str(row.trigger.id).startswith("app:")
    ]


async def _installed(client) -> list[str]:
    """The user-installed apps — natives (always on, never installed) are not in question."""
    apps = (await (await client.get("/api/apps")).json())["apps"]
    return [a["name"] for a in apps if not a.get("native")]


async def _review(client, source: str, **extra) -> dict:
    r = await client.post("/api/apps/preview", json={"source": source, **extra})
    assert r.status == 200, await r.text()
    return await r.json()


# ── a request that did not come through consent installs nothing ─────────────────────────


@pytest.mark.asyncio
async def test_a_bare_install_request_installs_nothing_and_answers_with_the_review(tmp_path):
    async with _client(tmp_path) as client:
        src = _app(tmp_path)
        r = await client.post("/api/apps", json={"source": src})

        assert r.status == 409, await r.text()
        body = await r.json()
        assert body["ok"] is False and body["needs_consent"] is True
        # A CLEAN scan is exactly the case that used to commit — it still has to wait.
        assert body["scan"]["verdict"] == "clean"
        # …and the refusal carries what the owner would be consenting to.
        assert body["displayName"] == "Daily Digest"
        assert body["disclosure"]["permissions"]["cron"] is True
        assert [c["name"] for c in body["disclosure"]["crons"]] == ["daily-digest"]
        assert body["disclosure"]["crons"][0]["scheduled"] is True
        assert len(body["consent"]) == 64

        assert await _installed(client) == []
        assert not (tmp_path / "apps" / "digest").exists()
        assert _app_triggers(tmp_path) == [], "an unconsented install scheduled a job"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stand_in",
    [{"confirm": True}, {"consent": True}, {"consent": 1}, {"consent": ""}, {"consent": "   "}],
    ids=["confirm-true", "consent-true", "consent-1", "consent-empty", "consent-blank"],
)
async def test_nothing_that_can_be_sent_blind_is_consent(tmp_path, stand_in):
    """``confirm: true`` WAS the consent flag; a client can send it without ever having
    fetched a review, which is precisely what consent has to rule out."""
    async with _client(tmp_path) as client:
        src = _app(tmp_path)
        r = await client.post("/api/apps", json={"source": src, **stand_in})
        assert r.status == 409, await r.text()
        assert await _installed(client) == []


# ── the review → consent → install path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_reviews_digest_installs_the_app_and_turns_its_job_on(tmp_path):
    async with _client(tmp_path) as client:
        src = _app(tmp_path)
        review = await _review(client, src)
        assert review["needs_consent"] is True and review["ok"] is False

        r = await client.post("/api/apps", json={"source": src, "consent": review["consent"]})
        assert r.status == 201, await r.text()
        # The success names the app for the person told about it — a pasted URL is all an
        # install-from-URL surface has to go on, and "Installed https://…" names nothing.
        installed = await r.json()
        assert (installed["displayName"], installed["version"]) == ("Daily Digest", "1.0.0")
        assert await _installed(client) == ["digest"]
        triggers = _app_triggers(tmp_path)
        assert [t.id for t in triggers] == ["app:digest:daily-digest"]
        assert triggers[0].enabled is True, "the dialog says the job is switched on — so it is"


@pytest.mark.asyncio
async def test_the_review_commits_nothing(tmp_path):
    async with _client(tmp_path) as client:
        await _review(client, _app(tmp_path))
        assert await _installed(client) == []
        assert _app_triggers(tmp_path) == []
        quarantine = tmp_path / "apps" / ".quarantine"
        assert not quarantine.exists() or list(quarantine.iterdir()) == []


@pytest.mark.asyncio
async def test_consent_is_to_the_reviewed_bytes_and_no_others(tmp_path):
    """A source that changes between review and install — a git remote serving a different
    tree to the second clone, or a local folder edited in between — must be reviewed
    again, never waved through on the first review's yes."""
    async with _client(tmp_path) as client:
        src = _app(tmp_path)
        first = await _review(client, src)

        manifest = json.loads((Path(src) / "app.json").read_text())
        manifest["permissions"]["agent"] = True
        manifest["crons"].append({"name": "hourly", "every": 3600, "message": "check in"})
        (Path(src) / "app.json").write_text(json.dumps(manifest), encoding="utf-8")

        r = await client.post("/api/apps", json={"source": src, "consent": first["consent"]})
        assert r.status == 409, await r.text()
        body = await r.json()
        assert "changed after it was reviewed" in body["error"]
        assert body["consent"] != first["consent"]
        assert [c["name"] for c in body["disclosure"]["crons"]] == ["daily-digest", "hourly"]
        assert await _installed(client) == []

        r = await client.post("/api/apps", json={"source": src, "consent": body["consent"]})
        assert r.status == 201, await r.text()


@pytest.mark.asyncio
async def test_a_job_without_the_cron_permission_is_disclosed_as_one_that_will_not_run(
    tmp_path,
):
    """The dialog's "installing turns this job on" and the trigger store read ONE predicate
    (``app_crons.schedules``), so they agree on both sides of the permission."""
    async with _client(tmp_path) as client:
        src = _app(tmp_path, permissions={"network": False})
        review = await _review(client, src)
        assert review["disclosure"]["crons"][0]["scheduled"] is False

        r = await client.post("/api/apps", json={"source": src, "consent": review["consent"]})
        assert r.status == 201, await r.text()
        assert _app_triggers(tmp_path) == []


@pytest.mark.asyncio
async def test_a_refusal_is_a_finished_review_and_an_unreadable_source_is_an_error(tmp_path):
    async with _client(tmp_path) as client:
        evil = _app(tmp_path, files={"scripts/x.sh": "rm -rf / --no-preserve-root\n"})
        review = await _review(client, evil)
        assert review["scan"]["verdict"] == "dangerous"
        assert review["needs_consent"] is False and review["consent"] == ""
        assert "refused" in review["error"]

        r = await client.post("/api/apps/preview", json={"source": str(tmp_path / "nope")})
        assert r.status == 400
        assert (await r.json())["error"]["code"] == "app_source_unresolved"


# ── an update asks again only when what the app gets changes ─────────────────────────────


@pytest.mark.asyncio
async def test_an_update_that_changes_what_the_app_gets_waits_for_consent(tmp_path):
    async with _client(tmp_path) as client:
        v1 = _app(tmp_path)
        r = await client.post(
            "/api/apps", json={"source": v1, "consent": (await _review(client, v1))["consent"]}
        )
        assert r.status == 201
        v2 = _app(
            tmp_path,
            subdir="src2",
            version="2.0.0",
            permissions={"cron": True, "network": False, "agent": True},
        )

        r = await client.post("/api/apps/digest/update", json={"source": v2})
        assert r.status == 409, await r.text()
        body = await r.json()
        assert "agent" not in body["previous"]["permissions"]
        assert body["disclosure"]["permissions"]["agent"] is True
        got = await (await client.get("/api/apps/digest")).json()
        assert got["installed"]["version"] == "1.0.0", "an unconsented update changed the app"

        review = await _review(client, v2, name="digest")
        assert review["needs_consent"] is True and review["previous"] is not None
        r = await client.post(
            "/api/apps/digest/update", json={"source": v2, "consent": review["consent"]}
        )
        assert r.status == 200, await r.text()
        updated = await r.json()
        assert (updated["displayName"], updated["version"]) == ("Daily Digest", "2.0.0")
        got = await (await client.get("/api/apps/digest")).json()
        assert got["installed"]["version"] == "2.0.0"


@pytest.mark.asyncio
async def test_an_update_that_changes_nothing_it_gets_needs_no_consent(tmp_path):
    async with _client(tmp_path) as client:
        v1 = _app(tmp_path)
        r = await client.post(
            "/api/apps", json={"source": v1, "consent": (await _review(client, v1))["consent"]}
        )
        assert r.status == 201
        v2 = _app(tmp_path, subdir="src2", version="1.1.0", files={"notes.md": "a fix\n"})

        review = await _review(client, v2, name="digest")
        assert review["needs_consent"] is False, "nothing new is being agreed to"
        r = await client.post("/api/apps/digest/update", json={"source": v2})
        assert r.status == 200, await r.text()


# ── the lifecycle layer holds the same line for in-process callers ───────────────────────


def test_an_install_without_consent_never_commits_even_on_a_clean_scan(tmp_path, monkeypatch):
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    src = _app(tmp_path)

    res = app_manager.install(src)
    assert res.ok is False and res.needs_consent is True
    assert res.scan is not None and res.scan.verdict.value == "clean"
    assert not (tmp_path / "apps" / "digest").exists()

    assert app_manager.install(src, consent=res.consent).ok
