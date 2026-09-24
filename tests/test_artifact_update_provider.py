"""The `artifact-update` action provider (Slice 9b, WF2-R15).

What it exists for: a dashboard-style template generates a skeleton once and refreshes it every
run by re-binding `{{nodes.x.output}}` slots. Without this the only way to write the artifact
would be a `stage` — a whole subagent session spawned to paste text into a file, costing a model
call and a lane slot for pure substitution. So this is a **zero-token** node.

The behaviours worth pinning:

* **upsert**, because a recurring workflow's first run has no artifact and its hundredth does.
  Making the template branch on that would put a `branch` node in every dashboard spec for a
  distinction the provider can absorb;
* **`snapshot` opt-in**, or a dashboard refreshed every five minutes buries the versions a human
  cares about under hundreds it does not;
* **the user's own name and description survive a refresh** — a template reasserting its metadata
  every run would silently revert a rename;
* **the slug is validated**, because it reaches here from a model-authorable config and becomes a
  directory name.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.registry import (
    _ensure_default_providers_registered,
    get_action_provider,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    # The artifact registry caches its native provider against the old root, so it has to be
    # cleared or every test in this file writes into the first test's home.
    from personalclaw.artifacts import registry as art_registry

    art_registry._providers.clear()
    return home


@pytest.fixture
def provider():
    _ensure_default_providers_registered()
    p = get_action_provider("artifact-update")
    assert p is not None, "artifact-update is not registered"
    return p


CTX = ActionContext(event="workflow_node")


def _store():
    from personalclaw.artifacts.registry import get_provider

    return get_provider()


class TestRegistration:
    def test_it_is_in_BOTH_registration_points(self, provider) -> None:
        """The two-step the plan warns about: a provider in the registry but not in
        `ALLOWED_HOOK_PROVIDERS` validates on a trigger, saves, and then fails at run time — and
        the reverse is a name the UI offers that dispatches to nothing."""
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        assert provider.name == "artifact-update"
        assert "artifact-update" in ALLOWED_HOOK_PROVIDERS

    def test_no_registered_provider_is_missing_from_the_allowlist(self) -> None:
        """The general form of the same check, so the NEXT provider cannot land half-registered."""
        from personalclaw.action_providers.registry import list_action_providers
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        _ensure_default_providers_registered()
        missing = set(list_action_providers()) - set(ALLOWED_HOOK_PROVIDERS)
        assert not missing, f"registered but not allowlisted: {sorted(missing)}"

    def test_it_has_a_human_label(self, provider) -> None:
        assert provider.display_name == "Update Artifact"


class TestUpsert:
    async def test_an_unknown_slug_is_CREATED(self, provider) -> None:
        result = await provider.execute(
            {"slug": "dash", "content": "<h1>v1</h1>", "name": "Dashboard"}, CTX
        )
        assert result.success, result.error
        assert json.loads(result.stdout)["created"] is True
        assert _store().get("dash").content == "<h1>v1</h1>"

    async def test_a_known_slug_is_UPDATED(self, provider) -> None:
        """The reason this is upsert: a recurring workflow's first run creates and its hundredth
        updates, and making the TEMPLATE branch on that is a `branch` node in every spec."""
        await provider.execute({"slug": "dash", "content": "v1"}, CTX)
        result = await provider.execute({"slug": "dash", "content": "v2"}, CTX)
        assert result.success, result.error
        assert json.loads(result.stdout)["created"] is False
        assert _store().get("dash").content == "v2"

    async def test_a_refresh_does_not_bump_the_version_by_default(self, provider) -> None:
        """A dashboard refreshed every five minutes would otherwise accumulate a version per
        refresh and bury the ones a human actually wants."""
        await provider.execute({"slug": "dash", "content": "v1"}, CTX)
        before = _store().get("dash").version
        await provider.execute({"slug": "dash", "content": "v2"}, CTX)
        assert _store().get("dash").version == before

    async def test_snapshot_opt_in_DOES_keep_history(self, provider) -> None:
        await provider.execute({"slug": "dash", "content": "v1"}, CTX)
        before = _store().get("dash").version
        await provider.execute({"slug": "dash", "content": "v2", "snapshot": True}, CTX)
        assert _store().get("dash").version > before

    async def test_a_refresh_preserves_the_users_own_name(self, provider) -> None:
        """A template that reasserted its metadata on every refresh would silently revert a
        rename the user made in the UI."""
        await provider.execute({"slug": "dash", "content": "v1", "name": "Original"}, CTX)
        _store().update("dash", name="What the user renamed it to")
        await provider.execute({"slug": "dash", "content": "v2", "name": "Original"}, CTX)
        assert _store().get("dash").name == "What the user renamed it to"

    async def test_the_stdout_carries_what_a_downstream_node_binds_to(self, provider) -> None:
        """A dashboard's "last updated v7" line reads this rather than re-fetching."""
        result = await provider.execute({"slug": "dash", "content": "v1"}, CTX)
        body = json.loads(result.stdout)
        assert body["slug"] == "dash"
        assert isinstance(body["version"], int)


