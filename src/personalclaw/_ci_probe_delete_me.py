"""Temporary CI probe. Verifies the lint-feedback relay after the actions bump (#1530).

This file exists only to make CI's `mypy` step fail so that ci.yml uploads a
lint-feedback artifact and pr-feedback.yml relays it into the PR conversation.
The PR carrying it is closed immediately and the branch deleted.
"""


def _probe_return_type() -> int:
    return "deliberately the wrong type"
