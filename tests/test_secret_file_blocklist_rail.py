"""#354 — the files surface must refuse every secret PersonalClaw itself writes.

**The bug was not a missing string; it was a hand-maintained list.** ``session_key`` — the HMAC
key that signs every dashboard session token — was documented as "the signing key" in
``dashboard/session_store.py`` and named a secret in ``security.py``, and the dashboard
blocklist still read it out through ``GET /api/file-read``. It had drifted once already
(``sel_hmac.key``/``.local_secret``/``telemetry_salt`` were added after #643), which is the
tell: a list that must be remembered is a list that will be forgotten.

So this file does not assert "``session_key`` is blocked". Any test that spells the names out
is a FOURTH hand-copy of them and cannot catch the next omission — it only knows what its own
author remembered, which is the same failure one level up. Instead it derives the names from
their real sources and asserts coverage:

1. :class:`TestAuthLayerFilesAreProtected` asks the AUTH LAYER where it keeps its files — every
   zero-arg ``*_path()`` helper the auth modules export — and requires each to be refused. This
   is the rail the issue asked for ("a test asserting every file the auth layer writes is
   blocklisted"), and it is what found the ``auth/`` directory: ``auth/credentials.json`` (the
   argon2id password hash), ``auth/enroll_codes.json`` and ``auth/pair_codes.json`` (live
   redeemable device codes) all answered ``200`` with their contents — ``session_key``'s exact
   omission, one directory over.
2. :class:`TestDeclarationIsTheOnlySource` requires the guard to be DERIVED: a synthetic name
   added to the declaration in ``security.py`` must be refused by the dashboard without any
   edit to ``handlers/files.py``. That is the property that makes the omission structurally
   impossible instead of fixed once.
3. :class:`TestRefusalsAreFailClosedAndQuiet` covers the two routes at the wire, including the
   error-shape half: a refusal must not echo the resolved absolute path or any file content.
4. :class:`TestVacuityFloor` keeps the whole file honest — an ordinary file must still be
   readable and an ordinary in-root path must still be revealable, so "refuse everything"
   cannot pass as a fix.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers.files import (
    _validate_dashboard_path,
    api_file_read,
    api_reveal_path,
)
from personalclaw.security import (
    HOME_SECRET_DIRS,
    HOME_SECRET_FILE_BASENAMES,
    OWN_SECRET_BASENAMES,
    is_sensitive_path,
)

#: Modules that own PersonalClaw's authentication state. Each exports zero-arg ``*_path()``
#: helpers naming the files it writes; the rail reads those rather than a copied list, so a new
#: auth file is covered on the day its path helper appears.
_AUTH_LAYER_MODULES = (
    "personalclaw.dashboard.session_store",
    "personalclaw.auth.credentials",
    "personalclaw.auth.enrollment",
    "personalclaw.auth.pairing",
)

#: Written into every seeded secret so a refusal body can be checked for LEAKAGE, not just for
#: a status code. A 400 that quotes the bytes it refused is not a refusal.
_MARKER = "ZZ-SECRET-MARKER-DO-NOT-DISCLOSE"


def _auth_layer_paths() -> dict[str, Path]:
    """``module.helper`` → the absolute path that helper returns, for the active home.

    Resolved by CALLING the helpers, so the location comes from the auth layer too — a file
    moved into a new subdirectory keeps its coverage asserted without this test being edited.
    """
    import importlib

    found: dict[str, Path] = {}
    for mod_name in _AUTH_LAYER_MODULES:
        mod = importlib.import_module(mod_name)
        for attr in sorted(vars(mod)):
            if not attr.endswith("_path") or attr.startswith("_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn) or inspect.signature(fn).parameters:
                continue
            result = fn()
            if isinstance(result, Path):
                found[f"{mod_name.rsplit('.', 1)[-1]}.{attr}"] = result
    return found


def _secret_targets(home: Path) -> dict[str, Path]:
    """Every declared secret, materialised under *home*, keyed by a readable label.

    Derived from ``security.py``'s two exported sets plus one probe file inside each secret
    DIRECTORY — a subtree guard is only meaningful if entries beneath it are refused.
    """
    targets: dict[str, Path] = {}
    for name in sorted(OWN_SECRET_BASENAMES | HOME_SECRET_FILE_BASENAMES):
        targets[name] = home / name
    for dirname in sorted(HOME_SECRET_DIRS):
        targets[f"{dirname}/probe.json"] = home / dirname / "probe.json"
    return targets


@pytest.fixture
def secret_home(tmp_path, monkeypatch):
    """*tmp_path* is both the active ``PERSONALCLAW_HOME`` and the only dashboard root.

    Both halves matter. The home makes the location tier resolve here (it reads
    ``PERSONALCLAW_HOME`` per call), and the root makes the path pass the allowlist — so a
    refusal in these tests can only come from the secret guards, never from being out of root.
    That is what the ``crons.json`` control below pins down.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(
        "personalclaw.dashboard.handlers.files._dashboard_roots",
        lambda: [("Home", str(tmp_path))],
    )
    return tmp_path


