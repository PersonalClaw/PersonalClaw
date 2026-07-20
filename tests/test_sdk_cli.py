"""Tests for personalclaw.sdk.cli — SetupContext + DoctorLine contract (Plan 32)."""
from personalclaw.sdk.cli import DoctorLine, SetupContext


class TestDoctorLine:
    def test_minimal(self):
        line = DoctorLine(label="Bot token", status="ok")
        assert line.label == "Bot token"
        assert line.status == "ok"
        assert line.detail == ""

    def test_with_detail(self):
        line = DoctorLine(label="Workspace", status="warn", detail="not verified")
        assert line.detail == "not verified"

    def test_all_statuses(self):
        for status in ("ok", "warn", "fail", "info"):
            assert DoctorLine(label="x", status=status).status == status


class TestSetupContext:
    def test_construct_and_defaults(self):
        store: dict[str, str] = {}
        ctx = SetupContext(
            app_name="slack-channel",
            get_credential=lambda k: store.get(k, ""),
            save_credential=lambda k, v: store.__setitem__(k, v),
            settings=object(),  # ProviderSettings handle (duck-typed here)
        )
        # print/input default to the builtins
        assert ctx.print is print
        assert ctx.input is input
        assert ctx.app_name == "slack-channel"

    def test_credential_accessors_round_trip(self):
        store: dict[str, str] = {}
        ctx = SetupContext(
            app_name="app",
            get_credential=lambda k: store.get(k, ""),
            save_credential=lambda k, v: store.__setitem__(k, v),
            settings=object(),
        )
        assert ctx.get_credential("MISSING") == ""
        ctx.save_credential("TOKEN", "secret-value")
        assert ctx.get_credential("TOKEN") == "secret-value"

    def test_injected_io(self):
        printed: list[str] = []
        ctx = SetupContext(
            app_name="app",
            get_credential=lambda k: "",
            save_credential=lambda k, v: None,
            settings=object(),
            print=printed.append,
            input=lambda prompt: "",  # non-interactive → empty
        )
        ctx.print("hello")
        assert printed == ["hello"]
        assert ctx.input("prompt? ") == ""
