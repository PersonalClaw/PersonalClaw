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


class AcpRequestError(AcpError):  # noqa: N818
    """The agent answered a request with a JSON-RPC error.

    Its ``message`` and ``data`` are the agent's own words for why — an adapter puts there the
    reason the program it drives failed — so both are kept (:func:`rpc_error_words`). Read only
    ``result``, a refused ``session/new`` used to say "returned no sessionId (result=None)".
    """

    def __init__(self, method: str, error: object):
        self.method = method
        self.error = error
        self.code = error.get("code") if isinstance(error, dict) else None
        super().__init__(f"{method} was refused: {rpc_error_words(error)}")


#: What a program's exit code conventionally says (sysexits and the shell), for the sentence
#: that names it. Codes without a convention are said as the number alone.
_EXIT_MEANINGS = {
    64: "a usage error: it was handed an argument or option it does not accept",
    126: "it could not be run",
    127: "a program it runs was not found",
}

#: The most of a program's last output an error carries — its end, where the reason is.
_LAST_WORDS_CAP = 600


def rpc_error_words(error: object) -> str:
    """A JSON-RPC error in its own words: ``message — data (code N)``, masked like any child's
    output, since an agent can repeat what a program it ran printed."""
    from personalclaw.security import mask_child_output

    if not isinstance(error, dict):
        return mask_child_output(str(error or "") or "no message", limit=_LAST_WORDS_CAP)
    message = str(error.get("message") or "").strip()
    data = error.get("data")
    if isinstance(data, dict):
        said = "; ".join(f"{k}: {v}" for k, v in data.items() if v not in (None, "", [], {}))
    elif isinstance(data, list):
        said = "; ".join(str(item) for item in data if item not in (None, ""))
    else:
        said = str(data or "").strip()
    if said and said != message:
        text = f"{message} — {said}" if message else said
    else:
        text = message
    code = error.get("code")
    text = f"{text or 'no message'}{f' (code {code})' if code is not None else ''}"
    return mask_child_output(text, limit=_LAST_WORDS_CAP)


def last_output_words(last_output: str) -> str:
    """``its last output: …`` for a program's (already masked) stderr tail, keeping its end; ""
    when it printed nothing."""
    tail = last_output.strip()
    if len(tail) > _LAST_WORDS_CAP:
        tail = "…" + tail[-_LAST_WORDS_CAP:]
    return f"its last output: {tail}" if tail else ""


def exit_words(program: str, code: int, last_output: str = "") -> str:
    """How an agent's program ended: ``<program> exited with code N (what N means); its last
    output: …``, or ``was ended by signal N``. *last_output* is already masked
    (``AcpProcess.stderr_tail``)."""
    meaning = _EXIT_MEANINGS.get(code)
    who = program or "the agent"
    if code < 0:  # the process did not exit: a signal ended it (asyncio's negative code)
        said = f"{who} was ended by signal {-code}"
    else:
        said = f"{who} exited with code {code}{f' ({meaning})' if meaning else ''}"
    tail = last_output_words(last_output)
    return f"{said}; {tail}" if tail else said


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
