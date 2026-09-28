"""Tests for speech-to-text transcription.

STT resolves through the typed registry: enabled lives in
``use_case_settings/stt.json``, the active model in ``active_models.json``. The
faster-whisper provider does the actual work.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.transcribe import is_available, transcribe_audio

# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    @pytest.mark.asyncio
    async def test_disabled(self):
        with patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": False},
        ):
            assert await is_available() is False

    @pytest.mark.asyncio
    async def test_enabled_no_active_model(self):
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.stt.registry.active_stt", return_value=None),
        ):
            assert await is_available() is False

    @pytest.mark.asyncio
    async def test_active_provider_unavailable(self):
        prov = MagicMock()
        prov.is_available = AsyncMock(return_value=False)
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
        ):
            assert await is_available() is False

    @pytest.mark.asyncio
    async def test_enabled_with_available_provider(self):
        prov = MagicMock()
        prov.is_available = AsyncMock(return_value=True)
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
            patch("personalclaw.transcribe._ffmpeg_present", return_value=True),
        ):
            # readiness now delegates to the active provider — local or remote.
            assert await is_available() is True


# ---------------------------------------------------------------------------
# transcribe_audio
# ---------------------------------------------------------------------------


class TestTranscribeAudio:
    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        with patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": False},
        ):
            assert await transcribe_audio("/tmp/test.webm") is None

    @pytest.mark.asyncio
    async def test_no_active_model_returns_none(self):
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=None),
        ):
            assert await transcribe_audio("/tmp/test.webm") is None

    @pytest.mark.asyncio
    async def test_successful_transcription(self):
        prov = MagicMock()
        prov.transcribe = AsyncMock(return_value="Hello world")
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True, "language_code": "en-US"},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
        ):
            result = await transcribe_audio("/tmp/test.webm")
        assert result == "Hello world"
        prov.transcribe.assert_awaited_once_with("/tmp/test.webm", model="turbo", language="en-US")

    @pytest.mark.asyncio
    async def test_provider_returns_none(self):
        prov = MagicMock()
        prov.transcribe = AsyncMock(return_value=None)
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
        ):
            assert await transcribe_audio("/tmp/test.webm") is None

    @pytest.mark.asyncio
    async def test_sensitive_path_blocked(self):
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=True),
        ):
            assert await transcribe_audio("/etc/shadow") is None

    @pytest.mark.asyncio
    async def test_small_file_uses_single_call(self, tmp_path):
        # A small audio file transcribes in one provider call (no segmentation),
        # even when ffmpeg is present.
        f = tmp_path / "small.wav"
        f.write_bytes(b"\x00" * 1024)
        prov = MagicMock()
        prov.transcribe = AsyncMock(return_value="hi")
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
            patch("personalclaw.transcribe._ffmpeg_present", return_value=True),
        ):
            result = await transcribe_audio(str(f))
        assert result == "hi"
        prov.transcribe.assert_awaited_once()  # single call, not segmented

    @pytest.mark.asyncio
    async def test_large_file_segments_and_stitches(self, tmp_path, monkeypatch):
        # A file over the segment threshold is split (ffmpeg) into chunks that are
        # transcribed sequentially and stitched.
        monkeypatch.setenv("PERSONALCLAW_STT_SEGMENT_THRESHOLD", "10")  # 10 bytes
        f = tmp_path / "big.wav"
        f.write_bytes(b"\x00" * 4096)
        prov = MagicMock()
        prov.transcribe = AsyncMock(side_effect=["part one", "part two"])

        # Fake the ffmpeg segmenter: create two segment files in the work dir.
        async def _fake_transcribe_segmented(provider, model_id, language, audio_path):
            # Exercise the real stitching contract without invoking ffmpeg.
            a = await provider.transcribe("seg0", model=model_id, language=language)
            b = await provider.transcribe("seg1", model=model_id, language=language)
            return " ".join(x for x in (a, b) if x)

        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
            patch("personalclaw.transcribe._ffmpeg_present", return_value=True),
            patch(
                "personalclaw.transcribe._transcribe_segmented",
                side_effect=_fake_transcribe_segmented,
            ),
        ):
            result = await transcribe_audio(str(f))
        assert result == "part one part two"

    @pytest.mark.asyncio
    async def test_large_file_no_ffmpeg_falls_back_single(self, tmp_path, monkeypatch):
        # Over threshold but ffmpeg absent → single provider call (no segmentation).
        monkeypatch.setenv("PERSONALCLAW_STT_SEGMENT_THRESHOLD", "10")
        f = tmp_path / "big.wav"
        f.write_bytes(b"\x00" * 4096)
        prov = MagicMock()
        prov.transcribe = AsyncMock(return_value="whole thing")
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
            patch("personalclaw.transcribe._ffmpeg_present", return_value=False),
        ):
            result = await transcribe_audio(str(f))
        assert result == "whole thing"
        prov.transcribe.assert_awaited_once_with(str(f), model="turbo", language="")


# ---------------------------------------------------------------------------
# L0 — rich transcript contract (segments + word timestamps + detailed path)
# ---------------------------------------------------------------------------


class TestTranscriptContract:
    def test_to_dict_nested_shape(self):
        from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord

        r = TranscriptResult(
            text="hello world",
            language="en",
            duration=1.5,
            segments=[
                TranscriptSegment(
                    0.0, 1.5, "hello world", words=[TranscriptWord(0.0, 0.5, "hello", 0.9)]
                )
            ],
        )
        d = r.to_dict()
        assert d["text"] == "hello world" and d["language"] == "en"
        assert d["segments"][0]["words"][0]["word"] == "hello"
        assert d["segments"][0]["speaker"] is None  # filled later by fusion (L1)

    @pytest.mark.asyncio
    async def test_default_detailed_wraps_flat_text(self):
        # A provider that only implements transcribe() inherits the default
        # transcribe_detailed, which wraps the flat text (no segments) — no provider
        # is forced to fabricate structure it doesn't have.
        from personalclaw.stt.provider import SttProvider

        class _Flat(SttProvider):
            name = "flat"
            display_name = "Flat"

            async def is_available(self):
                return True

            async def list_models(self):
                return []

            async def download_model(self, m):
                return True

            async def delete_model(self, m):
                return True

            async def transcribe(self, audio_path, model="", language=""):
                return "just text"

        p = _Flat()
        assert p.supports_segments is False and p.supports_word_timestamps is False
        r = await p.transcribe_detailed("/x.wav")
        assert r is not None and r.text == "just text" and r.segments == []

    @pytest.mark.asyncio
    async def test_detailed_none_when_flat_none(self):
        from personalclaw.stt.provider import SttProvider

        class _Empty(SttProvider):
            name = "e"
            display_name = "E"

            async def is_available(self):
                return True

            async def list_models(self):
                return []

            async def download_model(self, m):
                return True

            async def delete_model(self, m):
                return True

            async def transcribe(self, audio_path, model="", language=""):
                return None

        assert await _Empty().transcribe_detailed("/x.wav") is None

    @pytest.mark.asyncio
    async def test_transcribe_audio_detailed_returns_result(self, tmp_path):
        from personalclaw.stt.provider import TranscriptResult, TranscriptSegment
        from personalclaw.transcribe import transcribe_audio_detailed

        f = tmp_path / "a.wav"
        f.write_bytes(b"\x00" * 32)
        prov = MagicMock()
        prov.transcribe_detailed = AsyncMock(
            return_value=TranscriptResult(
                text="hi there", segments=[TranscriptSegment(0.0, 1.0, "hi there")]
            )
        )
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
        ):
            r = await transcribe_audio_detailed(str(f))
        assert r is not None and r.text == "hi there" and len(r.segments) == 1

    @pytest.mark.asyncio
    async def test_segmented_detailed_offsets_timestamps(self, tmp_path, monkeypatch):
        # The segmented detailed path must OFFSET each chunk's segment/word times by the
        # chunk's start (chunk 1 at _STT_SEGMENT_SECONDS) so the merged timeline is
        # continuous. We drive the merge helper directly with a fake per-chunk provider.
        from personalclaw import transcribe as T
        from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord

        monkeypatch.setattr(T, "_STT_SEGMENT_SECONDS", 600)

        # Each chunk reports LOCAL times [0..5]; two chunks → second offset by 600.
        def _chunk_result(_i):
            return TranscriptResult(
                text="chunk",
                duration=5.0,
                segments=[
                    TranscriptSegment(
                        0.0, 5.0, "chunk", words=[TranscriptWord(0.0, 5.0, "chunk", 1.0)]
                    )
                ],
            )

        calls = {"n": 0}

        async def _detailed(path, *, model="", language="", bias_terms=None):
            i = calls["n"]
            calls["n"] += 1
            return _chunk_result(i)

        prov = MagicMock()
        prov.transcribe_detailed = _detailed

        # Fake ffmpeg segmentation: create two segment files so the helper iterates twice.
        def _fake_listdir(p):
            return ["seg_00000.wav", "seg_00001.wav"]

        async def _fake_exec(*a, **k):
            proc = MagicMock()
            proc.wait = AsyncMock(return_value=0)
            return proc

        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("os.listdir", _fake_listdir),
            patch("asyncio.create_subprocess_exec", _fake_exec),
        ):
            r = await T._transcribe_segmented_detailed(
                prov, "turbo", "", str(tmp_path / "big.wav"), None
            )

        assert r is not None
        assert len(r.segments) == 2
        # First chunk stays at 0..5; second chunk offset to 600..605.
        assert r.segments[0].start == 0.0 and r.segments[0].end == 5.0
        assert r.segments[1].start == 600.0 and r.segments[1].end == 605.0
        assert r.segments[1].words[0].start == 600.0  # word times offset too


# ---------------------------------------------------------------------------
# Why speech-to-text is unavailable, and a provider's SttError
# ---------------------------------------------------------------------------

_REASON = "No credentials were found for this account. Sign in, then try again."


def _bound(prov):
    """Bind ``prov`` as the active STT provider, with STT on."""
    return (
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": True},
        ),
        patch("personalclaw.stt.registry.active_stt", return_value=(prov, "turbo")),
    )


class TestUnavailableReason:
    @pytest.mark.asyncio
    async def test_the_bound_providers_reason_is_the_reason(self):
        """🔴 Red before: nothing could say why — "not available" was all a surface had."""
        from personalclaw.transcribe import unavailable_reason

        prov = MagicMock()
        prov.is_available = AsyncMock(return_value=False)
        prov.unavailable_reason = AsyncMock(return_value=f"  {_REASON}\n")
        settings, active = _bound(prov)
        with settings, active:
            assert await unavailable_reason() == _REASON

    @pytest.mark.asyncio
    async def test_there_is_no_reason_while_it_is_available_off_or_unbound(self):
        from personalclaw.transcribe import unavailable_reason

        prov = MagicMock()
        prov.is_available = AsyncMock(return_value=True)
        prov.unavailable_reason = AsyncMock(return_value=_REASON)
        settings, active = _bound(prov)
        with settings, active:
            assert await unavailable_reason() == "", "an available provider has nothing to explain"
        with patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": False},
        ):
            assert await unavailable_reason() == ""
        with (
            patch(
                "personalclaw.providers.use_cases.load_use_case_settings",
                return_value={"enabled": True},
            ),
            patch("personalclaw.stt.registry.active_stt", return_value=None),
        ):
            assert await unavailable_reason() == ""

    @pytest.mark.asyncio
    async def test_a_provider_that_cannot_say_has_no_reason(self):
        """The ABC's default: an older provider that never learned to say why says nothing."""
        from personalclaw.stt.provider import SttProvider

        class _Quiet(SttProvider):
            name = "quiet"
            display_name = "Quiet"

            async def is_available(self) -> bool:
                return False

            async def transcribe(self, audio_path, model="", language=""):
                return None

        assert await _Quiet().unavailable_reason() == ""


