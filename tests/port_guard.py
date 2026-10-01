"""No test reaches a server it did not start: a connection to one is refused, and charged.

A test that reaches the host's own Ollama loads a model on a shared machine, and what it proves
depends on what that host has installed. One did: the browse vision-grounding test's live leg
asked the host's Ollama which models it had and, finding the recommended vision model pulled, ran
it (8.6 GB loaded) on every suite run. Another asked the host's Ollama to describe its models
whenever an earlier test on its worker had left the Ollama app's catalog registered. Nothing
stopped either, because a test that names ``localhost:11434`` and one that talks to it look the
same to a reader, and the second never named it at all.

So every ``connect`` in the test process is checked, against two rules:

* a TCP connection to a port a local model server listens on by default
  (:data:`LOCAL_MODEL_PORTS`), at any address, is refused unless this process is itself listening
  on that port (a test's own fake);
* a TCP connection to ANY port on this machine is refused unless this process holds that port: it
  bound it (a fake it serves, a free port it picked for a child it starts, or one it let go to stand
  for a server that is not there), or the test declared it with ``GUARD.own(port)`` (a port a child
  it started chose for itself and said which).

A refused connection fails before it is made, with ``ECONNREFUSED``, so the code under test sees
what it would see with no server there, and ``conftest`` fails the test that asked, by name.
Refused rather than recorded, because the harm is the call itself: a model loaded on the host, or a
request answered by the developer's own gateway, is not undone by a red test.

The mechanism is the SDK's (``personalclaw.sdk.testing.PortGuard``), which the apps suite installs
as well; what is this suite's own is here: the ports, the loopback rule, and the failure a test is
given. What it cannot see: a connection made by a child process (it has its own sockets), and the
rest ``PortGuard`` lists.
"""

from __future__ import annotations

from personalclaw.sdk.testing import refuse_ports

#: The default ports of the local model servers PersonalClaw's first-party providers speak to:
#: Ollama (11434), vLLM (8000) and ComfyUI (8188).
LOCAL_MODEL_PORTS = frozenset({11434, 8000, 8188})

#: The suite's guard, installed as this module is imported, which ``conftest`` does before
#: anything is collected: through ``refuse_ports``, the door the apps suite uses too.
GUARD = refuse_ports(LOCAL_MODEL_PORTS, what="a local model server's port", loopback=True)


def failure(refused: list[str]) -> str:
    """The failure a test that tried to reach a server it did not start is given."""
    return (
        "this test tried to reach a server it did not start (a real local model server, or "
        "another port on this machine), and was refused: "
        + "; ".join(refused)
        + ". Fake the endpoint: a server the test starts on a port of its own, a mocked "
        "transport, or GUARD.own(port) for a port a child the test started chose itself "
        "(tests/port_guard.py)."
    )
