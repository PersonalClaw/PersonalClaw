"""A document made from untrusted HTML.

HTML that reaches the document tools is agent- or web-authored, so untrusted. It is routed
through the platform's existing sanitizer (``web.extract.sanitize_html``) and credential redactor
(``security.redact_credentials``), never a second implementation, and only then handed to the
documents package (``documents.from_markup.document_from_safe_html``). The document it makes is
masked too (:func:`masked`), so credentials are masked in what the document holds, not only in the
markup it was made from. These steps live here, above ``personalclaw.documents``, so that package
stays a leaf that imports nothing of core and builds and tests alone (v0.4.0 B4, #5815).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from typing import Any, TypeVar, cast

from personalclaw.documents.from_markup import document_from_safe_html
from personalclaw.documents.model import DocumentModel, Run

_Model = TypeVar("_Model")


def document_from_html(html: str, *, title: str = "") -> DocumentModel:
    """Sanitize untrusted HTML, mask the credentials in it, convert it to a document, then mask
    the credentials in what the document holds.

    Raises ``web.extract.SanitizerUnavailable`` when nothing can sanitize it: the markup is never
    handed on unsanitized.
    """
    from personalclaw.security import redact_credentials
    from personalclaw.web.extract import sanitize_html

    safe = sanitize_html(html or "")
    safe, _ = redact_credentials(safe)
    return masked(document_from_safe_html(safe, title=title))


def masked(model: _Model) -> _Model:
    """*model* with each credential in the text it holds masked, as a reader of its file reads it.

    Every string is masked, and so is each sequence of formatted runs, joined as it reads: a value
    whose pieces sit in runs of different formatting is one value on the page. When the joined runs
    hold one, they become a single unformatted run of the masked text.
    """
    from personalclaw.security import redact_credentials

    def text(value: str) -> str:
        return redact_credentials(value)[0] if value else value

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return text(value)
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not is_dataclass(value) or isinstance(value, type):
            return value
        changes = {f.name: walk(getattr(value, f.name)) for f in fields(value) if f.init}
        runs = changes.get("runs")
        if runs:
            joined = "".join(run.text for run in runs)
            if text(joined) != joined:
                changes["runs"] = [Run(text=text(joined))]
        return replace(value, **changes)

    return cast(_Model, walk(model))
