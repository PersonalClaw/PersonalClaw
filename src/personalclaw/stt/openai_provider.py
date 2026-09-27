"""Remote OpenAI-compatible STT provider — transcription via the audio API.

Resolves a config.json ``providers[]`` entry (its ``endpoint`` + ``api_key``)
and transcribes through ``client.audio.transcriptions``. One instance is
registered per OpenAI-family provider configured in Settings, keyed by that
provider's name, so an ``openai:whisper-1`` active selection resolves to the
same OpenAI account that backs chat/embedding. The ``openai`` SDK is imported
lazily inside ``transcribe`` so importing this module never pulls the SDK.
"""

import asyncio
import logging
import os

from personalclaw.llm.registry import ProviderResolutionError, require_model
from personalclaw.stt.provider import SttProvider

logger = logging.getLogger(__name__)


class OpenAISttProvider(SttProvider):
    """Transcribe audio via an OpenAI-compatible hosted Whisper endpoint."""

    def __init__(self, *, provider_name: str, endpoint: str = "", api_key: str = "") -> None:
        self._provider_name = provider_name
        self._endpoint = endpoint
        self._api_key = api_key

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def display_name(self) -> str:
        return f"{self._provider_name} (remote STT)"

    async def is_available(self) -> bool:
        """Usable when a credential resolves and the openai SDK is importable."""
        if not self._resolve_api_key():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    # NOTE: no list_models/download_model/delete_model — this is a REMOTE (hosted)
    # provider on the INFERENCE axis only. Its models aren't downloaded/managed locally;
    # they surface for binding through the config-provider catalog (the LLM registry's
    # discovery, which tags whisper-1/gpt-4o-transcribe as ``stt``). Model management is
    # a separate axis (LocalModelProvider) that only local backends implement.

    async def transcribe(self, audio_path: str, model: str = "", language: str = "") -> str | None:
        # Like chat, a call names its model (the Speech-to-text binding in Settings → Models), and
        # one that names none is refused. The vendor's default (OpenAI's whisper-1) used to be
        # sent in its place.
        try:
            model_id = require_model(model)
        except ProviderResolutionError as exc:
            logger.error("Remote STT on %r refused: %s", self._provider_name, exc)
            return None
        try:
            import openai
        except ImportError:
            logger.error(
                "openai SDK not installed — cannot use remote STT. Install it with "
                "`pip install 'personalclaw[openai]'` (or reinstall the OpenAI provider "
                "app); run `personalclaw doctor` to check provider deps."
            )
            return None

        api_key = self._resolve_api_key()
        if not api_key:
            logger.error("No API key for remote STT provider %r", self._provider_name)
            return None

        lang = language.split("-")[0] if language else None
        base_url = self._endpoint or None

        async def _run() -> str | None:
            client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
            try:
                with open(audio_path, "rb") as fh:
                    kwargs: dict = {"model": model_id, "file": fh}
                    if lang:
                        kwargs["language"] = lang
                    resp = await client.audio.transcriptions.create(**kwargs)
                text = getattr(resp, "text", None)
                return text.strip() if isinstance(text, str) and text.strip() else None
            finally:
                with __import__("contextlib").suppress(Exception):
                    await client.close()

        try:
            return await asyncio.wait_for(_run(), timeout=300)
        except asyncio.TimeoutError:
            logger.error("Remote STT timed out for provider %r", self._provider_name)
            return None
        except Exception:
            logger.exception("Remote STT failed for provider %r", self._provider_name)
            return None

    def _resolve_api_key(self) -> str:
        """Configured key first, then a conventional ``<TYPE>_API_KEY`` env var."""
        if self._api_key:
            return self._api_key
        for var in ("OPENAI_API_KEY",):
            val = os.environ.get(var, "")
            if val:
                return val
        return ""
