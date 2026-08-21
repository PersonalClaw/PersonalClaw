"""DAS-9 — the per-file restore at the ROUTE level.

The module-level rails for a path subset live in ``test_durability_state_history``.
What this file exists to prove is the thing only the endpoint can get wrong: that
the two-phase confirmation still BINDS a confirm to the preview the user actually
read once a request can name a subset.

``expected_head`` alone stopped being a sufficient binding the moment ``paths``
existed. The head does not move when the *selection* changes, so a caller could
take a two-file preview and confirm ten files, or take a whole-root preview and
confirm one file, and the head check would wave both through. The user would be
shown one thing and another would be applied — which is exactly the failure the
mandatory preview exists to prevent, arriving through a different door. So the
refusal of a mismatched path set is the load-bearing test here; the rest of the
file keeps it honest:

* the whole-root request must behave EXACTLY as it shipped (a regression there
  breaks a live surface, and every path-set rail would still pass);
* ``null`` and ``[]`` must be the same request, or a client that omits the field
  where another sends an empty array gets spurious refusals;
* a mistyped path must come back as a 400 NAMING the path, distinct from the 404
  about the commit — "one of your paths is wrong" is not an actionable answer;
* a bad sha must still be a 404 even when ``paths`` is present, because the
  handler classifies those two failures out of one exception type and the cheap
  way to get that wrong is to call every failure a path problem.

Every test runs against an isolated home AND an isolated workspace: setting only
``PERSONALCLAW_HOME`` leaves the memory root pointed at the developer's real
``~/workplace``, and ``config_dir`` is pinned too because the handlers resolve
the home through ``service.active_home()``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from personalclaw.durability import state_history as sh

pytestmark = pytest.mark.skipif(not sh.git_available(), reason="git is required for time-travel")


# ── fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    home.mkdir(parents=True, exist_ok=True)
    ws.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))


@pytest.fixture
def home(tmp_path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def ws(tmp_path) -> Path:
    return tmp_path / "ws"


def _app(*, app_token: str = ""):
    from aiohttp import web

    from personalclaw.dashboard.handlers import durability as mod

    @web.middleware
    async def identity(request, handler):
        request["user"] = "owner"
        request["app"] = app_token
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app.router.add_post("/api/durability/history/{root}/{op}", mod.api_durability_history_operate)
    return app


def _client():
    from aiohttp.test_utils import TestClient, TestServer

    return TestClient(TestServer(_app()))


def _git_out(root: sh.HistoryRoot, home: Path, *args: str) -> str:
    gd = sh.git_dir(root, home=home)
    proc = subprocess.run(
        ["git", f"--git-dir={gd}", f"--work-tree={root.worktree}", *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(root.worktree),
    )
    return proc.stdout


CFG = "config.json"
ENT = "entity_settings/owner.json"


@pytest.fixture
def seeded(home, ws, monkeypatch):
    """Two commits on the config root, each touching TWO tracked files.

    Two files is the point: with one file a "subset" rail is vacuous, because
    restoring the subset and restoring the whole root would do the same thing and
    a handler that dropped ``paths`` on the floor would pass every assertion.
    """
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    root = next(r for r in sh.roots(home=home, workspace=ws) if r.id == "config")
    sh.ensure_repo(root, home=home)
    (home / "entity_settings").mkdir(parents=True, exist_ok=True)
    (home / CFG).write_text("cfg-v1\n")
    (home / ENT).write_text("ent-v1\n")
    first = sh.commit(root, home=home)
    (home / CFG).write_text("cfg-v2\n")
    (home / ENT).write_text("ent-v2\n")
    second = sh.commit(root, home=home)
    assert sh.commit_count(root, home=home) == 2
    # Vacuity floor for every subset rail below: the WHOLE-root operation really
    # does span both files, so naming one of them is a genuine narrowing.
    whole = sh.preview(root, first, operation="rollback", home=home)
    assert {f["path"] for f in whole["files"]} == {CFG, ENT}
    return root, first, second


def _on_disk(home: Path) -> tuple[str, str]:
    return (home / CFG).read_text(), (home / ENT).read_text()


async def _preview(client, op: str, sha: str, paths: object = "omit") -> dict:
    body: dict = {"sha": sha}
    if paths != "omit":
        body["paths"] = paths
    resp = await client.post(f"/api/durability/history/config/{op}", json=body)
    assert resp.status == 200, await resp.text()
    return await resp.json()


# ── phase one: a preview that names a subset ───────────────────────────────


class TestPerFilePreview:
    @pytest.mark.asyncio
    async def test_a_preview_with_paths_echoes_them_and_changes_nothing(self, seeded, home):
        _root, first, _second = seeded
        before = _on_disk(home)
        async with _client() as client:
            body = await _preview(client, "rollback", first, [CFG])
        assert body["confirmed"] is False
        assert body["expected_paths"] == [CFG], "phase one must hand back the path-set token"
        assert body["preview"]["paths"] == [CFG]
        assert [f["path"] for f in body["preview"]["files"]] == [
            CFG
        ], "the previewed diff must be the SUBSET, not the whole root"
        assert _on_disk(home) == before, "a preview must not act"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("paths", ["omit", None, []])
    async def test_null_omitted_and_empty_all_mean_the_whole_root(self, seeded, paths):
        _root, first, _second = seeded
        async with _client() as client:
            body = await _preview(client, "rollback", first, paths)
        assert body["expected_paths"] == [], "the whole root is the empty subset, once"
        assert {f["path"] for f in body["preview"]["files"]} == {CFG, ENT}


# ── phase two: the confirm must match the preview it cites ─────────────────


class TestPerFileConfirm:
    @pytest.mark.asyncio
    async def test_a_matching_confirm_restores_only_the_named_paths(self, seeded, home):
        _root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG])
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [CFG],
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 200, await resp.text()
            body = await resp.json()
        assert body["ok"] is True and body["paths"] == [CFG]
        assert _on_disk(home) == (
            "cfg-v1\n",
            "ent-v2\n",
        ), "the named file goes back; the unnamed one keeps its newer content"

    @pytest.mark.asyncio
    async def test_a_reordered_path_set_is_the_same_set(self, seeded, home):
        """Order must not matter: a set, not a sequence."""
        _root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG, ENT])
            assert prev["expected_paths"] == sorted([CFG, ENT])
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [ENT, CFG],
                    "expected_head": prev["expected_head"],
                    "expected_paths": [ENT, CFG],
                },
            )
            assert resp.status == 200, await resp.text()
        assert _on_disk(home) == ("cfg-v1\n", "ent-v1\n")

    @pytest.mark.asyncio
    async def test_a_confirm_that_widens_the_previewed_set_is_refused(self, seeded, home):
        """The load-bearing clause: previewed one file, confirmed the whole root."""
        root, first, _second = seeded
        head_before = _git_out(root, home, "rev-parse", "HEAD")
        before = _on_disk(home)
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG])
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    # No paths at all — i.e. the whole root — behind a one-file preview.
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 409, await resp.text()
            body = await resp.json()
        assert body["error"]["code"] == "preview_paths_mismatch"
        assert _on_disk(home) == before, "the refused call must not act"
        assert _git_out(root, home, "rev-parse", "HEAD") == head_before

    @pytest.mark.asyncio
    async def test_a_confirm_that_narrows_a_whole_root_preview_is_refused(self, seeded, home):
        root, first, _second = seeded
        head_before = _git_out(root, home, "rev-parse", "HEAD")
        before = _on_disk(home)
        async with _client() as client:
            prev = await _preview(client, "rollback", first)
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [CFG],
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 409, await resp.text()
            assert (await resp.json())["error"]["code"] == "preview_paths_mismatch"
        assert _on_disk(home) == before
        assert _git_out(root, home, "rev-parse", "HEAD") == head_before

    @pytest.mark.asyncio
    async def test_a_confirm_naming_a_different_file_is_refused(self, seeded, home):
        root, first, _second = seeded
        head_before = _git_out(root, home, "rev-parse", "HEAD")
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG])
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [ENT],
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 409
            assert (await resp.json())["error"]["code"] == "preview_paths_mismatch"
        assert _on_disk(home) == ("cfg-v2\n", "ent-v2\n")
        assert _git_out(root, home, "rev-parse", "HEAD") == head_before

    @pytest.mark.asyncio
    async def test_a_subset_confirm_without_the_path_token_is_refused(self, seeded, home):
        """A subset confirm has to carry the token, exactly like ``expected_head``."""
        _root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG])
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [CFG],
                    "expected_head": prev["expected_head"],
                },
            )
            assert resp.status == 409
            assert (await resp.json())["error"]["code"] == "preview_paths_mismatch"
        assert _on_disk(home) == ("cfg-v2\n", "ent-v2\n")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("sent", "token"), [("omit", "omit"), (None, []), ([], None)])
    async def test_null_and_empty_never_spuriously_mismatch(self, seeded, home, sent, token):
        """Whole root spelled four ways across the two phases still applies."""
        _root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first)
            payload: dict = {
                "sha": first,
                "confirm": True,
                "expected_head": prev["expected_head"],
            }
            if sent != "omit":
                payload["paths"] = sent
            if token != "omit":
                payload["expected_paths"] = token
            resp = await client.post("/api/durability/history/config/rollback", json=payload)
            assert resp.status == 200, await resp.text()
        assert _on_disk(home) == ("cfg-v1\n", "ent-v1\n")

    @pytest.mark.asyncio
    async def test_a_per_file_revert_keeps_the_unnamed_file(self, seeded, home):
        _root, _first, second = seeded
        async with _client() as client:
            prev = await _preview(client, "revert", second, [CFG])
            resp = await client.post(
                "/api/durability/history/config/revert",
                json={
                    "sha": second,
                    "confirm": True,
                    "paths": [CFG],
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 200, await resp.text()
            body = await resp.json()
        assert body["ok"] is True and body["paths"] == [CFG]
        assert _on_disk(home) == ("cfg-v1\n", "ent-v2\n")


# ── path validation: a typed 400 that names the path ───────────────────────


class TestPathValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad",
        ["../etc/passwd", "/etc/passwd", "entity_settings/../../escape", "~/secrets", ".", ""],
    )
    async def test_an_escaping_path_is_a_400_naming_it(self, seeded, home, bad):
        before = _on_disk(home)
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": seeded[1], "paths": [CFG, bad]},
            )
            assert resp.status == 400, await resp.text()
            body = await resp.json()
        assert body["error"]["code"] == "invalid_path"
        assert body["path"] == bad, "the user has to be told WHICH path"
        assert bad in body["error"]["message"] or repr(bad) in body["error"]["message"]
        assert _on_disk(home) == before

    @pytest.mark.asyncio
    async def test_an_unknown_path_is_a_400_naming_it_not_a_404(self, seeded, home):
        """The module refuses it; the route must not report it as a bad commit."""
        before = _on_disk(home)
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": seeded[1], "paths": ["nope.json"]},
            )
            assert resp.status == 400, await resp.text()
            body = await resp.json()
        assert body["error"]["code"] == "invalid_path"
        assert "nope.json" in body["error"]["message"]
        assert _on_disk(home) == before

    @pytest.mark.asyncio
    async def test_an_unknown_sha_with_paths_is_still_a_404(self, seeded):
        """The classification must not turn every failure into a path complaint."""
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": "f" * 40, "paths": [CFG]},
            )
            assert resp.status == 404, await resp.text()
            assert (await resp.json())["error"]["code"] == "unknown_commit"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["config.json", 7, {"path": "config.json"}, [1], [None]])
    async def test_a_non_list_or_non_string_paths_is_a_400_not_a_coercion(self, seeded, bad):
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": seeded[1], "paths": bad},
            )
            assert resp.status == 400, await resp.text()
            assert (await resp.json())["error"]["code"] == "bad_paths"

    @pytest.mark.asyncio
    async def test_a_malformed_expected_paths_is_refused_too(self, seeded):
        async with _client() as client:
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": seeded[1], "confirm": True, "expected_paths": "config.json"},
            )
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "bad_paths"


# ── the shipped whole-root surface must be untouched ───────────────────────


class TestWholeRootRegression:
    @pytest.mark.asyncio
    async def test_preview_then_confirm_still_works_with_no_paths_field(self, seeded, home):
        _root, first, second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first)
            assert prev["confirmed"] is False and prev["expected_head"]
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": first, "confirm": True, "expected_head": prev["expected_head"]},
            )
            assert resp.status == 200, await resp.text()
            body = await resp.json()
        assert body["ok"] is True and body["prior_head"] == second
        assert body["reload_required"] is True
        assert _on_disk(home) == ("cfg-v1\n", "ent-v1\n")

    @pytest.mark.asyncio
    async def test_a_stale_head_still_wins_over_the_path_check(self, seeded, home):
        """A moved tree is reported as stale, not as a path mismatch."""
        root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG])
            (home / CFG).write_text("cfg-v3\n")
            sh.commit(root, home=home)
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [CFG],
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 409
            body = await resp.json()
        assert body["error"]["code"] == "preview_stale"
        assert body["expected_paths"] == [CFG], "the refusal hands back a usable token"
        assert (home / CFG).read_text() == "cfg-v3\n"

    @pytest.mark.asyncio
    async def test_missing_sha_and_unknown_operation_are_unchanged(self, seeded):
        async with _client() as client:
            resp = await client.post("/api/durability/history/config/rollback", json={})
            assert resp.status == 400
            assert (await resp.json())["error"]["code"] == "sha_required"

            resp = await client.post(
                "/api/durability/history/config/obliterate", json={"sha": seeded[1]}
            )
            assert resp.status == 404
            assert (await resp.json())["error"]["code"] == "unknown_operation"

    @pytest.mark.asyncio
    async def test_an_app_scoped_caller_is_still_refused(self, seeded):
        from aiohttp.test_utils import TestClient, TestServer

        async with TestClient(TestServer(_app(app_token="notes"))) as client:
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": seeded[1], "paths": [CFG]},
            )
            assert resp.status == 403
            assert (await resp.json())["error"]["code"] == "owner_only"


# ── the audit line ─────────────────────────────────────────────────────────


class TestAudit:
    @pytest.mark.asyncio
    async def test_the_audit_line_carries_the_count_not_the_file_names(
        self, seeded, home, monkeypatch
    ):
        from personalclaw.dashboard.handlers import durability as mod

        seen: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            mod,
            "_audit_api",
            lambda request, op, outcome, resources: seen.append((op, outcome, resources)),
        )
        _root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first, [CFG])
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={
                    "sha": first,
                    "confirm": True,
                    "paths": [CFG],
                    "expected_head": prev["expected_head"],
                    "expected_paths": prev["expected_paths"],
                },
            )
            assert resp.status == 200, await resp.text()
        assert seen, "the applied operation must be audited"
        op, outcome, resources = seen[-1]
        assert (op, outcome) == ("durability.history_rollback", "allowed")
        assert "paths=1" in resources, "an audit reader must see it was a SUBSET"
        assert CFG not in resources, "an audit line is not a place for user file names"

    @pytest.mark.asyncio
    async def test_a_whole_root_operation_audits_as_zero_paths(self, seeded, monkeypatch):
        from personalclaw.dashboard.handlers import durability as mod

        seen: list[str] = []
        monkeypatch.setattr(
            mod,
            "_audit_api",
            lambda request, op, outcome, resources: seen.append(resources),
        )
        _root, first, _second = seeded
        async with _client() as client:
            prev = await _preview(client, "rollback", first)
            resp = await client.post(
                "/api/durability/history/config/rollback",
                json={"sha": first, "confirm": True, "expected_head": prev["expected_head"]},
            )
            assert resp.status == 200, await resp.text()
        assert "paths=0" in seen[-1], "zero is how a reader spots the whole-root case"
