"""Tests for voice_reply — provider-agnostic TTS orchestration (strip/split/
synthesize-speech/upload/stream). Piper-specific synthesis moved to the piper-tts app."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.voice_reply import (
    DEFAULT_RATE,
    _validate_rate,
    split_sentences,
    strip_markdown,
    synthesize_speech,
    upload_voice_to_channel,
    voice_reply,
)


class TestStripMarkdown:
    def test_removes_code_blocks(self) -> None:
        assert strip_markdown("before ```code``` after") == "before (code block) after"

    def test_removes_inline_code(self) -> None:
        assert strip_markdown("use `foo` here") == "use foo here"

    def test_removes_channel_deep_links(self) -> None:
        assert strip_markdown("<https://example.com|Example>") == "Example"
        assert strip_markdown("<https://example.com>") == "(link)"

    def test_removes_markdown_links(self) -> None:
        assert strip_markdown("[click](https://example.com)") == "click"

    def test_removes_bold_italic(self) -> None:
        assert strip_markdown("**bold** and *italic*") == "bold and italic"

    def test_removes_emoji_shortcodes(self) -> None:
        assert strip_markdown("hello :wave: world") == "hello world"

    def test_preserves_plain_text(self) -> None:
        assert strip_markdown("hello world") == "hello world"

    def test_collapses_whitespace(self) -> None:
        assert strip_markdown("a\n\n\n\nb") == "a\n\nb"

    def test_preserves_bullet_lists(self) -> None:
        result = strip_markdown("- item one\n- item two")
        assert "item one" in result
        assert "item two" in result


class TestValidation:
    def test_valid_rate(self) -> None:
        assert _validate_rate("95%") == "95%"
        assert _validate_rate("110%") == "110%"
        assert _validate_rate("50%") == "50%"

    def test_invalid_rate_returns_default(self) -> None:
        assert _validate_rate("banana") == DEFAULT_RATE
        assert _validate_rate("") == DEFAULT_RATE
        assert _validate_rate("1000%") == DEFAULT_RATE


class TestSplitSentences:
    def test_multi_sentence(self) -> None:
        assert split_sentences("Hello world. How are you?") == [
            "Hello world.",
            "How are you?",
        ]

    def test_single_sentence(self) -> None:
        assert split_sentences("Hello world.") == ["Hello world."]

    def test_empty_input(self) -> None:
        assert split_sentences("") == []

    def test_strips_markdown_before_splitting(self) -> None:
        assert split_sentences("**Bold sentence.** Another one.") == [
            "Bold sentence.",
            "Another one.",
        ]


# ── synthesize_speech() ──────────────────────────────────────────────────


class TestSynthesizeSpeech:
    @pytest.mark.asyncio
    async def test_piper_synthesis(self) -> None:
        prov = MagicMock()
        prov.synthesize = AsyncMock(return_value="/tmp/out.wav")
        out = await synthesize_speech(prov, "hello world")
        assert out == "/tmp/out.wav"
        prov.synthesize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_piper_empty_plain_returns_none(self) -> None:
        prov = MagicMock()
        prov.synthesize = AsyncMock(return_value="/tmp/out.wav")
        out = await synthesize_speech(prov, "   ")
        assert out is None
        prov.synthesize.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redacts_credentials_before_synthesis(self) -> None:
        """LLM output must be redacted for credentials before crossing into audio."""
        # AKIA... is a typical AWS key shape caught by redact_credentials.
        raw = "secret AKIAIOSFODNN7EXAMPLE here"
        captured_text: list[str] = []

        async def capture(text, **kwargs):
            captured_text.append(text)
            return "/tmp/out.wav"

        prov = MagicMock()
        prov.synthesize = AsyncMock(side_effect=capture)
        await synthesize_speech(prov, raw)

        assert captured_text, "provider should have been called"
        assert "AKIAIOSFODNN7EXAMPLE" not in captured_text[0]


# ── upload_voice_to_channel() ──────────────────────────────────────────────


class TestUploadVoiceToChannel:
    @pytest.mark.asyncio
    async def test_wav_filename_preserved(self, tmp_path) -> None:
        audio = tmp_path / "x.wav"
        audio.write_bytes(b"x")
        client = MagicMock()
        client.upload_file = AsyncMock(return_value=None)
        assert await upload_voice_to_channel(client, "C1", "t1", str(audio)) is True
        kwargs = client.upload_file.call_args.kwargs
        assert kwargs["filename"] == "voice-reply.wav"

    @pytest.mark.asyncio
    async def test_extensionless_defaults_to_wav(self, tmp_path) -> None:
        audio = tmp_path / "no_ext"
        audio.write_bytes(b"x")
        client = MagicMock()
        client.upload_file = AsyncMock(return_value=None)
        assert await upload_voice_to_channel(client, "C1", "t1", str(audio)) is True
        kwargs = client.upload_file.call_args.kwargs
        assert kwargs["filename"] == "voice-reply.wav"

    @pytest.mark.asyncio
    async def test_upload_exception_returns_false(self, tmp_path) -> None:
        audio = tmp_path / "x.wav"
        audio.write_bytes(b"x")
        client = MagicMock()
        client.upload_file = AsyncMock(side_effect=RuntimeError("channel down"))
        assert await upload_voice_to_channel(client, "C1", "t1", str(audio)) is False


# ── voice_reply() end-to-end ────────────────────────────────────────────


class TestVoiceReplyEndToEnd:
    @pytest.mark.asyncio
    async def test_synthesis_fails_returns_false(self) -> None:
        client = MagicMock()
        with patch(
            "personalclaw.voice_reply.synthesize_speech",
            new=AsyncMock(return_value=None),
        ):
            assert (
                await voice_reply(
                    client,
                    "C1",
                    "t1",
                    "hi",
                    provider=MagicMock(),
                )
                is False
            )

    @pytest.mark.asyncio
    async def test_success_uploads_and_unlinks(self, tmp_path) -> None:
        audio = tmp_path / "out.wav"
        audio.write_bytes(b"x" * 200)
        client = MagicMock()
        client.upload_file = AsyncMock(return_value=None)
        with patch(
            "personalclaw.voice_reply.synthesize_speech",
            new=AsyncMock(return_value=str(audio)),
        ):
            ok = await voice_reply(
                client,
                "C1",
                "t1",
                "hello",
                provider=MagicMock(),
            )
        assert ok is True
        assert not audio.exists()

    @pytest.mark.asyncio
    async def test_unlink_happens_even_on_upload_failure(self, tmp_path) -> None:
        audio = tmp_path / "out.wav"
        audio.write_bytes(b"x" * 200)
        client = MagicMock()
        client.upload_file = AsyncMock(side_effect=RuntimeError("boom"))
        with patch(
            "personalclaw.voice_reply.synthesize_speech",
            new=AsyncMock(return_value=str(audio)),
        ):
            ok = await voice_reply(
                client,
                "C1",
                "t1",
                "hi",
                provider=MagicMock(),
            )
        assert ok is False
        assert not audio.exists(), "temp audio must be cleaned up on upload failure"


# ── streaming_voice_reply() redaction ───────────────────────────────────


class TestStreamingVoiceReply:
    @pytest.mark.asyncio
    async def test_redacts_credentials_before_synthesis(self, tmp_path) -> None:
        from personalclaw.voice_reply import streaming_voice_reply

        sentences_seen: list[str] = []

        async def fake_synth(text, **kwargs):
            sentences_seen.append(text)
            out = tmp_path / f"s{len(sentences_seen)}.wav"
            out.write_bytes(b"x" * 200)
            return str(out)

        prov = MagicMock()
        prov.synthesize = AsyncMock(side_effect=fake_synth)
        gen = streaming_voice_reply(prov, "AKIAIOSFODNN7EXAMPLE is secret. Bye.")
        async for _idx, _sent, _bytes in gen:
            pass

        assert sentences_seen, "provider should have been called per sentence"
        for s in sentences_seen:
            assert "AKIAIOSFODNN7EXAMPLE" not in s

    @pytest.mark.asyncio
    async def test_skips_sentences_with_failed_synth(self, tmp_path) -> None:
        from personalclaw.voice_reply import streaming_voice_reply

        calls = {"n": 0}

        async def alternating(text, **kwargs):
            calls["n"] += 1
            if calls["n"] % 2 == 0:
                return None
            out = tmp_path / f"s{calls['n']}.wav"
            out.write_bytes(b"x" * 200)
            return str(out)

        prov = MagicMock()
        prov.synthesize = AsyncMock(side_effect=alternating)
        collected = []
        async for idx, sent, data in streaming_voice_reply(
            prov,
            "First. Second. Third.",
        ):
            collected.append(idx)

        # Only the odd-numbered calls succeed (1, 3).
        assert collected == [0, 2]
