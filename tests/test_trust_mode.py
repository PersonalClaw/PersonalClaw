"""Tests for the canonical process-global YOLO / auto-approve trust state."""

from unittest.mock import patch

import personalclaw.trust_mode as tm


class TestTrustModeCore:
    def test_default_off(self) -> None:
        assert tm.is_yolo_active() is False
        assert tm.yolo_from_config() is False
        assert tm.yolo_remaining_secs() is None

    def test_surface_enable_with_ttl(self) -> None:
        tm.enable_yolo(ttl_secs=1800)
        assert tm.is_yolo_active() is True
        assert tm.yolo_from_config() is False
        rem = tm.yolo_remaining_secs()
        assert rem is not None and 0 < rem <= 1800

    def test_ttl_expiry_on_read(self) -> None:
        tm.enable_yolo(ttl_secs=1800)
        tm._TRUST._expires_at = 1.0  # positive but far in the past
        assert tm.is_yolo_active() is False

    def test_config_permanent_never_expires(self) -> None:
        tm.enable_yolo(from_config=True)
        assert tm.yolo_from_config() is True
        assert tm.yolo_remaining_secs() is None
        with patch("time.monotonic", return_value=9e12):
            assert tm.is_yolo_active() is True

    def test_surface_cannot_downgrade_config(self) -> None:
        tm.enable_yolo(from_config=True)
        tm.enable_yolo(ttl_secs=60)  # no-op
        assert tm.yolo_from_config() is True
        assert tm.yolo_remaining_secs() is None

    def test_disable_clears_config(self) -> None:
        tm.enable_yolo(from_config=True)
        tm.disable_yolo()
        assert tm.is_yolo_active() is False
        assert tm.yolo_from_config() is False


class TestOnDisableCallbacks:
    def test_manual_disable_fires_callback(self) -> None:
        seen = []
        tm.register_on_disable(lambda reason: seen.append(reason))
        tm.enable_yolo(ttl_secs=60)
        tm.disable_yolo()
        assert "manual" in seen

    def test_expiry_fires_callback(self) -> None:
        seen = []
        tm.register_on_disable(lambda reason: seen.append(reason))
        tm.enable_yolo(ttl_secs=60)
        tm._TRUST._expires_at = 1.0
        tm.is_yolo_active()  # triggers expiry
        assert "expired" in seen

    def test_callback_exception_is_swallowed(self) -> None:
        def boom(_reason: str) -> None:
            raise RuntimeError("cb failed")

        tm.register_on_disable(boom)
        tm.enable_yolo(ttl_secs=60)
        # must not raise despite the failing callback
        tm.disable_yolo()
        assert tm.is_yolo_active() is False
