"""What the gateway runs for an app gets the child allowlist, never the gateway's environment.

The gateway's own environment holds every secret saved in PersonalClaw (``load_credentials``
exports them at start) and whatever the shell that started it had: AWS keys, provider API keys,
tokens. On main four app-driven spawns handed a copy of it to code PersonalClaw did not write:

* pip installing an app's ``pythonDependencies`` (``app_python._pip_env``), so a package's build
  step ran with every secret;
* an app's setup hooks, through ``app_python.app_packages_env``, which the SDK also publishes as
  the environment for an app's own Python child (piper-tts runs ``python -m piper`` in it);
* npm installing an ACP adapter (``acp.cli_resolve.provision_acp_adapter``), which runs the
  install scripts of the adapter and everything it depends on;
* an app's own MCP servers (``mcp_discovery.stdio_spawn_env``).

The engine install already had the allowlist, but without pip's own settings, so an index or
certificate that worked for an app's packages did not work for its engine.

Each test below plants credentials in the gateway's environment, runs the real spawn site, and
reads the environment the child itself saw. Each also checks the child got what it needs (PATH,
the installer's own settings), so a child that received nothing cannot pass.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest
from mcp_owner_allowed import allow

from personalclaw import sandbox
from personalclaw.apps import app_manager, app_python
from personalclaw.local_models import sidecar
from personalclaw.security import strip_url_userinfo
from tests.test_app_python_packages import _fake_dist, _manifest, _site
from tests.test_install_a_sidecar_engine import APP, _installed
from tests.test_install_a_sidecar_engine import _manifest as _engine_manifest
from tests.test_install_a_sidecar_engine import _stub_python

#: Credentials the way the gateway holds them: a cloud key, a provider key, and a token no
#: name-pattern would recognise.
_SECRETS = {
    "AWS_SECRET_ACCESS_KEY": "aws-secret-4d2a",
    "ANTHROPIC_API_KEY": "sk-ant-planted-91b0",
    "ACME_DEPLOY_PAT": "deploy-pat-5e77",
}
_PROXY_PASSWORD = "proxy-pass-7c1e"
_PROXY = f"http://ada:{_PROXY_PASSWORD}@proxy.invalid:3128"


@pytest.fixture(autouse=True)
def _gateway_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HTTPS_PROXY", _PROXY)
    monkeypatch.setattr(sandbox, "_declared_env_passthrough", lambda site: set())
    sandbox._left_out_said.clear()


def _leaked(env: dict[str, str]) -> list[str]:
    """Every planted secret or proxy password that reached *env*, by where it was found."""
    found = []
    for name, value in env.items():
        for secret in (*_SECRETS.values(), _PROXY_PASSWORD):
            if secret in value:
                found.append(f"{name} carries {secret}")
    return found


def _env_dump(path: Path) -> str:
    """A command that writes the environment it runs in to *path*, as JSON."""
    return (
        f'"{sys.executable}" -c "import json, os, pathlib; '
        f"pathlib.Path(r'{path}').write_text(json.dumps(dict(os.environ)))\""
    )


def _seen(path: Path) -> dict[str, str]:
    assert path.is_file(), "the child never ran"
    return json.loads(path.read_text(encoding="utf-8"))


# ── pip, installing an app's packages ─────────────────────────────────────────────────────────


def _package_that_records_its_build(root: Path, record: Path) -> Path:
    """A source tree whose build backend writes the environment it builds in to *record*.

    In-tree (``backend-path``) and with no build requirements, so pip builds it offline, exactly
    as it runs any package's build code: in a child of pip, with pip's environment.
    """
    tree = root / "pclaw-envprobe"
    tree.mkdir(parents=True)
    (tree / "pyproject.toml").write_text(
        textwrap.dedent("""\
            [build-system]
            requires = []
            build-backend = "envprobe_backend"
            backend-path = ["."]
            """),
        encoding="utf-8",
    )
    (tree / "envprobe_backend.py").write_text(
        textwrap.dedent(f"""\
            import base64, hashlib, json, os, zipfile

            def _files():
                info = "pclaw_envprobe-1.0.dist-info"
                files = {{
                    "pclaw_envprobe/__init__.py": "",
                    info + "/METADATA": (
                        "Metadata-Version: 2.1\\nName: pclaw-envprobe\\nVersion: 1.0\\n"
                    ),
                    info + "/WHEEL": (
                        "Wheel-Version: 1.0\\nGenerator: t\\nRoot-Is-Purelib: true\\n"
                        "Tag: py3-none-any\\n"
                    ),
                }}
                lines = []
                for rel, text in files.items():
                    sha = hashlib.sha256(text.encode()).digest()
                    digest = base64.urlsafe_b64encode(sha).rstrip(b"=").decode()
                    lines.append(f"{{rel}},sha256={{digest}},{{len(text.encode())}}")
                files[info + "/RECORD"] = "\\n".join([*lines, info + "/RECORD,,"]) + "\\n"
                return files

            def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
                with open(r"{record}", "w") as out:
                    json.dump(dict(os.environ), out)
                name = "pclaw_envprobe-1.0-py3-none-any.whl"
                with zipfile.ZipFile(os.path.join(wheel_directory, name), "w") as zf:
                    for rel, text in _files().items():
                        zf.writestr(rel, text)
                return name
            """),
        encoding="utf-8",
    )
    return tree


def test_a_packages_build_step_runs_without_the_gateways_secrets(tmp_path, monkeypatch):
    """THE defect: pip ran with a copy of the gateway's environment, so the build code of every
    package an app declares could read every credential the gateway holds."""
    record = tmp_path / "build-saw.json"
    tree = _package_that_records_its_build(tmp_path / "src", record)
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_NO_CACHE_DIR", "1")
    monkeypatch.setenv("PIP_TARGET", str(tmp_path / "somewhere-else"))
    spec = f"pclaw-envprobe @ {tree.as_uri()}"

    app_manager._install_python_deps(_manifest([spec], name="probe-app", label="Probe App"))

    seen = _seen(record)
    assert _leaked(seen) == [], "a package's build step read the gateway's secrets"
    # What pip needs, it still has: its PATH, its own settings, and the proxy's address.
    assert seen.get("PATH")
    assert seen.get("PIP_NO_INDEX") == "1"
    assert seen.get("HTTPS_PROXY") == "http://proxy.invalid:3128"
    # And never a setting that would move the install out of app-python.
    assert "PIP_TARGET" not in seen
    assert (_site() / "pclaw_envprobe" / "__init__.py").is_file()
    sys.modules.pop("pclaw_envprobe", None)


def test_a_setup_hook_runs_without_the_gateways_secrets(tmp_path):
    record = tmp_path / "hook-saw.json"
    app_manager._run_hook(_env_dump(record), cwd=tmp_path, timeout=60, env_name="onInstall")

    seen = _seen(record)
    assert _leaked(seen) == [], "a setup hook read the gateway's secrets"
    assert seen.get("PATH")


def test_a_setup_hook_still_imports_the_apps_packages_without_the_secrets(tmp_path):
    """The one thing a hook's environment adds on purpose: where the app's packages are."""
    _fake_dist("pclaw-fixture-dep", "1.0")
    env = app_python.app_packages_env()
    assert _leaked(env) == []
    assert str(_site()) in env["PYTHONPATH"].split(":")


def test_the_sdks_environment_for_an_apps_child_is_the_allowlist():
    """``personalclaw.sdk.util.app_packages_env`` is what an app hands its own Python child. With
    no app package installed it returned None, which a caller passes as ``env=None``: the child
    inherits the gateway's whole environment."""
    from personalclaw.sdk.util import app_packages_env

    env = app_packages_env()
    assert env is not None, "None means the child inherits every secret the gateway holds"
    assert _leaked(env) == []
    assert env.get("PATH")


