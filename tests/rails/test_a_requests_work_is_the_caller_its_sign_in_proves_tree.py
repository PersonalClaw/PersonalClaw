"""The source-tree checks split from
``tests/test_a_requests_work_is_the_caller_its_sign_in_proves.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from a_requests_work_is_the_caller_its_sign_in_proves_shared import _header_reads
from rails_repo import REPO

#: The one module that reads the session a request names, and judges it by who signed the
#: request in (``approval_answer.work_of_request``, beside the principal it builds on).
THE_RULE = "personalclaw/approval_answer.py"


SRC = REPO / "src"


def test_only_the_one_rule_reads_the_session_a_request_names():
    readers = {
        path.relative_to(SRC).as_posix(): found
        for path, tree in source_index.package_trees()
        if (found := _header_reads(tree))
    }

    # The control: the rule itself reads it, so a scan that read nothing would fail here.
    assert readers.pop(THE_RULE, None), "the scan found no read of the header at all"
    assert readers == {}, (
        "these read the session a request names in X-Session-Key themselves; ask "
        "approval_answer.work_of_request, which judges it by who signed the request in: "
        f"{readers}"
    )
