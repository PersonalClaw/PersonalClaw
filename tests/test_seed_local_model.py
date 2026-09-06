"""Tests for ``personalclaw.seed_local_model`` — the bind step beside ``--seed``.

Both directions are pinned, because the graceful-degradation half is the one that
regresses silently:

1. **A model IS reachable** — the provider app, the ``config.json`` ``providers[]``
   entry and the ``active_models.json`` chat binding all land, with no credential
   anywhere, and the endpoint/model come from configuration rather than a literal.
2. **No model is reachable** — the home is left byte-identical to what the fixture
   wrote, the exit code stays 0, and the reason is printed. A demo seed that errors,
   hangs or half-populates on a machine without Ollama is worse than one that
   populates nothing, so "wrote nothing" is asserted as a whole-tree invariant, not
   just as an absent file.

The reachable direction is driven against a real loopback HTTP stub speaking Ollama's
``/api/tags``, not a monkeypatched probe: the thing most likely to break is the wire
shape (which field carries the model id, how capabilities are inferred from it), and a
patched probe cannot see that.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from personalclaw import seed as seed_mod
from personalclaw import seed_local_model as slm

FIXTURE_NAME = "demo-home"

# Two tags whose ids alone must classify correctly: the chat model is the OLDER of the
# two chat entries so recency ordering is actually exercised, and the embedding model
# carries a family name with no "embed" substring in the id itself.
_TAGS = {
    "models": [
        {
            "name": "llama3.2:3b",
            "model": "llama3.2:3b",
            "modified_at": "2026-01-02T00:00:00Z",
            "details": {"families": ["llama"]},
        },
        {
            "name": "demo-chat:8b",
            "model": "demo-chat:8b",
            "modified_at": "2026-07-15T00:00:00Z",
            "details": {"families": ["llama"]},
        },
        {
            "name": "nomic-text:v1",
            "model": "nomic-text:v1",
            "modified_at": "2026-06-23T00:00:00Z",
            "details": {"families": ["nomic-bert"]},
        },
    ]
}


class _TagsHandler(BaseHTTPRequestHandler):
    """Minimal Ollama stand-in: answers ``/api/tags`` and 404s everything else."""

    payload: dict = _TAGS

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        if self.path != "/api/tags":
            self.send_error(404)
            return
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr access log — it is noise under -q."""


