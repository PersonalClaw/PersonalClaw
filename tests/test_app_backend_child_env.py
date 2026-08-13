"""A gateway secret does NOT reach an app backend's environment (EI-12 D1).

🔴 WHAT WAS MEASURED BEFORE THE FIX. `apps/backend_runtime.py` built the backend's
environment as `dict(os.environ)` — a full copy of the gateway's. PHF-4 had already
converted the hook, cron-script and bash-action sites to the `sandbox.build_child_env`
allowlist and deliberately left this one to D1, which made the app backend the WIDEST
remaining inheritance in the tree and the least deserving of it: an app backend is
third-party code, scanned but not trusted at install, running for as long as the app is
enabled. `config/loader.py` seeds `~/.personalclaw/.env` credentials into `os.environ` so
"trusted children" inherit them, so every one of those credentials was readable by any
installed app's backend via `os.environ`.

The test below plants two credential-shaped variables in the gateway's own environment and
drives the REAL spawn path — `BackendSupervisor.start` → `spawn_shim_argv` →
`subprocess.Popen` → a genuine Python child that dumps its OWN `os.environ` to disk. The
assertions read that dump, so they are the child's view, not a dict we constructed.

Non-vacuity: the same dump must contain `PORT`, `PATH`, `PERSONALCLAW_APP_NAME` and the
app secret. A test that only proved absence would pass just as well against a backend that
received nothing at all, or never started.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from personalclaw.apps import manager
from personalclaw.apps.backend_runtime import BackendSupervisor
from personalclaw.apps.manifest import AppManifest
from personalclaw.sdk.security import APP_SECRET_ENV

#: A credential shape a name-pattern denylist would have caught.
_PLANTED = "ACME_CLOUD_API_KEY"
#: A shape no name-pattern denylist in the tree recognises — the false-negative class the
#: allowlist exists to close.
_PLANTED_UNGUESSABLE = "ACME_DEPLOY_PAT"
_SECRET_VALUE = "planted-secret-value-4c71"

#: The spawn is a fresh interpreter through the ceiling shim, then the app's own
#: interpreter. Under full-suite xdist load a 0.3s spawn can take tens of seconds of wall
#: time from CPU contention — same headroom the sibling child-env suite uses.
_WAIT_SECS = 90


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Apps, secrets and data dirs all live under a tmp home — never the real one.

    `PERSONALCLAW_HOME` is set as well as the two `config_dir` patches because the app
    secret store and the data-dir helper are reached through import-bound module state that
    a `config_dir` patch alone does not always cover.
    """
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _plant_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the secrets in the GATEWAY's environment — the position they really occupy."""
    monkeypatch.setenv(_PLANTED, _SECRET_VALUE)
    monkeypatch.setenv(_PLANTED_UNGUESSABLE, _SECRET_VALUE)


