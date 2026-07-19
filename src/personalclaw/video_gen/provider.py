"""Abstract base for video-generation providers.

Shaped like :class:`~personalclaw.image_gen.provider.ImageGenProvider` — ``generate``
is ``async`` so a provider can hide its submit->poll loop (FAL's queue) behind the
signature. The ABC normalizes the contract so the caller never sees the difference
between a synchronous endpoint and an async queue platform.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VideoGenModel:
    """A model a video-gen provider offers.

    ``aspect_ratios`` are the supported output ratios (e.g. ``"16:9"``);
    ``max_duration_s`` is the maximum supported clip length in seconds.
    Hosted models need no download, so ``downloaded`` defaults True for remotes.
    """

    name: str
    description: str = ""
    aspect_ratios: list[str] = field(default_factory=list)
    max_duration_s: int = 10
    downloaded: bool = True
    active: bool = False


@dataclass
class VideoResult:
    """One generated video, normalized across providers.

    A provider returns a ``url`` (possibly expiring) pointing to the video file.
    The capability layer materializes it so delivery survives URL expiry.
    ``mime`` is typically ``video/mp4``.
    """

    url: str = ""
    mime: str = "video/mp4"
    local_path: str = ""
    duration_s: float = 0.0


class VideoGenProvider(ABC):
    """Provider interface for video generation backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """True when a credential resolves and the backend is reachable."""
        ...

    @abstractmethod
    async def list_models(self) -> list[VideoGenModel]:
        """List the models this provider offers."""
        ...

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        duration_seconds: float = 5.0,
        aspect_ratio: str = "",
        **opts: Any,
    ) -> list[VideoResult]:
        """Generate a video from ``prompt``.

        A submit->poll provider MUST own its poll loop inside this coroutine,
        bounded by a per-provider timeout — never leak the async/sync difference
        to the caller.
        """
        ...

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}


class VideoGenError(Exception):
    """A provider could not generate (bad credential, unsupported op, timeout).

    Carries a human-facing message the tool surfaces to the model + the user.
    """
