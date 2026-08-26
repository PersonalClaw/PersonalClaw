"""PEP-9 — the React artifact build path: static output, the resource ceiling, and a
failure that is REPORTED rather than swallowed or hung.

The clauses this file exists to hold, each asserted at the CALL SITE rather than by
exercising the mechanism in isolation:

* **The build rides the resource-limited spawn.** Two independent proofs. (1) The argv
  actually handed to the OS at the build's one spawn is inspected: it must be the
  post-exec shim carrying the ``build`` profile's own policy, with the bundler after
  ``--``. (2) The *child* is asked what ceiling it received — the test lowers its own
  soft ``RLIMIT_NOFILE`` to a literal it supplies, so "the child reports something other
  than that literal, namely its hard limit" is evidence the shim ran, and the baseline
  (the same stub spawned raw) proves the literal is what a child sees WITHOUT the shim.
  That is the vacuity floor: the pinned value comes from the child, the floor comes from
  a number this file chose.

* **The build never reaches the network.** The toolchain is discovered, never installed:
  a host with no toolchain gets a refusal, and the argv census asserts no install verb
  and no URL can appear in the one command that runs.

* **A failure is reported, not swallowed.** A non-zero bundler, a bundler that writes
  nothing, and a bundler that never finishes each raise with the reason in the sentence;
  the deploy route turns that into a 422 whose body is what ``errText`` shows the user;
  and in every failing case NOTHING is published — no served files, no registry row.

The bundler is stubbed with a tiny ``sh`` script for every test here, because the real
esbuild lives in ``node_modules`` (absent in a fresh clone, and never a test dependency).
The real toolchain path is exercised by hand and recorded in the plan's execution log.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.build import (
    BUNDLE_CSS,
    BUNDLE_JS,
    ENTRY_HTML,
    TOOLCHAIN_ROOT_ENV,
    ArtifactBuildError,
    build_argv,
    build_react_artifact,
    entry_document,
    entry_module,
    needs_build,
    resolve_toolchain,
)
from personalclaw.artifacts.deploy import SERVE_URL_PREFIX, ArtifactDeployStore
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.native import NativeArtifactProvider

#: The soft NOFILE this file installs on ITSELF before probing a child. It is a literal
#: chosen here, which is what makes it a floor rather than a restatement of the value it
#: is meant to pin: a child that reports 4321 inherited our limit and therefore did NOT
#: go through the shim.
PROBE_SOFT_NOFILE = 4321

JSX = "function App() { return <h1>hello</h1> }"


# ── harness ──────────────────────────────────────────────────────────────────


@contextmanager
def lowered_nofile(soft: int):
    """Lower this process' soft ``RLIMIT_NOFILE`` to *soft*, restoring it after.

    Guarantees the discriminating condition the child probe needs (soft < hard) instead
    of hoping the host provides it: on a host where soft already equals hard, the
    ``build`` profile's raise-to-hard would be unobservable and the probe below would be
    vacuously green.
    """
    orig_soft, orig_hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if orig_hard != resource.RLIM_INFINITY and orig_hard <= soft:
        pytest.fail(
            f"cannot host the ceiling probe: hard RLIMIT_NOFILE is {orig_hard}, which is "
            f"not above the probe's soft limit {soft}"
        )
    resource.setrlimit(resource.RLIMIT_NOFILE, (soft, orig_hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (orig_soft, orig_hard))


def _toolchain(root: Path, script: str) -> Path:
    """Lay out a toolchain root whose ``esbuild`` is *script*. Returns the root."""
    nm = root / "node_modules"
    (nm / ".bin").mkdir(parents=True, exist_ok=True)
    for pkg in ("react", "react-dom"):
        (nm / pkg).mkdir(parents=True, exist_ok=True)
        (nm / pkg / "package.json").write_text(json.dumps({"name": pkg, "version": "0.0.0-test"}))
    esbuild = nm / ".bin" / "esbuild"
    esbuild.write_text(script)
    esbuild.chmod(0o755)
    return root


_OUTFILE_SH = """\
out=""
for a in "$@"; do
  case "$a" in --outfile=*) out="${a#--outfile=}" ;; esac
