"""``huggingface_hub``, in a PersonalClaw process, sends only a token a caller handed it.

Left to itself the library looks a token up for any call that passes none: the environment, then
the token file ``huggingface-cli login`` writes in the Hugging Face folder other tools share. That
file is outside the home, and PersonalClaw reads it only once the owner allows that folder. Every
app passes PersonalClaw's own answer or ``False``, but libraries built on the hub make calls of
their own that pass none, and no app can reach those. So every ``personalclaw`` command turns the
library's lookup off before an app can import it.

The library is driven for real, in a fresh interpreter (it reads the switch once, at import),
against a PLANTED token file in a scratch Hugging Face folder and no token in the environment. The
control proves that planted file is what the library finds with its lookup on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from personalclaw import cli
from personalclaw.local_models import hf_token

#: The library's own name for the switch, spelled out rather than imported, so a constant that
#: named anything else would fail here and not only in the library's silence.
SWITCH = "HF_HUB_DISABLE_IMPLICIT_TOKEN"
_PLANTED = "hf_planted_for_this_test_only"
_HANDED = "hf_handed_over_by_the_caller"

_PROBE = f"""
import json
from huggingface_hub.utils import build_hf_headers
print(json.dumps({{
    "none_passed": build_hf_headers().get("authorization"),
    "handed": build_hf_headers(token={_HANDED!r}).get("authorization"),
}}))
"""


def _hub_headers(tmp_path, *, switch: str | None) -> dict:
    """The Authorization header a fresh process's huggingface_hub sends, with *switch* set."""
    hf_home = tmp_path / "hf"
    hf_home.mkdir(exist_ok=True)
    (hf_home / "token").write_text(_PLANTED, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "HUGGING_FACE_"))}
    # Nothing here can reach the real Hugging Face folder: its home and its folder are scratch.
    env.update({"HF_HOME": str(hf_home), "HOME": str(tmp_path)})
    if switch is not None:
        env[SWITCH] = switch
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_every_command_turns_the_libraries_own_lookup_off(monkeypatch):
    """Even over a shell that turned it back on: the owner's allow switch is the one that
    decides whether that file is read, not an inherited environment variable."""
    monkeypatch.setenv(SWITCH, "0")
    monkeypatch.setattr(cli, "_doctor_paths", lambda: None)
    monkeypatch.setattr(sys, "argv", ["personalclaw", "doctor", "--paths"])

    cli.main()

    assert os.environ[SWITCH] == "1"


def test_with_it_off_the_hub_finds_no_token_and_still_sends_one_it_is_handed(tmp_path, monkeypatch):
    looked_up = _hub_headers(tmp_path, switch=None)
    assert (
        looked_up["none_passed"] == f"Bearer {_PLANTED}"
    ), "control: with its lookup on, the library reads the planted token file by itself"

    monkeypatch.setenv(SWITCH, "0")
    hf_token.keep_the_hub_from_finding_tokens()
    switched = _hub_headers(tmp_path, switch=os.environ[SWITCH])

    assert switched["none_passed"] is None, "the library found a token nobody handed it"
    assert switched["handed"] == f"Bearer {_HANDED}", "a token an app hands over still goes"
