"""SDK: the security helpers an app applies to untrusted content.

``fence_untrusted`` wraps free-text an app surfaces from an external source (scraped
web results, third-party API payloads) in ``<untrusted_content>`` fences so a
prompt-injection in that data is treated as data, not instructions — the same fencing
core applies. An app that ingests external text uses this rather than reimplementing it.
"""

from personalclaw.security import fence_untrusted  # noqa: F401

__all__ = ["fence_untrusted"]
