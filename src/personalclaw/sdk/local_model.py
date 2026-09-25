"""SDK: the local-model management contract — ``LocalModel`` + ``LocalModelProvider``.

A model-provider app that owns locally-downloadable models implements
:class:`LocalModelProvider` (list / download / delete, optionally search) so the host
lists, downloads, deletes, and surfaces its models uniformly — the app declares the
models it introduces; core hardcodes none. The app ALSO subclasses its use-case ABC
(``SttProvider`` / ``TtsProvider`` / ``DiarizationProvider`` / ``EmbeddingProvider``) for
inference. Import these from the SDK so core internals can evolve underneath the app.

``CapabilityMatrix`` is here because ``LocalModel.matrix`` is typed with it — an app
declaring what its models can do has to be able to name the declaration's type.

**The artifact-admission rail rides alongside** (``parse_declaration`` and friends, from
``personalclaw.bundled_model``). An app that downloads a model weight has to answer three
questions a release gate also answers — is this licence one we may redistribute, are these the
bytes that were signed off, and is this within the declared size — and the answers must be the
SAME answers. Exporting core's implementation is the point: a second copy inside an app is a
second licence allowlist and a second digest check that can drift from the one CI enforces,
and the drift would be invisible because each half looks internally consistent. The
``DOWNLOAD_*`` outcomes come too, because "it failed" is not actionable and the four failures
(unreachable / bad status / truncated / digest mismatch) need four different things from a user.
``LicenceDecision`` is here for the same reason ``CapabilityMatrix`` is: it is what
``licence_decision()`` RETURNS, and a published function whose return type an app cannot import
is published only halfway — the app would have to recover the type off the value, which
type-checks against nothing.

🔴 WHAT IS NOT HERE, AND WHY — read before adding it back. Three questions, three functions:
``licence_decision`` for the licence, and ``verify_download`` for the other two, because the
digest and the size ceiling are ONE verdict about one file. ``sha256_file`` and
``size_decision`` are that verdict's internals: an app that called them itself would be
re-assembling ``verify_download`` by hand, which is the second implementation this facade exists
to prevent. The outcome constants are the four an app has to TELL APART, not the whole set a
``DownloadResult`` can carry: success is ``DownloadResult.ok``, and ``over-budget`` reaches a user
through ``DownloadResult.detail`` with nothing for an app to branch on. Every name was measured
against the inert-surface census when this was narrowed, and each one left had no consumer
anywhere — not in core, not in the first-party apps repository. Publishing a name nothing
imports is a promise the census cannot tell apart from a dead one, so the way to add one back is
beside the app that needs it.
"""

from personalclaw.bundled_model import (  # noqa: F401
    DECLARATION_RELPATH,
    DOWNLOAD_BAD_STATUS,
    DOWNLOAD_DIGEST_MISMATCH,
    DOWNLOAD_TRUNCATED,
    DOWNLOAD_UNREACHABLE,
    BundleDeclaration,
    BundleDeclarationError,
    DownloadResult,
    LicenceDecision,
    licence_decision,
    parse_declaration,
    verify_download,
)
from personalclaw.local_models.provider import (  # noqa: F401
    CapabilityMatrix,
    LocalModel,
    LocalModelProvider,
)

__all__ = [
    "LocalModel",
    "LocalModelProvider",
    "CapabilityMatrix",
    "BundleDeclaration",
    "BundleDeclarationError",
    "DownloadResult",
    "LicenceDecision",
    "DECLARATION_RELPATH",
    "DOWNLOAD_UNREACHABLE",
    "DOWNLOAD_BAD_STATUS",
    "DOWNLOAD_TRUNCATED",
    "DOWNLOAD_DIGEST_MISMATCH",
    "licence_decision",
    "parse_declaration",
    "verify_download",
]
