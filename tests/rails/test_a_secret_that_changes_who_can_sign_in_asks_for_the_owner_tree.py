"""The source-tree checks split from
``tests/test_a_secret_that_changes_who_can_sign_in_asks_for_the_owner.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import a_secret_that_changes_who_can_sign_in_asks_for_the_owner_shared
import source_index

import personalclaw
from personalclaw.config.credentials import owner_id_credential
from personalclaw.inbound.auth import client_surfaces, token_env_key
from personalclaw.secrets_vault import SIGN_IN_ENVIRONMENT, is_sign_in_key

_clean_state = a_secret_that_changes_who_can_sign_in_asks_for_the_owner_shared._clean_state


def test_the_names_are_the_ones_the_gateway_reads():
    """Each sign-in name comes from the module that reads it; the environment's are literals, so
    each must still be read somewhere in the gateway, or it is a name that changes nothing."""
    for surface in client_surfaces():
        assert is_sign_in_key(token_env_key(surface))
    assert is_sign_in_key(owner_id_credential("discord"))
    assert not is_sign_in_key("GITHUB_TOKEN")
    assert not is_sign_in_key("PERSONALCLAW_OWNER")
    root = Path(inspect.getfile(personalclaw)).parent
    sources = {
        path: source_index.text(path)
        for path in source_index.under(source_index.package_files(), root.resolve())
        if path.name != "secrets_vault.py"
    }
    for name in SIGN_IN_ENVIRONMENT:
        assert is_sign_in_key(name)
        readers = [path for path, text in sources.items() if f'"{name}"' in text]
        assert readers, f"{name} is listed as a sign-in setting, but the gateway never reads it"
