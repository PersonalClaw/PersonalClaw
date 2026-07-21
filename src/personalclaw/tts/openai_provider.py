"""Remote OpenAI-compatible TTS provider — speech via the audio API.

Resolves a config.json ``providers[]`` entry (its ``endpoint`` + ``api_key``)
and synthesizes through ``client.audio.speech``. One instance is registered per
OpenAI-family provider configured in Settings, keyed by that provider's name, so
an ``openai:tts-1`` active selection routes to the same OpenAI account that backs
chat/embedding. The ``openai`` SDK is imported lazily inside ``synthesize``.

The TTS "voice" ref is the model id (``tts-1`` / ``gpt-4o-mini-tts``); the spoken
voice persona (alloy / nova / …) is a behavior setting in
``use_case_settings/tts.json``, defaulting to ``alloy``.
"""

import asyncio
import logging
import os
import tempfile
from typing import Any

from personalclaw.tts.provider import TtsProvider

logger = logging.getLogger(__name__)

# Built-in speech personas the hosted models expose. Selected via the
# ``voice`` behavior setting; ``alloy`` is the API default.
SPEECH_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_DEFAULT_SPEECH_VOICE = "alloy"


class OpenAITtsProvider(TtsProvider):
    """Synthesize speech via an OpenAI-compatible hosted TTS endpoint."""

    def __init__(
        self, *, provider_name: str, provider_type: str = "", endpoint: str = "", api_key: str = ""
    ) -> None:
        self._provider_name = provider_name
        self._provider_type = provider_type
        self._endpoint = endpoint
        self._api_key = api_key

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return f"{self._provider_name} (remote TTS)"

    def _default_model(self) -> str:
        """The vendor's unpinned TTS default from its app-contributed catalog
        (personalclaw.media_catalogs); empty when the type contributed none."""
        from personalclaw.media_catalogs import get_media_catalog

        cat = get_media_catalog("tts", self._provider_type)
        return cat.default_model if cat else ""

    async def is_available(self) -> bool:
        if not self._resolve_api_key():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    # NOTE: no list_voices/download_voice/delete_voice — this is a REMOTE (hosted) TTS
    # provider on the INFERENCE axis only. Its models aren't downloaded/managed locally;
    # they surface for binding via the config-provider catalog (tagged ``tts``). Voice/
    # model management is the separate LocalModelProvider axis, which only local backends
    # (piper) implement.

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        output_path: str = "",
        *,
        speech_voice: str = "",
        speed: float = 1.0,
        **opts: Any,
    ) -> str | None:
        """Synthesize *text* to a ``.mp3`` file. Returns the path or None.

        ``voice`` is the model id (``tts-1`` …); ``speech_voice`` selects the
        persona (alloy / nova / …). ``speed`` maps to the API ``speed`` param.
        Caller owns deleting the returned file.
        """
        try:
            import openai
        except ImportError:
            logger.error("openai SDK not installed — cannot use remote TTS")
            return None

        api_key = self._resolve_api_key()
        if not api_key:
            logger.error("No API key for remote TTS provider %r", self._provider_name)
            return None

        if not text.strip():
            return None

        # Unpinned falls back to the vendor's contributed default (OpenAI's tts-1,
        # from the openai-models app); a type with no contributed catalog requires a
        # pinned model.
        model_id = voice or self._default_model()
        if not model_id:
            logger.error(
                "No TTS model selected for %r (this endpoint has no contributed "
                "default); pin one in Settings → Models.",
                self._provider_name,
            )
            return None
        persona = speech_voice or _DEFAULT_SPEECH_VOICE
        base_url = self._endpoint or None

        if output_path:
            path = output_path
        else:
            fd, path = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)

        async def _run() -> str | None:
            client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
            try:
                kwargs: dict = {"model": model_id, "voice": persona, "input": text}
                if speed and speed != 1.0:
                    kwargs["speed"] = speed
                resp = await client.audio.speech.create(**kwargs)
                audio = resp.read() if hasattr(resp, "read") else getattr(resp, "content", b"")
                if asyncio.iscoroutine(audio):
                    audio = await audio
                if not audio:
                    return None
                with open(path, "wb") as fh:
                    fh.write(audio)
                return path
            finally:
                with __import__("contextlib").suppress(Exception):
                    await client.close()

        try:
            return await asyncio.wait_for(_run(), timeout=120)
        except asyncio.TimeoutError:
            logger.error("Remote TTS timed out for provider %r", self._provider_name)
            self._cleanup(path, output_path)
            return None
        except Exception:
            logger.exception("Remote TTS failed for provider %r", self._provider_name)
            self._cleanup(path, output_path)
            return None

    @staticmethod
    def _cleanup(path: str, output_path: str) -> None:
        # Only remove a temp file we created — never the caller's output_path.
        if path and not output_path:
            with __import__("contextlib").suppress(OSError):
                os.unlink(path)

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        val = os.environ.get("OPENAI_API_KEY", "")
        return val
