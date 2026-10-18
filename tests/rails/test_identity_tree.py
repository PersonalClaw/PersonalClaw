"""The source-tree checks split from
``tests/test_identity.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from identity_shared import REPO


class TestOneSlugSuggester:
    """The display-name → handle suggestion has exactly ONE implementation.

    The suggester is a FRONTEND concern: it pre-fills a field the user can still edit,
    while the server re-normalizes whatever is finally submitted (``slugify_username`` at
    the PUT boundary and again on load). So the rule lives in TypeScript, where the
    keystroke is, and this module deliberately does not carry a Python twin — the one it
    had was exported, never called, and would have read as the canonical rule to the next
    reader while the shipped suggestion came from somewhere else entirely.

    What CAN drift, silently, is the pair that remains: the TS mirror of the slug rule and
    the cap it copies. These two tests are the rail — the same shape as
    ``test_api_version_one_origin.py``'s constant-parity check.
    """

    TS_MODULE = REPO / "web" / "src" / "app" / "identity.tsx"

    def test_exactly_one_frontend_slug_implementation(self):
        """NEGATIVE CONTROL. A second NFKD folder under ``web/src`` means a second
        convention was minted — which is how this change's predecessor ended up with a
        Python function and a TypeScript function claiming the same job."""
        folders = sorted(
            p.relative_to(REPO).as_posix()
            for p in source_index.web_files()
            if p.suffix.startswith(".ts") and "NFKD" in source_index.text(p)
        )
        assert folders == [self.TS_MODULE.relative_to(REPO).as_posix()], (
            "the display-name→handle rule must have ONE home "
            f"(web/src/app/identity.tsx); found {folders}"
        )
