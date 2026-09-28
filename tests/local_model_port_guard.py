"""No test reaches a real local model server: a connection to one's port is refused, and charged.

A test that reaches the host's own Ollama loads a model on a shared machine, and what it proves
depends on what that host has installed. One did: the browse vision-grounding test's live leg
asked the host's Ollama which models it had and, finding the recommended vision model pulled, ran
it (8.6 GB loaded) on every suite run. Nothing stopped it, because a test that names
``localhost:11434`` and one that talks to it look the same to a reader.

So every ``connect`` in the test process is checked: a TCP connection to a port a local model
server listens on by default (:data:`LOCAL_MODEL_PORTS`) is refused with ``ECONNREFUSED`` before it
is made, unless this process is itself listening on that port (a test's own fake). The code under
test sees what it would see with no server there, and ``conftest`` fails the test that asked, by
name. Refused rather than recorded, because the harm is the call itself: a model loaded on the
host is not undone by a red test.

The mechanism is the SDK's (``personalclaw.sdk.testing.PortGuard``), which the apps suite
installs as well; what is this suite's own is here: the ports, and the failure a test is given.
What it cannot see: a connection made by a child process (it has its own sockets), and a server
on a port that is not in the list.
"""

from __future__ import annotations

from personalclaw.sdk.testing import refuse_ports

#: The default ports of the local model servers PersonalClaw's first-party providers speak to:
#: Ollama (11434), vLLM (8000) and ComfyUI (8188).
LOCAL_MODEL_PORTS = frozenset({11434, 8000, 8188})

#: The suite's guard, installed as this module is imported, which ``conftest`` does before
#: anything is collected: through ``refuse_ports``, the door the apps suite uses too.
GUARD = refuse_ports(LOCAL_MODEL_PORTS, what="a local model server's port")


def failure(refused: list[str]) -> str:
    """The failure a test that tried to reach a local model server is given."""
    return (
        "this test tried to reach a real local model server, and was refused: "
        + "; ".join(refused)
        + ". Fake the endpoint: a server the test starts on a port of its own, or a mocked "
        "transport (tests/local_model_port_guard.py)."
    )
