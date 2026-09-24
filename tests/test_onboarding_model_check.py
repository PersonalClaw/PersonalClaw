"""Onboarding's "can I chat yet" VERIFICATION — ``GET /api/onboarding/model-check``.

**The defect this route exists for, stated as the premise these tests pin.**
``GET /api/onboarding``'s ``needs_model`` is derived from ``can_resolve_use_case``, which is
documented as — and must remain — a **no-instantiate** probe: it runs on a hot GET and on
every workflow preflight, so it may not build anything. Its first branch returns ``True`` as
soon as ``active_models.json`` holds a ref for the use case, *without checking the ref still
resolves*. Writing that ref is the LAST thing the onboarding model step does, so the step was
reading its own write back as proof that chat works.

Measured, and asserted by :class:`TestThePremise` below: on a home whose ``config.json``
carries ``{"name": "my-openai", "type": "openai"}`` with no app registering that type, plus a
``chat`` binding to it, ``can_resolve_use_case("chat")`` is ``True`` — hence
``needs_model: false``, hence a green "you're ready" — while
``resolve_provider_for_use_case("chat")`` raises ``ERR_MODEL_UNRESOLVED``.

Four properties carry the route, one rail each:

1. **A declaration is not a build.** The route answers with what the BUILD did, so the state
   above is reported ``ok: false``. (:class:`TestThePremise`, :class:`TestVerdict`)
2. **The cause is the bridge's, relayed — never paraphrased.** The route adds no analysis of
   its own, which is the only arrangement in which the number of causes a user can tell apart
   equals the number the bridge can. Falsified by a route that emits one house sentence.
   (:class:`TestCauseIsRelayedVerbatim`)
3. **A crash is a NO, never a silent yes.** Neither an unexpected exception nor an envelope-less
   refusal may produce ``ok: true``, and the text they surface is masked.
   (:class:`TestNeverASilentYes`)
4. **A pass names the mechanism.** ``source`` distinguishes "the ref you bound answered" from
   "a configured provider answered because you bound nothing" — different sentences, and the
   second must not be reported as a choice. (:class:`TestVerdict`)

Every test drives a real ``config.json`` under ``tmp_path``; none touches the real home.
``bootstrap_cli_providers``-equivalent replay (``sync_entries_from_config``) is explicit in the
fixture, because a bare test process registers nothing at import and every lookup would
otherwise report "provider absent" for a reason that has nothing to do with the code under test.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.dashboard.handlers.model_check import api_onboarding_model_check
from personalclaw.errors import AgentError
from personalclaw.providers.provider_bridge import (
    ProviderResolutionError,
    can_resolve_use_case,
    resolve_provider_for_use_case,
)


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """The whole config home under ``tmp_path`` — the real home is never read or written."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _write_home(home, *, providers: list[dict] | None = None, chat: list[str] | None = None):
    """Write a ``config.json`` + binding store, then replay both into the live registry.

    The replay is what a gateway does at boot (``sync_entries_from_config``, the third step of
    ``bootstrap_cli_providers``). Without it a bare process sees an empty registry and every
    resolution fails for the WRONG reason — the trap where the error message lies about the
    cause. The registry is module-global, so it is also cleared per test.
    """
    from personalclaw.llm.registry import get_default_registry, sync_entries_from_config

    (home / "config.json").write_text(json.dumps({"providers": providers or []}), encoding="utf-8")
    if chat is not None:
        (home / "active_models.json").write_text(json.dumps({"chat": chat}), encoding="utf-8")
    registry = get_default_registry()
    registry._entries.clear()  # noqa: SLF001 — a module-global registry needs a per-test reset
    sync_entries_from_config()
    return registry


async def _call() -> dict:
    resp = await api_onboarding_model_check(MagicMock())
    assert resp.status == 200, "a verdict about the home is never a failed request"
    return json.loads(resp.body.decode())


# ── 1. the premise: the coarse probe and a real build disagree ────────────────


