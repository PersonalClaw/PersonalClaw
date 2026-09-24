"""Abstract base for diarization providers (core L1).

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
        """Return speaker turns for *audio_path*, or None on failure / no model."""
        ...

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}
