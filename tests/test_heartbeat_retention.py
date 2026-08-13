"""Tests for heartbeat task retention via HEARTBEAT_KEEP sentinel."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import personalclaw.heartbeat as hb_mod
from personalclaw.heartbeat import (
    _HEADER,
    HeartbeatService,
    _should_keep,
    is_keep_response,
    strip_keep_sentinel,
)


class TestShouldKeep:
    def test_none_returns_false(self) -> None:
        assert not _should_keep(None)

    def test_empty_string_returns_false(self) -> None:
        assert not _should_keep("")

    def test_normal_response_returns_false(self) -> None:
        assert not _should_keep("Ticket is resolved. All done!")

    def test_sentinel_returns_true(self) -> None:
        assert _should_keep("Ticket still open. HEARTBEAT_KEEP")

    def test_sentinel_case_insensitive(self) -> None:
        assert _should_keep("Not done yet. heartbeat_keep")

    def test_sentinel_mid_text(self) -> None:
        assert _should_keep("Status: Assigned. HEARTBEAT_KEEP — will retry.")


class TestStripKeepSentinel:
    def test_removes_sentinel(self) -> None:
        assert strip_keep_sentinel("Still open. HEARTBEAT_KEEP") == "Still open."

    def test_no_sentinel_unchanged(self) -> None:
        assert strip_keep_sentinel("All done!") == "All done!"

    def test_empty_string(self) -> None:
        assert strip_keep_sentinel("") == ""


class TestHeartbeatRetention:
    @pytest.mark.asyncio
    async def test_completed_task_removed(self, tmp_path: Path) -> None:
        """Task without HEARTBEAT_KEEP is removed after processing."""

        async def on_task(text: str, deliver: str) -> str:
            return "Done! Ticket resolved."

        svc = HeartbeatService(memory=MagicMock(), on_task=on_task)
        hb_path = tmp_path / "HEARTBEAT.md"
        hb_path.write_text(_HEADER + "- Check ticket ABC\n")

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: hb_path
        try:
            await svc._process_heartbeat_file()
        finally:
            hb_mod.heartbeat_path = original

        content = hb_path.read_text()
        assert "Check ticket ABC" not in content

    @pytest.mark.asyncio
    async def test_incomplete_task_kept(self, tmp_path: Path) -> None:
        """Task with HEARTBEAT_KEEP in response is retained."""

        async def on_task(text: str, deliver: str) -> str:
            return "Ticket still Assigned. HEARTBEAT_KEEP"

        svc = HeartbeatService(memory=MagicMock(), on_task=on_task)
        hb_path = tmp_path / "HEARTBEAT.md"
        hb_path.write_text(_HEADER + "- Check ticket ABC\n")

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: hb_path
        try:
            await svc._process_heartbeat_file()
        finally:
            hb_mod.heartbeat_path = original

        content = hb_path.read_text()
        assert "Check ticket ABC" in content

    @pytest.mark.asyncio
    async def test_mixed_tasks_partial_retention(self, tmp_path: Path) -> None:
        """Only incomplete tasks are retained; completed ones are removed."""

        async def on_task(text: str, deliver: str) -> str:
            if "pending" in text:
                return "Still pending. HEARTBEAT_KEEP"
            return "All done!"

        svc = HeartbeatService(memory=MagicMock(), on_task=on_task)
        hb_path = tmp_path / "HEARTBEAT.md"
        hb_path.write_text(_HEADER + "- Check pending ticket\n- Check resolved ticket\n")

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: hb_path
        try:
            await svc._process_heartbeat_file()
        finally:
            hb_mod.heartbeat_path = original

        content = hb_path.read_text()
        assert "pending ticket" in content
        assert "resolved ticket" not in content

    @pytest.mark.asyncio
    async def test_exception_still_retains(self, tmp_path: Path) -> None:
        """Tasks that raise exceptions are still retained."""

        async def on_task(text: str, deliver: str) -> str:
            raise RuntimeError("connection error")

        svc = HeartbeatService(memory=MagicMock(), on_task=on_task)
        hb_path = tmp_path / "HEARTBEAT.md"
        hb_path.write_text(_HEADER + "- Check ticket XYZ\n")

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: hb_path
        try:
            await svc._process_heartbeat_file()
        finally:
            hb_mod.heartbeat_path = original

        content = hb_path.read_text()
        assert "Check ticket XYZ" in content

    @pytest.mark.asyncio
    async def test_none_return_treated_as_complete(self, tmp_path: Path) -> None:
        """Callback returning None removes the task."""

        async def on_task(text: str, deliver: str) -> None:
            pass

        svc = HeartbeatService(memory=MagicMock(), on_task=on_task)
        hb_path = tmp_path / "HEARTBEAT.md"
        hb_path.write_text(_HEADER + "- Legacy task\n")

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: hb_path
        try:
            await svc._process_heartbeat_file()
        finally:
            hb_mod.heartbeat_path = original

        content = hb_path.read_text()
        assert "Legacy task" not in content

    @pytest.mark.asyncio
    async def test_deliver_tag_preserved_on_keep(self, tmp_path: Path) -> None:
        """Deliver target is preserved when task is retained."""

        async def on_task(text: str, deliver: str) -> str:
            return "Not done. HEARTBEAT_KEEP"

        svc = HeartbeatService(memory=MagicMock(), on_task=on_task)
        hb_path = tmp_path / "HEARTBEAT.md"
        hb_path.write_text(_HEADER + "- Check ticket  <!-- deliver:C08HZAWV4TP -->\n")

        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: hb_path
        try:
            await svc._process_heartbeat_file()
        finally:
            hb_mod.heartbeat_path = original

        content = hb_path.read_text()
        assert "Check ticket" in content
        assert "deliver:C08HZAWV4TP" in content


class TestDeliverySuppression:
    """Tests for the gateway-level delivery suppression logic.

    The gateway suppresses _deliver_result when is_keep_response() returns True.
    """

    def test_incomplete_task_suppresses_delivery(self) -> None:
        """When HEARTBEAT_KEEP is present → suppress."""
        assert is_keep_response("Ticket still Assigned. HEARTBEAT_KEEP")

    def test_completed_task_allows_delivery(self) -> None:
        """When no HEARTBEAT_KEEP → deliver."""
        assert not is_keep_response("Ticket resolved! All done.")

    def test_no_response_allows_delivery(self) -> None:
        """_No response._ (fallback) has no sentinel → deliver."""
        assert not is_keep_response("_No response._")

    def test_none_allows_delivery(self) -> None:
        """None response → deliver."""
        assert not is_keep_response(None)

    def test_case_insensitive(self) -> None:
        """Lowercase variant also suppresses."""
        assert is_keep_response("Still checking. heartbeat_keep")


class TestMaintenanceOwnership:
    """PLATFORM-RESILIENCE §4.4 / PR2-11 — success criterion #6.

    Store maintenance has exactly ONE owner per tick. With the remediation engine enabled
    (the default) the heartbeat runs none of it; with the engine disabled the heartbeat's
    legacy pass runs all of it on its original cadence, so turning the engine off can never
    leave a system with NO maintenance.
    """

    @staticmethod
    def _service(monkeypatch, tmp_path, *, engine_enabled: bool, tick: int):
        """A heartbeat driven for exactly one beat, with the engine flag forced."""
        import time
        from types import SimpleNamespace

        from personalclaw.config.loader import AppConfig

        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        fake = SimpleNamespace(
            resilience=SimpleNamespace(
                remediation=SimpleNamespace(
                    enabled=engine_enabled,
                    target_score=90,
                    max_cost_usd=1.0,
                    idle_minutes_healthy=60,
                    tick_minutes_degraded=5,
                )
            ),
            memory=SimpleNamespace(history_max_days=365),
        )
        monkeypatch.setattr(AppConfig, "load", staticmethod(lambda *a, **k: fake))

        svc = HeartbeatService.__new__(HeartbeatService)
        svc._tick = tick
        svc._processing = True  # skip the HEARTBEAT.md work
        svc._memory = MagicMock()
        svc._memory.rebuild_index.return_value = 7
        svc._consolidator = None
        svc._on_due_commitments = None
        svc._on_auto_archive = None
        svc._interval = 60
        # enabled + not due → the engine owns maintenance without doing a real run
        svc._remediation_next_ts = time.time() + 3600
        return svc

    @pytest.mark.asyncio
    async def test_engine_enabled_means_heartbeat_runs_no_maintenance(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The retirement itself: on the very ticks the legacy pass used to fire, an enabled
        engine means the heartbeat touches nothing."""
        aged = []
        pruned = []
        monkeypatch.setattr("personalclaw.skills.curator.run_aging", lambda *a, **k: aged.append(1))
        monkeypatch.setattr(
            "personalclaw.sel.sel", lambda: MagicMock(prune=lambda: pruned.append(1))
        )
        for tick in (hb_mod._FTS_REBUILD_TICKS, hb_mod._PRUNE_TICKS):
            svc = self._service(monkeypatch, tmp_path, engine_enabled=True, tick=tick)
            original = hb_mod.heartbeat_path
            hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
            try:
                await svc._beat()
            finally:
                hb_mod.heartbeat_path = original
            svc._memory.rebuild_index.assert_not_called()
            svc._memory.prune_history.assert_not_called()
        assert aged == [] and pruned == []

    @pytest.mark.asyncio
    async def test_engine_disabled_restores_every_maintenance_pass(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """`remediation.enabled=false` must not leave the user with no maintenance at all —
        the documented fallback, tested rather than asserted in prose."""
        aged = []
        pruned = []
        monkeypatch.setattr("personalclaw.skills.curator.run_aging", lambda *a, **k: aged.append(1))
        monkeypatch.setattr(
            "personalclaw.sel.sel", lambda: MagicMock(prune=lambda: pruned.append(1))
        )

        # the FTS cadence
        svc = self._service(
            monkeypatch, tmp_path, engine_enabled=False, tick=hb_mod._FTS_REBUILD_TICKS
        )
        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
        try:
            await svc._beat()
            svc._memory.rebuild_index.assert_called_once()

            # the daily cadence: history prune + SEL prune + skill aging
            svc = self._service(
                monkeypatch, tmp_path, engine_enabled=False, tick=hb_mod._PRUNE_TICKS
            )
            await svc._beat()
        finally:
            hb_mod.heartbeat_path = original
        svc._memory.prune_history.assert_called_once_with(keep_days=365)
        assert pruned == [1], "SEL prune did not run in the fallback"
        assert aged == [1], "skill aging did not run in the fallback"

    @pytest.mark.asyncio
    async def test_engine_failure_falls_back_instead_of_skipping_maintenance(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """If the engine itself raises, maintenance must still happen this tick — a broken
        owner is the one case where the heartbeat takes over (logged, not silent)."""
        svc = self._service(
            monkeypatch, tmp_path, engine_enabled=True, tick=hb_mod._FTS_REBUILD_TICKS
        )

        async def _boom() -> bool:
            raise RuntimeError("engine exploded")

        monkeypatch.setattr(svc, "_maybe_remediate", _boom, raising=False)
        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
        try:
            await svc._beat()  # must not raise
        finally:
            hb_mod.heartbeat_path = original
        svc._memory.rebuild_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_enabled_but_not_due_still_owns_maintenance(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Ownership follows the config flag, not whether this particular tick was due —
        otherwise every non-due tick would re-run the legacy pass."""
        svc = self._service(
            monkeypatch, tmp_path, engine_enabled=True, tick=hb_mod._FTS_REBUILD_TICKS
        )
        assert await svc._maybe_remediate() is True
        svc._memory.rebuild_index.assert_not_called()


class TestCommitmentDeliveryHook:
    """M5e: the heartbeat invokes the on_due_commitments callback each beat
    (the proactive-check-in delivery driver), guarded so it never kills the tick."""

    @pytest.mark.asyncio
    async def test_beat_invokes_due_commitments(self, tmp_path: Path) -> None:
        calls = []

        async def on_due() -> None:
            calls.append(1)

        mem = MagicMock()
        mem.rebuild_index.return_value = 0
        svc = HeartbeatService(memory=mem, on_task=None, on_due_commitments=on_due)
        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
        try:
            await svc._beat()
        finally:
            hb_mod.heartbeat_path = original
        assert calls == [1]

    @pytest.mark.asyncio
    async def test_beat_survives_delivery_error(self, tmp_path: Path) -> None:
        """A delivery callback raising must not crash the beat."""

        async def on_due() -> None:
            raise RuntimeError("delivery boom")

        mem = MagicMock()
        mem.rebuild_index.return_value = 0
        svc = HeartbeatService(memory=mem, on_task=None, on_due_commitments=on_due)
        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
        try:
            await svc._beat()  # must not raise
        finally:
            hb_mod.heartbeat_path = original

    @pytest.mark.asyncio
    async def test_no_hook_is_noop(self, tmp_path: Path) -> None:
        """When the gateway didn't wire delivery (None), the beat still runs."""
        mem = MagicMock()
        mem.rebuild_index.return_value = 0
        svc = HeartbeatService(memory=mem, on_task=None)
        original = hb_mod.heartbeat_path
        hb_mod.heartbeat_path = lambda: tmp_path / "HEARTBEAT.md"
        try:
            await svc._beat()  # must not raise
        finally:
            hb_mod.heartbeat_path = original
