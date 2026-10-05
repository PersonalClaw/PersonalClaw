"""Abstract base for diarization providers.

Diarization takes audio → speaker TURNS (time ranges tagged SPEAKER_00/01/…). It is
unsupervised (finds *distinct* speakers, not identities) and produces NO words — so it
never touches vocabulary correction. Naming the anonymous speakers is a separate, cheap
step done in the Minutes app. Mirrors the ``stt/`` provider shape so a diarization app
plugs into the ``diarization`` use-case exactly as an STT app plugs into ``stt``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpeakerTurn:
    """One diarized speaker segment: [start, end) tagged with an anonymous label."""

    start: float
    end: float
    speaker: str  # e.g. "SPEAKER_00"


class DiarizationError(Exception):
    """A diarization provider could not tell the speakers apart, and its message says why.

    The message is product copy, shown as written: what went wrong and what to do. It is what
    separates a failed diarization from a recording with one speaker: both used to come back
    as ``None`` or no turns, so a two-voice clip whose audio the provider could not even read
    landed "Diarization done" with no speakers, and nothing said why.
    """


@dataclass
class DiarizationModel:
    name: str
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False
    gated: bool = False  # True when the model needs a license/token (e.g. pyannote/HF)
    languages: list[str] = field(default_factory=list)


class DiarizationProvider(ABC):
    """Provider interface for speaker-diarization backends — the INFERENCE axis (``diarize``).

    Model MANAGEMENT (download/delete of local diarization models) is a SEPARATE axis:
    a local backend (the ONNX / pyannote apps) ALSO subclasses
    :class:`~personalclaw.local_models.provider.LocalModelProvider`. Keeping the axes
    independent means a future remote/hosted diarization service would implement only this
    inference axis, with no local-management stubs.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Whether this provider is installed + usable (deps present, token if gated)."""
        ...

    def untestable_reason(self) -> str:
        """Why Settings → Models offers no Test for this provider's models, or ``""`` when it does.

        A Test (``providers.model_test``) is one real :meth:`diarize` of a half-second tone. A
        provider that cannot afford even that on a click says so here, and its rows show the
        sentence instead of a Test.
        """
        return ""

    @abstractmethod
    async def diarize(
        self,
        audio_path: str,
        *,
        model: str = "",
        num_speakers: int | None = None,
        min_speakers: int | None = None,
        max_speakers: int | None = None,
    ) -> list[SpeakerTurn] | None:
        """Speaker turns for *audio_path*: ``[]`` when no one spoke in it.

        *audio_path* is any recording the product accepts (a knowledge upload keeps its own
        format: ``.m4a``, ``.mp3``, ``.webm``, a video's extracted ``.wav``), so a provider
        decodes it to what its model needs. Raises :class:`DiarizationError` when it could not
        diarize and can say why. ``None`` is a failure it cannot explain, and a call that names
        no model is refused with ``None``.
        """
        ...

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}
