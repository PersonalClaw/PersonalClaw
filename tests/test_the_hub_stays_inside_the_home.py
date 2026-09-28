"""``huggingface_hub``, in every process PersonalClaw starts, stays inside the home.

Two things the library does by itself, which no app can reach because libraries built on the hub
make calls of their own:

* for a call that passes no token it looks one up — the environment, then the token file
  ``huggingface-cli login`` writes in the Hugging Face folder other tools share — and sends it.
  That file is outside the home, and PersonalClaw reads it only once the owner allows that folder;
* it keeps the chunk cache of every Xet transfer in that shared folder too, whatever folder the
  download itself was told to fill.

Every ``personalclaw`` command sets both before an app can import the library, and a child process
gets the same values. The library is driven for real, in a fresh interpreter (it reads both once,
at import), against a scratch Hugging Face folder holding a PLANTED token file. The controls prove
that folder is where the library reads and writes with the settings off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from personalclaw import cli
from personalclaw.local_models import hub_env
from personalclaw.sandbox import build_child_env

#: The library's own names for the two settings, spelled out rather than imported, so a constant
#: that named anything else would fail here and not only in the library's silence.
TOKEN_SWITCH = "HF_HUB_DISABLE_IMPLICIT_TOKEN"
XET_CACHE = "HF_XET_CACHE"
#: Neutral fakes: the library sends whatever string it holds, so nothing here needs a token's shape.
_PLANTED = "fake-hub-token-planted-by-this-test"
_HANDED = "fake-hub-token-handed-over-by-the-caller"

_PROBE = f"""
import json
from huggingface_hub import constants
from huggingface_hub.utils import build_hf_headers
print(json.dumps({{
    "none_passed": build_hf_headers().get("authorization"),
    "handed": build_hf_headers(token={_HANDED!r}).get("authorization"),
    "xet_cache": constants.HF_XET_CACHE,
}}))
"""


def _hub(tmp_path, settings: dict[str, str]) -> dict:
    """What a fresh process's huggingface_hub does with *settings* set, and nothing else of ours."""
    hf_home = tmp_path / "hf"
    hf_home.mkdir(exist_ok=True)
    (hf_home / "token").write_text(_PLANTED, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "HUGGING_FACE_"))}
    # Nothing here can reach the real Hugging Face folder: its home and its folder are scratch.
    env.update({"HF_HOME": str(hf_home), "HOME": str(tmp_path), **settings})
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def _run_a_command(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_doctor_paths", lambda: None)
    monkeypatch.setattr(sys, "argv", ["personalclaw", "doctor", "--paths"])
    cli.main()


def _inside(path: str, home) -> bool:
    return os.path.commonpath([path, str(home)]) == str(home)


def test_every_command_sets_both_over_what_it_inherited(monkeypatch):
    """Even over a shell that set them otherwise: the owner's allow switch decides whether a file
    outside the home is read, and nothing writes there, whatever an inherited variable says."""
    from personalclaw.config.loader import config_dir

    monkeypatch.setenv(TOKEN_SWITCH, "0")
    monkeypatch.setenv(XET_CACHE, "/elsewhere/huggingface/xet")

    _run_a_command(monkeypatch)

    assert os.environ[TOKEN_SWITCH] == "1"
    assert _inside(os.environ[XET_CACHE], config_dir()), "the transfer cache is outside the home"


def test_the_cache_goes_to_the_home_a_dotenv_names(tmp_path, monkeypatch):
    """A ``.env`` in the working directory can name the home (``PERSONALCLAW_HOME``), and it is
    read after the command starts: the cache follows the home the command runs on, not the one it
    would have used without that file."""
    named = tmp_path / "named-home"
    (tmp_path / ".env").write_text(f"PERSONALCLAW_HOME={named}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Unset, the way it is recorded: the `.env` sets it, and teardown takes it away again.
    monkeypatch.setenv("PERSONALCLAW_HOME", "")
    monkeypatch.delenv("PERSONALCLAW_HOME")

    _run_a_command(monkeypatch)

    assert os.environ["PERSONALCLAW_HOME"] == str(named), "control: the .env named the home"
    assert _inside(os.environ[XET_CACHE], named.resolve()), os.environ[XET_CACHE]


def test_the_library_finds_no_token_and_caches_in_the_home(tmp_path, monkeypatch):
    left_alone = _hub(tmp_path, {})
    assert (
        left_alone["none_passed"] == f"Bearer {_PLANTED}"
    ), "control: with its lookup on, the library reads the planted token file by itself"
    assert left_alone["xet_cache"] == str(
        tmp_path / "hf" / "xet"
    ), "control: left alone, the library caches Xet transfers in the shared folder"

    home = tmp_path / "pclaw-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    told = _hub(tmp_path, hub_env.hub_env())

    assert told["none_passed"] is None, "the library found a token nobody handed it"
    assert told["handed"] == f"Bearer {_HANDED}", "a token an app hands over still goes"
    assert told["xet_cache"].startswith(str(home.resolve())), told["xet_cache"]


def test_a_child_process_is_told_the_same(tmp_path, monkeypatch):
    """A model engine PersonalClaw starts runs the hub too: it gets both, from the allowlist."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))
    values = hub_env.hub_env()
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    env = build_child_env(site="model-sidecar")

    assert {name: env.get(name) for name in values} == values
