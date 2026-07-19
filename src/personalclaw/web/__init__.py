"""Web fetch/extract pipeline — the hardened path behind the `web_fetch` tool.

Content extraction (`extract.py`) is the shared core both `web_fetch` and the
knowledge web-url connector use, so there is ONE boilerplate-removing extractor (not
two forks). The fetch pipeline itself routes through the egress chokepoint
(`personalclaw.net`) so a fetch is never unguarded.
"""

from personalclaw.web.extract import ExtractedDoc, extract_main_content
from personalclaw.web.fetch import (
    ExtractOutcome,
    FetchOutcome,
    record_seen_urls,
    url_has_provenance,
    web_extract,
    web_fetch,
)
from personalclaw.web.render import RenderResult, render_url

__all__ = [
    "ExtractedDoc",
    "extract_main_content",
    "FetchOutcome",
    "ExtractOutcome",
    "web_fetch",
    "web_extract",
    "record_seen_urls",
    "url_has_provenance",
    "RenderResult",
    "render_url",
]
