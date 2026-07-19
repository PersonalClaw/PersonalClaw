"""The local-model management contract — descriptor + provider ABC.

A provider that owns local downloadable models implements this so core can list,
download, and delete them uniformly, and surface them for use-case binding. It is the
*management* axis only; a provider ALSO subclasses its use-case ABC (SttProvider,
TtsProvider, …) for the *inference* axis. The two are orthogonal by design: management
is identical across use-cases, inference is not.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LocalModel:
    """One model a local provider offers — everything the download UI + binding need.

    A provider RETURNS these from :meth:`LocalModelProvider.list_models`; core stores
    no per-model knowledge of its own. ``capabilities`` names the use-cases the model
    serves (``["stt"]``, ``["chat", "embedding"]``, …) so it appears under the right
    use-case in Settings → Models and the runtime can bind + inference against it.
    """

    name: str
    size_mb: float = 0
    description: str = ""
    downloaded: bool = False
    capabilities: list[str] = field(default_factory=list)
    gated: bool = False          # needs a token / license acceptance (e.g. pyannote)
    source: str = ""             # display-only origin hint (HF repo, GitHub release, ollama.com)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.name,
            "size_mb": self.size_mb,
            "size": int(self.size_mb or 0) * 1024 * 1024,
            "description": self.description,
            "downloaded": self.downloaded,
            "capabilities": list(self.capabilities),
            "gated": self.gated,
            "source": self.source,
        }


class LocalModelProvider(ABC):
    """A provider that owns locally-downloadable models.

    The management contract. Implementers also subclass their use-case ABC for
    inference; core resolves *this* surface for download/delete/list + availability,
    and the use-case registry for inference. Duck-typed at the registration seam —
    any ``type: model`` app whose provider implements these methods is registered.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable registry key (the provider/app name)."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human label for the provider's download card."""
        ...

    #: A provider whose catalog is DYNAMIC (populated from a search term, e.g. ollama's
    #: remote library) sets this True → the UI renders a search box. A fixed-catalog
    #: provider (piper/whisper/…) leaves it False → the UI lists :meth:`list_models`.
    searchable: bool = False

    @abstractmethod
    async def is_available(self) -> bool:
        """Whether this provider can run here (its runtime deps are importable)."""
        ...

    @abstractmethod
    async def list_models(self) -> list[LocalModel]:
        """The models to show in downloads — downloaded AND downloadable.

        A fixed-catalog provider returns its full known set. A :attr:`searchable`
        provider returns just the locally-present models here (discovery of the rest
        goes through :meth:`search_models`)."""
        ...

    async def search_models(self, query: str) -> list[LocalModel]:
        """Search a dynamic remote catalog for installable models (``searchable`` only).

        Default: no remote catalog → empty. Overridden by ollama to scrape its library.
        """
        return []

    @abstractmethod
    async def download_model(self, model_name: str) -> bool:
        """Fetch a model's weights locally. Returns True on success.

        A gated model with no token configured returns False (the UI greys it until a
        token is set). Long fetches run inside the download-job runner off the loop.
        """
        ...

    @abstractmethod
    async def delete_model(self, model_name: str) -> bool:
        """Remove a downloaded model. Returns True on success (False if not present)."""
        ...

    def cache_dir(self) -> str | None:
        """The dir whose on-disk growth tracks a download (best-effort progress bar).

        None → the job runner falls back to the shared models root; progress degrades
        to indeterminate rather than coupling core to a backend's cache layout.
        """
        return None
