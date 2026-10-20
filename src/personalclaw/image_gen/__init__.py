"""Image generation — a first-class PClaw capability on the ``image_gen`` use-case.

Mirrors the STT/TTS pattern: an :class:`ImageGenProvider` ABC, a registry that
resolves the active provider+model from ``active_models.json``, one core
OpenAI-Images-compatible adapter (built per configured OpenAI-family provider),
and per-platform removable bundles for the bespoke async-queue platforms (FAL).

The one deviation from the STT template is async tolerance: image platforms are
frequently submit->poll, so :meth:`ImageGenProvider.generate` / ``edit`` are
``async`` and each provider hides its own poll loop behind that signature — the
caller never sees the sync/async difference.
"""