class TestThePremise:
    """The measured disagreement the route exists to close.

    If a future change makes ``can_resolve_use_case`` build (it must not — it is the workflow
    preflight's probe and runs on a hot GET), THIS test goes red rather than the route silently
    becoming redundant. That is the point of pinning a premise.
    """

    def test_a_binding_to_an_unbuildable_provider_reads_resolvable(self, _isolate_home):
        _write_home(
            _isolate_home,
            providers=[{"name": "my-openai", "type": "openai", "model": "gpt-4o-mini"}],
            chat=["my-openai:gpt-4o-mini"],
        )
        # The coarse probe says yes — so `GET /api/onboarding` reports `needs_model: false`…
        assert can_resolve_use_case("chat") is True
        # …and the build says no.
        with pytest.raises(ProviderResolutionError) as caught:
            resolve_provider_for_use_case("chat")
        assert caught.value.agent_error is not None
        assert caught.value.agent_error.code == "ERR_MODEL_UNRESOLVED"

    @pytest.mark.asyncio
    async def test_the_route_reports_the_build_not_the_declaration(self, _isolate_home):
        _write_home(
            _isolate_home,
            providers=[{"name": "my-openai", "type": "openai", "model": "gpt-4o-mini"}],
            chat=["my-openai:gpt-4o-mini"],
        )
        body = await _call()
        assert body["ok"] is False, "the one assertion the whole route is for"
        assert body["code"] == "ERR_MODEL_UNRESOLVED"
        assert body["why"] and body["fix"], "a refusal carries both halves or it is unactionable"


# ── 2. the verdict shape ──────────────────────────────────────────────────────


class TestVerdict:
    @pytest.mark.asyncio
    async def test_a_pass_through_an_explicit_binding_says_binding(self, _isolate_home):
        # The provider entry is required in `config.json`, not decoration:
        # `load_active_models` PRUNES refs naming a provider config.json no longer has, so a
        # ref with no entry behind it reads as no binding at all.
        _write_home(_isolate_home, providers=[{"name": "stub", "type": "stub"}], chat=["stub:m"])
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            return_value=_FakeProvider(),
        ):
            body = await _call()
        assert body == {"ok": True, "source": "binding", "bound": ["stub:m"]}

    @pytest.mark.asyncio
    async def test_a_pass_with_nothing_bound_says_fallback(self, _isolate_home):
        """No ref in the store ⇒ resolution came from the implicit "first capable provider"
        rule, and the UI must not report that as a model the user chose."""
        _write_home(_isolate_home, chat=[])
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            return_value=_FakeProvider(),
        ):
            body = await _call()
        assert body == {"ok": True, "source": "fallback", "bound": []}

    @pytest.mark.asyncio
    async def test_a_verified_provider_is_shut_down(self, _isolate_home):
        """A build that is thrown away must be closed. Resolution constructs a real HTTP
        client (``OllamaProvider.__init__`` makes an ``httpx.AsyncClient``), so a verification
        that skipped teardown would leak one per check — and the check is re-runnable."""
        _write_home(_isolate_home, providers=[{"name": "stub", "type": "stub"}], chat=["stub:m"])
        fake = _FakeProvider()
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            return_value=fake,
        ):
            await _call()
        assert fake.shutdowns == 1

    @pytest.mark.asyncio
    async def test_a_home_with_nothing_at_all_refuses_with_the_no_provider_cause(
        self, _isolate_home
    ):
        """The empty-home case: no providers, no binding. It must refuse — and name the act
        that fixes it, because this is the state a first run starts in."""
        _write_home(_isolate_home, providers=[], chat=[])
        body = await _call()
        assert body["ok"] is False
        assert "no provider" in body["why"].lower()
        assert "Settings → Providers" in body["fix"]


# ── 3. the cause is relayed, not paraphrased ──────────────────────────────────