@pytest.fixture
def seeded_home(secret_home):
    """*secret_home* with every declared secret written as a real file holding ``_MARKER``.

    Real files, not just paths: the identity (inode) layer of the guard needs something to
    ``stat``, and a leak check needs bytes that could actually come back.
    """
    for target in _secret_targets(secret_home).values():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{_MARKER}\n")
    return secret_home


@pytest.fixture
def mock_sel():
    with patch("personalclaw.sel.sel") as m:
        m.return_value = MagicMock()
        yield m.return_value


def _app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/file-read", api_file_read)
    app.router.add_post("/api/reveal", api_reveal_path)
    return app


# ── 1. the rail: ask the auth layer, do not copy its names ────────────────────


class TestAuthLayerFilesAreProtected:
    """Every file the authentication layer writes must be refused by the files surface.

    Derived from the auth modules' own ``*_path()`` helpers. This is the test that would have
    caught #354 the day ``session_store.py`` was written, and it is the test that caught
    ``auth/`` while fixing #354.
    """

    def test_the_auth_layer_declares_the_files_this_rail_covers(self, secret_home):
        """Guard the rail itself: if the discovery finds nothing, everything below is vacuous.

        A rail that silently enumerates an empty set passes forever. The floor is deliberately
        stated as a MINIMUM rather than an exact list, so adding an auth file does not fail
        here — it fails in the coverage test below if it is unprotected, which is the point.
        """
        found = _auth_layer_paths()
        assert len(found) >= 5, f"auth-layer path discovery returned too little: {found}"
        names = {p.name for p in found.values()}
        # The signing key is the subject of #354; the password hash and both code stores are
        # what the rail found next. Named here as a floor on the DISCOVERY, not as the guard's
        # source of truth.
        assert {"session_key", "credentials.json", "enroll_codes.json", "pair_codes.json"} <= names

    def test_every_auth_layer_file_is_refused_by_the_dashboard_guard(self, secret_home):
        """``_validate_dashboard_path`` — the one function all 16 path-taking routes call.

        Asserted at the guard rather than per route because that is where the single decision
        is made; the route-level halves are covered at the wire further down.
        """
        leaked = []
        for label, target in _auth_layer_paths().items():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{_MARKER}\n")
            if _validate_dashboard_path(str(target)) is not None:
                leaked.append(f"{label} -> {target.name}")
        assert (
            not leaked
        ), f"auth-layer files readable through the dashboard files surface: {leaked}"

    def test_every_auth_layer_file_is_refused_by_the_shared_path_guard(self, secret_home):
        """``is_sensitive_path`` — the guard the bash hooks, the terminal cwd check and the
        action denylist all consult.

        Both guards, because #643 was exactly the case where one knew and the other did not.
        A secret that only the dashboard refuses is still reachable through the agent.
        """
        leaked = [
            label
            for label, target in _auth_layer_paths().items()
            if not is_sensitive_path(str(target))
        ]
        assert not leaked, f"auth-layer files unknown to is_sensitive_path: {leaked}"


# ── 2. derived, not re-listed ─────────────────────────────────────────────────


