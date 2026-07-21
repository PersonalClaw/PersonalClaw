"""Tests for the shared filetype-keyed upload size policy (personalclaw.uploads.policy)."""

import pytest

from personalclaw.uploads import policy as P

_MB = 1024 * 1024
_GB = 1024 * _MB


class TestCategoryMapping:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("clip.mp4", "video"),
            ("movie.mov", "video"),
            ("x.mkv", "video"),
            ("song.mp3", "audio"),
            ("voice.wav", "audio"),
            ("a.flac", "audio"),
            ("photo.png", "image"),
            ("iphone.heic", "image"),
            ("scan.tiff", "image"),
            ("doc.pdf", "document"),
            ("report.docx", "document"),
            ("data.csv", "document"),
            ("script.py", "document"),
            ("bundle.zip", "archive"),
            ("t.tar.gz", "archive"),
            ("a.7z", "archive"),
            ("mystery.bin", "other"),
            ("noext", "other"),
        ],
    )
    def test_category_for(self, filename: str, expected: str):
        assert P.category_for(filename) == expected

    def test_mime_disambiguates_webm_audio_vs_video(self):
        # A browser audio recording is audio/webm; the extension alone is video.
        assert P.category_for("rec.webm", "audio/webm") == "audio"
        assert P.category_for("clip.webm", "video/webm") == "video"


class TestLimits:
    def test_default_limits(self):
        assert P.limit_for_category("video") == 2 * _GB
        assert P.limit_for_category("audio") == 1 * _GB
        assert P.limit_for_category("image") == 200 * _MB
        assert P.limit_for_category("document") == 100 * _MB
        assert P.limit_for_category("archive") == 500 * _MB
        assert P.limit_for_category("other") == 100 * _MB

    def test_unknown_category_falls_to_other(self):
        assert P.limit_for_category("bogus") == P.limit_for_category("other")

    def test_max_category_limit_is_video(self):
        assert P.max_category_limit() == 2 * _GB

    def test_limit_for_file(self):
        assert P.limit_for("a.mp4") == 2 * _GB
        assert P.limit_for("a.png") == 200 * _MB

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_UPLOAD_LIMIT_VIDEO", str(3 * _GB))
        assert P.limit_for_category("video") == 3 * _GB
        assert P.max_category_limit() == 3 * _GB

    def test_env_override_ignores_garbage(self, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_UPLOAD_LIMIT_IMAGE", "not-a-number")
        assert P.limit_for_category("image") == 200 * _MB
        monkeypatch.setenv("PERSONALCLAW_UPLOAD_LIMIT_IMAGE", "-5")
        assert P.limit_for_category("image") == 200 * _MB


class TestCheckUpload:
    def test_within_limit_ok(self):
        c = P.check_upload("ok.mp4", size=1 * _GB)
        assert c.ok and c.category == "video" and c.limit == 2 * _GB

    def test_over_limit_413(self):
        c = P.check_upload("big.mp4", size=3 * _GB)
        assert not c.ok and c.status == 413
        assert "video" in c.reason and "2 GB" in c.reason

    def test_image_over_its_lower_cap(self):
        c = P.check_upload("huge.png", size=250 * _MB)
        assert not c.ok and c.status == 413 and "200 MB" in c.reason

    def test_no_size_is_ok(self):
        # Called up front without a known size (streaming) → accept, carry the limit.
        c = P.check_upload("x.mp4")
        assert c.ok and c.limit == 2 * _GB

    def test_override_limit_caps_lower(self):
        # STT surface may cap audio lower than the 1 GB category default.
        c = P.check_upload("long.mp3", size=100 * _MB, override_limit=25 * _MB)
        assert not c.ok and c.status == 413 and "25 MB" in c.reason

    def test_override_higher_than_category_ignored(self):
        # override only ever caps LOWER; a higher override can't exceed the policy.
        c = P.check_upload("shot.png", size=250 * _MB, override_limit=5 * _GB)
        assert not c.ok  # still gated at 200 MB image cap
        assert c.limit == 200 * _MB


class TestSinglePostThreshold:
    def test_default(self):
        assert P.single_post_threshold() == 50 * _MB

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_UPLOAD_SINGLE_POST_THRESHOLD", str(10 * _MB))
        assert P.single_post_threshold() == 10 * _MB


class TestLimitsTable:
    def test_table_has_all_categories(self):
        table = P.limits_table()
        assert set(table) == set(P.UPLOAD_CATEGORIES)
        assert all(v > 0 for v in table.values())