# ── the engine install ────────────────────────────────────────────────────────────────────────


def test_an_engine_install_gets_pips_own_settings(tmp_path, monkeypatch):
    """The engine install had the allowlist and nothing of pip's: an index that installs an app's
    packages could not install its engine without the operator passing it through by hand."""
    monkeypatch.setenv("PIP_INDEX_URL", "https://pypi.corp.invalid/simple")
    monkeypatch.setenv("PIP_TARGET", str(tmp_path / "somewhere-else"))
    live = _installed(_engine_manifest(dependencies={"sidecarDependencies": ["pclaw-engine==1.0"]}))
    record = tmp_path / "engine-pip-saw.json"
    _stub_python(live / "venv", _env_dump(record) + "\n")

    install = sidecar.SidecarInstall.for_app(APP)
    assert install is not None and install.run_one("deps"), install.status()

    seen = _seen(record)
    assert seen.get("PIP_INDEX_URL") == "https://pypi.corp.invalid/simple"
    assert "PIP_TARGET" not in seen
    assert _leaked(seen) == []


def test_an_index_that_wanted_the_login_says_which_setting_lost_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://ada:index-token@pypi.corp.invalid/simple")
    live = _installed(_engine_manifest(dependencies={"sidecarDependencies": ["pclaw-engine==1.0"]}))
    _stub_python(
        live / "venv",
        """\
        echo "ERROR: HTTP error 401 while getting https://pypi.corp.invalid/simple/pclaw-engine/"
        exit 1
        """,
    )

    install = sidecar.SidecarInstall.for_app(APP)
    assert install is not None and not install.run_one("deps")

    assert install.reason == "network"
    assert "PIP_INDEX_URL" in install.remediation and "HTTPS_PROXY" in install.remediation
    assert "Settings → Security → Child environment passthrough" in install.remediation