class TestDeclarationIsTheOnlySource:
    """The dashboard guard must FOLLOW the declaration in ``security.py``.

    If it carries its own copy of the names, these tests fail — which is the whole ask of
    #354: make the omission structurally impossible rather than fixed once.
    """

    def test_a_name_added_to_the_declaration_is_refused_without_touching_files_py(
        self, secret_home, monkeypatch
    ):
        """Add a synthetic secret to ``HOME_SECRET_FILE_BASENAMES``; the dashboard must refuse
        it immediately. Under the old hand-copied list this could only pass if someone also
        edited ``handlers/files.py`` — i.e. it could not pass at all."""
        import personalclaw.security as sec

        synthetic = "zz_future_signing_key"
        target = secret_home / synthetic
        target.write_text(f"{_MARKER}\n")
        assert _validate_dashboard_path(str(target)) is not None, "control: not blocked yet"

        monkeypatch.setattr(
            sec, "HOME_SECRET_FILE_BASENAMES", sec.HOME_SECRET_FILE_BASENAMES | {synthetic}
        )
        assert (
            _validate_dashboard_path(str(target)) is None
        ), "the dashboard blocklist is not derived from security.HOME_SECRET_FILE_BASENAMES"

    def test_a_dir_added_to_the_declaration_covers_its_whole_subtree(
        self, secret_home, monkeypatch
    ):
        """The subtree half. A directory entry must refuse files BENEATH it, including one
        nobody has written yet — that is why ``auth/`` is a dir entry and not three names."""
        import personalclaw.security as sec

        target = secret_home / "zz_future_auth" / "not_yet_invented.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{_MARKER}\n")
        assert _validate_dashboard_path(str(target)) is not None, "control: not blocked yet"

        monkeypatch.setattr(sec, "HOME_SECRET_DIRS", sec.HOME_SECRET_DIRS | {"zz_future_auth"})
        monkeypatch.setattr(
            sec,
            "_SENSITIVE_PCLAW_HOME_ENTRIES",
            tuple(sorted(sec.HOME_SECRET_FILE_BASENAMES | sec.HOME_SECRET_DIRS)),
        )
        assert (
            _validate_dashboard_path(str(target)) is None
        ), "a secret directory does not cover entries beneath it"

    def test_the_union_is_not_maintained_separately(self):
        """``_SENSITIVE_PCLAW_HOME_ENTRIES`` must BE the union of the two exported sets.

        It is the tuple ~70 call sites consume. If it were maintained beside them, the two
        halves could disagree, which is #354's shape a third time.
        """
        import personalclaw.security as sec

        assert set(sec._SENSITIVE_PCLAW_HOME_ENTRIES) == set(
            sec.HOME_SECRET_FILE_BASENAMES | sec.HOME_SECRET_DIRS
        )


# ── 3. both routes, fail closed, no leakage ──────────────────────────────────


