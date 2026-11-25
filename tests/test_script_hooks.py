"""Tests for script hooks system (ScriptHookStore, run_script_hook, etc.)."""

import platform
import sys
from pathlib import Path

import pytest

from personalclaw.hooks import (
    HOOK_EVENT_AGENT_SPAWN,
    HOOK_EVENT_PRE_TOOL_USE,
    HOOK_EVENT_USER_PROMPT_SUBMIT,
    ScriptHook,
    ScriptHookStore,
    run_script_hook,
)

_IS_MACOS = platform.system() == "Darwin"

#: The owner's yes to a `bash` action (`triggers.grants`). These tests are about how a hook runs;
#: that an ungranted one does not is `test_a_grant_is_for_the_action_the_owner_allowed`'s.
_GRANTED = {"providers": ["bash"]}


@pytest.fixture
def hook_store(tmp_path: Path) -> ScriptHookStore:
    """Create a temporary hook store."""
    return ScriptHookStore(tmp_path)


class TestScriptHook:
    """Test ScriptHook dataclass."""

    def test_to_dict(self):
        hook = ScriptHook(
            id="test-123",
            name="test-hook",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={"command": "echo test"},
            timeout=30,
            enabled=True,
        )
        d = hook.to_dict()
        assert d["id"] == "test-123"
        assert d["name"] == "test-hook"
        assert d["event"] == HOOK_EVENT_USER_PROMPT_SUBMIT
        assert d["provider"] == "bash"
        assert d["provider_config"] == {"command": "echo test"}
        assert d["timeout"] == 30
        assert d["enabled"] is True

    def test_from_dict(self):
        d = {
            "id": "test-456",
            "name": "another-hook",
            "event": HOOK_EVENT_PRE_TOOL_USE,
            "provider": "bash",
            "provider_config": {"command": "echo pre"},
            "timeout": 10,
            "enabled": False,
            "matcher": "fs_*",
        }
        hook = ScriptHook.from_dict(d)
        assert hook.id == "test-456"
        assert hook.name == "another-hook"
        assert hook.event == HOOK_EVENT_PRE_TOOL_USE
        assert hook.provider == "bash"
        assert hook.provider_config == {"command": "echo pre"}
        assert hook.timeout == 10
        assert hook.enabled is False
        assert hook.matcher == "fs_*"


class TestScriptHookStore:
    """Test ScriptHookStore CRUD operations."""

    def test_create_hook(self, hook_store: ScriptHookStore):
        hook = hook_store.create(
            {
                "name": "test-create",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo hello"},
                "timeout": 30,
            }
        )
        assert hook.name == "test-create"
        assert hook.event == HOOK_EVENT_USER_PROMPT_SUBMIT
        assert hook.enabled is True
        assert len(hook.id) > 0

    def test_get_hook(self, hook_store: ScriptHookStore):
        hook = hook_store.create(
            {
                "name": "test-get",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo test"},
            }
        )
        retrieved = hook_store.get(hook.id)
        assert retrieved is not None
        assert retrieved.id == hook.id
        assert retrieved.name == "test-get"

    def test_get_nonexistent(self, hook_store: ScriptHookStore):
        assert hook_store.get("nonexistent-id") is None

    def test_list_hooks(self, hook_store: ScriptHookStore):
        hook_store.create(
            {
                "name": "hook1",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo 1"},
            }
        )
        hook_store.create(
            {
                "name": "hook2",
                "event": HOOK_EVENT_PRE_TOOL_USE,
                "provider_config": {"command": "echo 2"},
            }
        )
        hooks = hook_store.list_all()
        assert len(hooks) == 2
        assert {h.name for h in hooks} == {"hook1", "hook2"}

    def test_update_hook(self, hook_store: ScriptHookStore):
        hook = hook_store.create(
            {
                "name": "test-update",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo original"},
            }
        )
        updated = hook_store.update(
            hook.id, {"name": "updated-name", "provider_config": {"command": "echo updated"}}
        )
        assert updated is not None
        assert updated.name == "updated-name"
        assert updated.provider_config == {"command": "echo updated"}

    def test_update_nonexistent(self, hook_store: ScriptHookStore):
        result = hook_store.update("nonexistent", {"name": "foo"})
        assert result is None

    def test_delete_hook(self, hook_store: ScriptHookStore):
        hook = hook_store.create(
            {
                "name": "test-delete",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo delete"},
            }
        )
        assert hook_store.delete(hook.id) is True
        assert hook_store.get(hook.id) is None

    def test_delete_nonexistent(self, hook_store: ScriptHookStore):
        assert hook_store.delete("nonexistent") is False

    def test_toggle_enabled(self, hook_store: ScriptHookStore):
        hook = hook_store.create(
            {
                "name": "test-toggle",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo toggle"},
            }
        )
        assert hook.enabled is True

        toggled = hook_store.toggle(hook.id)
        assert toggled is not None
        assert toggled.enabled is False

        toggled_again = hook_store.toggle(hook.id)
        assert toggled_again is not None
        assert toggled_again.enabled is True

    def test_persistence(self, tmp_path: Path):
        """Test that hooks persist to disk."""
        store1 = ScriptHookStore(tmp_path)
        hook = store1.create(
            {
                "name": "persist-test",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo persist"},
            }
        )

        # Load from same file
        store2 = ScriptHookStore(tmp_path)
        retrieved = store2.get(hook.id)
        assert retrieved is not None
        assert retrieved.name == "persist-test"


