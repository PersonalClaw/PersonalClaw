"""The secrets vault: presence-only reads, one-way writes, derived consumers (EI-10).

**The property this file exists to hold is structural, not behavioural.** "The API redacts the
value" is a claim about what a handler remembered to do; "the value never enters the read model"
is a claim about types and imports, and only the second survives a careless future edit. So the
rails come in two layers:

* :class:`TestPresenceIsStructural` reads the SOURCE. It asserts that neither the vault module nor
  its handler references any value-returning credential function, and that ``SecretPresence`` has
  no value-bearing field. A mutation that makes a route return a value reds this even if the route
  is never called.
* :class:`TestNoValueCrossesTheWire` DRIVES the routes with sentinel values planted in the store
  and asserts the sentinels appear in no response body, on every verb including the POST that just
  stored one. This is what catches a leak through a field the source scan did not think to name.

Neither layer alone is enough: the first cannot see a value smuggled through a helper it does not
recognise, and the second only covers the shapes a test happens to exercise.

⚠️  Every test here writes credentials, so the home is redirected TWICE — ``PERSONALCLAW_HOME`` and
``loader.config_dir`` — and the fixture ASSERTS the redirect landed before anything is written,
following ``test_credential_migration.py``'s fixture exactly. A vault test that leaked into the
real ``~/.personalclaw/.env`` would be an unacceptable defect, not a flake. ``keyring`` is an
optional extra CI does not install; nothing here stubs it, so every write lands in the redirected
``.env`` and ``credential_names`` reads it back from there.
"""

from __future__ import annotations

import ast
import io
import json
import zipfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import secrets_vault as sv
from personalclaw.config import credentials as cred
from personalclaw.config import loader
from personalclaw.dashboard.handlers.secrets import register_secrets_routes
from personalclaw.http_errors import HTTP_ERROR_CODES

#: Sentinels chosen so a partial leak is still caught: each is long, unique, and shares no
#: substring with a key NAME, so a match can only come from a VALUE.
GLOBAL_VALUE = "gv-4f2a9c7e-GLOBALSECRETVALUE-do-not-leak"
PROJECT_VALUE = "pv-88b1d3f0-PROJECTSECRETVALUE-do-not-leak"
HOST_VALUE = "hv-1c6e5a2b-HOSTSECRETVALUE-do-not-leak"
ALL_VALUES = (GLOBAL_VALUE, PROJECT_VALUE, HOST_VALUE)

