"""Tests for the channel-agnostic per-session memory-restriction registry."""

import personalclaw.session_restrictions as sr


class TestSessionRestrictions:
    def setup_method(self):
        sr._temporary.clear()
        sr._incognito.clear()

    def test_defaults_false(self):
        assert sr.is_temporary("k") is False
        assert sr.is_incognito("k") is False
        assert sr.is_restricted("k") is False

    def test_temporary(self):
        sr.mark_temporary("k")
        assert sr.is_temporary("k") is True
        assert sr.is_incognito("k") is False
        assert sr.is_restricted("k") is True

    def test_incognito(self):
        sr.mark_incognito("k")
        assert sr.is_incognito("k") is True
        assert sr.is_restricted("k") is True

    def test_clear(self):
        sr.mark_temporary("k")
        sr.mark_incognito("k")
        sr.clear("k")
        assert sr.is_restricted("k") is False

    def test_bounded_lru_eviction(self):
        original = sr._MAX
        sr._MAX = 3
        try:
            for key in ("a", "b", "c", "d"):
                sr.mark_temporary(key)
            assert sr.is_temporary("a") is False  # evicted
            assert sr.is_temporary("d") is True
        finally:
            sr._MAX = original
