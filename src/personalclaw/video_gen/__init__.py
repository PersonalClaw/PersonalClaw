"""Video-generation provider registry and ABC.

Mirrors ``personalclaw.image_gen`` — a thin typed-registry so video-gen
providers (FAL, Runway, etc.) can be contributed by removable app bundles
and resolved by the ``video_gen`` active-model binding at runtime.
"""
