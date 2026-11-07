"""The dashboard's page asks for signed addresses in requests the signing route takes.

The route refuses a request for more than ``signed_urls.MAX_URLS`` addresses, and the page splits
each batch at ``MAX_URLS_PER_REQUEST`` (``web/src/lib/signedUrl.ts``). The two numbers live in two
languages with nothing to share them, and a page whose chunk is larger has its long lists refused,
which no behaviour test on either side sees. So this rail reads the page's number from its source.
"""

from __future__ import annotations

import re

import source_index

from personalclaw.dashboard import signed_urls

_SIGNED_URL_TS = source_index.REPO / "web" / "src" / "lib" / "signedUrl.ts"
_DECLARED = re.compile(r"^export const MAX_URLS_PER_REQUEST = (\d+)$", re.MULTILINE)


def test_the_page_splits_a_batch_at_the_routes_limit() -> None:
    declared = _DECLARED.findall(source_index.text(_SIGNED_URL_TS))
    assert len(declared) == 1, "signedUrl.ts declares MAX_URLS_PER_REQUEST once, as a number"
    assert int(declared[0]) == signed_urls.MAX_URLS, "the page's chunk isn't the route's limit"
