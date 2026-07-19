"""Web fetch for knowledge ingestion.

``WebUrlConnector`` fetches any public web page → markdown text; it is used by the
bookmark node-graph (``BookmarkScrapeNode``) to scrape a bookmark's URL at ingest.
``BaseConnector`` is the minimal interface it implements.
"""

from personalclaw.knowledge.connectors.base import BaseConnector
from personalclaw.knowledge.connectors.web_url import WebUrlConnector

__all__ = ['BaseConnector', 'WebUrlConnector']
