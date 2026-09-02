"""KL-18 — the retrieval payload returns the passage that matched, not a neighbor.

Research draft T02: the retriever computes exactly where a match lives
(``line_range`` from ``_attach_locator``), but the tool payload returned
``content[:cap]`` — the document HEAD. For any match deeper than the cap the
model received text that never matched. These tests pin the fix with distinct
sentinel chunks and the exact off-by-one join T02 warns about.
"""

from __future__ import annotations

from personalclaw.action_providers.knowledge_retrieve_provider import (
    DETAIL_CAPS,
    _passage_window,
    _shape_hit,
)

# A document of three distinct sentinel regions, each well over the compact cap
# so the head window can never accidentally contain a deep sentinel.
_HEAD = "\n".join(f"alpha-head-{i} " + "pad " * 40 for i in range(30))
_MID = "\n".join(f"bravo-middle-{i} " + "pad " * 40 for i in range(30))
_TAIL = "\n".join(f"charlie-tail-{i} " + "pad " * 40 for i in range(30))
_DOC = _HEAD + "\n" + _MID + "\n" + _TAIL
_MID_START = len(_HEAD.split("\n")) + 1  # 1-based first line of the middle region


class TestPassageWindow:
    def test_deep_match_returns_the_matched_passage_not_the_head(self) -> None:
        """The T02 defect, reproduced then fixed: a match in the MIDDLE sentinel
        region must yield middle text — the old head-cap payload contained only
        alpha-head text for the same hit."""
        cap = DETAIL_CAPS["compact"]
        old_behavior = _DOC[:cap]
        assert "bravo-middle-0" not in old_behavior  # the defect was real

        text, windowed = _passage_window(_DOC, [_MID_START, _MID_START + 2], cap)
        assert windowed
        assert "bravo-middle-0" in text
        assert "alpha-head-0" not in text

    def test_the_join_is_one_based_inclusive(self) -> None:
        """lines[start-1:end], never lines[start:end] — the off-by-one returns a
        neighbor-shifted passage that drops the first matched line."""
        doc = "\n".join(f"L{i}" for i in range(1, 11))
        text, windowed = _passage_window(doc, [5, 6], cap=1000)
        assert windowed
        # One line of leading context (L4), then the passage itself.
        assert "L5" in text and "L6" in text
        body = text.lstrip("…")
        assert body.split("\n")[0] == "L4"  # context line, not a dropped L5

    def test_matched_text_is_at_the_front_so_the_cap_cannot_cut_it(self) -> None:
        text, _ = _passage_window(_DOC, [_MID_START, _MID_START + 1], DETAIL_CAPS["compact"])
        # The first sentinel token appears within the first couple of lines.
        head_of_window = "\n".join(text.split("\n")[:3])
        assert "bravo-middle" in head_of_window

    def test_no_locator_keeps_the_head_cap_unchanged(self) -> None:
        cap = DETAIL_CAPS["compact"]
        text, windowed = _passage_window(_DOC, None, cap)
        assert not windowed
        assert text == _DOC[:cap]

    def test_invalid_locators_fall_back_safely(self) -> None:
        cap = 100
        for bad in ([0, 2], [5, 99999], ["x", "y"], [3], [7, 5]):
            text, windowed = _passage_window(_DOC, bad, cap)
            assert not windowed
            assert text == _DOC[:cap]

    def test_brief_detail_stays_empty(self) -> None:
        text, windowed = _passage_window(_DOC, [_MID_START, _MID_START + 1], 0)
        assert text == "" and not windowed


class TestShapedPayload:
    def test_payload_carries_passage_and_citation_fields(self) -> None:
        hit = {
            "id": "it-1",
            "title": "Sentinel Doc",
            "summary": "s",
            "content": _DOC,
            "score": 0.03,
            "match_type": "vector",
            "line_range": [_MID_START, _MID_START + 2],
            "section": "Middle Section",
        }
        shaped = _shape_hit(None, hit, query="bravo", detail="compact")
        assert "bravo-middle-0" in shaped["content"]
        assert "alpha-head-0" not in shaped["content"]
        assert shaped["section"] == "Middle Section"
        assert shaped["line_range"] == [_MID_START, _MID_START + 2]
        assert shaped["content_windowed"] is True

    def test_payload_without_locator_matches_legacy_shape(self) -> None:
        hit = {
            "id": "it-2",
            "title": "T",
            "summary": "",
            "content": _DOC,
            "score": 0.03,
            "match_type": "keyword",
        }
        shaped = _shape_hit(None, hit, query="alpha", detail="compact")
        assert shaped["content"] == _DOC[: DETAIL_CAPS["compact"]]
        assert shaped["line_range"] is None
        assert shaped["content_windowed"] is False
