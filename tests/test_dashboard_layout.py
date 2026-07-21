"""Tests for the dashboard layout sanitizer (Slice 5 — customization).

`_sanitize_dashboard_layout` guards the persisted home-dashboard layout: it must
accept a valid layout, coerce/clamp numeric fields to the 12-col grid, drop
unknown/duplicate widget ids, treat empty as reset, and reject malformed shapes
(→ 400 at the handler)."""

from personalclaw.dashboard.handlers.files import _sanitize_dashboard_layout


class TestSanitizeDashboardLayout:
    def test_empty_is_reset(self) -> None:
        assert _sanitize_dashboard_layout({}) == {}
        assert _sanitize_dashboard_layout(None) == {}

    def test_valid_layout_round_trips(self) -> None:
        out = _sanitize_dashboard_layout(
            {"widgets": [{"id": "hero", "x": 0, "y": 0, "w": 12, "h": 1}], "v": 1}
        )
        assert out == {
            "widgets": [{"id": "hero", "x": 0, "y": 0, "w": 12, "h": 1, "hidden": False}],
            "v": 1,
        }

    def test_hidden_flag_preserved(self) -> None:
        out = _sanitize_dashboard_layout(
            {"widgets": [{"id": "memory", "x": 9, "y": 6, "w": 3, "h": 2, "hidden": True}]}
        )
        assert out["widgets"][0]["hidden"] is True

    def test_clamps_out_of_range(self) -> None:
        out = _sanitize_dashboard_layout(
            {"widgets": [{"id": "tasks", "x": 99, "y": -5, "w": 99, "h": 99}]}
        )
        w = out["widgets"][0]
        assert 0 <= w["x"] <= 11
        assert w["y"] >= 0
        assert 1 <= w["w"] <= 12
        assert 1 <= w["h"] <= 12

    def test_drops_unknown_widget_ids(self) -> None:
        out = _sanitize_dashboard_layout(
            {
                "widgets": [
                    {"id": "hero", "x": 0, "y": 0, "w": 12, "h": 1},
                    {"id": "totally-not-a-widget", "x": 0, "y": 1, "w": 4, "h": 2},
                ]
            }
        )
        assert [w["id"] for w in out["widgets"]] == ["hero"]

    def test_drops_duplicate_ids(self) -> None:
        out = _sanitize_dashboard_layout(
            {
                "widgets": [
                    {"id": "hero", "x": 0, "y": 0, "w": 12, "h": 1},
                    {"id": "hero", "x": 0, "y": 2, "w": 6, "h": 1},
                ]
            }
        )
        assert len(out["widgets"]) == 1

    def test_all_unknown_collapses_to_reset(self) -> None:
        # A layout whose every id is unknown yields no widgets → treated as reset.
        assert (
            _sanitize_dashboard_layout(
                {"widgets": [{"id": "nope", "x": 0, "y": 0, "w": 4, "h": 2}]}
            )
            == {}
        )

    def test_rejects_non_dict(self) -> None:
        assert _sanitize_dashboard_layout("not-a-dict") is None
        assert _sanitize_dashboard_layout([1, 2, 3]) is None

    def test_rejects_bad_widgets_shape(self) -> None:
        assert _sanitize_dashboard_layout({"widgets": "nope"}) is None
        assert _sanitize_dashboard_layout({"widgets": ["not-a-dict"]}) is None

    def test_rejects_non_numeric_coords(self) -> None:
        assert (
            _sanitize_dashboard_layout(
                {"widgets": [{"id": "hero", "x": "abc", "y": 0, "w": 12, "h": 1}]}
            )
            is None
        )
