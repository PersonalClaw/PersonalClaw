"""Abstract base for image-generation providers.

Shaped like :class:`~personalclaw.stt.provider.SttProvider`, with one deliberate
deviation — ``generate``/``edit`` are ``async`` so a provider can hide its own
submit->poll loop (FAL's ``queue.fal.run``) behind the signature. OpenAI's adapter
calls a synchronous endpoint under it; the caller never sees the difference. This
async-internally contract is what lets one ABC serve both the sync "OpenAI-Images
standard" camp and the async bespoke-platform camp.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImageGenModel:
    """A model an image-gen provider offers.

    ``sizes`` are the supported output sizes (e.g. ``"1024x1024"``);
    ``supports_edit`` marks models that accept a source image for in-place edits.
    Hosted models need no download, so ``downloaded`` defaults True for remotes.
    """

    name: str
    description: str = ""
    sizes: list[str] = field(default_factory=list)
    supports_edit: bool = False
    downloaded: bool = True
    active: bool = False


@dataclass
class ImageResult:
    """One generated/edited image, normalized across providers.

    A provider returns either a ``url`` (possibly expiring) or inline ``b64``;
    the capability layer materializes both to ``local_path`` so delivery survives
    URL expiry. ``revised_prompt`` is the provider's rewritten prompt when offered
    (OpenAI gpt-image returns one).
    """

    local_path: str = ""
    mime: str = "image/png"
    url: str = ""
    b64: str = ""
    revised_prompt: str = ""


class ImageGenProvider(ABC):
    """Provider interface for image generation + editing backends."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    async def is_available(self) -> bool:
        """True when a credential resolves and the SDK/runtime is importable."""
        ...

    @abstractmethod
    async def list_models(self) -> list[ImageGenModel]:
        """List the models this provider offers (downloaded + downloadable)."""
        ...

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        """Generate ``n`` images from ``prompt``.

        A submit->poll provider MUST own its poll loop inside this coroutine,
        bounded by a per-provider timeout — never leak the async/sync difference
        to the caller.
        """
        ...

    @abstractmethod
    async def edit(
        self,
        prompt: str,
        *,
        source_image: str,
        mask: str = "",
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        """Edit ``source_image`` per ``prompt`` (optionally within ``mask``).

        ``source_image``/``mask`` are local file paths. Providers without an edit
        endpoint raise :class:`ImageGenError`.
        """
        ...

    # ── native-only, deferred (IG7): local model lifecycle ──
    async def download_model(self, model_name: str) -> bool:
        """Download/install a local model. Remotes return False (always present)."""
        return False

    async def delete_model(self, model_name: str) -> bool:
        """Delete a downloaded local model. Remotes return False (nothing local)."""
        return False

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "display_name": self.display_name}


class ImageGenError(Exception):
    """A provider could not generate/edit (bad credential, unsupported op, timeout).

    Carries a human-facing message the tool surfaces to the model + the user.
    """
