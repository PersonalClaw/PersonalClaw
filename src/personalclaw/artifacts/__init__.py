"""Artifact entity — named, versioned LLM-generated content as a provider entity.

Mirrors the Task/Prompt entity shape: an ``ArtifactProvider`` ABC + a registry +
the bundled ``NativeArtifactProvider`` (on-disk). Callers dispatch through the
registry, never a singleton.
"""

from personalclaw.artifacts import registry
from personalclaw.artifacts.models import Artifact, ArtifactEvent
from personalclaw.artifacts.provider import ArtifactProvider

__all__ = ["registry", "Artifact", "ArtifactEvent", "ArtifactProvider"]