class TestRunScriptHook:
    """Test run_script_hook execution."""

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        hook = ScriptHook(
            id="test-1",
            name="success",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={"command": "echo success"},
            timeout=30,
            enabled=True,
            capabilities=_GRANTED,
        )
        result = await run_script_hook(hook, "test-context")
        assert result.hook_id == "test-1"
        assert result.exit_code == 0
        assert "success" in result.stdout
        assert result.error == ""
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_non_zero_exit(self):
        hook = ScriptHook(
            id="test-2",
            name="fail",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={"command": "exit 1"},
            timeout=30,
            enabled=True,
            capabilities=_GRANTED,
        )
        result = await run_script_hook(hook, "test-context")
        assert result.exit_code == 1
        assert result.error == ""  # exit code is not an error, just non-zero

    @pytest.mark.asyncio
    async def test_timeout(self):
        hook = ScriptHook(
            id="test-3",
            name="timeout",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={"command": "sleep 10"},
            timeout=1,
            enabled=True,
            capabilities=_GRANTED,
        )
        result = await run_script_hook(hook, "test-context")
        assert "Timed out" in result.error
        assert result.duration_ms >= 1000  # at least 1 second

    @pytest.mark.asyncio
    async def test_exit_code_2_blocks(self):
        """Exit code 2 means hook blocks the operation."""
        hook = ScriptHook(
            id="test-4",
            name="block",
            event=HOOK_EVENT_PRE_TOOL_USE,
            provider="bash",
            provider_config={"command": "exit 2"},
            timeout=30,
            enabled=True,
            capabilities=_GRANTED,
        )
        result = await run_script_hook(hook, "test-context")
        assert result.exit_code == 2

    @pytest.mark.skipif(_IS_MACOS, reason="Flaky stdin piping through macOS sandbox")
    @pytest.mark.asyncio
    async def test_stdin_json(self):
        """Hook receives JSON via stdin."""
        hook = ScriptHook(
            id="test-5",
            name="stdin",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={
                "command": f"{sys.executable} -c 'import sys, json; print(json.load(sys.stdin)[\"hook_event_name\"])'"  # noqa: E501
            },
            timeout=30,
            enabled=True,
            capabilities=_GRANTED,
        )
        result = await run_script_hook(hook, "test-context")
        assert result.exit_code == 0
        assert HOOK_EVENT_USER_PROMPT_SUBMIT in result.stdout

    @pytest.mark.asyncio
    async def test_env_vars(self):
        """Hook receives context via environment variables."""
        hook = ScriptHook(
            id="test-6",
            name="env",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={"command": "echo $PERSONALCLAW_HOOK_EVENT"},
            timeout=30,
            enabled=True,
            capabilities=_GRANTED,
        )
        result = await run_script_hook(hook, "test-context")
        assert HOOK_EVENT_USER_PROMPT_SUBMIT in result.stdout

    @pytest.mark.asyncio
    async def test_hook_updates_metadata(self):
        """Hook execution updates last_run, last_status, run_count."""
        hook = ScriptHook(
            id="test-7",
            name="metadata",
            event=HOOK_EVENT_USER_PROMPT_SUBMIT,
            provider="bash",
            provider_config={"command": "echo test"},
            timeout=30,
            enabled=True,
            capabilities=_GRANTED,
            last_run=0,
            last_status="",
            run_count=0,
        )
        await run_script_hook(hook, "test-context")
        assert hook.last_run > 0
        assert hook.last_status == "ok"
        assert hook.run_count == 1


