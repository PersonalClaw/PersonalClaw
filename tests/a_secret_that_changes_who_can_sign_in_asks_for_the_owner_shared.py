"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_a_secret_that_changes_who_can_sign_in_asks_for_the_owner.py``

Its checks:
``tests/rails/test_a_secret_that_changes_who_can_sign_in_asks_for_the_owner_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import pytest

from personalclaw.auth.credentials import TOTP_SECRET_KEY
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.dashboard import token_auth
from personalclaw.inbound.auth import token_env_key

BYPASS = "PERSONALCLAW_BYPASS_LOCAL_NETWORKS"


#: One name of each kind, each from the module that reads it.
SIGN_IN_NAMES = {
    "the local-network bypass": BYPASS,
    "the authenticator's seed": TOTP_SECRET_KEY,
    "an inbound surface's token": token_env_key("mcp"),
    "a channel owner's id": owner_id_credential("telegram"),
    "the id every channel shared": CRED_OWNER_ID,
    "whether sign-in is asked for": "PERSONALCLAW_AUTH_MODE",
    "the first password a container enrolls": "PERSONALCLAW_LOGIN_PASSWORD",
}


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    token_auth.use_persistent_secret()
    token_auth.revoke_all_sessions()
    # A stored secret is mirrored into this process's environment: give every name back as it was.
    for name in {*SIGN_IN_NAMES.values(), "GITHUB_TOKEN"}:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    yield
    token_auth.revoke_all_sessions()
