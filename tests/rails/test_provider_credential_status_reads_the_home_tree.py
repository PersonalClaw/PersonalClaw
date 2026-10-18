"""The source-tree checks split from
``tests/test_provider_credential_status_reads_the_home.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

import source_index
from rails_repo import REPO

_SRC = REPO / "src" / "personalclaw"


def test_no_call_site_passes_the_credentials_file_as_the_home():
    """The rail, because the fix is one argument and the mistake is invisible at the call site.

    `CredentialStore(home)` and `CredentialStore(home / "credentials.json")` are both valid
    Python that construct without raising — the store simply resolves a path that cannot exist
    and every `resolve` raises `KeyError`. Nothing else in the tree fails, which is how this
    survived. Asserted over the source so a future call site cannot re-make it.
    """
    offenders = []
    pattern = re.compile(r"CredentialStore\([^#]*?credentials\.json")
    for path in source_index.package_files():
        for num, line in enumerate(source_index.text(path).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_SRC)}:{num}: {line.strip()}")
    assert not offenders, (
        "CredentialStore takes the HOME and reads `<home>/.env` itself; these pass a file, "
        "so the store reads beneath it and finds nothing:\n" + "\n".join(offenders)
    )
