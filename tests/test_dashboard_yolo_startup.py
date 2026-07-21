"""Tests that ``agent.yolo=true`` enables dashboard YOLO at gateway startup."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from personalclaw.dashboard.server import _apply_startup_yolo
from personalclaw.dashboard.state import DashboardState


def _make_state() -> DashboardState:
    return DashboardState(
        sessions=MagicMock(),
        crons=MagicMock(),
        lessons=MagicMock(),
        start_time=0.0,
    )


def _cfg(yolo: bool) -> SimpleNamespace:
    return SimpleNamespace(agent=SimpleNamespace(yolo=yolo))


def test_apply_startup_yolo_enables_when_config_true() -> None:
    """``agent.yolo=true`` activates dashboard YOLO and emits an SEL audit event."""
    state = _make_state()
    assert state.is_yolo_active() is False

    with patch("personalclaw.sel.sel") as mock_sel:
        _apply_startup_yolo(state, _cfg(yolo=True))

    assert state.is_yolo_active() is True
    mock_sel.return_value.log_api_access.assert_called_once()
    kwargs = mock_sel.return_value.log_api_access.call_args.kwargs
    assert kwargs["caller"] == "dashboard:startup"
    assert kwargs["operation"] == "mode_change:yolo"
    assert kwargs["outcome"] == "enabled"


def test_apply_startup_yolo_noop_when_config_false() -> None:
    """``agent.yolo=false`` leaves dashboard in interactive mode and emits no audit."""
    state = _make_state()

    with patch("personalclaw.sel.sel") as mock_sel:
        _apply_startup_yolo(state, _cfg(yolo=False))

    assert state.is_yolo_active() is False
    mock_sel.return_value.log_api_access.assert_not_called()


def test_apply_startup_yolo_refuses_when_sel_fails() -> None:
    """SEL audit failures must block YOLO activation (fail-closed audit trail)."""
    state = _make_state()

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("sel down")

    with patch("personalclaw.sel.sel", side_effect=_boom):
        _apply_startup_yolo(state, _cfg(yolo=True))

    assert state.is_yolo_active() is False


def test_apply_startup_yolo_refuses_when_log_api_access_fails() -> None:
    """Audit emission failure (not just sel() resolution) must also fail closed."""
    state = _make_state()

    with patch("personalclaw.sel.sel") as mock_sel:
        mock_sel.return_value.log_api_access.side_effect = RuntimeError("sel write failed")
        _apply_startup_yolo(state, _cfg(yolo=True))

    assert state.is_yolo_active() is False


class TestDashboardYoloFromConfig:
    """Config-driven YOLO is permanent (no 6h TTL) and cannot be downgraded to a
    TTL by a subsequent interactive toggle. State delegates to the canonical
    :mod:`personalclaw.trust_mode`; these assert on the observable posture and its
    single source of truth.
    """

    def test_from_config_sets_flag_and_no_ttl(self) -> None:
        import personalclaw.trust_mode as tm

        state = _make_state()
        state.enable_yolo(from_config=True)
        assert state.is_yolo_active() is True
        assert tm.yolo_from_config() is True
        assert state.yolo_remaining_secs() is None  # permanent → no countdown

    def test_interactive_enable_cannot_downgrade_config(self) -> None:
        """Dashboard toggle must not overwrite config-permanent yolo with 6h TTL."""
        state = _make_state()
        state.enable_yolo(from_config=True)
        state.enable_yolo()  # interactive — should be no-op
        assert state.yolo_remaining_secs() is None, "Config yolo should remain permanent"

    def test_config_yolo_never_expires(self) -> None:
        state = _make_state()
        state.enable_yolo(from_config=True)
        with patch("time.monotonic", return_value=9999999999.0):
            assert state.is_yolo_active() is True

    def test_disable_clears_from_config(self) -> None:
        import personalclaw.trust_mode as tm

        state = _make_state()
        state.enable_yolo(from_config=True)
        state.disable_yolo()
        assert tm.yolo_from_config() is False
        assert state.is_yolo_active() is False
