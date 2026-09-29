"""The libraries PersonalClaw's features load stay inside the home, in every process it starts.

Some things a library does by itself, which no app can reach because it does them inside its own
calls:

* huggingface_hub, for a call that passes no token, looks one up — the environment, then the token
  file ``huggingface-cli login`` writes in the Hugging Face folder other tools share — and sends it.
  That file is outside the home, and PersonalClaw reads it only once the owner allows that folder.
  It keeps the chunk cache of every Xet transfer in that shared folder too, whatever folder the
  download itself was told to fill, and before its first request it fetches a list of AI tools
  from the Hub and keeps it there;
* onnxruntime, as it loads, starts its maker's telemetry: a device identifier and a queue of events
  about the machine, in a folder of the maker's under the user's home;
* tree-sitter-language-pack downloads the code map's grammars into a folder of its own in the
  user's cache.

Every ``personalclaw`` command sets each library's own setting before an app can import it, and a
child process gets the same values. Each library is driven for real, in a fresh interpreter (each
reads its settings once), with a scratch ``HOME``, a scratch cache folder, a scratch Hugging Face
folder and none of the suite's own variables: a library reads markers there too (onnxruntime starts
no telemetry on a CI runner), so the probe behaves as on the owner's machine wherever the suite
runs. The controls prove where each library writes with the settings off.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from personalclaw import cli
from personalclaw.codegraph import parse
from personalclaw.library_env import LIBRARY_ENV_NAMES, library_env
from personalclaw.sandbox import build_child_env

#: The libraries' own names for the settings, spelled out rather than imported, so a constant that
#: named anything else would fail here and not only in the library's silence.
TOKEN_SWITCH = "HF_HUB_DISABLE_IMPLICIT_TOKEN"
XET_CACHE = "HF_XET_CACHE"
HUB_TELEMETRY = "HF_HUB_DISABLE_TELEMETRY"
ORT_TELEMETRY = "ORT_DISABLE_TELEMETRY"
GRAMMAR_CACHE = "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"
#: Neutral fakes: the library sends whatever string it holds, so nothing here needs a token's shape.
_PLANTED = "fake-hub-token-planted-by-this-test"
_HANDED = "fake-hub-token-handed-over-by-the-caller"
#: All a probe inherits from the process that runs the suite: what starting a program needs. The
#: libraries read far more than their own settings for these behaviours, from variables no list
#: here could name: huggingface_hub honours the generic telemetry switches too, and onnxruntime
#: starts no telemetry at all where a CI variable is set (`CI`, `GITHUB_ACTIONS` and eleven more
#: that build services set). A probe that inherited the developer's variables, or a CI runner's,
#: would show the library on that machine, not on the owner's; each run holds the settings under
#: test and nothing else.
_INHERITED = ("PATH",)
#: The two variables every step of a GitHub Actions job carries, where this suite runs in CI.
_CI_RUNNER = {"CI": "true", "GITHUB_ACTIONS": "true"}
#: A proxy nothing listens on, so a library in a probe has nowhere to send what it would upload.
_NOWHERE = "http://127.0.0.1:9"

_HUB_PROBE = f"""
import json
from huggingface_hub import constants
from huggingface_hub.utils import build_hf_headers
print(json.dumps({{
    "none_passed": build_hf_headers().get("authorization"),
    "handed": build_hf_headers(token={_HANDED!r}).get("authorization"),
    "xet_cache": constants.HF_XET_CACHE,
}}))
"""


def _installed(module: str) -> bool:
    """Whether *module* is here — found, never imported: importing onnxruntime in this process is
    what would write its device identifier into the real home."""
    return importlib.util.find_spec(module) is not None


#: macOS's sandbox profile for a probe: nothing leaves the machine, and this machine's own
#: addresses (a fake endpoint a test serves) still answer.
_NO_REMOTE_NETWORK = (
    "(version 1)(allow default)(deny network-outbound (remote ip))"
    '(allow network-outbound (remote ip "localhost:*"))'
)


def _offline(argv: list[str]) -> list[str]:
    """*argv*, with no network beyond this machine where the system can take it away (macOS's
    sandbox)."""
    sandbox = "/usr/bin/sandbox-exec"
    if not os.path.exists(sandbox):
        return argv
    return [sandbox, "-p", _NO_REMOTE_NETWORK, *argv]


def _fresh(tmp_path: Path, code: str, settings: dict[str, str]) -> str:
    """The last line *code* prints in a fresh interpreter with *settings* and nothing else of ours
    (:data:`_INHERITED`): its home, cache folder and Hugging Face folder are scratch (a planted
    token file in the last), and any upload it attempts has no route."""
    home = tmp_path / "home"
    hf_home = tmp_path / "hf"
    for folder in (home, hf_home):
        folder.mkdir(exist_ok=True)
    (hf_home / "token").write_text(_PLANTED, encoding="utf-8")
    env = {name: os.environ[name] for name in _INHERITED if name in os.environ}
    env.update(
        {
            "HOME": str(home),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "HF_HOME": str(hf_home),
            "https_proxy": _NOWHERE,
            "HTTPS_PROXY": _NOWHERE,
            "all_proxy": _NOWHERE,
            "ALL_PROXY": _NOWHERE,
            # A fake endpoint a test serves on this machine is reached directly.
            "no_proxy": "127.0.0.1,localhost",
            "NO_PROXY": "127.0.0.1,localhost",
            **settings,
        }
    )
    done = subprocess.run(
        _offline([sys.executable, "-c", code]),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return done.stdout.strip().splitlines()[-1]


def _hub(tmp_path: Path, settings: dict[str, str]) -> dict:
    """What a fresh process's huggingface_hub does with *settings* set, and nothing else of ours."""
    return json.loads(_fresh(tmp_path, _HUB_PROBE, settings))


