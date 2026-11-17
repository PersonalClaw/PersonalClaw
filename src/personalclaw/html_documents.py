"""A document made from untrusted HTML.

HTML that reaches the document tools is agent- or web-authored, so untrusted. It is routed
through the platform's existing sanitizer (``web.extract.sanitize_html``) and credential redactor
(``security.redact_credentials``), never a second implementation, and only then handed to the
documents package (``documents.from_markup.document_from_safe_html``). The two steps live here,
above ``personalclaw.documents``, so that package stays a leaf that imports nothing of core and
builds and tests alone (v0.4.0 B4, #5815).
"""

from __future__ import annotations

from personalclaw.documents.from_markup import document_from_safe_html
from personalclaw.documents.model import DocumentModel


def document_from_html(html: str, *, title: str = "") -> DocumentModel:
    """Sanitize untrusted HTML, mask the credentials in it, then convert it to a document.

    Raises ``web.extract.SanitizerUnavailable`` when nothing can sanitize it: the markup is never
    handed on unsanitized.
    """
    from personalclaw.security import redact_credentials
    from personalclaw.web.extract import sanitize_html

    safe = sanitize_html(html or "")
    safe, _ = redact_credentials(safe)
    return document_from_safe_html(safe, title=title)
