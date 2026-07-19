"""Removable per-platform image-gen bundles (bespoke async-queue providers).

Each module here is a vendor-specific :class:`ImageGenProvider` kept OUT of the
vendor-neutral core. The image-gen registry imports a bundle's ``register_*``
hook (import-guarded), so removing a bundle simply removes its provider.
"""