class TestCauseIsRelayedVerbatim:
    """The route's whole contract: it does not have an opinion.

    These are the nine ``why``/``fix`` pairs ``provider_bridge`` derives. Asserting them
    field-for-field is what makes "the count of causes the user sees equals the count the
    backend distinguishes" a testable claim rather than a hope — a route that summarised would
    pass an "is something shown" assertion while showing one sentence for nine states.
    """

    CAUSES = [
        (
            "unmappable use case",
            "use case 'chat' maps to no provider capability",
            "report use case",
        ),
        ("unreadable config", "config.json could not be read", "repair config.json"),
        ("no entry by that name", "no provider named 'my-openai' is in config.json", "re-add"),
        ("present but unregistered", "is not registered in the running gateway", "re-save"),
        ("no app claims the type", "no installed app registers that type", "install an app"),
        ("app installed but disabled", "installed but DISABLED", "enable"),
        ("app failed to load", "the app failed to load", "import error"),
        ("capability mismatch", "does not declare the 'chat' capability", "declares"),
        ("credential has no secret", "has no secret in the credential store", "set"),
    ]

    @pytest.mark.parametrize(("name", "why_fragment", "fix_fragment"), CAUSES)
    @pytest.mark.asyncio
    async def test_each_cause_reaches_the_body_unchanged(
        self, _isolate_home, name, why_fragment, fix_fragment
    ):
        why = f"SENTINEL-WHY {name}: {why_fragment} …"
        fix = f"SENTINEL-FIX {name}: {fix_fragment} …"
        error = AgentError(code="ERR_MODEL_UNRESOLVED", what="w", why=why, fix=fix)
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            side_effect=ProviderResolutionError("ignored", error),
        ):
            body = await _call()
        assert body["ok"] is False
        assert body["why"] == why, "the bridge's sentence, byte for byte"
        assert body["fix"] == fix
        assert body["what"] == "w"

    @pytest.mark.asyncio
    async def test_nine_causes_produce_nine_distinct_bodies(self, _isolate_home):
        """The count assertion the parametrised test implies but cannot state."""
        seen = set()
        for name, why_fragment, fix_fragment in self.CAUSES:
            error = AgentError(
                code="ERR_MODEL_UNRESOLVED",
                what="w",
                why=f"{name}: {why_fragment}",
                fix=f"{name}: {fix_fragment}",
            )
            with patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                side_effect=ProviderResolutionError("ignored", error),
            ):
                body = await _call()
            seen.add((body["why"], body["fix"]))
        assert len(seen) == len(self.CAUSES)


# ── 4. never a silent yes ─────────────────────────────────────────────────────


class TestNeverASilentYes:
    @pytest.mark.asyncio
    async def test_an_envelope_less_refusal_still_refuses(self, _isolate_home):
        """``ProviderResolutionError.agent_error`` is optional. Assuming it present would turn
        a real refusal into a 500 and the step would render nothing at all."""
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            side_effect=ProviderResolutionError("Unknown use case: 'chat'"),
        ):
            body = await _call()
        assert body["ok"] is False
        assert body["code"] == "ERR_MODEL_UNRESOLVED"
        assert "Unknown use case" in body["why"]
        assert body["fix"]

    @pytest.mark.asyncio
    async def test_an_unexpected_crash_is_a_no(self, _isolate_home):
        """A factory that raises something other than ``ProviderResolutionError`` must not
        read as ready. The exception's TYPE is named, because the class is the one part of an
        unexpected failure that is safe and useful to show."""
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            side_effect=RuntimeError("factory exploded"),
        ):
            body = await _call()
        assert body["ok"] is False
        assert "RuntimeError" in body["why"]
        assert "gateway log" in body["fix"]

    @pytest.mark.asyncio
    async def test_a_crash_quoting_a_credential_is_masked(self, _isolate_home):
        """A provider factory's message can quote the configuration it was handed, and this
        body reaches a browser. The redactor is the tree's display mask, not a local guess."""
        with patch(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
            side_effect=RuntimeError("bad key sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"),
        ):
            body = await _call()
        assert body["ok"] is False
        assert "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_an_unreadable_binding_store_does_not_decide_the_verdict(self, _isolate_home):
        """The binding read is only there to label a PASS. If it fails, the verdict is still
        the build's — a probe fault may not manufacture either answer."""
        _write_home(_isolate_home, providers=[{"name": "stub", "type": "stub"}], chat=["stub:m"])
        with (
            patch(
                "personalclaw.providers.use_cases.active_model_refs",
                side_effect=OSError("store unreadable"),
            ),
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                return_value=_FakeProvider(),
            ),
        ):
            body = await _call()
        assert body["ok"] is True
        assert body["source"] == "fallback", "unknown bindings degrade to the weaker claim"


class _FakeProvider:
    """A built provider, only as much of one as the route touches (``shutdown``)."""

    def __init__(self) -> None:
        self.shutdowns = 0

    async def shutdown(self) -> None:
        self.shutdowns += 1
