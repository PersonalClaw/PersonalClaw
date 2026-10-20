"""Abstract base for STT providers + the rich-transcript contract.

The flat ``transcribe() -> str`` is the primitive every provider implements. The rich
``transcribe_detailed() -> TranscriptResult`` is an ADDITIVE capability: a provider that
can emit segments + word timestamps overrides it and flips the capability flags; one that
can't inherits the default, which wraps its flat text in a single-field TranscriptResult.
So callers that want structure get it where available and degrade cleanly elsewhere — no
second transcription path, no provider forced to fabricate data it doesn't have.
"""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TranscriptWord:
    """One decoded word with its time span + per-word confidence (0..1)."""

    start: float
    end: float
    word: str
    prob: float = 1.0


@dataclass
class TranscriptSegment:
    """A contiguous span of speech. ``speaker`` is filled later by the speaker-fusion
    node when diarization is present; ``words`` carries word-level timestamps when
    the provider supports them."""

    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list["TranscriptWord"] = field(default_factory=list)


@dataclass
class TranscriptResult:
    """The rich transcript: a flat ``text`` (back-compat / FTS / embeddings) PLUS the
    structured ``segments`` (click-to-seek, speaker labels, timestamp chunking). Persisted
    as JSON in a node output's ``metadata`` (no schema migration)."""

    text: str
    language: str = ""
    duration: float = 0.0
    segments: list["TranscriptSegment"] = field(default_factory=list)

    def to_dict(self) -> dict:
        """The persisted JSON shape (nested dataclasses flattened)."""
        return asdict(self)


@dataclass
class SttModel:
    name: str
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    active: bool = False
    language_codes: list[str] = field(default_factory=list)


class SttError(Exception):
    """A speech-to-text provider could not transcribe, and its message says why.

    The message is product copy, shown as written: what is missing or went wrong, and what to
    do. It is what separates a failed transcription from a recording with no speech in it —
    both used to come back as ``None``, so a knowledge item whose provider could not sign in
    landed "done" with an empty transcript, and the composer's microphone went quiet.
    """


class SttProvider(ABC):
    """Provider interface for speech-to-text backends — the INFERENCE axis only.

    Speech-to-text as a capability: ``transcribe`` (+ the rich ``transcribe_detailed``).
    This is orthogonal to model MANAGEMENT (download/delete): a LOCAL backend
    (faster-whisper) ALSO subclasses :class:`~personalclaw.local_models.provider.
    LocalModelProvider` to own its downloadable models; a REMOTE/hosted backend
    (OpenAI whisper-1) implements ONLY this inference axis — it has no local models to
    manage, so it carries no management stubs. Management and inference are clean,
    independent axes; a provider opts into each only if it genuinely serves it.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this provider is installed and usable."""
        ...

    async def unavailable_reason(self) -> str:
        """Why :meth:`is_available` answers False, as the sentence a surface shows: what is
        missing and what to do. ``""`` when this provider cannot say, and the surface uses its
        own words.

        Asked only after ``is_available()`` said False. A remote provider whose credentials
        failed knows why — its SDK told it — and "speech-to-text isn't available" on its own sent
        the user to set up a model that was already set up.
        """
        return ""

    def untestable_reason(self) -> str:
        """Why Settings → Models offers no Test for this provider's models, or ``""`` when it does.

        A Test (``providers.model_test``) is one real :meth:`transcribe` of a half-second tone. A
        provider that cannot afford even that on a click says so here, and its rows show the
        sentence instead of a Test.
        """
        return ""

    @abstractmethod
    async def transcribe(self, audio_path: str, model: str = "", language: str = "") -> str | None:
        """Transcribe an audio file: its text, empty when there was no speech.

        Raises :class:`SttError` when it could not transcribe and can say why. ``None`` is a
        failure it cannot explain.
        """
        ...

    async def transcribe_detailed(
        self,
        audio_path: str,
        *,
        model: str = "",
        language: str = "",
        bias_terms: list[str] | None = None,
    ) -> "TranscriptResult | None":
        """Rich transcription → segments + word timestamps.

        Default implementation wraps the flat :meth:`transcribe` output in a
        single-field :class:`TranscriptResult` (no segments), so every existing provider
        keeps working. Providers that can emit structure (e.g. faster-whisper) override
        this and flip :attr:`supports_segments` / :attr:`supports_word_timestamps`.
        ``bias_terms`` is the Lexicon's pre-decode vocabulary hint; providers
        without :attr:`supports_bias_terms` ignore it. Fails as :meth:`transcribe` does.
        """
        text = await self.transcribe(audio_path, model=model, language=language)
        return TranscriptResult(text=text) if text is not None else None

    # ── rich-transcript capability flags (default False; overriding providers flip) ──
    @property
    def supports_segments(self) -> bool:
        return False

    @property
    def supports_word_timestamps(self) -> bool:
        return False

    @property
    def supports_bias_terms(self) -> bool:
        return False

    @property
    def supports_streaming(self) -> bool:
        return False

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "supports_streaming": self.supports_streaming,
        }
