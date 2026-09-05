"""#609 — a lifecycle trigger's Test is a rehearsal, not a fire.

The panel's Test button called ``run_script_hook`` with nothing distinguishing it
from a real fire: it bumped ``run_count``/``last_run``/``last_status`` (so
"Ran 2× · ok" could describe a trigger that never actually fired) and delivered
the action's real side effect unmarked into the live inbox. The event-trigger
sibling already threads a ``test`` flag whose contract is "test only tags the
payload, so a provider can tell a rehearsal from the real thing" — these rails
pin the same contract onto the hooks seam, both directions:

- ``test=True``: payload tagged, gates still applied, NO history writes.
- default: byte-identical to before — real fires keep recording.
- the notify provider marks a tagged rehearsal's title, on BOTH trigger paths.
"""

from __future__ import annotations

import platform

import pytest

from personalclaw.hooks import HOOK_EVENT_USER_PROMPT_SUBMIT, ScriptHook, run_script_hook

_IS_MACOS = platform.system() == "Darwin"
pytestmark = pytest.mark.skipif(
    not _IS_MACOS and platform.system() != "Linux", reason="bash provider tests need a POSIX shell"
)


def _hook(**over) -> ScriptHook:
    base = dict(
        id="t-609",
        name="rehearsal-hook",
        event=HOOK_EVENT_USER_PROMPT_SUBMIT,
        provider="bash",
        provider_config={"command": "echo fired"},
        timeout=10,
        enabled=True,
    )
    base.update(over)
    return ScriptHook(**base)


class TestLifecycleTestIsARehearsal:
    @pytest.mark.asyncio
    async def test_a_rehearsal_never_writes_fire_history(self):
        hook = _hook(run_count=1, last_status="ok", last_run=1000.0)
        result = await run_script_hook(hook, "ctx", test=True)
        # The action genuinely ran (that is what Test verifies)…
        assert "fired" in (result.stdout or "")
        # …but the REAL history is untouched: run_count/last_run/last_status are
        # the trigger's only fire record, and a rehearsal is not a fire.
        assert hook.run_count == 1
        assert hook.last_status == "ok"
        assert hook.last_run == 1000.0

    @pytest.mark.asyncio
    async def test_a_real_fire_still_records(self):
        hook = _hook(run_count=1, last_status="ok", last_run=1000.0)
        await run_script_hook(hook, "ctx")
        assert hook.run_count == 2
        assert hook.last_status == "ok"
        assert hook.last_run > 1000.0

    @pytest.mark.asyncio
    async def test_a_failing_rehearsal_also_stays_off_the_record(self):
        # The rule must hold on EVERY terminal branch, not just success — a test
        # of a broken trigger must not stamp `error` into the real history either.
        hook = _hook(provider="no-such-provider", run_count=3, last_status="ok")
        result = await run_script_hook(hook, "ctx", test=True)
        assert result.error
        assert hook.run_count == 3
        assert hook.last_status == "ok"

    @pytest.mark.asyncio
    async def test_the_payload_is_tagged_and_the_callers_dict_is_not_mutated(self):
        seen: dict = {}

        class _Spy:
            async def execute(self, action_config, ctx, timeout=30):
                from personalclaw.action_providers.base import ActionResult

                seen.update(ctx.payload or {})
                return ActionResult(success=True, exit_code=0, stdout="ok")

        from personalclaw.action_providers import registry

        registry._providers["spy-609"] = _Spy()  # type: ignore[assignment]
        try:
            hook = _hook(provider="spy-609")
            caller_event = {"hook_event_name": hook.event, "cwd": "/tmp"}
            await run_script_hook(hook, "ctx", hook_event=caller_event, test=True)
            # The event-path contract verbatim: the tag rides the payload.
            assert seen.get("test") is True
            # The caller's dict was copied, not mutated.
            assert "test" not in caller_event
        finally:
            registry._providers.pop("spy-609", None)


class TestNotifyMarksRehearsals:
    def _ctx(self, payload):
        from personalclaw.action_providers.base import ActionContext

        return ActionContext(event="Error", context="c", payload=payload)

    @pytest.mark.asyncio
    async def test_tagged_payload_prefixes_the_title(self, monkeypatch):
        from personalclaw.action_providers import notify_provider as np

        sent: list[tuple] = []

        class _State:
            def notify(self, kind, title, body):
                sent.append((kind, title, body))

        class _Services:
            state = _State()

        monkeypatch.setattr(np, "get_action_services", lambda: _Services())
        prov = np.create_provider()
        cfg = {"title_template": "Inventory agent hit an error", "kind": "error"}
        r1 = await prov.execute(cfg, self._ctx({"test": True}))
        r2 = await prov.execute(cfg, self._ctx({}))
        assert r1.success and r2.success
        # The rehearsal is marked; the real one is untouched — an unmarked test
        # notification is indistinguishable from a live alert in the inbox.
        assert sent[0][1] == "[test] Inventory agent hit an error"
        assert sent[1][1] == "Inventory agent hit an error"
