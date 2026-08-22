"""ACP protocol errors — the leaf module both the client and the transport/session
layers raise, so neither has to import the other just to name an exception.

Kept dependency-free (no ``acp`` imports) so ``client.py``, ``transport.py`` and
``session.py`` can all import from here without a cycle."""

from __future__ import annotations

from personalclaw.constants import JSONRPC_METHOD_NOT_FOUND


class AcpError(Exception):
    """Base ACP error."""


class AcpMethodNotFound(AcpError):  # noqa: N818
    """The agent answered JSON-RPC ``-32601`` — it does not implement this method.

    Split out of the generic :class:`AcpError` for exactly one reason: it is the only
    error whose meaning is "this agent CANNOT do that", so it is the only one a caller
    may answer by substituting a different path. Every other JSON-RPC error means an
    attempt failed and must still surface — a caller that degraded on all of them would
    swallow real failures behind a silent substitution.
    """

    def __init__(self, method: str, error: object = None):
        self.method = method
        self.code = JSONRPC_METHOD_NOT_FOUND
        self.error = error
        super().__init__(f"Method not found: {method}")


class AcpCommandFailedAfterOutput(AcpError):  # noqa: N818
    """A slash command was rejected as unknown AFTER the turn had already streamed.

    The deliberate refusal case. Re-issuing the input as a plain prompt is only safe while
    the turn has produced nothing: once frames have gone out, a second turn would append a
    duplicate answer to the same assistant message, re-run whatever tools already ran, and
    bill the work twice. So this turn stops with an explanation instead — the message is
    written for the user, because it is what the chat error bubble renders.
    """

    def __init__(self, command: str):
        self.command = command
        super().__init__(
            f"The agent rejected `{command}` as an unknown command after it had already "
            "produced output, so it was NOT re-sent as a plain message — doing that would "
            "duplicate the reply above and bill the turn twice. Send it again as a plain "
            "question if you want an answer."
        )


class AcpCommandsUnsupported(AcpError):  # noqa: N818
    """The agent never advertised the slash-command extension, so nothing was sent.

    Distinct from :class:`AcpMethodNotFound`: that one is a *reply*, this one is a
    refusal to ask. Raised by the capability gate before any wire write, so the turn is
    still untouched — the caller can re-issue the input as a plain prompt with no risk of
    duplicating output the agent already streamed.
    """

    def __init__(self, command: str = ""):
        self.command = command
        super().__init__(
            f"This agent does not support slash commands{f' ({command})' if command else ''}"
        )


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
