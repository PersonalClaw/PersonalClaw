"""The source-tree checks split from
``tests/test_config_transactions.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from config_transactions_shared import _raw_config_writes
from rails_repo import REPO

#: The one module allowed to replace ``config.json`` itself.
_THE_WRITER = "config/transactions.py"


def test_nothing_writes_the_config_file_around_the_transaction():
    root = REPO / "src" / "personalclaw"
    offenders = {
        str(path.relative_to(root)): lines
        for path, tree in source_index.package_trees()
        if str(path.relative_to(root)) != _THE_WRITER and (lines := _raw_config_writes(tree))
    }
    assert offenders == {}, f"raw config.json writers: {offenders}"