# ── npm, installing an ACP adapter ────────────────────────────────────────────────────────────


def test_an_acp_adapter_install_runs_without_the_gateways_secrets(tmp_path, monkeypatch):
    """npm runs the install scripts of the adapter and of every package under it."""
    from personalclaw.acp import cli_resolve

    monkeypatch.delenv("PERSONALCLAW_ACP_NO_PROVISION", raising=False)
    monkeypatch.setenv("npm_config_registry", "https://npm.corp.invalid/")
    monkeypatch.setenv("npm_config__authToken", "npm-token-3f9d")
    monkeypatch.setenv("NPM_CONFIG_GLOBAL", "true")
    prefix = tmp_path / "acp-adapters"
    node_dir = tmp_path / "node" / "bin"
    node_dir.mkdir(parents=True)
    record = tmp_path / "npm-saw.json"
    for name, body in (
        ("node", "exit 0\n"),
        (
            "npm",
            f"{_env_dump(record)}\n"
            f'mkdir -p "{prefix}/node_modules/.bin"\n'
            f'printf "#!/bin/sh\\n" > "{prefix}/node_modules/.bin/fake-acp"\n'
            f'chmod +x "{prefix}/node_modules/.bin/fake-acp"\n',
        ),
    ):
        (node_dir / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
        (node_dir / name).chmod(0o755)
    monkeypatch.setattr(cli_resolve, "resolve_node_ge", lambda *a, **k: str(node_dir / "node"))
    monkeypatch.setattr(cli_resolve, "_managed_bin_dir", lambda: prefix)

    assert cli_resolve.provision_acp_adapter("@fake/acp", ["fake-acp"])

    seen = _seen(record)
    assert _leaked(seen) == [], "an npm install script read the gateway's secrets"
    assert seen["PATH"].split(":")[0] == str(node_dir)
    assert seen.get("npm_config_registry") == "https://npm.corp.invalid/"
    assert "npm_config__authToken" not in seen and "NPM_CONFIG_GLOBAL" not in seen


# ── an app's MCP server ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_apps_mcp_server_runs_without_the_gateways_secrets(tmp_path, monkeypatch):
    """Spawned by the real probe. Its declared ``env`` still reaches it, and a server of your own
    keeps the gateway's environment, like any program you start."""
    from personalclaw import mcp_discovery

    monkeypatch.setattr(mcp_discovery, "_cache_probe", lambda server: None)
    recorded: dict[str, dict[str, str]] = {}
    for name in ("notes-app:search", "my-own-server"):
        record = tmp_path / f"{name.replace(':', '-')}.json"
        script = tmp_path / f"{name.replace(':', '-')}.py"
        script.write_text(
            f"import json, os\nopen(r'{record}', 'w').write(json.dumps(dict(os.environ)))\n",
            encoding="utf-8",
        )
        server = mcp_discovery.McpServerInfo(
            name=name, command=sys.executable, args=[str(script)], env={"NOTES_DIR": "/notes"}
        )
        allow(server)
        await mcp_discovery.probe_server(server)
        recorded[name] = _seen(record)

    app_server = recorded["notes-app:search"]
    assert _leaked(app_server) == [], "an app's MCP server read the gateway's secrets"
    assert app_server.get("NOTES_DIR") == "/notes" and app_server.get("PATH")
    assert recorded["my-own-server"].get("ANTHROPIC_API_KEY") == _SECRETS["ANTHROPIC_API_KEY"]


# ── the builder ───────────────────────────────────────────────────────────────────────────────


def test_a_proxy_reaches_a_child_as_its_address_without_the_login():
    env = sandbox.build_child_env(
        site="t",
        source={
            "HTTPS_PROXY": "http://ada:pw@proxy.corp:3128",
            "http_proxy": "ada:pw@proxy.corp:3128",
            "ALL_PROXY": "socks5://tok@proxy.corp:1080",
            "NO_PROXY": "localhost,.corp",
        },
    )
    assert env == {
        "ALL_PROXY": "socks5://proxy.corp:1080",
        "HTTPS_PROXY": "http://proxy.corp:3128",
        "NO_PROXY": "localhost,.corp",
        "http_proxy": "proxy.corp:3128",
    }


def test_a_python_child_writes_bytecode_where_the_gateway_reads_it():
    """Measured on the first cut of this change: pip installed an app package over another version
    and wrote the new cache beside the source, while the gateway read the old one from its prefix
    (same size, same second) and ran the old version's code."""
    env = sandbox.build_child_env(site="t", source={"PYTHONPYCACHEPREFIX": "/var/pyc"})
    assert env == {"PYTHONPYCACHEPREFIX": "/var/pyc"}


def test_a_proxy_the_operator_names_is_passed_as_it_is(monkeypatch):
    monkeypatch.setattr(sandbox, "_declared_env_passthrough", lambda site: {"HTTPS_PROXY"})
    env = sandbox.build_child_env(site="t", source={"HTTPS_PROXY": "http://ada:pw@proxy:3128"})
    assert env == {"HTTPS_PROXY": "http://ada:pw@proxy:3128"}


def test_an_installer_gets_its_own_settings_and_nothing_that_moves_the_install():
    source = {
        "PIP_INDEX_URL": "https://ada:tok@pypi.corp/simple",
        "PIP_TRUSTED_HOST": "pypi.corp",
        "PIP_TARGET": "/t",
        "PIP_PREFIX": "/p",
        "PIP_ROOT": "/r",
        "PIP_USER": "1",
        "npm_config_registry": "https://npm.corp/",
        "npm_config__auth": "YWRhOnB3",
        "npm_config__authToken": "tok",
        "NPM_CONFIG_PREFIX": "/n",
        "npm_config_global": "true",
    }
    assert sandbox.build_child_env(site="t", source=source) == {}
    assert sandbox.build_child_env(site="t", source=source, installer="pip") == {
        "PIP_INDEX_URL": "https://pypi.corp/simple",
        "PIP_TRUSTED_HOST": "pypi.corp",
    }
    assert sandbox.build_child_env(site="t", source=source, installer="npm") == {
        "npm_config_registry": "https://npm.corp/",
    }


def test_a_login_taken_out_is_logged_once_per_site(caplog):
    with caplog.at_level("WARNING", logger="personalclaw.sandbox"):
        for _ in range(3):
            sandbox.build_child_env(site="cron-script", source={"HTTPS_PROXY": _PROXY})
    said = [r.getMessage() for r in caplog.records if "HTTPS_PROXY" in r.getMessage()]
    assert len(said) == 1 and "env_passthrough" in said[0], said
    assert _PROXY_PASSWORD not in said[0]


def test_a_proxy_that_wanted_the_login_is_named_in_the_install_failure():
    output = (
        "WARNING: Retrying (Retry(total=4)) after connection broken by 'ProxyError('Unable to "
        "connect to proxy', OSError('Tunnel connection failed: 407 Proxy Authentication "
        "Required'))': /simple/requests/\n"
        "ERROR: No matching distribution found for requests\n"
    )
    target = app_python.Declared(name="dep-app", label="Dep App", requirements=["requests"])

    message, actionable = app_python.explain_failure(output, target, [])

    assert message.startswith("Couldn't download Dep App's Python packages (requests).")
    assert "HTTPS_PROXY without the user name and password in it" in message
    assert "Settings → Security → Child environment passthrough" in message
    assert not actionable


def test_a_network_failure_with_no_login_taken_out_keeps_its_own_sentence(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    output = "Tunnel connection failed: 407 Proxy Authentication Required\nProxyError\n"
    target = app_python.Declared(name="dep-app", label="Dep App", requirements=["requests"])

    message, _ = app_python.explain_failure(output, target, [])

    assert "Child environment passthrough" not in message
    assert "Check its internet connection or proxy settings" in message


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        ("http://ada:pw@proxy:3128", "http://proxy:3128"),
        ("https://__token__:pypi-AgEI@pypi.example/simple", "https://pypi.example/simple"),
        ("http://tok@proxy:3128/path?q=1", "http://proxy:3128/path?q=1"),
        ("http://proxy:3128", "http://proxy:3128"),
        ("no url here", "no url here"),
    ],
)
def test_strip_url_userinfo_keeps_the_address(value, kept):
    assert strip_url_userinfo(value) == kept