class TestScriptHookStoreFire:
    """Test ScriptHookStore.fire() method."""

    @pytest.mark.asyncio
    async def test_fire_enabled_hooks(self, hook_store: ScriptHookStore):
        hook1 = hook_store.create(
            {
                "name": "enabled",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo enabled"},
                "timeout": 30,
                "capabilities": _GRANTED,
                "enabled": True,
            }
        )
        hook_store.create(
            {
                "name": "disabled",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo disabled"},
                "timeout": 30,
                "capabilities": _GRANTED,
                "enabled": False,
            }
        )
        results = await hook_store.fire(HOOK_EVENT_USER_PROMPT_SUBMIT, "test-context")
        assert len(results) == 1
        assert results[0].hook_id == hook1.id

    @pytest.mark.asyncio
    async def test_fire_correct_event(self, hook_store: ScriptHookStore):
        hook_store.create(
            {
                "name": "prompt-hook",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo prompt"},
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        hook_store.create(
            {
                "name": "tool-hook",
                "event": HOOK_EVENT_PRE_TOOL_USE,
                "provider_config": {"command": "echo tool"},
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        results = await hook_store.fire(HOOK_EVENT_USER_PROMPT_SUBMIT, "test-context")
        assert len(results) == 1
        assert "prompt" in results[0].stdout

    @pytest.mark.asyncio
    async def test_fire_with_matcher(self, hook_store: ScriptHookStore):
        hook_store.create(
            {
                "name": "fs-hook",
                "event": HOOK_EVENT_PRE_TOOL_USE,
                "provider_config": {"command": "echo matched"},
                "timeout": 30,
                "capabilities": _GRANTED,
                "matcher": "fs_*",
            }
        )
        # Should match
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="fs_write")
        assert len(results) == 1
        assert "matched" in results[0].stdout

        # Should not match
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="git_commit")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_fire_blocking_hook(self, hook_store: ScriptHookStore):
        """Exit code 2 means blocked."""
        hook_store.create(
            {
                "name": "blocker",
                "event": HOOK_EVENT_PRE_TOOL_USE,
                "provider_config": {"command": "exit 2"},
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test")
        assert len(results) == 1
        assert results[0].exit_code == 2

    @pytest.mark.asyncio
    async def test_fire_multiple_hooks(self, hook_store: ScriptHookStore):
        """Multiple hooks for same event fire in order."""
        hook_store.create(
            {
                "name": "first",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo first"},
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        hook_store.create(
            {
                "name": "second",
                "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
                "provider_config": {"command": "echo second"},
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        results = await hook_store.fire(HOOK_EVENT_USER_PROMPT_SUBMIT, "test")
        assert len(results) == 2
        # Results maintain insertion order
        assert "first" in results[0].stdout
        assert "second" in results[1].stdout

    @pytest.mark.skipif(_IS_MACOS, reason="Flaky stdin piping through macOS sandbox")
    @pytest.mark.asyncio
    async def test_fire_with_tool_input(self, hook_store: ScriptHookStore):
        """Tool input passed to hook via stdin."""
        hook_store.create(
            {
                "name": "input-hook",
                "event": HOOK_EVENT_PRE_TOOL_USE,
                "provider_config": {
                    "command": f'{sys.executable} -c \'import sys, json; print(json.load(sys.stdin).get("tool_input", {{}}).get("test_key"))\''  # noqa: E501
                },
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        results = await hook_store.fire(
            HOOK_EVENT_PRE_TOOL_USE,
            "test",
            tool_name="test_tool",
            tool_input={"test_key": "test_value"},
        )
        assert len(results) == 1
        assert "test_value" in results[0].stdout

    @pytest.mark.asyncio
    async def test_fire_no_hooks(self, hook_store: ScriptHookStore):
        """Fire with no matching hooks returns empty list."""
        results = await hook_store.fire(HOOK_EVENT_USER_PROMPT_SUBMIT, "test")
        assert results == []

    @pytest.mark.asyncio
    async def test_fire_agent_spawn_returns_stdout(self, hook_store: ScriptHookStore):
        """AgentSpawn hook stdout should be available for context injection."""
        hook_store.create(
            {
                "name": "startup-prefs",
                "event": HOOK_EVENT_AGENT_SPAWN,
                "provider_config": {"command": "echo 'Enable caveman mode'"},
                "timeout": 30,
                "capabilities": _GRANTED,
            }
        )
        results = await hook_store.fire(HOOK_EVENT_AGENT_SPAWN, "session-key")
        assert len(results) == 1
        assert results[0].succeeded
        assert "caveman" in results[0].stdout


def _hook(store: ScriptHookStore, name: str, command: str, **extra) -> ScriptHook:
    return store.create(
        {
            "name": name,
            "event": extra.pop("event", HOOK_EVENT_PRE_TOOL_USE),
            "provider_config": {"command": command},
            "timeout": extra.pop("timeout", 30),
            "capabilities": extra.pop("capabilities", _GRANTED),
            **extra,
        }
    )


class TestMatchingHooksRunSideBySide:
    """Matching hooks run side by side: a fire takes its slowest hook, not their sum (#4053)."""

    @pytest.mark.asyncio
    async def test_matching_hooks_run_side_by_side(self, hook_store: ScriptHookStore):
        import time

        # Run one after another, they take over 3.6 s; side by side, the slowest's 1.5 s.
        for name, wait in (("first", "1.5"), ("second", "1.2"), ("third", "0.9")):
            _hook(hook_store, name, f"sleep {wait}; echo {name}")
        started = time.monotonic()
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        took = time.monotonic() - started
        assert took < 3.0, f"the hooks ran one after another ({took:.1f} s)"
        assert [r.hook_name for r in results] == ["first", "second", "third"]
        for result, name in zip(results, ("first", "second", "third")):
            assert f"{name}\n" in result.stdout + "\n", "each result is its own hook's"

    @pytest.mark.asyncio
    async def test_the_same_hook_configured_twice_runs_once_per_fire(
        self, hook_store: ScriptHookStore, tmp_path: Path
    ):
        ran = tmp_path / "ran.txt"
        one = _hook(hook_store, "one", f"echo ran >> {ran}")
        two = _hook(hook_store, "two", f"echo ran >> {ran}")
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        assert ran.read_text().splitlines() == ["ran"]
        assert [(r.hook_id, r.exit_code) for r in results] == [(one.id, 0), (two.id, 0)]
        reread = ScriptHookStore(tmp_path)
        for hook in (one, two):
            held = reread.get(hook.id)
            assert (held.run_count, held.last_status) == (1, "ok")

    @pytest.mark.asyncio
    async def test_copies_with_different_grants_run_under_their_own(
        self, hook_store: ScriptHookStore, tmp_path: Path
    ):
        ran = tmp_path / "ran.txt"
        granted = _hook(hook_store, "granted", f"echo ran >> {ran}")
        ungranted = _hook(hook_store, "ungranted", f"echo ran >> {ran}", capabilities={})
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        assert ran.read_text().splitlines() == ["ran"], "one run, under the granted copy's yes"
        by_id = {r.hook_id: r for r in results}
        assert by_id[granted.id].exit_code == 0
        assert by_id[ungranted.id].exit_code != 0 and by_id[ungranted.id].error

    @pytest.mark.asyncio
    async def test_copies_with_different_timeouts_each_keep_their_own(
        self, hook_store: ScriptHookStore
    ):
        _hook(hook_store, "patient", "sleep 1.5; echo done", timeout=5)
        _hook(hook_store, "hasty", "sleep 1.5; echo done", timeout=1)
        results = await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        assert [(r.hook_name, r.exit_code) for r in results] == [("patient", 0), ("hasty", -1)]

    @pytest.mark.asyncio
    async def test_each_hook_keeps_how_long_its_last_run_took(
        self, hook_store: ScriptHookStore, tmp_path: Path
    ):
        hook = _hook(hook_store, "slow", "sleep 0.3; echo ok")
        await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        held = ScriptHookStore(tmp_path).get(hook.id)
        assert held.last_duration_ms >= 300
        assert held.to_dict()["last_duration_ms"] == held.last_duration_ms


class TestTheGatingSeamCombinesTheirAnswers:
    """On the gating seam the answers combine: a block wins, in configured order, and a hook
    that did not answer refuses the call, naming itself and how long it was given."""

    @staticmethod
    async def _verdict(store: ScriptHookStore, *hooks: ScriptHook):
        from personalclaw.pre_tool_hooks import _verdict

        results = await store.fire_for_ids(
            HOOK_EVENT_PRE_TOOL_USE, [h.id for h in hooks], tool_name="write_file"
        )
        return _verdict("write_file", results)

    @pytest.mark.asyncio
    async def test_a_block_beats_one_that_did_not_answer(self, hook_store: ScriptHookStore):
        """The hook that did not answer is first in the order and gives up first (1 s); the block
        comes later in both, and still speaks."""
        late = _hook(hook_store, "late", "sleep 3", timeout=1)
        block = _hook(hook_store, "policy", "sleep 1.6; echo 'not that file' >&2; exit 2")
        said = await self._verdict(hook_store, late, block)
        assert said.blocked and said.refusal == "policy:not that file"

    @pytest.mark.asyncio
    async def test_the_first_block_in_configured_order_speaks(self, hook_store: ScriptHookStore):
        """The second hook blocks first in time; the first in the order still speaks."""
        first = _hook(hook_store, "first", "sleep 0.8; echo 'first says no' >&2; exit 2")
        second = _hook(hook_store, "second", "echo 'second says no' >&2; exit 2")
        said = await self._verdict(hook_store, first, second)
        assert said.refusal == "first:first says no"

    @pytest.mark.asyncio
    async def test_a_hook_that_does_not_answer_blocks_on_the_gating_seam(
        self, hook_store: ScriptHookStore
    ):
        late = _hook(hook_store, "late", "sleep 3", timeout=1)
        said = await self._verdict(hook_store, late)
        assert said.refused and not said.blocked
        assert said.error == "“late” did not answer in 1 s"


def test_a_hook_from_another_home_arrives_without_its_last_runs_duration() -> None:
    from personalclaw.hooks import HOOK_RUNTIME_FIELDS, hook_arrived_from_another_home

    row = ScriptHook(id="h1", name="policy", last_duration_ms=1200, last_status="ok").to_dict()
    assert "last_duration_ms" in HOOK_RUNTIME_FIELDS
    assert "last_duration_ms" not in hook_arrived_from_another_home(row)


@pytest.mark.asyncio
async def test_a_fire_stopped_midway_leaves_none_of_its_hooks_running(
    hook_store: ScriptHookStore, tmp_path: Path
):
    """A stopped turn cancels its fire: every hook running side by side is ended, not only one."""
    import asyncio
    import os

    pids = [tmp_path / f"pid-{n}" for n in range(3)]
    for n, pid in enumerate(pids):
        _hook(hook_store, f"waits-{n}", f"echo $$ > {pid}; exec sleep 30")
    fire = asyncio.create_task(
        hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
    )
    for _ in range(100):
        if all(p.exists() and p.read_text().strip() for p in pids):
            break
        await asyncio.sleep(0.05)
    assert all(p.exists() for p in pids), "the hooks did not all start side by side"
    fire.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fire

    def alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    left = [int(p.read_text()) for p in pids if alive(int(p.read_text()))]
    assert left == [], f"hook processes still running after the stop: {left}"


def test_the_automations_row_serves_how_long_the_hooks_last_run_took() -> None:
    from personalclaw.dashboard.handlers.triggers import _serialize_lifecycle

    hook = ScriptHook(id="h1", name="policy", event=HOOK_EVENT_PRE_TOOL_USE, last_duration_ms=1200)
    assert _serialize_lifecycle(hook, [])["last_duration_ms"] == 1200
    assert _serialize_lifecycle(ScriptHook(id="h2"), [])["last_duration_ms"] == 0


def test_a_hooks_run_does_not_make_an_open_edit_of_it_stale() -> None:
    """A run stamps how long it took on the row; the edit form's revision does not move with it."""
    from personalclaw.dashboard.handlers.trigger_revisions import with_revision
    from personalclaw.dashboard.handlers.triggers import _serialize_lifecycle

    before = ScriptHook(id="h1", name="policy", event=HOOK_EVENT_PRE_TOOL_USE)
    after = ScriptHook(id="h1", name="policy", event=HOOK_EVENT_PRE_TOOL_USE, last_duration_ms=900)
    revision = with_revision(_serialize_lifecycle(before, []), schedule=False)["revision"]
    assert with_revision(_serialize_lifecycle(after, []), schedule=False)["revision"] == revision


def _recorded(monkeypatch, wait: float = 0.3) -> dict:
    """Each hook's run, as when it started and ended, in place of running its action."""
    import asyncio
    import time

    from personalclaw.hooks import ScriptHookResult

    spans: dict = {}

    async def run(hook, context="", hook_event=None, **_kw):
        started = time.monotonic()
        await asyncio.sleep(wait)
        spans[hook.name] = (started, time.monotonic())
        return ScriptHookResult(hook_id=hook.id, hook_name=hook.name, event=hook.event, exit_code=0)

    monkeypatch.setattr("personalclaw.hooks.run_script_hook", run)
    return spans


def _overlap(a: tuple, b: tuple) -> bool:
    return a[0] < b[1] and b[0] < a[1]


class TestWhichHooksKeepTheirOrder:
    """Off the owner's own policy commands on a tool call, hooks keep their configured order, one
    after another: a store written without a lock, a delivery, and an app's provider (#4053)."""

    @staticmethod
    def _hooks(store: ScriptHookStore, event: str, *providers: str) -> None:
        for n, provider in enumerate(providers):
            store.create(
                {
                    "name": f"h{n}",
                    "event": event,
                    "provider": provider,
                    "provider_config": {"n": n},
                    "capabilities": {"providers": [provider]},
                }
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "providers",
        [("inbox-op", "knowledge-persist"), ("notify", "send-message"), ("acme-webhook",) * 2],
        ids=["store writers", "deliveries", "an app's provider"],
    )
    async def test_on_a_tool_call_these_keep_their_order(self, hook_store, monkeypatch, providers):
        spans = _recorded(monkeypatch)
        self._hooks(hook_store, HOOK_EVENT_PRE_TOOL_USE, *providers)
        await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        assert spans["h0"][1] <= spans["h1"][0], "they ran at once"

    @pytest.mark.asyncio
    async def test_on_a_tool_call_policy_commands_run_at_once_beside_the_rest(
        self, hook_store, monkeypatch
    ):
        spans = _recorded(monkeypatch)
        self._hooks(hook_store, HOOK_EVENT_PRE_TOOL_USE, "bash", "run-script", "inbox-op")
        await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        assert _overlap(spans["h0"], spans["h1"]), "the policy commands ran one after another"
        assert _overlap(spans["h0"], spans["h2"]), "the one in order waited for the rest"

    @pytest.mark.asyncio
    async def test_on_other_events_every_hook_keeps_its_order(self, hook_store, monkeypatch):
        spans = _recorded(monkeypatch)
        self._hooks(hook_store, HOOK_EVENT_USER_PROMPT_SUBMIT, "bash", "run-script", "bash")
        results = await hook_store.fire(HOOK_EVENT_USER_PROMPT_SUBMIT, "test")
        assert spans["h0"][1] <= spans["h1"][0] and spans["h1"][1] <= spans["h2"][0]
        assert [r.hook_name for r in results] == ["h0", "h1", "h2"]


@pytest.mark.asyncio
async def test_a_stop_ends_the_hook_running_in_order_and_starts_no_other(
    hook_store: ScriptHookStore, tmp_path: Path
):
    import asyncio
    import os

    first, second = tmp_path / "pid-1", tmp_path / "pid-2"
    for pid in (first, second):
        _hook(hook_store, pid.name, f"echo $$ > {pid}; exec sleep 30", event=HOOK_EVENT_AGENT_SPAWN)
    fire = asyncio.create_task(hook_store.fire(HOOK_EVENT_AGENT_SPAWN, "session-key"))
    for _ in range(100):
        if first.exists() and first.read_text().strip():
            break
        await asyncio.sleep(0.05)
    fire.cancel()
    with pytest.raises(asyncio.CancelledError):
        await fire
    await asyncio.sleep(0.3)
    assert not second.exists(), "a hook in order started after the stop"
    try:
        os.kill(int(first.read_text()), 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "the hook running when the turn stopped is still running"


class TestARunsTimeAndItsDurationArePaired:
    """Every run that stamps when it ran stamps how long it took, so the two always describe the
    same run: 0 for a run that was not timed. A rehearsal stamps neither."""

    @pytest.mark.asyncio
    async def test_a_rehearsal_after_a_timed_run_leaves_the_pair_as_it_was(
        self, hook_store: ScriptHookStore
    ):
        hook = _hook(hook_store, "slow", "sleep 0.3; echo ok")
        await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        ran, took = hook.last_run, hook.last_duration_ms
        assert ran and took >= 300
        await run_script_hook(hook, "test", test=True)
        assert (hook.last_run, hook.last_duration_ms) == (ran, took)

    @pytest.mark.asyncio
    async def test_a_run_that_was_not_timed_clears_the_older_runs_duration(
        self, hook_store: ScriptHookStore
    ):
        hook = _hook(hook_store, "slow", "sleep 0.3; echo ok")
        await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        ran = hook.last_run
        hook.capabilities = {}  # its next fire is refused before it runs
        await hook_store.fire(HOOK_EVENT_PRE_TOOL_USE, "test", tool_name="read_file")
        assert hook.last_run > ran
        assert hook.last_duration_ms == 0, "an older run's time shows beside a newer run"
