"""The source-tree checks split from
``tests/test_knowledge_contradiction.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import source_index
from rails_repo import REPO


def test_no_module_composes_the_knowledge_path_itself():
    """A second copy of the path is how the split-brain happened. The helper is the only place it
    is written, so a future caller cannot reintroduce a divergent one by accident."""
    root = REPO / "src/personalclaw"
    offenders = [
        str(path.relative_to(root))
        for path in source_index.package_files()
        if '"knowledge" / "knowledge.db"' in source_index.text(path) and path.name != "store.py"
    ]
    assert offenders == [], f"these compose the knowledge path directly: {offenders}"