class TestContent:
    async def test_EMPTY_content_is_a_legitimate_write(self, provider) -> None:
        """A dashboard with nothing to report yet. Treating "" as missing would silently skip
        that write and leave the previous run's data on screen looking current."""
        result = await provider.execute({"slug": "dash", "content": ""}, CTX)
        assert result.success, result.error
        assert _store().get("dash").content == ""

    async def test_a_dict_is_rendered_as_stable_JSON(self, provider) -> None:
        """A transform binding often yields a dict. `str(dict)` would put Python repr (single
        quotes, `True`) into an artifact a browser may parse as JSON."""
        await provider.execute({"slug": "data", "content": {"b": 2, "a": 1, "ok": True}}, CTX)
        body = _store().get("data").content
        assert json.loads(body) == {"a": 1, "b": 2, "ok": True}
        assert "'" not in body and "True" not in body

    async def test_identical_dicts_render_byte_identically(self, provider) -> None:
        """Sorted keys, so two identical refreshes do not record a version for a no-op."""
        await provider.execute({"slug": "a", "content": {"x": 1, "y": 2}}, CTX)
        first = _store().get("a").content
        await provider.execute({"slug": "b", "content": {"y": 2, "x": 1}}, CTX)
        assert _store().get("b").content == first


class TestRefusals:
    @pytest.mark.parametrize(
        "config,expect",
        [
            ({"content": "x"}, "slug"),
            ({"slug": "", "content": "x"}, "slug"),
            ({"slug": "dash"}, "content"),
            ({"slug": "dash", "content": "x", "kind": "bogus"}, "kind"),
        ],
    )
    async def test_a_missing_or_bad_field_names_itself(self, provider, config, expect) -> None:
        """The provider's error is what an author reads at the node that caused it, so it has to
        name the field rather than say "invalid configuration"."""
        result = await provider.execute(config, CTX)
        assert result.success is False
        assert expect in result.error

    @pytest.mark.parametrize(
        "slug", ["../etc/passwd", "/absolute", "Has Spaces", "UPPER", "dot.dot", "a" * 80]
    )
    async def test_a_dangerous_or_malformed_slug_is_refused(self, provider, slug) -> None:
        """The slug arrives from a model-authorable config and becomes a directory name. The
        store's writer guards traversal too, but failing HERE gives the author the error at the
        node that named it."""
        result = await provider.execute({"slug": slug, "content": "x"}, CTX)
        assert result.success is False
        assert "valid id" in result.error

    async def test_a_store_failure_is_reported_not_raised(self, provider, monkeypatch) -> None:
        """An action provider that raised would be wrapped in a generic envelope; naming the slug
        is what makes it debuggable."""
        from personalclaw.artifacts import registry as art_registry

        class Broken:
            def get(self, slug, **kw):
                return None

            def create(self, **kw):
                raise RuntimeError("disk is full")

        monkeypatch.setattr(art_registry, "get_provider", lambda name=None: Broken())
        result = await provider.execute({"slug": "dash", "content": "x"}, CTX)
        assert result.success is False
        assert "dash" in result.error and "disk is full" in result.error

    async def test_no_artifact_provider_is_reported_clearly(self, provider, monkeypatch) -> None:
        from personalclaw.artifacts import registry as art_registry

        monkeypatch.setattr(art_registry, "get_provider", lambda name=None: None)
        result = await provider.execute({"slug": "dash", "content": "x"}, CTX)
        assert result.success is False
        assert "no artifact provider" in result.error


class TestZeroToken:
    async def test_it_makes_no_model_call(self, provider) -> None:
        """The whole reason it exists: writing resolved content is substitution, and doing it
        through a `stage` would spend a subagent session per refresh.

        Asserted structurally — the provider must not import a model surface at all.
        """
        import inspect

        from personalclaw.action_providers import artifact_update_provider as mod

        source = inspect.getsource(mod)
        for forbidden in ("one_shot_completion", "SubagentManager", "get_provider_for_use_case"):
            assert forbidden not in source, f"{forbidden} would make this a token-spending node"

    async def test_it_reports_a_duration(self, provider) -> None:
        """The Run Ledger records per-node duration; a provider that left it zero would make a
        slow disk invisible in the one place it would show up."""
        result = await provider.execute({"slug": "dash", "content": "x"}, CTX)
        assert result.duration_ms >= 0
