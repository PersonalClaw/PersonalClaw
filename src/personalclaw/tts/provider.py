"""Abstract base for TTS providers — the INFERENCE axis (``synthesize``).

TTS as a capability: turn text into audio. Model MANAGEMENT (download/delete voices) is
a SEPARATE axis: a LOCAL backend (piper) subclasses :class:`LocalTtsProvider` (which mixes
in :class:`~personalclaw.local_models.provider.LocalModelProvider` and bridges its voices
to the uniform local-model contract); a REMOTE/hosted backend (OpenAI TTS) subclasses only
:class:`TtsProvider` — its voices aren't downloaded/managed, so it carries no voice-
management stubs. The two axes are independent.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from personalclaw.local_models.provider import LocalModel, LocalModelProvider


@dataclass
class TtsVoice:
    name: str
    language: str = ""
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False


class TtsProvider(ABC):
    """Text-to-speech INFERENCE interface — ``synthesize`` only."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool: ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "",
        output_path: str = "",
        *,
        speed: float = 1.0,
        **opts: Any,
    ) -> str | None:
        """Synthesize text to an audio file. Returns the path or None on failure.

        ``voice`` is the provider's model/voice id; ``speed`` is the common
        speaking-rate control. Provider-specific knobs (e.g. an OpenAI speech
        persona) arrive in ``opts``. The caller owns deleting the returned file.
        """
        ...

    async def can_synthesize(self, voice: str = "") -> bool:
        """Whether this provider can produce audio for *voice* right now.

        Defaults to :meth:`is_available`; providers whose readiness depends on a
        downloaded asset (e.g. a Piper ``.onnx``) override to verify it.
        """
        return await self.is_available()

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}


class LocalTtsProvider(TtsProvider, LocalModelProvider):
    """A LOCAL TTS backend — inference (:class:`TtsProvider`) PLUS local-model management
    (:class:`LocalModelProvider`). Its downloadable VOICES are its local models: implement
    the voice methods below and this bridges them to the uniform management contract
    (``list_models``/``download_model``/``delete_model``). Piper subclasses this; a remote
    TTS provider subclasses plain :class:`TtsProvider` instead."""

    @abstractmethod
    async def list_voices(self) -> list[TtsVoice]: ...

    @abstractmethod
    async def download_voice(self, voice_name: str) -> bool: ...

    @abstractmethod
    async def delete_voice(self, voice_name: str) -> bool: ...

    # voices ARE the local models — bridge the management contract to the voice methods.
    async def list_models(self) -> list[LocalModel]:
        return [
            LocalModel(
                name=v.name,
                size_mb=v.size_mb,
                description=v.description,
                downloaded=v.downloaded,
                capabilities=["tts"],
            )
            for v in await self.list_voices()
        ]

    async def download_model(self, model_name: str) -> bool:
        return await self.download_voice(model_name)

    async def delete_model(self, model_name: str) -> bool:
        return await self.delete_voice(model_name)