class TestRefusalsAreFailClosedAndQuiet:
    """Every declared secret is refused at the wire by both path-taking routes under test,
    with a 4xx that discloses neither the resolved absolute path nor any file content."""

    @pytest.mark.asyncio
    async def test_file_read_refuses_every_declared_secret(self, seeded_home, mock_sel):
        async with TestClient(TestServer(_app())) as client:
            leaked = []
            for label, target in _secret_targets(seeded_home).items():
                resp = await client.get("/api/file-read", params={"path": str(target)})
                body = await resp.text()
                if resp.status < 400 or _MARKER in body:
                    leaked.append(f"{label} ({resp.status})")
            assert not leaked, f"/api/file-read disclosed declared secrets: {leaked}"

    @pytest.mark.asyncio
    async def test_reveal_refuses_every_declared_secret_and_never_spawns(
        self, seeded_home, mock_sel
    ):
        """``/api/reveal`` hands its path to ``open``/``xdg-open``, so the assertion is not
        only the status — nothing may be SPAWNED. A 403 that already launched the file has
        refused nothing."""
        with patch("subprocess.Popen") as popen:
            async with TestClient(TestServer(_app())) as client:
                leaked = []
                for label, target in _secret_targets(seeded_home).items():
                    resp = await client.post(
                        "/api/reveal", json={"path": str(target), "action": "reveal"}
                    )
                    if resp.status < 400:
                        leaked.append(f"{label} ({resp.status})")
                assert not leaked, f"/api/reveal accepted declared secrets: {leaked}"
            popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_reveal_refuses_a_path_outside_every_root(self, secret_home, mock_sel):
        """Fail closed on the unknowable case: a perfectly ordinary file that simply is not in
        any root the dashboard surfaces. It carries no ``..`` and is not sensitive, which is
        precisely why the two checks that predate the allowlist did not catch it (#655)."""
        outside = Path(os.path.realpath(str(secret_home.parent))) / "zz-outside.txt"
        outside.write_text("ordinary\n")
        with patch("subprocess.Popen") as popen:
            async with TestClient(TestServer(_app())) as client:
                resp = await client.post(
                    "/api/reveal", json={"path": str(outside), "action": "reveal"}
                )
                assert resp.status == 400
            popen.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_refusal_body_names_neither_the_path_nor_the_contents(
        self, seeded_home, mock_sel
    ):
        """The error-shape half of fail-closed: a refusal must not become an oracle. Neither
        the resolved absolute path (which would confirm the file's location on a gateway whose
        home the caller is guessing) nor the bytes may appear in the response."""
        target = seeded_home / "session_key"
        async with TestClient(TestServer(_app())) as client:
            read = await client.get("/api/file-read", params={"path": str(target)})
            read_body = await read.text()
            reveal = await client.post(
                "/api/reveal", json={"path": str(target), "action": "reveal"}
            )
            reveal_body = await reveal.text()

        for label, body in (("file-read", read_body), ("reveal", reveal_body)):
            assert _MARKER not in body, f"{label} echoed file contents"
            assert str(target) not in body, f"{label} echoed the resolved path"
            assert str(seeded_home) not in body, f"{label} echoed the resolved home"
            # A stack trace or module name would tell the caller how the refusal was reached.
            assert "Traceback" not in body and "personalclaw" not in body


# ── 4. vacuity floor ─────────────────────────────────────────────────────────


class TestVacuityFloor:
    """Without these, a guard that refuses EVERYTHING would satisfy every test above."""

    @pytest.mark.asyncio
    async def test_an_ordinary_file_in_root_is_still_served(self, seeded_home, mock_sel):
        """``crons.json`` is real PersonalClaw home state the explorer legitimately shows —
        an ordinary file sitting in the same directory as the refused secrets, so it isolates
        the secret guards from the root allowlist."""
        ordinary = seeded_home / "crons.json"
        ordinary.write_text('{"jobs": []}\n')
        async with TestClient(TestServer(_app())) as client:
            resp = await client.get("/api/file-read", params={"path": str(ordinary)})
            assert resp.status == 200
            assert '{"jobs": []}' in await resp.text()

    @pytest.mark.asyncio
    async def test_an_ordinary_file_in_root_is_still_revealable(self, seeded_home, mock_sel):
        """The reveal counterpart: the explorer's "Reveal in Finder" button must still work.
        ``Popen`` is patched, so this asserts the decision to spawn without spawning."""
        ordinary = seeded_home / "notes.md"
        ordinary.write_text("# notes\n")
        with (
            patch("subprocess.Popen") as popen,
            patch("shutil.which", return_value="/usr/bin/xdg-open"),
        ):
            async with TestClient(TestServer(_app())) as client:
                resp = await client.post(
                    "/api/reveal", json={"path": str(ordinary), "action": "reveal"}
                )
                assert resp.status == 200
                assert json.loads(await resp.text()) == {"ok": True}
            popen.assert_called_once()

    def test_an_ordinary_name_that_merely_resembles_a_secret_is_allowed(self, secret_home):
        """The name tier must not over-block. A user's own ``my_session_key.md`` or a
        ``credentials`` folder inside a WORKSPACE is not PersonalClaw's auth material — which
        is why the directory entries are location-scoped rather than basename rules."""
        for ok_name in ("my_session_key.md", "sessions.json.bak", "authors.md"):
            target = secret_home / ok_name
            target.write_text("ok\n")
            assert _validate_dashboard_path(str(target)) is not None, f"{ok_name} over-blocked"
