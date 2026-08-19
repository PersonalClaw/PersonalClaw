"""ACP protocol errors — the leaf module both the client and the transport/session
layers raise, so neither has to import the other just to name an exception.

Kept dependency-free (no ``acp`` imports) so ``client.py``, ``transport.py`` and
``session.py`` can all import from here without a cycle."""

from __future__ import annotations


class AcpError(Exception):
    """Base ACP error."""


class AcpTimeoutError(AcpError):
    """Prompt timed out."""

    def __init__(self, partial_output: str = ""):
        self.partial_output = partial_output
        super().__init__("ACP prompt timed out")


class AcpPermissionNeeded(AcpError):  # noqa: N818
    """Tool approval required."""

    def __init__(self, prompt: str, response_so_far: str = ""):
        self.prompt = prompt
        self.response_so_far = response_so_far
        super().__init__("Permission needed")


class AcpProcessDied(AcpError):  # noqa: N818
    """ACP agent subprocess exited unexpectedly."""


class AcpWorkspaceUnresolved(AcpError):  # noqa: N818
    """No containable working directory resolved, so no ACP CLI may be spawned.

    Distinct from every other error here in the one way a caller cares about: retrying on
    a different spawn path cannot help, because every path resolves the SAME workspace and
    would make the same wrong choice. So a caller that degrades other ACP failures to a
    fallback spawn path must let THIS one through — degrading only moves the escape.
    """
