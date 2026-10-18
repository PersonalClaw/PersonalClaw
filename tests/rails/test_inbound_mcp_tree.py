"""The source-tree checks split from
``tests/test_inbound_mcp.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import inbound_mcp_shared
import source_index

from personalclaw.inbound import mcp_http

_isolate = inbound_mcp_shared._isolate


class TestProtocolRevision:
    def test_the_tree_contains_no_session_handling(self):
        """The regression lock: no CODE reads or sets a session header.

        Greps code lines only, skipping comments — the first version of this test matched
        the module's own comment explaining that it has no sessions, which made the lock
        unsatisfiable while looking like a genuine failure.
        """
        import pathlib

        root = pathlib.Path(mcp_http.__file__).parent
        hits = []
        for path in source_index.under(source_index.package_files(), root.resolve()):
            for line in source_index.text(path).splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "Mcp-Session-Id" in stripped or "mcp_session_id" in stripped:
                    hits.append(f"{path.name}: {stripped[:60]}")
        assert hits == [], f"session handling reappeared in {hits}"
