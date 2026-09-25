"""SDK: the local-model management contract — ``LocalModel`` + ``LocalModelProvider``.

A model-provider app that owns locally-downloadable models implements
:class:`LocalModelProvider` (list / download / delete, optionally search) so the host
lists, downloads, deletes, and surfaces its models uniformly — the app declares the
models it introduces; core hardcodes none. The app ALSO subclasses its use-case ABC
(``SttProvider`` / ``TtsProvider`` / ``DiarizationProvider`` / ``EmbeddingProvider``) for
inference. Import these from the SDK so core internals can evolve underneath the app.

``CapabilityMatrix`` is here because ``LocalModel.matrix`` is typed with it — an app
declaring what its models can do has to be able to name the declaration's type.
"""

from personalclaw.local_models.provider import (  # noqa: F401
    CapabilityMatrix,
    LocalModel,
    LocalModelProvider,
)

__all__ = ["LocalModel", "LocalModelProvider", "CapabilityMatrix"]
