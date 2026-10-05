"""Remote OpenAI-Images-compatible image-gen provider.

Resolves a config.json ``providers[]`` entry (its ``endpoint`` + ``api_key``)
and generates/edits via ``client.images``. One instance is registered per
OpenAI-family provider configured in Settings, keyed by that provider's name, so
a ``<name>:gpt-image-1`` active selection resolves to the same account that backs
that provider's chat/embedding. The ``openai`` SDK is imported lazily so importing
this module never pulls the SDK.

Synchronous endpoint under the async ``generate``/``edit`` signature — the OpenAI
Images API returns inline, so there's no poll loop here (FAL's bundle owns one).
This adapter also covers OpenAI-Images-compatible servers (Azure OpenAI, LocalAI)
via ``base_url``.
"""

import asyncio
import base64
import logging
import os
from typing import Any

from personalclaw.image_gen.provider import (
    ImageGenError,
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)

logger = logging.getLogger(__name__)

_GEN_TIMEOUT_S = 180


class OpenAIImageProvider(ImageGenProvider):
    """Generate/edit images via an OpenAI-Images-compatible hosted endpoint."""

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
        return f"{self._provider_name} (remote image)"

    def _catalog_models(self) -> list[ImageGenModel]:
        """The vendor's curated image models from its app-contributed catalog
        (personalclaw.media_catalogs), or [] when the provider type contributed none.

        The image_gen registry builds one adapter per OpenAI-compatible config
        provider, but only the vendor whose app contributed a catalog (OpenAI:
        gpt-image-1/dall-e-*) advertises curated models — a bring-your-own or
        different-vendor endpoint (e.g. Alibaba wan2.7-image) contributes none, so it
        advertises nothing here and the user binds a real model id that generate()
        forwards. No vendor id or host is hard-coded in core."""
        from personalclaw.media_catalogs import get_media_catalog

        cat = get_media_catalog("image_gen", self._provider_type)
        if not cat:
            return []
        return [
            ImageGenModel(
                name=m.name,
                description=m.description,
                sizes=list(m.extra.get("sizes", [])),
                supports_edit=bool(m.extra.get("supports_edit", False)),
            )
            for m in cat.models
        ]

    async def is_available(self) -> bool:
        """Usable when a credential resolves and the openai SDK is importable."""
        if not self._resolve_api_key():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    async def unavailable_reason(self) -> str:
        """Why this adapter cannot make images, when it is one that lists image models (its
        vendor's app contributed a catalog). One built for a vendor with no image catalog says
        nothing: an OpenAI-compatible chat server it serves is no image provider."""
        if not self._catalog_models() or await self.is_available():
            return ""
        if not self._resolve_api_key():
            return (
                f"{self._provider_name} has no API key, so it can't make images. Add the key on "
                f"the {self._provider_name} instance in Settings → Providers, or set "
                "OPENAI_API_KEY."
            )
        return (
            f"{self._provider_name} can't make images: the openai package isn't installed. "
            "Install it with `pip install 'personalclaw[openai]'` (or reinstall the OpenAI "
            "provider app), then restart PersonalClaw."
        )

    async def list_models(self) -> list[ImageGenModel]:
        # Only the vendor whose app contributed a catalog for this provider type
        # advertises curated models (OpenAI: gpt-image-1/dall-e-*). A different-vendor
        # or bring-your-own endpoint (Alibaba wan2.7-image, …) contributes none →
        # advertise nothing (no bogus models that'd 404); the user pins a real id that
        # generate() forwards.
        models = self._catalog_models()
        if not models:
            return []
        from personalclaw.image_gen.registry import active_image_gen

        resolved = active_image_gen()
        active_model = resolved[1] if resolved and resolved[0].name == self._provider_name else ""
        for m in models:
            m.downloaded = True
            m.active = m.name == active_model
        return models

    @staticmethod
    def _named(model: str) -> str:
        """The model a call names, or the SDK's refusal as an ``ImageGenError`` when it names none.

        Like chat, a call names its model: the Image · Generation binding in Settings → Models.
        The vendor's catalog lists the models to bind and is no default; the adapter used to send
        its default (OpenAI's gpt-image-1) for a call that named none.
        """
        from personalclaw.llm.registry import ProviderResolutionError, require_model

        try:
            return require_model(model)
        except ProviderResolutionError as exc:
            raise ImageGenError(str(exc)) from exc

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "",
        size: str = "",
        n: int = 1,
        **opts: Any,
    ) -> list[ImageResult]:
        # The model BEFORE the client: a call that names none is a request error independent of
        # SDK availability, so it must raise whether or not the openai SDK is installed (it is
        # an optional extra). Constructing the client first would mask it
        # behind "SDK not installed" in a stripped install.
        model_id = self._named(model)
        client = self._client()
        kwargs: dict[str, Any] = {"model": model_id, "prompt": prompt, "n": max(1, n)}
        if size:
            kwargs["size"] = size

        async def _run() -> list[ImageResult]:
            try:
                resp = await client.images.generate(**kwargs)
                return self._parse_response(resp)
            finally:
                await self._close(client)

        return await self._await(_run, "generate")

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
        # The model before the client (see generate()): a call that names none raises whether
        # or not the SDK is installed.
        model_id = self._named(model)
        client = self._client()
        kwargs: dict[str, Any] = {"model": model_id, "prompt": prompt, "n": max(1, n)}
        if size:
            kwargs["size"] = size

        async def _run() -> list[ImageResult]:
            img_fh = open(source_image, "rb")  # noqa: SIM115 — closed in finally
            mask_fh = open(mask, "rb") if mask else None  # noqa: SIM115
            try:
                kwargs["image"] = img_fh
                if mask_fh is not None:
                    kwargs["mask"] = mask_fh
                resp = await client.images.edit(**kwargs)
                return self._parse_response(resp)
            finally:
                img_fh.close()
                if mask_fh is not None:
                    mask_fh.close()
                await self._close(client)

        return await self._await(_run, "edit")

    # ── internals ──

    def _client(self) -> Any:
        try:
            import openai
        except ImportError as e:
            raise ImageGenError(
                "The openai SDK is not installed — cannot use remote image gen."
            ) from e
        api_key = self._resolve_api_key()
        if not api_key:
            raise ImageGenError(
                f"No API key configured for image provider {self._provider_name!r}."
            )
        return openai.AsyncOpenAI(api_key=api_key, base_url=self._endpoint or None)

    async def _await(self, run: Any, op: str) -> list[ImageResult]:
        try:
            return await asyncio.wait_for(run(), timeout=_GEN_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            raise ImageGenError(
                f"Image {op} timed out for provider {self._provider_name!r}."
            ) from e
        except ImageGenError:
            raise
        except Exception as e:  # noqa: BLE001 — normalize SDK/HTTP errors to a clean message
            logger.exception("Remote image %s failed for provider %r", op, self._provider_name)
            raise ImageGenError(f"Image {op} failed: {e}") from e

    @staticmethod
    def _parse_response(resp: Any) -> list[ImageResult]:
        """Normalize an OpenAI Images response to ``ImageResult`` (url | b64)."""
        out: list[ImageResult] = []
        for item in getattr(resp, "data", None) or []:
            b64 = getattr(item, "b64_json", None)
            url = getattr(item, "url", None)
            revised = getattr(item, "revised_prompt", None) or ""
            if b64:
                out.append(ImageResult(b64=b64, mime="image/png", revised_prompt=revised))
            elif url:
                out.append(ImageResult(url=url, mime="image/png", revised_prompt=revised))
        if not out:
            raise ImageGenError("Image provider returned no images.")
        return out

    @staticmethod
    async def _close(client: Any) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            await client.close()

    def _resolve_api_key(self) -> str:
        """Configured key first, then a conventional ``OPENAI_API_KEY`` env var."""
        if self._api_key:
            return self._api_key
        return os.environ.get("OPENAI_API_KEY", "")


def decode_b64_image(b64: str) -> bytes:
    """Decode a base64 image payload to raw bytes (shared by the cache-materializer)."""
    return base64.b64decode(b64)
