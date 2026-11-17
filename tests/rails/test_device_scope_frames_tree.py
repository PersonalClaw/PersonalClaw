"""The frame census of an automation client's socket (scoped-pairing design §4).

``device_scopes.FRAME_SCOPES`` is every frame type a scoped socket may hear; any other is unheard
by construction. This reads every frame the gateway sends (a literal first argument to
``broadcast_ws``, the frame census's own pattern) and holds each one about a loop, an approval,
the waiting list or the gateway's health to an explicit verdict: a row in ``FRAME_SCOPES``, or a
place in ``FRAMES_UNHEARD``. So a loop frame added tomorrow is classified on purpose, and no row
names a frame nothing sends.
"""

from __future__ import annotations

import re

import source_index

from personalclaw.dashboard import device_scopes

_SENT = re.compile(r"""broadcast_ws(?:_subagent_subscribers)?\(\s*["']([a-z_.:-]+)["']""")

#: The frame names that are about a family a scope touches.
_FAMILY = re.compile(r"loop|approval|waiting|work_phase|health|spa_build")


def _sent() -> set[str]:
    sent: set[str] = set()
    for path in source_index.package_files():
        if "static" in path.parts:
            continue
        sent.update(_SENT.findall(source_index.text(path)))
    return sent


def test_every_frame_of_a_scoped_family_has_a_verdict() -> None:
    sent = _sent()
    assert {"work_phase", "approval"} <= sent, "the census reads the frames the gateway sends"
    classified = set(device_scopes.FRAME_SCOPES) | set(device_scopes.FRAMES_UNHEARD)
    missing = sorted(f for f in sent if _FAMILY.search(f) and f not in classified)
    assert not missing, f"frames of a scoped family with no verdict: {missing}"


def test_no_verdict_names_a_frame_nothing_sends() -> None:
    sent = _sent() | {"spa_build"}  # sent through send_ws_event on connect, not broadcast
    ghosts = sorted((set(device_scopes.FRAME_SCOPES) | set(device_scopes.FRAMES_UNHEARD)) - sent)
    assert not ghosts, ghosts


def test_this_release_hears_no_approval() -> None:
    assert all(
        row.scope in ("loops", device_scopes.SHELL) for row in device_scopes.FRAME_SCOPES.values()
    )