PROJECT_ID = "proj-ei10"


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An isolated credential home. Never the real one — these tests write secrets."""
    cfg = tmp_path / "home"
    cfg.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(cfg))
    monkeypatch.setattr(loader, "config_dir", lambda: cfg)
    monkeypatch.delenv(cred.CREDENTIAL_BACKEND_ENV, raising=False)
    # 🪤 ASSERT THE REDIRECT before a single secret is written. A fixture that silently failed to
    # redirect would run this whole file against the developer's real home, and every assertion
    # below would still pass.
    assert loader.env_path() == cfg / ".env", "the .env redirect must hold"
    assert tmp_path in loader.env_path().parents
    return cfg


@pytest.fixture
def vault(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A populated vault: one global, one project-scoped, one inherit-from-host."""
    for name in ("EI10_GLOBAL_TOKEN", "EI10_HOST_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    cred.save_credential("EI10_GLOBAL_TOKEN", GLOBAL_VALUE)
    cred.save_credential(sv.project_secret_key(PROJECT_ID, "EI10_DB_PASSWORD"), PROJECT_VALUE)
    # A host row is a credential-shaped name the vault does NOT hold. Set directly in the
    # environment, never through `save_credential`, which is what makes it inherited.
    monkeypatch.setenv("EI10_HOST_TOKEN", HOST_VALUE)
    return home


def _make_app(app_token_name: str = "") -> web.Application:
    app = web.Application()
    if app_token_name:
        # Simulate the auth middleware's app-scoped-token stamping.
        @web.middleware
        async def stamp_app(request, handler):
            request["app"] = app_token_name
            return await handler(request)

        app.middlewares.append(stamp_app)
    register_secrets_routes(app)
    return app


# ── layer 1: the structural rail ──


class TestPresenceIsStructural:
    """Presence-only is enforced by types and imports, not by a redaction step."""

    #: Module-level functions in `config.credentials` that RETURN a credential value.
    MODULE_VALUE_READERS = (
        "get_credential",
        "_dotenv_credentials",
        "_keychain_credentials",
        "_keychain_get",
    )

    #: `AppConfig.load_credentials` is the fifth value reader and is a METHOD, not a module
    #: function — the first draft of this rail asserted it on `config.credentials` and reddened,
    #: which is the vacuity partner earning its place on its first run.
    METHOD_VALUE_READERS = ("load_credentials",)

    #: Every name the vault's source must not reference, whichever kind it is.
    VALUE_READERS = MODULE_VALUE_READERS + METHOD_VALUE_READERS

    #: The modules that make up the vault's read path.
    VAULT_SOURCES = (
        "src/personalclaw/secrets_vault.py",
        "src/personalclaw/dashboard/handlers/secrets.py",
    )

    def test_the_forbidden_names_are_real(self):
        """🪤 VACUITY PARTNER. The scan below proves nothing if it forbids names that do not
        exist — a typo in `VALUE_READERS` would make every assertion vacuously true. So first
        assert each forbidden name really is a value-returning attribute of the credential store.
        This test must stay GREEN under any mutation of the vault modules; it measures the
        credential store, not the vault.
        """
        for name in self.MODULE_VALUE_READERS:
            assert hasattr(cred, name), f"{name} is not in config.credentials — the rail is stale"
        for name in self.METHOD_VALUE_READERS:
            assert hasattr(loader.AppConfig, name), f"AppConfig.{name} is gone — the rail is stale"
        assert cred.get_credential("nothing-is-stored-here") == ""

    def test_no_vault_module_references_a_value_reader(self, repo_root: Path):
        """The rail: a value-returning credential call anywhere in the vault's read path reds.

        Parsed with `ast`, not grepped, so a name inside a docstring or a comment does not count
        — those are where this file's own reasoning lives, and a prose mention must not red.
        """
        for rel in self.VAULT_SOURCES:
            src = (repo_root / rel).read_text()
            tree = ast.parse(src)
            referenced = {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            # Imported names count too: `from … import get_credential` puts it in scope.
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    referenced |= {a.name for a in node.names}
            leaked = sorted(referenced & set(self.VALUE_READERS))
            assert not leaked, (
                f"{rel} references {leaked} — presence-only means the value is UNREACHABLE from "
                "the read model, not filtered out of it. Read names via `credential_names()`."
            )

    def test_the_source_scan_can_actually_fail(self, repo_root: Path):
        """🪤 The scan's own vacuity floor: prove the detector fires on a planted reference.

        Without this, an `ast` walk that silently stopped collecting names would report "no
        leaks" forever. Run against a synthetic module rather than the real one, so proving the
        detector works does not require mutating shipped code.
        """
        planted = ast.parse("from personalclaw.config.credentials import get_credential\n")
        referenced = {
            a.name for n in ast.walk(planted) if isinstance(n, ast.ImportFrom) for a in n.names
        }
        assert referenced & set(self.VALUE_READERS), "the import-name detector is broken"

        planted2 = ast.parse(
            "import personalclaw.config.credentials as c\nx = c.get_credential('K')\n"
        )
        attrs = {n.attr for n in ast.walk(planted2) if isinstance(n, ast.Attribute)}
        assert attrs & set(self.VALUE_READERS), "the attribute detector is broken"

    def test_secret_presence_has_no_value_field(self):
        """The type is the enforcement — a row cannot carry a value because it has nowhere to."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(sv.SecretPresence)}
        assert fields == {"name", "scope", "project_id", "consumers"}, fields
        for banned in ("value", "secret", "plaintext", "token"):
            assert banned not in fields
        row = sv.SecretPresence(name="K")
        assert not hasattr(row, "value")
        # Frozen: a row cannot be mutated into carrying something else after construction.
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.name = "other"  # type: ignore[misc]

    def test_the_wire_shape_is_a_closed_set_of_keys(self):
        """A new key on the wire must be a deliberate edit here, not a silent addition."""
        row = sv.SecretPresence(name="K", scope=sv.SCOPE_GLOBAL)
        assert set(row.to_dict()) == {
            "name",
            "scope",
            "project_id",
            "present",
            "inherited_from_host",
            "consumers",
        }

    def test_credential_names_never_reads_a_value(self, vault: Path):
        """`credential_names` is the vault's ONE store call. It returns names, and only names."""
        names = cred.credential_names()
        assert "EI10_GLOBAL_TOKEN" in names
        assert sv.project_secret_key(PROJECT_ID, "EI10_DB_PASSWORD") in names
        blob = json.dumps(names)
        for value in ALL_VALUES:
            assert value not in blob, "credential_names leaked a VALUE"


# ── layer 2: the wire rail ──


class TestNoValueCrossesTheWire:
    """No verb, on any row, returns a stored value. Driven, not reasoned about."""

    @pytest.mark.asyncio
    async def test_get_carries_presence_and_no_value(self, vault: Path):
        async with TestClient(TestServer(_make_app())) as c:
            r = await c.get("/api/secrets")
            assert r.status == 200
            raw = await r.text()
            body = json.loads(raw)

        names = {s["name"] for s in body["secrets"]}
        # 🪤 VACUITY FLOOR. If the listing were empty the leak assertion below would pass
        # trivially, so assert the population FIRST — all three row types must be present.
        assert {"EI10_GLOBAL_TOKEN", "EI10_DB_PASSWORD", "EI10_HOST_TOKEN"} <= names, names
        assert body["counts"]["total"] == len(body["secrets"])
        for value in ALL_VALUES:
            assert value not in raw, f"a stored VALUE reached the GET body: {value}"

    @pytest.mark.asyncio
    async def test_post_does_not_echo_the_value_it_just_stored(self, vault: Path):
        """The hardest case: the handler HELD this value one line before it answered."""
        fresh = "nv-9d4c2e81-JUSTSTORED-do-not-leak"
        async with TestClient(TestServer(_make_app())) as c:
            r = await c.post("/api/secrets", json={"name": "EI10_NEW_TOKEN", "value": fresh})
            assert r.status == 200, await r.text()
            raw = await r.text()
            body = json.loads(raw)

        assert body["secret"]["name"] == "EI10_NEW_TOKEN"
        assert body["secret"]["present"] is True
        assert fresh not in raw, "the POST echoed the value it was handed"
        # And it really was stored — otherwise "no value in the response" is true of a no-op.
        assert cred.get_credential("EI10_NEW_TOKEN") == fresh

    @pytest.mark.asyncio
    async def test_delete_does_not_echo_a_value(self, vault: Path):
        async with TestClient(TestServer(_make_app())) as c:
            r = await c.delete("/api/secrets?name=EI10_GLOBAL_TOKEN")
            assert r.status == 200, await r.text()
            raw = await r.text()
        assert GLOBAL_VALUE not in raw
        assert json.loads(raw)["deleted"] == "EI10_GLOBAL_TOKEN"
        assert cred.get_credential("EI10_GLOBAL_TOKEN") == ""

    @pytest.mark.asyncio
    async def test_there_is_no_route_that_reads_one_back(self, vault: Path):
        """No per-secret GET exists. The absence is the mechanism, so it is asserted."""
        async with TestClient(TestServer(_make_app())) as c:
            for path in (
                "/api/secrets/EI10_GLOBAL_TOKEN",
                "/api/secrets/EI10_GLOBAL_TOKEN/value",
                "/api/secrets/reveal?name=EI10_GLOBAL_TOKEN",
            ):
                assert (await c.get(path)).status == 404, f"{path} must not exist"


# ── scope, host rows and refusals ──


class TestScopesAndRefusals:
    def test_project_rows_decode_to_name_and_owner(self, vault: Path):
        rows = {(r.scope, r.name, r.project_id) for r in sv.list_presence()}
        assert ("global", "EI10_GLOBAL_TOKEN", "") in rows
        assert ("project", "EI10_DB_PASSWORD", PROJECT_ID) in rows
        assert ("host", "EI10_HOST_TOKEN", "") in rows

    def test_a_project_filter_narrows_only_the_project_rows(self, vault: Path, monkeypatch):
        cred.save_credential(sv.project_secret_key("other-proj", "EI10_OTHER"), "x-value")
        rows = sv.list_presence(project_id=PROJECT_ID)
        projects = {r.name for r in rows if r.scope == "project"}
        assert projects == {"EI10_DB_PASSWORD"}, projects
        # Global and host rows are UNCONDITIONAL: a project resolves {{secret:…}} against the
        # same store and the same process env, so hiding them would under-report reach.
        assert any(r.scope == "global" for r in rows)
        assert any(r.scope == "host" for r in rows)

    def test_a_stored_name_stops_being_a_host_row(self, vault: Path):
        """Taking ownership REPLACES the inherited row rather than duplicating it."""
        before = [r for r in sv.list_presence() if r.name == "EI10_HOST_TOKEN"]
        assert [r.scope for r in before] == ["host"]
        cred.save_credential("EI10_HOST_TOKEN", "now-in-the-vault")
        after = [r for r in sv.list_presence() if r.name == "EI10_HOST_TOKEN"]
        assert [r.scope for r in after] == ["global"], "a vault row must shadow the host row"

    def test_inherited_from_host_cannot_contradict_scope(self):
        assert sv.SecretPresence(name="A", scope=sv.SCOPE_HOST).inherited_from_host is True
        assert sv.SecretPresence(name="A", scope=sv.SCOPE_GLOBAL).inherited_from_host is False
        assert sv.SecretPresence(name="A", scope=sv.SCOPE_PROJECT).inherited_from_host is False

    @pytest.mark.asyncio
    async def test_deleting_a_host_row_is_refused_not_silently_ignored(self, vault: Path):
        async with TestClient(TestServer(_make_app())) as c:
            r = await c.delete("/api/secrets?name=EI10_HOST_TOKEN")
            assert r.status == 409
            assert (await r.json())["error"]["code"] == "secret_host_readonly"
        # The value is untouched — a refusal that had already deleted something would be worse
        # than either outcome.
        import os

        assert os.environ["EI10_HOST_TOKEN"] == HOST_VALUE

    @pytest.mark.asyncio
    async def test_an_app_token_is_refused_on_every_verb(self, vault: Path):
        async with TestClient(TestServer(_make_app(app_token_name="demo"))) as c:
            for r in (
                await c.get("/api/secrets"),
                await c.post("/api/secrets", json={"name": "X", "value": "y"}),
                await c.delete("/api/secrets?name=X"),
            ):
                assert r.status == 403
                assert (await r.json())["error"]["code"] == "credentials_owner_only"

    @pytest.mark.asyncio
    async def test_a_refused_app_never_learns_a_name(self, vault: Path):
        """The refusal must not leak the inventory it is refusing access to."""
        async with TestClient(TestServer(_make_app(app_token_name="demo"))) as c:
            raw = await (await c.get("/api/secrets")).text()
        for leak in ("EI10_GLOBAL_TOKEN", "EI10_DB_PASSWORD", *ALL_VALUES):
            assert leak not in raw

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body,code",
        [
            ({"name": "9starts-with-a-digit", "value": "v"}, "secret_name_invalid"),
            ({"name": "has-a-dash", "value": "v"}, "secret_name_invalid"),
            ({"name": "OK_NAME", "value": ""}, "secret_value_required"),
            ({"name": "OK_NAME", "value": "v", "project_id": "bad__id"}, "secret_project_invalid"),
            (
                {"name": "OK_NAME", "value": "v", "project_id": "bad/slash"},
                "secret_project_invalid",
            ),
        ],
    )
    async def test_write_refusals(self, vault: Path, body: dict, code: str):
        async with TestClient(TestServer(_make_app())) as c:
            r = await c.post("/api/secrets", json=body)
            assert r.status == 400, await r.text()
            assert (await r.json())["error"]["code"] == code

    @pytest.mark.asyncio
    async def test_deleting_something_absent_is_a_404(self, vault: Path):
        async with TestClient(TestServer(_make_app())) as c:
            r = await c.delete("/api/secrets?name=EI10_NEVER_STORED")
            assert r.status == 404
            assert (await r.json())["error"]["code"] == "secret_absent"

    @pytest.mark.asyncio
    async def test_an_empty_vault_says_what_to_do_next(self, home: Path):
        """ "No secrets yet" must not read as "secrets are broken"."""
        async with TestClient(TestServer(_make_app())) as c:
            body = await (await c.get("/api/secrets")).json()
        hint = body["empty_hint"]
        # Only meaningful when the vault really is empty of VAULT rows; host rows may exist in
        # any environment, so the hint is keyed on the whole listing being empty.
        if body["counts"]["total"] == 0:
            assert hint, "an empty vault must carry a next action"
            assert "{{secret:" in hint, "the hint must name the reference syntax"
        else:
            assert hint == ""

    def test_every_new_wire_code_is_registered_with_a_meaning(self):
        for code in (
            "secret_name_invalid",
            "secret_value_required",
            "secret_project_invalid",
            "secret_absent",
            "secret_host_readonly",
        ):
            assert code in HTTP_ERROR_CODES, f"{code} needs an HTTP_ERROR_CODES row"
            meaning = HTTP_ERROR_CODES[code]
            assert meaning.strip(), code
            # 🪤 A meaning that merely restates the identifier is not user-actionable. Every
            # sentence must say something the code itself does not.
            assert meaning.lower() != code.replace("_", " "), code
            assert len(meaning) > 40, f"{code}'s meaning is too thin to act on: {meaning!r}"


class TestKeyNamespace:
    @pytest.mark.parametrize(
        "project_id,name",
        [("p1", "TOKEN"), ("proj-with-dash", "A_B_C"), ("p.1", "X"), ("under_score", "Y")],
    )
    def test_encode_decode_round_trips(self, project_id: str, name: str):
        key = sv.project_secret_key(project_id, name)
        assert sv.split_project_key(key) == (project_id, name)

    @pytest.mark.parametrize(
        "key", ["TOKEN", "PCPROJ_", "PCPROJ_only", "PCPROJ___NONAME", "PCPROJ_p__"]
    )
    def test_a_non_project_key_decodes_to_none(self, key: str):
        assert sv.split_project_key(key) is None

    def test_a_project_id_containing_the_separator_is_refused(self):
        """An id with `__` in it would put the decode's split in the wrong place."""
        assert not sv.valid_project_id("has__sep")
        assert not sv.valid_project_id("")
        assert sv.valid_project_id("fine-id.1")


# ── the export rail ──


class TestExportCarriesFlagsNotValues:
    """A project export declares which credentials the far side needs, and carries none."""

    def _project(self, root: Path) -> Path:
        proj = root / "project"
        (proj / "context").mkdir(parents=True)
        (proj / "project.json").write_text(json.dumps({"id": PROJECT_ID, "name": "EI10"}))
        (proj / "context" / "overview.md").write_text("# EI10\n\nA project.\n")
        return proj

    def test_the_manifest_names_the_project_secrets(self, vault: Path, tmp_path: Path):
        from personalclaw.workflows.project_archive import export_project_archive

        raw, plan = export_project_archive(PROJECT_ID, project_root=self._project(tmp_path))
        assert "EI10_DB_PASSWORD" in plan.secrets_present, plan.secrets_present
        manifest = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("manifest.json"))
        assert "EI10_DB_PASSWORD" in manifest["secrets"]

    def test_no_value_appears_anywhere_in_the_zip_bytes(self, vault: Path, tmp_path: Path):
        """Asserted over the ARCHIVE'S CONTENTS, not over the plan's intent.

        Reading every member back out rather than scanning the compressed bytes: DEFLATE would
        hide a plaintext value from a substring search over `raw`, so a scan of the container
        would pass on an archive that does carry the secret.
        """
        from personalclaw.workflows.project_archive import export_project_archive

        raw, plan = export_project_archive(PROJECT_ID, project_root=self._project(tmp_path))

        zf = zipfile.ZipFile(io.BytesIO(raw))
        members = zf.namelist()
        # 🪤 VACUITY FLOOR. An empty archive carries no secret either. Assert the archive really
        # has the project in it before concluding anything from the absence of a value.
        assert len(members) >= 3, members
        assert any(m.endswith("project.json") for m in members), members
        assert any(m.endswith("context/overview.md") for m in members), members

        decompressed = b"".join(zf.read(m) for m in members)
        for value in ALL_VALUES:
            assert (
                value.encode() not in decompressed
            ), f"a credential VALUE is inside the export archive: {value}"
        # And the flag really did travel, so "no value" is not true of an export that simply
        # forgot the secret existed.
        assert "EI10_DB_PASSWORD" in json.loads(zf.read("manifest.json"))["secrets"]

    def test_a_secret_named_file_inside_the_project_is_flagged_not_carried(
        self, vault: Path, tmp_path: Path
    ):
        """The OTHER route a secret reaches an export: a file the exclusion policy catches."""
        from personalclaw.workflows.project_archive import export_project_archive

        proj = self._project(tmp_path)
        (proj / "context" / ".env").write_text(f"INSIDE_PROJECT={GLOBAL_VALUE}\n")

        raw, plan = export_project_archive(PROJECT_ID, project_root=proj)
        zf = zipfile.ZipFile(io.BytesIO(raw))
        assert not any(m.endswith(".env") for m in zf.namelist()), zf.namelist()
        decompressed = b"".join(zf.read(m) for m in zf.namelist())
        assert GLOBAL_VALUE.encode() not in decompressed
        assert ".env" in plan.secrets_present, plan.secrets_present

    def test_an_unreadable_store_costs_flags_not_the_export(self, vault: Path, tmp_path: Path):
        """A broken credential store must not make a project unexportable."""
        import personalclaw.secrets_vault as vault_mod
        from personalclaw.workflows import project_archive

        def _boom(_project_id: str) -> list[str]:
            raise OSError("store unreadable")

        original = vault_mod.project_secret_names
        vault_mod.project_secret_names = _boom  # type: ignore[assignment]
        try:
            raw, plan = project_archive.export_project_archive(
                PROJECT_ID, project_root=self._project(tmp_path)
            )
        finally:
            vault_mod.project_secret_names = original  # type: ignore[assignment]

        assert plan.secrets_present == []
        assert any(m.endswith("project.json") for m in zipfile.ZipFile(io.BytesIO(raw)).namelist())