def _install_backend_app(
    home: Path, name: str, dump_to: Path, *, permissions: dict | None = None
) -> AppManifest:
    """Install an app whose backend dumps its own environment to *dump_to* and exits.

    The destination is baked into the SOURCE rather than passed via env, because passing it
    through the environment is exactly the channel under test — an allowlist would withhold
    it and the probe would look like a pass-by-silence.
    """
    appdir = home / "apps" / name
    (appdir / "backend").mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "displayName": name,
        "description": "env probe",
        "backend": {"entryPoint": "backend/server.py", "type": "python"},
        "permissions": permissions if permissions is not None else {},
    }
    (appdir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    (appdir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )
    (appdir / "backend" / "server.py").write_text(
        "import json, os\n"
        f"with open({str(dump_to)!r}, 'w', encoding='utf-8') as fh:\n"
        "    json.dump(dict(os.environ), fh)\n",
        encoding="utf-8",
    )
    return AppManifest.from_json_file(appdir / "app.json")


def _child_env(sup: BackendSupervisor, manifest: AppManifest, dump_to: Path) -> dict[str, str]:
    """Start the real backend and return the environment the CHILD reported."""
    rb = sup.start(manifest)
    assert rb is not None, "the backend never started — the spawn, not the filter, is broken"
    try:
        deadline = time.monotonic() + _WAIT_SECS
        while time.monotonic() < deadline:
            if dump_to.exists() and dump_to.stat().st_size > 0:
                break
            time.sleep(0.05)
        assert dump_to.exists() and dump_to.stat().st_size > 0, (
            "the backend child produced no env dump — it never ran, so an absence "
            "assertion below would be vacuous"
        )
        return dict(json.loads(dump_to.read_text(encoding="utf-8")))
    finally:
        sup.stop(manifest.name)


# ── the real spawn ──


def test_an_app_backend_cannot_read_a_planted_gateway_secret(tmp_path: Path) -> None:
    """Driven through `BackendSupervisor.start`, asserted from the child's own os.environ."""
    dump = tmp_path / "env-dump.json"
    manifest = _install_backend_app(tmp_path, "envprobe", dump, permissions={"storage": True})
    env = _child_env(BackendSupervisor(), manifest, dump)

    # Non-vacuity FIRST: the child really started and really received its contract.
    assert "PATH" in env, "the child got no PATH — the spawn, not the filter, is broken"
    assert env.get("PERSONALCLAW_APP_NAME") == "envprobe"
    assert int(env["PORT"]) > 0
    assert env.get(APP_SECRET_ENV), "the backend lost its proxy secret"
    assert env.get("PERSONALCLAW_APP_DATA_DIR") == str(manager.app_data_dir("envprobe"))

    # The planted gateway secrets are absent — by name and by value.
    assert _PLANTED not in env
    assert _PLANTED_UNGUESSABLE not in env
    assert _SECRET_VALUE not in env.values()

    # And it is an allowlist, not a wider copy that happens to miss these two: the child's
    # environment is a small set, not the gateway's population.
    assert len(env) < len(os.environ), (len(env), len(os.environ))


def _platform_injected_names() -> set[str]:
    """Names the PLATFORM adds to a child after exec, measured rather than assumed.

    🔴 Measured on this host: a Python child spawned with a literally empty `env={}` still
    reports `__CF_USER_TEXT_ENCODING` (CoreFoundation, Darwin) and `LC_CTYPE` (the
    interpreter's own UTF-8 coercion). Neither was inherited — with `env={}` there was
    nothing to inherit — so excusing them is not excusing a leak.

    This is measured at run time instead of hardcoding the two Darwin names so the
    closed-set assertion below stays exact on a platform with a different injected set,
    rather than silently widening to whatever the current OS happens to add.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-c", "import json,os;print(json.dumps(dict(os.environ)))"],
        env={},
        capture_output=True,
        text=True,
        check=True,
    )
    return set(json.loads(out.stdout))


def test_the_backend_env_is_the_allowlist_plus_only_the_computed_four(tmp_path: Path) -> None:
    """Nothing outside `CHILD_ENV_BASE_NAMES` + the four computed names may arrive.

    The blunt form of the assertion, and the one that makes this suite more than a
    two-name spot check. A future call site that reintroduced a copy — or quietly widened
    the base to make some app boot — reds here, rather than passing because the two names
    this suite happens to plant are still missing.
    """
    from personalclaw.sandbox import CHILD_ENV_BASE_NAMES

    dump = tmp_path / "env-dump.json"
    manifest = _install_backend_app(tmp_path, "envprobe2", dump, permissions={"storage": True})
    env = _child_env(BackendSupervisor(), manifest, dump)

    computed = {"PORT", "PERSONALCLAW_APP_NAME", APP_SECRET_ENV, "PERSONALCLAW_APP_DATA_DIR"}
    allowed = set(CHILD_ENV_BASE_NAMES) | computed | _platform_injected_names()
    unexpected = set(env) - allowed
    assert not unexpected, f"a backend received undeclared variables: {sorted(unexpected)}"
    # The exclusion above must not be able to swallow the planted secrets.
    assert _PLANTED not in allowed and _PLANTED_UNGUESSABLE not in allowed


def test_withheld_names_are_logged_against_the_app_backend_site(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An app author whose variable stopped arriving must be able to find out why.

    The log line has to name THIS site, otherwise a missing variable in an app backend is
    indistinguishable from a bug in the app.
    """
    dump = tmp_path / "env-dump.json"
    manifest = _install_backend_app(tmp_path, "envprobe3", dump, permissions={"storage": True})
    with caplog.at_level("DEBUG", logger="personalclaw.sandbox"):
        _child_env(BackendSupervisor(), manifest, dump)

    assert any(
        "app-backend child env" in r.getMessage()
        and _PLANTED in r.getMessage()
        and "env_passthrough" in r.getMessage()
        for r in caplog.records
    ), "no withheld-name line named the app-backend site"


# ── the P3 storage gate survives the allowlist ──


def test_a_backend_without_storage_still_gets_no_data_dir(tmp_path: Path) -> None:
    dump = tmp_path / "env-dump.json"
    manifest = _install_backend_app(tmp_path, "nostore", dump, permissions={})
    env = _child_env(BackendSupervisor(), manifest, dump)

    assert "PERSONALCLAW_APP_DATA_DIR" not in env
    assert env.get("PERSONALCLAW_APP_NAME") == "nostore"  # non-vacuity: it did run


def test_a_declared_passthrough_cannot_reopen_the_storage_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one way `PERSONALCLAW_APP_DATA_DIR` could now arrive by inheritance.

    The name is not in the base, but `sandbox.env_passthrough` is an operator surface that
    takes ANY non-credential-shaped name. Declaring this one — with the gateway itself
    carrying the variable — would otherwise hand every storage-less backend a data dir at
    once, silently undoing sandbox P3. Driven at the real call site, not against the builder.
    """
    monkeypatch.setenv("PERSONALCLAW_APP_DATA_DIR", str(tmp_path / "leaked"))
    monkeypatch.setattr(
        "personalclaw.sandbox._declared_env_passthrough",
        lambda site: {"PERSONALCLAW_APP_DATA_DIR"},
    )
    dump = tmp_path / "env-dump.json"
    manifest = _install_backend_app(tmp_path, "nostore2", dump, permissions={})
    env = _child_env(BackendSupervisor(), manifest, dump)

    assert "PERSONALCLAW_APP_DATA_DIR" not in env, "the P3 storage gate was reopened by config"
    assert env.get("PERSONALCLAW_APP_NAME") == "nostore2"  # non-vacuity: it did run