done
"""


def stub_ok(bundle: str = "window.__built=1;", css: str | None = None) -> str:
    """A bundler that succeeds, writing *bundle* (and optionally a stylesheet)."""
    css_line = ""
    if css is not None:
        css_line = f'[ -n "$out" ] && printf %s {json.dumps(css)} > "${{out%.js}}.css"\n'
    return (
        "#!/bin/sh\n"
        + _OUTFILE_SH
        + f'[ -n "$out" ] && printf %s {json.dumps(bundle)} > "$out"\n'
        + css_line
        + "exit 0\n"
    )


def stub_ok_with_external(url: str) -> str:
    """A bundler that succeeds but reports, in its metafile, that it left *url* external —
    exactly what real esbuild does with a URL import (rc 0, no warning)."""
    meta = json.dumps({"inputs": {"entry.jsx": {"imports": [{"path": url, "external": True}]}}})
    return (
        "#!/bin/sh\n"
        + _OUTFILE_SH
        + 'meta=""\n'
        + 'for a in "$@"; do\n'
        + '  case "$a" in --metafile=*) meta="${a#--metafile=}" ;; esac\n'
        + "done\n"
        + f'[ -n "$out" ] && printf %s {json.dumps("import " + json.dumps(url) + ";")} > "$out"\n'
        + f'[ -n "$meta" ] && printf %s {json.dumps(meta)} > "$meta"\n'
        + "exit 0\n"
    )


def stub_fails(message: str) -> str:
    return f"#!/bin/sh\nprintf %s\\\\n {json.dumps(message)} >&2\nexit 1\n"


def stub_silent_success() -> str:
    """Exits 0 and writes no bundle — the 'succeeded' build with nothing to show."""
    return "#!/bin/sh\nexit 0\n"


def stub_hangs() -> str:
    return "#!/bin/sh\nsleep 30\n"


def stub_reports_limits(report: Path) -> str:
    """A bundler that records the ceiling IT received, then emits a bundle."""
    return (
        "#!/bin/sh\n"
        + _OUTFILE_SH
        + f'printf "%s %s\\n" "$(ulimit -Sn)" "$(ulimit -Hn)" > {json.dumps(str(report))}\n'
        + '[ -n "$out" ] && printf %s "window.__built=1;" > "$out"\n'
        + "exit 0\n"
    )


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An isolated home. ``PERSONALCLAW_HOME`` is the safe lever: read per call, cached
    nowhere, so it also redirects the import-bound stores a ``config_dir`` patch misses."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(h))
    from personalclaw.config.loader import config_dir

    assert config_dir() == h, "PERSONALCLAW_HOME did not redirect config_dir"
    return h


@pytest.fixture
def provider(home) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=home / "artifacts")


@pytest.fixture
def patched_native(provider):
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


async def _client(app_provider) -> TestClient:
    app = web.Application()
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def _read(report: Path) -> tuple[str, str]:
    soft, hard = report.read_text().split()
    return soft, hard


# ── the build rides the resource-limited spawn ───────────────────────────────


@pytest.mark.asyncio
async def test_the_build_spawn_is_the_shim_carrying_the_build_policy(
    tmp_path, home, monkeypatch
) -> None:
    """The argv the OS receives at the build's ONE spawn: the post-exec shim, the
    ``build`` profile's policy, then the bundler. Asserted against literals, so a
    profile swap or a raw spawn reds this."""
    root = _toolchain(tmp_path / "tc", stub_ok())
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    seen: list[list[str]] = []
    real = asyncio.create_subprocess_exec

    async def recorder(*argv, **kwargs):
        seen.append([str(a) for a in argv])
        return await real(*argv, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    await build_react_artifact(slug="w", source=JSX, files_root=tmp_path / "out")

    assert len(seen) == 1, f"the build must spawn exactly once, saw {len(seen)}"
    argv = seen[0]
    assert argv[:3] == [sys.executable, "-m", "personalclaw._spawn_exec_shim"], (
        "the build did not go through the post-exec resource shim: " f"{argv[:3]}"
    )
    # The `build` profile's policy, spelled out rather than recomputed: NOFILE raised to
    # the inherited hard limit (the sentinel the shim resolves in-child) and the OOM bias
    # KEPT. `tool` would carry a numeric NOFILE soft; `none` would carry no shim at all.
    assert json.loads(argv[3]) == {
        "limits": {"RLIMIT_NOFILE": ["hard", "hard"]},
        "oom_score_adj": 1000,
    }
    assert argv[4] == "--"
    assert argv[5] == str(root / "node_modules" / ".bin" / "esbuild")


@pytest.mark.asyncio
async def test_the_ceiling_actually_reaches_the_build_child(tmp_path, home, monkeypatch) -> None:
    """In-child evidence, with its own vacuity floor.

    The floor is the first assertion: spawned WITHOUT the build path, the stub reports the
    soft limit this file installed (``PROBE_SOFT_NOFILE``). So "the build child reports
    something else, and specifically its hard limit" cannot be a coincidence of the host.
    """
    baseline = tmp_path / "baseline.txt"
    through = tmp_path / "through.txt"
    root_base = _toolchain(tmp_path / "tc-base", stub_reports_limits(baseline))
    root_through = _toolchain(tmp_path / "tc-through", stub_reports_limits(through))

    with lowered_nofile(PROBE_SOFT_NOFILE):
        # FLOOR: the same stub, spawned raw. A child that skipped the shim inherits ours.
        proc = await asyncio.create_subprocess_exec(
            str(root_base / "node_modules" / ".bin" / "esbuild"),
            "--outfile=" + str(tmp_path / "ignored.js"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        base_soft, base_hard = await _read(baseline)
        assert base_soft == str(PROBE_SOFT_NOFILE), (
            "the floor is broken: an UNSHIMMED child did not inherit this process' soft "
            f"NOFILE ({base_soft} != {PROBE_SOFT_NOFILE}) — the probe below cannot "
            "discriminate on this host"
        )
        assert base_soft != base_hard, "soft == hard unshimmed: the raise-to-hard is unobservable"

        # THE PROPERTY: through the build path, the child's soft limit was raised to hard.
        monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root_through))
        await build_react_artifact(slug="w", source=JSX, files_root=tmp_path / "out")

    got_soft, got_hard = await _read(through)
    assert got_soft != str(PROBE_SOFT_NOFILE), (
        "the build child inherited this process' soft NOFILE — it did NOT go through the "
        "resource-limited spawn"
    )
    assert got_soft == got_hard, (
        f"the build child's NOFILE is {got_soft}, not raised to its hard limit {got_hard} — "
        "this is not the `build` profile's ceiling"
    )


# ── the build never reaches the network ──────────────────────────────────────


def test_the_build_command_cannot_install_or_fetch(tmp_path, home, monkeypatch) -> None:
    """A census of the ONE command the build runs. Core-owned end to end: the artifact
    contributes source at a path, never an argument, so there is no install verb and no
    URL for it to smuggle in."""
    root = _toolchain(tmp_path / "tc", stub_ok())
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    argv = build_argv(
        resolve_toolchain(),
        tmp_path / "entry.jsx",
        tmp_path / "out" / BUNDLE_JS,
        tmp_path / "out" / "meta.json",
    )

    assert argv[0] == str(root / "node_modules" / ".bin" / "esbuild")
    assert "--bundle" in argv
    # The metafile is load-bearing, not diagnostics: it is the only signal that says an
    # import was left external (measured — esbuild is silent about a URL import at rc 0).
    assert any(a.startswith("--metafile=") for a in argv)
    joined = " ".join(argv).lower()
    for forbidden in ("npm", "npx", "yarn", "pnpm", "install", "://", "--servedir", "--serve"):
        assert forbidden not in joined, f"the build argv contains {forbidden!r}: {argv}"


@pytest.mark.asyncio
async def test_an_import_left_outside_the_bundle_is_refused(tmp_path, home, monkeypatch) -> None:
    """MEASURED with esbuild 0.25.12: a URL import builds at rc 0 with no warning and the
    URL is baked into the output as an external import. Only the metafile says so, so the
    refusal reads it — otherwise a deployed page would fetch off-origin."""
    root = _toolchain(tmp_path / "tc", stub_ok_with_external("https://esm.sh/lodash"))
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    files_root = tmp_path / "out"
    with pytest.raises(ArtifactBuildError) as exc:
        await build_react_artifact(slug="w", source=JSX, files_root=files_root)
    assert "unbundled" in exc.value.what
    assert "esm.sh" in exc.value.why
    assert not files_root.exists(), "an externally-referencing bundle was published"


def test_a_clean_metafile_leaves_no_externals(tmp_path) -> None:
    """The floor for the refusal above: the SAME reader over a metafile with
    ``external`` absent yields nothing, so the refusal is keyed on the flag rather than
    on the presence of imports."""
    from personalclaw.artifacts.build import external_imports

    clean = tmp_path / "clean.json"
    clean.write_text(
        json.dumps(
            {"inputs": {"entry.jsx": {"imports": [{"path": "react", "kind": "import-statement"}]}}}
        )
    )
    assert external_imports(clean) == []
    dirty = tmp_path / "dirty.json"
    dirty.write_text(
        json.dumps(
            {
                "inputs": {
                    "entry.jsx": {"imports": [{"path": "https://x.example/y", "external": True}]}
                }
            }
        )
    )
    assert external_imports(dirty) == ["https://x.example/y"]
    assert external_imports(tmp_path / "absent.json") == []


def test_a_host_with_no_toolchain_is_refused_not_installed_into(
    tmp_path, home, monkeypatch
) -> None:
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(tmp_path / "nothing-here"))
    with patch(
        "personalclaw.artifacts.build.toolchain_candidates",
        return_value=[tmp_path / "nothing-here"],
    ):
        with pytest.raises(ArtifactBuildError) as exc:
            resolve_toolchain()
    assert "never installs" in exc.value.why
    assert TOOLCHAIN_ROOT_ENV in exc.value.fix


def test_a_partial_toolchain_is_not_accepted(tmp_path, home, monkeypatch) -> None:
    """esbuild alone is not a toolchain: without react/react-dom on disk the bundle would
    have to resolve them from somewhere, and there is nowhere but the network."""
    root = tmp_path / "half"
    (root / "node_modules" / ".bin").mkdir(parents=True)
    esbuild = root / "node_modules" / ".bin" / "esbuild"
    esbuild.write_text(stub_ok())
    esbuild.chmod(0o755)
    with patch("personalclaw.artifacts.build.toolchain_candidates", return_value=[root]):
        with pytest.raises(ArtifactBuildError):
            resolve_toolchain()


def test_the_served_page_references_only_its_own_files() -> None:
    """The document the build writes must satisfy PEP-8's ``default-src 'none'`` /
    ``script-src 'self'`` fence with nothing blocked — so no CDN, no absolute URL."""
    doc = entry_document("My widget", with_css=True)
    assert f'src="./{BUNDLE_JS}"' in doc
    assert f'href="./{BUNDLE_CSS}"' in doc
    assert "://" not in doc, "the served document reaches off-origin"
    assert 'id="root"' in doc
    # And no stylesheet link when the build emitted no CSS: the page must not request a
    # file that is not there.
    assert BUNDLE_CSS not in entry_document("My widget", with_css=False)


def test_the_entry_module_keeps_the_preview_contract() -> None:
    """A body authored for the in-chat preview (top-level ``App``, ``React``/``ReactDOM``
    globals) bundles unchanged: the globals are bound AND assigned, and the body is
    spliced verbatim."""
    mod = entry_module(JSX)
    assert JSX in mod
    assert "from 'react'" in mod and "from 'react-dom/client'" in mod
    assert "globalThis.React" in mod and "globalThis.ReactDOM" in mod
    assert "getElementById('root')" in mod


# ── a failure is reported, not swallowed ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failing_bundler_is_reported_and_publishes_nothing(
    tmp_path, home, monkeypatch
) -> None:
    root = _toolchain(tmp_path / "tc", stub_fails("entry.jsx:3:8: ERROR: Unexpected }"))
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    files_root = tmp_path / "out"
    with pytest.raises(ArtifactBuildError) as exc:
        await build_react_artifact(slug="w", source=JSX, files_root=files_root)

    msg = str(exc.value)
    assert "Unexpected }" in msg, f"the bundler's own reason was swallowed: {msg}"
    assert "exited 1" in msg, f"the bundler's exit status was not reported: {msg}"
    assert exc.value.what and exc.value.why and exc.value.fix
    assert "Fix:" in msg
    assert not files_root.exists(), "a failed build published files"


@pytest.mark.asyncio
async def test_a_bundler_that_writes_nothing_is_not_treated_as_success(
    tmp_path, home, monkeypatch
) -> None:
    """Exit 0 with no bundle is the swallowed-write shape: it must be a reported failure,
    not a deployment of nothing."""
    root = _toolchain(tmp_path / "tc", stub_silent_success())
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    files_root = tmp_path / "out"
    with pytest.raises(ArtifactBuildError) as exc:
        await build_react_artifact(slug="w", source=JSX, files_root=files_root)
    assert BUNDLE_JS in exc.value.why
    assert not files_root.exists()


@pytest.mark.asyncio
async def test_a_build_that_never_finishes_is_stopped_and_reported(
    tmp_path, home, monkeypatch
) -> None:
    """The 'not a hang' clause: bounded wall-clock, then a sentence."""
    root = _toolchain(tmp_path / "tc", stub_hangs())
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    started = time.monotonic()
    with pytest.raises(ArtifactBuildError) as exc:
        await build_react_artifact(slug="w", source=JSX, files_root=tmp_path / "out", timeout=1.0)
    elapsed = time.monotonic() - started
    assert "timed out" in exc.value.what
    assert elapsed < 20, f"the build was not bounded: {elapsed:.1f}s for a 1s timeout"


@pytest.mark.asyncio
async def test_an_empty_react_body_is_refused_before_any_spawn(tmp_path, home, monkeypatch) -> None:
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(_toolchain(tmp_path / "tc", stub_ok())))
    with pytest.raises(ArtifactBuildError) as exc:
        await build_react_artifact(slug="w", source="   \n", files_root=tmp_path / "out")
    assert "nothing to build" in exc.value.what


# ── the deploy route: it builds, and it reports ──────────────────────────────


def test_only_react_needs_a_build() -> None:
    assert needs_build("react") and needs_build("  React ")
    for kind in ("widget", "html", "markdown", ""):
        assert not needs_build(kind)


@pytest.mark.asyncio
async def test_deploy_builds_a_react_artifact_and_serves_the_static_bundle(
    tmp_path, patched_native, monkeypatch
) -> None:
    """PEP-9's first clause end to end: deploy builds, and the deploy route serves the
    emitted static files (not the JSX) with the entry the build declared."""
    root = _toolchain(tmp_path / "tc", stub_ok(bundle="window.__built=42;", css="body{margin:0}"))
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    prov = patched_native
    art = prov.create(name="Counter", content=JSX, kind="react")
    client = await _client(prov)
    try:
        resp = await client.post(f"/api/artifacts/{art.slug}/deploy")
        assert resp.status == 200, await resp.text()
        payload = await resp.json()
        assert payload["deployment"]["entry"] == ENTRY_HTML
        assert sorted(payload["build"]["files"]) == sorted([ENTRY_HTML, BUNDLE_JS, BUNDLE_CSS])

        page = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")
        assert page.status == 200
        body = await page.text()
        assert f'src="./{BUNDLE_JS}"' in body
        assert "function App" not in body, "the raw JSX reached the served page"

        js = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/{BUNDLE_JS}")
        assert js.status == 200
        assert "window.__built=42;" in await js.text()

        css = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/{BUNDLE_CSS}")
        assert css.status == 200
        assert "margin:0" in await css.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_deploy_reports_a_build_failure_and_publishes_nothing(
    tmp_path, patched_native, monkeypatch
) -> None:
    """The failure reaches the USER on the surface that caused it: a 422 whose ``error``
    string is exactly what ``errText`` puts in the toast — and no deployment row, so a
    broken app is never reachable at a URL."""
    root = _toolchain(tmp_path / "tc", stub_fails('entry.jsx:4:1: ERROR: Expected ")"'))
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(root))
    prov = patched_native
    art = prov.create(name="Broken", content=JSX, kind="react")
    client = await _client(prov)
    try:
        resp = await client.post(f"/api/artifacts/{art.slug}/deploy")
        assert resp.status == 422, await resp.text()
        message = (await resp.json())["error"]
        assert "React build failed" in message
        assert 'Expected ")"' in message, f"the bundler's reason was swallowed: {message}"
        assert "Fix:" in message

        assert ArtifactDeployStore(prov.root).get(art.slug) is None, "a failed build published"
        assert (await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")).status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_react_artifact_with_no_bundle_is_never_served_as_its_own_body(
    tmp_path, patched_native
) -> None:
    """The fallback that serves a single-body artifact AS its entry must not apply to a
    react artifact: answering JSX with ``text/html`` would put unbundled source on the
    origin and render nothing."""
    prov = patched_native
    art = prov.create(name="Unbuilt", content=JSX, kind="react")
    # Registered directly, bypassing the deploy route's build — the only way to reach the
    # unbuilt state, and exactly the state a stale registry row would leave behind.
    ArtifactDeployStore(prov.root).deploy(art.slug)
    client = await _client(prov)
    try:
        page = await client.get(f"{SERVE_URL_PREFIX}/{art.slug}/")
        assert page.status == 404
        assert "function App" not in await page.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_rebuilding_drops_a_stale_stylesheet(tmp_path, home, monkeypatch) -> None:
    """A component that stops importing CSS must stop linking one: the built names are
    replaced on every build, so a rebuild cannot leave the page requesting a file the
    current bundle never produced."""
    files_root = tmp_path / "out"
    monkeypatch.setenv(
        TOOLCHAIN_ROOT_ENV, str(_toolchain(tmp_path / "tc1", stub_ok(css="body{color:red}")))
    )
    first = await build_react_artifact(slug="w", source=JSX, files_root=files_root)
    assert BUNDLE_CSS in first.files
    assert (files_root / BUNDLE_CSS).is_file()

    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(_toolchain(tmp_path / "tc2", stub_ok())))
    second = await build_react_artifact(slug="w", source=JSX, files_root=files_root)
    assert BUNDLE_CSS not in second.files
    assert not (files_root / BUNDLE_CSS).exists()
    assert BUNDLE_CSS not in (files_root / ENTRY_HTML).read_text()


@pytest.mark.asyncio
async def test_the_build_leaves_no_workspace_behind(tmp_path, home, monkeypatch) -> None:
    """The temp build workspace is removed on the failing path too — a bundler crash must
    not accumulate node_modules symlinks in the temp directory."""
    import tempfile

    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(_toolchain(tmp_path / "tc", stub_fails("boom"))))
    # A PRIVATE temp root, not the shared one: sibling xdist workers create and remove
    # their own build workspaces, so a count taken over the shared directory measures the
    # other workers rather than this build.
    tmp_root = tmp_path / "tmproot"
    tmp_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_root))
    made: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*a, **kw):
        path = real_mkdtemp(*a, **kw)
        made.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    with pytest.raises(ArtifactBuildError):
        await build_react_artifact(slug="leak", source=JSX, files_root=tmp_path / "out")

    # Floor first: a workspace really WAS created, so "nothing left behind" cannot be an
    # artefact of the build never getting that far.
    assert made and "pc-artifact-build-leak-" in made[0], f"no build workspace created: {made}"
    assert not Path(made[0]).exists(), f"build workspace leaked: {made[0]}"
    assert not sorted(tmp_root.glob("pc-artifact-build-*"))


def test_the_toolchain_env_lever_wins_and_is_read_per_call(tmp_path, home, monkeypatch) -> None:
    """The lever is an env var read on every call — no cache — so pointing the build at a
    different toolchain does not need a gateway restart."""
    from personalclaw.artifacts.build import toolchain_candidates

    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(tmp_path / "one"))
    assert toolchain_candidates()[0] == tmp_path / "one"
    monkeypatch.setenv(TOOLCHAIN_ROOT_ENV, str(tmp_path / "two"))
    assert toolchain_candidates()[0] == tmp_path / "two"
    monkeypatch.delenv(TOOLCHAIN_ROOT_ENV)
    assert toolchain_candidates()[0] != tmp_path / "two"
    # The home-local toolchain slot follows the isolated home, never the real one.
    assert os.environ["PERSONALCLAW_HOME"] in str(toolchain_candidates()[-1])