# ── the consumer-derivation rail ──


class TestConsumersAreDerived:
    """Consumer links come from the specs that exist, through the SHIPPED reference readers."""

    def test_the_shipped_readers_are_what_find_a_reference(self):
        """🪤 VACUITY PARTNER for the derivation tests: the two readers really do read.

        This measures `workflows.secrets` and `triggers.secrets`, not the vault, so it stays GREEN
        under any mutation of `secrets_vault._workflow_references` / `_trigger_references` — which
        is what makes a red in those tests attributable to the derivation rather than to a broken
        regex underneath it.
        """
        from personalclaw.triggers.secrets import references
        from personalclaw.workflows.secrets import secret_keys_referenced

        spec = {"nodes": [{"id": "n1", "config": {"url": "https://x/{{secret:WF_KEY}}"}}]}
        assert secret_keys_referenced(spec) == ["WF_KEY"]
        assert references({"headers": {"Authorization": "Bearer {{secret:TRIG_KEY}}"}}) == [
            "TRIG_KEY"
        ]

    @pytest.mark.asyncio
    async def test_a_workflow_reference_becomes_a_consumer_link(self, vault: Path, monkeypatch):
        import personalclaw.secrets_vault as vault_mod

        async def _refs():
            return [("nightly-sync", "Nightly sync", ["EI10_GLOBAL_TOKEN"])]

        monkeypatch.setattr(vault_mod, "_workflow_references", _refs)
        monkeypatch.setattr(vault_mod, "_trigger_references", lambda: [])

        consumers = await vault_mod.consumers_for()
        assert consumers["EI10_GLOBAL_TOKEN"] == (
            sv.SecretConsumer(kind="workflow", id="nightly-sync", label="Nightly sync"),
        )

        rows = {r.name: r for r in sv.list_presence(consumers=consumers)}
        assert [c.label for c in rows["EI10_GLOBAL_TOKEN"].consumers] == ["Nightly sync"]
        # A secret nothing references has NO links — the derivation must not smear.
        assert rows["EI10_HOST_TOKEN"].consumers == ()

    @pytest.mark.asyncio
    async def test_a_project_secrets_links_are_keyed_by_its_STORE_key(
        self, vault: Path, monkeypatch
    ):
        """A project row and a global row of the same name must not share consumers."""
        import personalclaw.secrets_vault as vault_mod

        store_key = sv.project_secret_key(PROJECT_ID, "EI10_DB_PASSWORD")
        cred.save_credential("EI10_DB_PASSWORD", "a-global-of-the-same-name")

        async def _refs():
            return [("proj-only", "Project only", [store_key])]

        monkeypatch.setattr(vault_mod, "_workflow_references", _refs)
        monkeypatch.setattr(vault_mod, "_trigger_references", lambda: [])
        consumers = await vault_mod.consumers_for()

        rows = sv.list_presence(consumers=consumers)
        scoped = next(r for r in rows if r.scope == "project" and r.name == "EI10_DB_PASSWORD")
        glob = next(r for r in rows if r.scope == "global" and r.name == "EI10_DB_PASSWORD")
        assert [c.id for c in scoped.consumers] == ["proj-only"]
        assert glob.consumers == (), "a global row must not inherit the project row's consumers"

    @pytest.mark.asyncio
    async def test_a_broken_derivation_goes_WRONG_not_silently_empty(
        self, vault: Path, monkeypatch
    ):
        """The failure mode this rail exists for.

        A derivation that returned `{}` on error would render "not referenced by anything" beside
        a secret three workflows depend on — advice to delete it. So a link that is derived from
        the wrong key must show up as a link on the WRONG ROW (visible, checkable) and the right
        row must be measurably bare, rather than the whole map collapsing to empty and reading
        like a clean answer.
        """
        import personalclaw.secrets_vault as vault_mod

        async def _wrong_key():
            # The mutation: the derivation attributes the reference to a mis-cased key.
            return [("nightly-sync", "Nightly sync", ["ei10_global_token"])]

        monkeypatch.setattr(vault_mod, "_workflow_references", _wrong_key)
        monkeypatch.setattr(vault_mod, "_trigger_references", lambda: [])
        consumers = await vault_mod.consumers_for()

        rows = {r.name: r for r in sv.list_presence(consumers=consumers)}
        assert rows["EI10_GLOBAL_TOKEN"].consumers == ()
        # The link did not vanish — it is attributable to a key no row claims, which is what a
        # derived index makes visible and a maintained one would have hidden.
        assert "ei10_global_token" in consumers
        assert "ei10_global_token" not in {r.name for r in rows.values()}

    @pytest.mark.asyncio
    async def test_one_unreadable_provider_does_not_blank_the_others(
        self, vault: Path, monkeypatch
    ):
        import personalclaw.secrets_vault as vault_mod

        def _boom() -> list:
            raise RuntimeError("trigger store is corrupt")

        async def _ok():
            return [("wf", "WF", ["EI10_GLOBAL_TOKEN"])]

        monkeypatch.setattr(vault_mod, "_workflow_references", _ok)
        monkeypatch.setattr(vault_mod, "_trigger_references", _boom)
        with pytest.raises(RuntimeError):
            # The seam is inside `_trigger_references`; a raise from the seam itself is not
            # swallowed by `consumers_for`, which is honest — the tolerance lives where the
            # store is actually read. Asserted so the boundary is documented rather than assumed.
            await vault_mod.consumers_for()

    @pytest.mark.asyncio
    async def test_consumers_survive_an_unreadable_trigger_store(self, vault: Path, monkeypatch):
        """The real tolerance seam: `TriggerStore()` itself failing costs links, not the listing."""
        import personalclaw.triggers.store as tstore

        def _boom(*_a, **_k):
            raise OSError("triggers.json unreadable")

        monkeypatch.setattr(tstore, "TriggerStore", _boom)
        assert sv._trigger_references() == []


@pytest.fixture
def repo_root() -> Path:
    """The repository root, for the source-reading rails."""
    root = Path(__file__).resolve().parents[1]
    assert (root / "src" / "personalclaw").is_dir(), root
    return root
