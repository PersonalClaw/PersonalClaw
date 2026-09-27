"""Tests for the canonical process-global YOLO / auto-approve trust state."""

import json
from unittest.mock import patch

import personalclaw.config.loader as loader
import personalclaw.trust_mode as tm


def _config_yolo(on: bool) -> None:
    """``agent.yolo`` in the config, where config-driven YOLO is read back from while it is on."""
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("agent", {})["yolo"] = on
    path.write_text(json.dumps(data))


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
        _config_yolo(True)
        tm.enable_yolo(from_config=True)
        assert tm.yolo_from_config() is True
        assert tm.yolo_remaining_secs() is None
        with patch("time.monotonic", return_value=9e12):
            assert tm.is_yolo_active() is True

    def test_surface_cannot_downgrade_config(self) -> None:
        _config_yolo(True)
        tm.enable_yolo(from_config=True)
        tm.enable_yolo(ttl_secs=60)  # no-op
        assert tm.yolo_from_config() is True
        assert tm.yolo_remaining_secs() is None

    def test_disable_clears_config(self) -> None:
        _config_yolo(True)
        tm.enable_yolo(from_config=True)
        tm.disable_yolo()
        assert tm.is_yolo_active() is False
        assert tm.yolo_from_config() is False


class TestConfigYoloFollowsTheConfig:
    """Config-driven YOLO ends when the config no longer sets it, however the config changed.

    It was applied once at startup and then only by the Settings PATCH, so a Durability rollback,
    ``personalclaw config set agent.yolo false`` or a hand edit left YOLO approving every call until
    a restart (`approval_grants`, rule 1).
    """

    def test_taking_it_out_of_the_config_ends_it(self) -> None:
        _config_yolo(True)
        tm.enable_yolo(from_config=True)
        assert tm.is_yolo_active() is True, "premise"
        seen: list[str] = []
        tm.register_on_disable(seen.append)
        _config_yolo(False)  # a rollback, the CLI, a hand edit — not the Settings switch
        assert tm.is_yolo_active() is False
        assert tm.yolo_from_config() is False
        assert seen == ["config"]

    def test_putting_it_back_does_not_turn_it_on(self) -> None:
        """Only in the revocation direction: turning it ON takes Settings or a restart, the two
        paths that audit it."""
        _config_yolo(False)
        assert tm.is_yolo_active() is False
        _config_yolo(True)
        assert tm.is_yolo_active() is False

    def test_a_surface_yolo_does_not_read_the_config(self) -> None:
        """The dashboard pill and a channel's ``!yolo`` are not config-driven: a TTL ends them."""
        _config_yolo(False)
        tm.enable_yolo(ttl_secs=600)
        assert tm.is_yolo_active() is True


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