@pytest.fixture
def ollama_stub() -> str:
    """Serve ``/api/tags`` on an ephemeral loopback port; yield the base URL."""
    server = HTTPServer(("127.0.0.1", 0), _TagsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def closed_port() -> str:
    """A loopback URL nothing is listening on — the no-Ollama machine."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def seeded_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real ``demo-home``-seeded ``$PERSONALCLAW_HOME``, env pointed at it.

    Env vars that would otherwise leak an operator's own machine into the assertions
    are cleared — these tests must not pass or fail based on whether the dev running
    them happens to have Ollama up.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    for var in (
        slm.ENDPOINT_ENV,
        slm.MODEL_ENV,
        slm.EMBEDDING_MODEL_ENV,
        slm.APPS_DIR_ENV,
    ):
        monkeypatch.delenv(var, raising=False)
    seed_mod.seed(FIXTURE_NAME)
    return home


def _install_stub_provider_app(home: Path) -> Path:
    """Pre-install a marker ``ollama-models`` app in the home.

    This is the SHIPPED path, not a shortcut: on a plain ``pip install`` the provider
    app arrives via the App Store, so "already installed" is the case the bind step
    hits most often. Only ``installed.json`` + ``app.json`` are read when deciding, so
    a marker is a faithful stand-in and the test needs no apps-repo checkout.
    """
    app = home / "apps" / slm.PROVIDER_APP
    app.mkdir(parents=True)
    (app / "app.json").write_text(
        json.dumps({"name": slm.PROVIDER_APP, "version": "0.1.0", "displayName": "Ollama"}),
        encoding="utf-8",
    )
    (app / "installed.json").write_text(
        json.dumps(
            {
                "name": slm.PROVIDER_APP,
                "version": "0.1.0",
                "displayName": "Ollama",
                "enabled": True,
                "origin": "local",
                "schemaVersion": 2,
            }
        ),
        encoding="utf-8",
    )
    return app


def _tree(home: Path) -> dict[str, int]:
    """Home-relative path -> size, for every file. The untouched-tree assertion."""
    return {
        str(p.relative_to(home)): p.stat().st_size for p in sorted(home.rglob("*")) if p.is_file()
    }


# ── the fixture itself stays machine-independent ────────────────────────────


def test_the_fixture_itself_binds_no_model(seeded_home: Path) -> None:
    """``demo-home`` must not carry an endpoint, a provider entry or a binding.

    The whole reason the binding is a separate step is that the fixture is a bare
    ``copytree`` and therefore identical on every machine. If a provider entry or an
    ``active_models.json`` ever gets committed into the fixture tree, every seeded home
    would point at whoever authored it.
    """
    cfg = json.loads((seeded_home / "config.json").read_text(encoding="utf-8"))
    assert "providers" not in cfg
    assert not (seeded_home / "active_models.json").exists()
    assert not (seeded_home / "apps").exists()


# ── direction 1: a model IS reachable ───────────────────────────────────────


def test_binds_the_provider_entry_and_the_chat_use_case(
    seeded_home: Path, ollama_stub: str
) -> None:
    """The three writes land, and the chat use case resolves to the bound model."""
    _install_stub_provider_app(seeded_home)

    result = slm.bind_local_model(endpoint=ollama_stub)

    assert result.status == slm.BOUND, result.detail
    assert result.ok
    # Newest chat-capable tag wins — not merely the first in the payload.
    assert result.model == "demo-chat:8b"
    # An embedding family whose id has no "embed" substring must still land under
    # embedding rather than being miscategorised as a second chat model.
    assert result.embedding_model == "nomic-text:v1"

    cfg = json.loads((seeded_home / "config.json").read_text(encoding="utf-8"))
    entry = next(p for p in cfg["providers"] if p["name"] == slm.PROVIDER_ENTRY_NAME)
    assert entry["type"] == slm.PROVIDER_TYPE
    assert entry["model"] == "demo-chat:8b"
    assert entry["options"]["endpoint"] == ollama_stub

    active = json.loads((seeded_home / "active_models.json").read_text(encoding="utf-8"))
    assert active["chat"] == [f"{slm.PROVIDER_ENTRY_NAME}:demo-chat:8b"]
    assert active["embedding"] == [f"{slm.PROVIDER_ENTRY_NAME}:nomic-text:v1"]


def test_the_bound_entry_resolves_through_the_real_registry(
    seeded_home: Path, ollama_stub: str
) -> None:
    """``sync_entries_from_config`` is what a booting gateway runs — assert on THAT.

    Asserting only on the written JSON would pass even if the entry were shaped in a
    way the registry drops on the floor, which is exactly how a home ends up seeded,
    configured, and still unable to resolve a model.
    """
    from personalclaw.llm.registry import reset_default_registry, sync_entries_from_config
    from personalclaw.providers.use_cases import active_model_refs

    _install_stub_provider_app(seeded_home)
    slm.bind_local_model(endpoint=ollama_stub)

    reset_default_registry()
    try:
        sync_entries_from_config()
        from personalclaw.llm.registry import get_default_registry

        entry = get_default_registry().get_entry(slm.PROVIDER_ENTRY_NAME)
        assert entry.type == slm.PROVIDER_TYPE
        assert entry.model == "demo-chat:8b"
        assert entry.options["endpoint"] == ollama_stub
        assert active_model_refs("chat") == [f"{slm.PROVIDER_ENTRY_NAME}:demo-chat:8b"]
    finally:
        reset_default_registry()


def test_no_credential_is_written_anywhere(seeded_home: Path, ollama_stub: str) -> None:
    """A local Ollama needs none, and the committed path must never acquire one."""
    _install_stub_provider_app(seeded_home)
    slm.bind_local_model(endpoint=ollama_stub)

    cfg = json.loads((seeded_home / "config.json").read_text(encoding="utf-8"))
    entry = next(p for p in cfg["providers"] if p["name"] == slm.PROVIDER_ENTRY_NAME)
    assert "credential" not in entry
    blob = json.dumps(entry)
    assert "api_key" not in blob and "token" not in blob


def test_endpoint_and_model_are_configurable_by_env(
    seeded_home: Path, ollama_stub: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither the endpoint nor the model may be a hardcoded machine-specific value."""
    _install_stub_provider_app(seeded_home)
    monkeypatch.setenv(slm.ENDPOINT_ENV, ollama_stub)
    monkeypatch.setenv(slm.MODEL_ENV, "llama3.2:3b")

    result = slm.bind_local_model()

    assert result.status == slm.BOUND
    assert result.endpoint == ollama_stub
    assert result.model == "llama3.2:3b"


def test_the_default_endpoint_is_not_operator_specific() -> None:
    """The shipped default has to be the stock local one, and carry no credential."""
    assert slm.DEFAULT_ENDPOINT == "http://localhost:11434"
    assert "@" not in slm.DEFAULT_ENDPOINT


def test_a_second_run_leaves_the_existing_binding_alone(
    seeded_home: Path, ollama_stub: str
) -> None:
    """Re-running must not append a duplicate provider entry."""
    _install_stub_provider_app(seeded_home)
    assert slm.bind_local_model(endpoint=ollama_stub).status == slm.BOUND

    again = slm.bind_local_model(endpoint=ollama_stub)

    assert again.status == slm.ALREADY_BOUND
    assert again.ok
    assert again.wrote == []
    cfg = json.loads((seeded_home / "config.json").read_text(encoding="utf-8"))
    assert len(cfg["providers"]) == 1


def test_installs_the_provider_app_from_a_local_source(
    seeded_home: Path, ollama_stub: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the app absent but a local source given, the app is installed too."""
    source_root = tmp_path / "apps-repo"
    app_src = source_root / slm.PROVIDER_APP
    app_src.mkdir(parents=True)
    (app_src / "app.json").write_text(
        json.dumps(
            {
                "name": slm.PROVIDER_APP,
                "version": "0.1.0",
                "displayName": "Ollama",
                "description": "Local model provider used by the demo seed test.",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(slm.APPS_DIR_ENV, str(source_root))

    result = slm.bind_local_model(endpoint=ollama_stub)

    assert result.status == slm.BOUND, result.detail
    assert f"apps/{slm.PROVIDER_APP}/" in result.wrote
    assert (seeded_home / "apps" / slm.PROVIDER_APP / "installed.json").is_file()


# ── direction 2: NO model is reachable (must degrade, not error) ─────────────


def test_no_ollama_writes_nothing_at_all(seeded_home: Path, closed_port: str) -> None:
    """Nothing listening: the home stays byte-identical to the seeded fixture."""
    _install_stub_provider_app(seeded_home)
    before = _tree(seeded_home)

    result = slm.bind_local_model(endpoint=closed_port)

    assert result.status == slm.SKIPPED_NO_SERVER
    assert not result.ok
    assert result.wrote == []
    assert _tree(seeded_home) == before
    # The message has to say what to do, not just that something is missing.
    assert slm.ENDPOINT_ENV in result.detail


def test_no_ollama_does_not_raise_and_does_not_fail_the_command(
    seeded_home: Path, closed_port: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI step exits 0 on a machine with no Ollama — a skip is not a failure.

    Most people running ``--seed demo-home`` will not have a local model. If the bind
    step returned non-zero, ``gateway --seed demo-home --seed-local-model`` would abort
    instead of starting against the plain fixture.
    """
    import argparse

    args = argparse.Namespace(
        local_model_endpoint=closed_port,
        local_model=None,
        local_model_apps_dir=None,
    )

    assert slm.seed_local_model_cmd(args) == 0

    err = capsys.readouterr().err
    assert slm.SKIPPED_NO_SERVER in err


def test_a_reachable_endpoint_with_no_models_writes_nothing(
    seeded_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama up but empty: still nothing written, and the message says to pull one."""
    _install_stub_provider_app(seeded_home)
    monkeypatch.setattr(_TagsHandler, "payload", {"models": []})
    server = HTTPServer(("127.0.0.1", 0), _TagsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        before = _tree(seeded_home)
        result = slm.bind_local_model(endpoint=f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.status == slm.SKIPPED_NO_MODEL
    assert result.wrote == []
    assert _tree(seeded_home) == before
    assert slm.MODEL_ENV in result.detail


def test_a_missing_provider_app_writes_no_provider_entry(
    seeded_home: Path, ollama_stub: str
) -> None:
    """A model without the app that can build it must NOT produce a config entry.

    This is the half-populated state the whole design exists to avoid: core registers
    no ``ollama`` provider type of its own, so a ``providers[]`` entry with no
    installed app behind it is a binding that resolves by name and then fails at the
    first turn — the failure mode is one indirection away from where the mistake was.
    """
    before = _tree(seeded_home)

    result = slm.bind_local_model(endpoint=ollama_stub)

    assert result.status == slm.SKIPPED_NO_PROVIDER_APP
    assert result.wrote == []
    assert _tree(seeded_home) == before
    assert slm.PROVIDER_APP in result.detail


def test_a_named_but_unpulled_model_still_binds_and_says_so(
    seeded_home: Path, ollama_stub: str
) -> None:
    """Naming a model is an instruction; Ollama can pull it later. Report it, don't lie."""
    _install_stub_provider_app(seeded_home)

    result = slm.bind_local_model(endpoint=ollama_stub, model="not-pulled-yet:1b")

    assert result.status == slm.BOUND
    assert result.model == "not-pulled-yet:1b"
    assert "not pulled" in result.detail


# ── CLI surface ─────────────────────────────────────────────────────────────


def _subprocess_env(tmp_path: Path) -> dict[str, str]:
    """Env for a child ``python -m personalclaw...`` run, mirroring test_seed.py.

    ``HOME`` is redirected so a child can never touch the dev's real
    ``~/.personalclaw``, and the user site dir is preserved because overriding ``HOME``
    otherwise loses the deps installed there.
    """
    import os as _os

    repo_root = Path(__file__).resolve().parent.parent
    env = {**_os.environ, "HOME": str(tmp_path)}
    real_home = _os.environ.get("HOME", "")
    if real_home:
        import site

        user_site = site.getusersitepackages()
        if isinstance(user_site, str) and _os.path.isdir(user_site):
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = user_site + (_os.pathsep + existing if existing else "")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo_root / "src") + (_os.pathsep + existing if existing else "")
    env["PERSONALCLAW_PROJECT_DIR"] = str(repo_root)
    for var in (slm.ENDPOINT_ENV, slm.MODEL_ENV, slm.EMBEDDING_MODEL_ENV, slm.APPS_DIR_ENV):
        env.pop(var, None)
    return env


def test_gateway_help_documents_the_flag_and_its_overrides(tmp_path: Path) -> None:
    """``personalclaw gateway --help`` registers the flag and all three overrides.

    Same tracer-bullet shape as ``test_seed.py``'s wiring test: ``run_gateway`` is a
    long-lived server, so ``--help`` (exits 0 after printing usage) is what proves the
    flag is registered and the ``seed_local_model`` import resolves clean.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "personalclaw", "gateway", "--help"],
        env=_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    for flag in (
        "--seed-local-model",
        "--local-model-endpoint",
        "--local-model",
        "--local-model-apps-dir",
    ):
        assert flag in result.stdout, f"{flag} missing from gateway --help"


def test_the_standalone_entry_point_exits_zero_with_no_ollama(
    tmp_path: Path, closed_port: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``python -m personalclaw.seed_local_model`` degrades cleanly in a real process.

    Covers the entry point evals / research-lab homes use — a home that already exists
    and must be bound WITHOUT re-seeding or booting a gateway.
    """
    import subprocess
    import sys

    home = tmp_path / "existing-home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    seed_mod.seed(FIXTURE_NAME)
    before = _tree(home)

    env = _subprocess_env(tmp_path)
    env["PERSONALCLAW_HOME"] = str(home)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "personalclaw.seed_local_model",
            "--local-model-endpoint",
            closed_port,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert slm.SKIPPED_NO_SERVER in (result.stdout + result.stderr)
    assert _tree(home) == before
