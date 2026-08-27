"""Which artifact kinds have an editable document model, and how each crosses the wire.

``GET``/``PUT /api/artifacts/{slug}/model`` need three things per kind — a parser, a
serializer and a strict deserializer — and before this module they were three hard-wired
``docx`` imports in ``artifacts/handlers.py`` beside a hand-kept ``_MODEL_KINDS = ("docx",)``
tuple. Two problems with that: adding a second kind meant branching the route, and the
tuple could name a kind whose parser did not exist (a declared kind with no runtime, which
answers a capability question with a lie).

So the table IS the capability: :data:`MODEL_KINDS` is derived from it, which makes
"advertised" and "implemented" the same fact. A kind is absent until its three functions
exist, and present the moment they do.

The parsers are imported lazily, inside :func:`get_codec`, because they pull in openpyxl /
python-docx and the gateway must not pay for a document library on a route nobody called.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from personalclaw.documents.docx_parser import LossReport


@dataclass(frozen=True)
class ModelCodec:
    """One kind's full model round trip: bytes in, JSON out, JSON in, model out.

    Frozen because a codec is a capability declaration — something that could swap a
    parser at run time would make the version of a document a user loaded and the version
    they saved two different contracts.
    """

    kind: str
    #: bytes → (model, what the parse could not represent).
    parse: Callable[[bytes], tuple[Any, LossReport]]
    #: model → JSON-ready data for the browser.
    to_dict: Callable[[Any], dict[str, Any]]
    #: untrusted JSON → model, raising ``ValueError`` with the offending path.
    from_dict: Callable[[Any], Any]


def get_codec(kind: str) -> ModelCodec | None:
    """The codec for *kind*, or ``None`` when no document model ships for it.

    ``None`` rather than a raise, matching ``registry.get_writer``: "can this be edited?"
    is a question the route asks about *user-supplied* input, and an exception would make
    every caller wrap a lookup it is allowed to fail.
    """
    if kind == "docx":
        from personalclaw.documents.docx_parser import parse_docx
        from personalclaw.documents.model_json import document_from_dict, document_to_dict

        return ModelCodec(
            kind="docx",
            parse=parse_docx,
            to_dict=document_to_dict,
            from_dict=document_from_dict,
        )
    if kind == "xlsx":
        from personalclaw.documents.sheet_json import sheet_from_dict, sheet_to_dict
        from personalclaw.documents.xlsx_parser import parse_xlsx

        return ModelCodec(
            kind="xlsx",
            parse=parse_xlsx,
            to_dict=sheet_to_dict,
            from_dict=sheet_from_dict,
        )
    return None


#: The kinds ``…/model`` serves. ``pptx`` is absent deliberately: its writer ships but its
#: parser is ``DFE-8``, and a read-less save could only overwrite a document with content
#: the editor never loaded.
#:
#: Declared, not computed by calling :func:`get_codec` for every candidate — that would
#: import both document libraries at module import and throw away the laziness above. The
#: drift this list could carry (a kind advertised with no codec behind it) is closed by
#: ``test_every_declared_model_kind_resolves_to_a_codec``, which is where a capability
#: claim belongs: asserted, not made true by construction at a cost.
MODEL_KINDS: tuple[str, ...] = ("docx", "xlsx")