def _files_under(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


def _run_a_command(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_doctor_paths", lambda: None)
    monkeypatch.setattr(sys, "argv", ["personalclaw", "doctor", "--paths"])
    cli.main()


def _inside(path: str, home) -> bool:
    return os.path.commonpath([path, str(home)]) == str(home)


def test_an_apps_test_suite_applies_what_every_command_sets():
    """The apps repository's test harness loads these libraries too, and sets what it gets from
    the SDK before anything is collected: it must be this, not a copy that could drift."""
    from personalclaw.sdk.testing import library_env as published

    assert published is library_env


def test_the_names_are_the_settings():
    assert tuple(library_env()) == LIBRARY_ENV_NAMES
    assert set(LIBRARY_ENV_NAMES) == {
        TOKEN_SWITCH,
        XET_CACHE,
        HUB_TELEMETRY,
        ORT_TELEMETRY,
        GRAMMAR_CACHE,
    }


def test_every_command_sets_them_over_what_it_inherited(monkeypatch):
    """Even over a shell that set them otherwise: the owner's allow switch decides whether a file
    outside the home is read, and nothing writes there or reports on its use, whatever an
    inherited variable says."""
    from personalclaw.config.loader import config_dir

    monkeypatch.setenv(TOKEN_SWITCH, "0")
    monkeypatch.setenv(XET_CACHE, "/elsewhere/huggingface/xet")
    monkeypatch.setenv(HUB_TELEMETRY, "0")
    monkeypatch.setenv(ORT_TELEMETRY, "0")
    monkeypatch.setenv(GRAMMAR_CACHE, "/elsewhere/grammars")

    _run_a_command(monkeypatch)

    assert os.environ[TOKEN_SWITCH] == "1"
    assert os.environ[HUB_TELEMETRY] == "1"
    assert os.environ[ORT_TELEMETRY] == "1"
    assert _inside(os.environ[XET_CACHE], config_dir()), "the transfer cache is outside the home"
    assert _inside(os.environ[GRAMMAR_CACHE], config_dir()), "the grammars are outside the home"


def test_the_caches_go_to_the_home_a_dotenv_names(tmp_path, monkeypatch):
    """A ``.env`` in the working directory can name the home (``PERSONALCLAW_HOME``), and it is
    read after the command starts: the caches follow the home the command runs on, not the one it
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
    assert _inside(os.environ[GRAMMAR_CACHE], named.resolve()), os.environ[GRAMMAR_CACHE]


def test_the_hub_finds_no_token_and_caches_in_the_home(tmp_path, monkeypatch):
    left_alone = _hub(tmp_path, {})
    assert (
        left_alone["none_passed"] == f"Bearer {_PLANTED}"
    ), "control: with its lookup on, the library reads the planted token file by itself"
    assert left_alone["xet_cache"] == str(
        tmp_path / "hf" / "xet"
    ), "control: left alone, the library caches Xet transfers in the shared folder"

    home = tmp_path / "pclaw-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    told = _hub(tmp_path, library_env())

    assert told["none_passed"] is None, "the library found a token nobody handed it"
    assert told["handed"] == f"Bearer {_HANDED}", "a token an app hands over still goes"
    assert told["xet_cache"].startswith(str(home.resolve())), told["xet_cache"]


class _FakeHub(http.server.ThreadingHTTPServer):
    """A Hugging Face endpoint on this machine that answers the AI-tool list and records each
    request it gets."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        super().__init__(("127.0.0.1", 0), _FakeHubHandler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _FakeHubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        body = json.dumps({"standardEnvVars": ["AI_AGENT"], "harnesses": {}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_hub():
    server = _FakeHub()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_the_hub_keeps_no_list_of_ai_tools_in_the_shared_folder(tmp_path, monkeypatch, fake_hub):
    """Before its first request the library asks the Hub for a list of AI tools, to name the one
    it runs under in its User-Agent, and keeps the list in the shared Hugging Face folder. Told
    as every command tells it, it asks nothing and keeps nothing there."""
    listed = tmp_path / "hf" / ".agent_harnesses.json"
    endpoint = {"HF_ENDPOINT": fake_hub.url}

    _hub(tmp_path, endpoint)
    assert any(
        p.startswith("/api/agent-harnesses") for p in fake_hub.paths
    ), f"control: left alone, the library fetches the list ({fake_hub.paths})"
    assert listed.is_file(), "control: left alone, the library keeps the list in the shared folder"

    listed.unlink()
    fake_hub.paths.clear()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))
    _hub(tmp_path, {**library_env(), **endpoint})

    assert fake_hub.paths == [], "the library asked the Hub for something nobody requested"
    assert not listed.exists(), "the library wrote into the shared Hugging Face folder"


