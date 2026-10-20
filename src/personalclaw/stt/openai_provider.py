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
from personalclaw.net.client import EgressBlocked
from personalclaw.net.http_clients import http_client
from personalclaw.providers.failure_copy import connectivity_guidance, sentence_with_detail
from personalclaw.stt.provider import SttError, SttProvider

logger = logging.getLogger(__name__)

#: How long one hosted transcription may take. A remote service that has not answered by
#: then is not going to; a local model has no such cap (its caller's budget scales with the
#: recording).
_REMOTE_TIMEOUT_S = 300


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
        """The transcript, ``""`` when the service heard no speech.

        A transcription that could not run raises :class:`SttError` saying why (no SDK, no
        key, the service's own failure, no answer in time), so it is never read as audio with
        no speech in it. A call that names no model is refused with ``None`` before anything is
        sent, as every media call is."""
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
        except ImportError as exc:
            raise SttError(
                "Remote speech-to-text needs the openai package, which isn't installed. Install "
                "it with `pip install 'personalclaw[openai]'` (or reinstall the OpenAI provider "
                "app); `personalclaw doctor` checks provider dependencies."
            ) from exc

        api_key = self._resolve_api_key()
        if not api_key:
            raise SttError(
                f"{self._provider_name} has no API key, so it can't transcribe. Add its key in "
                "Settings → Providers."
            )

        lang = language.split("-")[0] if language else None
        base_url = self._endpoint or None

        async def _run() -> str:
            # The upload asks the egress guard first, under the owner's Network egress settings.
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                http_client=http_client(
                    endpoint=base_url or "",
                    model_provider=True,
                    refused_as=openai.OpenAIError,
                    follow_redirects=True,
                ),
            )
            try:
                with open(audio_path, "rb") as fh:
                    kwargs: dict = {"model": model_id, "file": fh}
                    if lang:
                        kwargs["language"] = lang
                    resp = await client.audio.transcriptions.create(**kwargs)
                text = getattr(resp, "text", None)
                return text.strip() if isinstance(text, str) else ""
            finally:
                with __import__("contextlib").suppress(Exception):
                    await client.close()

        try:
            return await asyncio.wait_for(_run(), timeout=_REMOTE_TIMEOUT_S)
        except EgressBlocked as exc:
            # The guard's own sentence: what was not reached, and the setting that decided it.
            raise SttError(str(exc)) from exc
        except asyncio.TimeoutError as exc:
            logger.error("Remote STT timed out for provider %r", self._provider_name)
            raise SttError(
                f"{self._provider_name} didn't finish transcribing within "
                f"{_REMOTE_TIMEOUT_S // 60} minutes, so speech-to-text stopped waiting. Try "
                "again; if it keeps happening, try a shorter recording."
            ) from exc
        except Exception as exc:
            logger.exception("Remote STT failed for provider %r", self._provider_name)
            raise SttError(
                connectivity_guidance(exc, endpoint=self._endpoint)
                or sentence_with_detail(
                    f"{self._provider_name} could not transcribe this audio.", exc
                )
            ) from exc

    def _resolve_api_key(self) -> str:
        """Configured key first, then a conventional ``<TYPE>_API_KEY`` env var."""
        if self._api_key:
            return self._api_key
        for var in ("OPENAI_API_KEY",):
            val = os.environ.get(var, "")
            if val:
                return val
        return ""