class TestSttErrorReachesTheCaller:
    @pytest.mark.asyncio
    async def test_a_providers_reason_reaches_the_caller_of_transcribe_audio(self):
        from personalclaw.sdk.stt import SttError  # as an app's provider raises it

        prov = MagicMock()
        prov.transcribe = AsyncMock(side_effect=SttError(_REASON))
        settings, active = _bound(prov)
        with (
            settings,
            active,
            patch("personalclaw.security.is_sensitive_path", return_value=False),
            pytest.raises(SttError, match="Sign in, then try again"),
        ):
            await transcribe_audio("/tmp/test.webm")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("detailed", [False, True])
    async def test_a_segment_that_says_why_it_failed_is_raised_not_read_as_silence(
        self, tmp_path, detailed
    ):
        """🔴 Red before: each segment's failure was logged and skipped, so a provider that
        could not sign in produced an empty transcript — read as a recording with no speech."""
        from personalclaw import transcribe as T
        from personalclaw.sdk.stt import SttError  # as an app's provider raises it

        prov = MagicMock()
        prov.transcribe = AsyncMock(side_effect=SttError(_REASON))
        prov.transcribe_detailed = AsyncMock(side_effect=SttError(_REASON))

        async def _fake_exec(*a, **k):
            proc = MagicMock()
            proc.wait = AsyncMock(return_value=0)
            return proc

        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("os.listdir", lambda p: ["seg_00000.wav", "seg_00001.wav"]),
            patch("asyncio.create_subprocess_exec", _fake_exec),
            pytest.raises(SttError, match="Sign in, then try again"),
        ):
            if detailed:
                await T._transcribe_segmented_detailed(
                    prov, "turbo", "", str(tmp_path / "big.wav"), None
                )
            else:
                await T._transcribe_segmented(prov, "turbo", "", str(tmp_path / "big.wav"))

    def test_apps_reach_the_error_through_the_sdk(self):
        """The tests above raise it as an app does; this is that it is core's own class."""
        from personalclaw.sdk import stt as sdk_stt
        from personalclaw.stt.provider import SttError as core_error

        assert sdk_stt.SttError is core_error and "SttError" in sdk_stt.__all__