@pytest.mark.skipif(not _installed("onnxruntime"), reason="needs onnxruntime (the [dev] extra)")
@pytest.mark.parametrize("suite_env", ["as it is", "a CI runner's"])
def test_onnxruntime_leaves_no_device_identifier_behind(tmp_path, monkeypatch, suite_env):
    """Loading onnxruntime starts its maker's telemetry: a device identifier and a queue of events
    about the machine, in a folder under the user's home. Told as every command tells it, it
    writes nothing there. The control runs with the telemetry on for the moment an import takes,
    with no route to send anything (see :func:`_fresh`).

    The library starts no telemetry where a CI variable is set, and every CI step sets one, so a
    probe that inherited the suite's variables had a control that could not fire on the runners
    the suite runs on. Run under a CI runner's variables here too, the control still fires."""
    if suite_env == "a CI runner's":
        for name, value in _CI_RUNNER.items():
            monkeypatch.setenv(name, value)
    load = "import onnxruntime\nprint('loaded')"
    home = tmp_path / "home"

    assert _fresh(tmp_path, load, {}) == "loaded"
    written = _files_under(home)
    assert any(
        Path(f).name == "deviceid" for f in written
    ), f"control: left alone, onnxruntime keeps a device identifier in the home folder: {written}"

    for f in written:
        (home / f).unlink()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))

    assert _fresh(tmp_path, load, library_env()) == "loaded"
    assert _files_under(home) == [], "onnxruntime wrote into the user's home"


def test_the_code_maps_grammars_download_into_the_home(tmp_path, monkeypatch):
    """The language pack keeps each grammar it downloads in a folder of its own in the user's
    cache. Told as every command tells it, it keeps them in the home."""
    where = "import tree_sitter_language_pack as pack\nprint(pack.cache_dir())"

    left_alone = str(Path(_fresh(tmp_path, where, {})).resolve())
    assert _inside(
        left_alone, (tmp_path / "home").resolve()
    ), f"control: left alone, the grammars go to the user's cache: {left_alone}"

    home = tmp_path / "pclaw-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    told = str(Path(_fresh(tmp_path, where, library_env())).resolve())

    assert _inside(told, home.resolve()), told


def test_the_code_maps_remedy_fills_the_folder_it_reads(tmp_path, monkeypatch):
    """A grammar that cannot load names the pre-fetch that fixes it. The language pack keeps its
    grammars where it is told, so the pre-fetch must be told the same folder, or it fills one
    PersonalClaw never reads."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))

    remedy = parse._parser_remedy()

    assert f'{GRAMMAR_CACHE}="{library_env()[GRAMMAR_CACHE]}"' in remedy, remedy


def test_a_child_process_is_told_the_same(tmp_path, monkeypatch):
    """A model engine or a voice PersonalClaw starts loads the same libraries: it gets every
    setting, from the allowlist."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "pclaw-home"))
    values = library_env()
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    env = build_child_env(site="model-sidecar")

    assert {name: env.get(name) for name in values} == values
