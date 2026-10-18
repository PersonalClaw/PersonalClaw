"""The source-tree checks split from
``tests/test_provider_config_secrets.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import source_index
from rails_repo import REPO

from personalclaw.apps.secret_fields import SECRET_MASK


def test_the_masking_policy_has_exactly_one_implementation():
    """The rail. Every config route must resolve the mask sentinel to the same module.

    The divergence existed because the Apps handler owned a private ``_SECRET_MASK`` +
    ``_mask_secret_config`` while the Providers handler owned nothing. Keyed on the sentinel
    VALUE rather than a symbol name, because a second copy is what forks the policy — and a
    copy is exactly as likely to be spelled inline as named. (``doctor.py`` has an unrelated
    ``_SECRET_MASK`` naming an unresolved-secret placeholder for workflow previews; it is a
    different concept with a different value, which is why the value is the test.)
    """
    src = REPO / "src" / "personalclaw"
    carriers = sorted(
        str(p.relative_to(src))
        for p in source_index.package_files()
        if SECRET_MASK in source_index.text(p)
    )
    assert carriers == ["apps/secret_fields.py"], (
        f"the sensitive-field mask sentinel appears in more than one module: {carriers}. "
        "One rule, one owner — a second copy is how /api/apps and /api/providers came to "
        "disagree about whether a token is write-only."
    )
