"""React artifact build path — build once, serve static (PEP-9).

A ``kind='react'`` artifact's body is JSX defining a top-level ``App`` component,
authored against the ``React``/``ReactDOM`` globals (the contract
``web/src/ui/widget/widgetSrcdoc.ts`` established for the in-chat preview). That
preview transforms the JSX **in the browser** with Babel and pulls React from a
CDN — fine for a bubble, impossible for a deployed page: PEP-8's serve route
fences the document with ``default-src 'none'`` and ``connect-src 'none'``, so a
served page can load only ``'self'``. Hence this module: transform and bundle the
JSX **once, on the server**, and write plain static files into the artifact's
``webapp/`` directory for :func:`~personalclaw.artifacts.handlers.serve_deployed_artifact`
to hand out. No per-artifact dev server, no CDN, no runtime transform.

Three properties are load-bearing.

*The build rides the resource-limited spawn.* An artifact body is arbitrary
model- or user-authored source, and a bundler run over it is exactly the
unbounded-spawn hazard PLATFORM-HARDENING-FLOORS §1 exists to bound. So the one
spawn here goes through :func:`personalclaw.sandbox.create_subprocess_limited`
with :data:`~personalclaw.sandbox.PROFILE_BUILD` — the same profile the model
sidecar's installer and the loop's worktree git steps use — and there is no
second, unwrapped path. ``tests/test_spawn_ceiling_audit.py`` censuses the site;
``tests/test_artifact_react_build.py`` proves the ceiling reaches the child by
reading the child's own ``ulimit``.

*The build never reaches the network.* The toolchain is **discovered, never
installed**: :func:`resolve_toolchain` looks for an already-present
``node_modules`` that carries ``esbuild``, ``react`` and ``react-dom``, and a
host without one gets a legible refusal instead of an ``npm install``. The argv
is core-owned and fixed (an ``esbuild --bundle`` invocation — esbuild resolves
imports on disk and has no remote fetch), the artifact contributes only *source*,
and the child environment comes from :func:`personalclaw.sandbox.build_child_env`
so it does not even inherit the operator's proxy configuration. That covers the
*build*; it does NOT by itself cover the *bundle*, and the difference was measured:
esbuild silently treats ``import x from 'https://esm.sh/lodash'`` as external and
bakes the URL into the output at rc 0 with no warning. So the build reads the
bundler's own metafile and refuses any import left outside the bundle
(:func:`external_imports`) — a deployed page must fetch nothing.

*A failure is reported, never swallowed, and never a hang.* Every failure mode
raises :class:`ArtifactBuildError`, whose message is a WHAT — WHY. Fix: FIX
sentence, and the deploy route returns that sentence in the standard
``{"error": ...}`` envelope so ``errText`` puts it in front of the user
verbatim. A build that stops making progress is killed at
:data:`BUILD_TIMEOUT_SECS` and reported as a timeout. Nothing is written into the
artifact's served files unless the bundle actually came out, so a failed build
can never leave a half-built app published.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from personalclaw.atomic_write import atomic_write, atomic_write_bytes
from personalclaw.config.loader import config_dir
from personalclaw.sandbox import PROFILE_BUILD, build_child_env

logger = logging.getLogger(__name__)

#: Artifact kinds whose deployment requires a build step before anything is
#: servable. A ``widget``/``html`` artifact IS its own entry document; a ``react``
#: artifact is source that means nothing to a browser until it is bundled.
BUILD_REQUIRED_KINDS = frozenset({"react"})

#: Environment lever naming a directory whose ``node_modules`` holds the build
#: toolchain. Read per call (never cached) so a test or an operator can point the
#: build at a toolchain without restarting the gateway.
TOOLCHAIN_ROOT_ENV = "PERSONALCLAW_ARTIFACT_BUILD_ROOT"

#: What a toolchain root must already contain. All three, in ONE ``node_modules``:
#: the bundler plus the runtime it has to resolve. Requiring them together is what
#: lets the refusal name a single fix instead of three.
_ESBUILD_RELPATHS = ("node_modules/.bin/esbuild", "node_modules/esbuild/bin/esbuild")
_RUNTIME_RELPATHS = ("node_modules/react/package.json", "node_modules/react-dom/package.json")

#: The built bundle's file names inside the artifact's ``webapp/`` directory. These
#: three are the ONLY names the build owns: a rebuild replaces them and leaves any
#: other file the user put there alone.
ENTRY_HTML = "index.html"
BUNDLE_JS = "bundle.js"
BUNDLE_CSS = "bundle.css"

#: Wall-clock ceiling for one build. A bundler that stops making progress must be
#: a reported failure, not a spinner: this is the "not a hang" half of PEP-9's
#: done-when. Generous enough for a cold esbuild over a large component, short
#: enough that a wedged build is noticed in one sitting.
BUILD_TIMEOUT_SECS = 90

#: Upper bound on the source we will hand a bundler, and on the bundle we will
#: publish. A react artifact is a widget, not an application tree.
MAX_SOURCE_BYTES = 512 * 1024
MAX_BUNDLE_BYTES = 8 * 1024 * 1024

#: Failure sentences are read in a toast. Past this a message is a log, not a
#: message — the full bundler output stays in the gateway log.
_MAX_DETAIL = 400

#: The build workspace's entry module name. ``.jsx`` so esbuild applies the JSX
#: loader without being told twice.
_ENTRY_MODULE = "entry.jsx"


class ArtifactBuildError(RuntimeError):
    """A build that did not produce a bundle, phrased for the person who asked.

    Carries the three parts separately so a caller can render them apart, and
    joins them into ``str(exc)`` because every current surface (the deploy route's
    error envelope, the gateway log) wants one sentence.
    """

    def __init__(self, what: str, why: str, fix: str) -> None:
        self.what = what.strip()
        self.why = _clip(why.strip())
        self.fix = fix.strip()
        super().__init__(f"{self.what} — {self.why} Fix: {self.fix}")


def _clip(text: str, limit: int = _MAX_DETAIL) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Toolchain:
    """A resolved, already-installed build toolchain.

    *root* is the directory whose ``node_modules`` the build symlinks into its
    workspace, which is how ``import 'react'`` resolves without a copy or an
    install.
    """

    root: Path
    esbuild: Path

    @property
    def node_modules(self) -> Path:
        return self.root / "node_modules"


@dataclass
class BuildResult:
    """What a successful build put on disk."""

    entry: str = ENTRY_HTML
    files: list[str] = field(default_factory=list)
    bundle_bytes: int = 0
    warnings: str = ""


def needs_build(kind: str) -> bool:
    """Whether deploying an artifact of *kind* has to build first."""
    return (kind or "").strip().lower() in BUILD_REQUIRED_KINDS


def toolchain_candidates() -> list[Path]:
    """The directories probed for a toolchain, in order.

    Three, deliberately: the explicit lever, the source checkout (where
    ``npm ci`` has already put every dependency the dashboard needs — the
    "reuse the existing frontend build tooling" the plan asks for), and a
    home-local toolchain a wheel-installed user can drop in without touching the
    package.
    """
    roots: list[Path] = []
    override = (os.environ.get(TOOLCHAIN_ROOT_ENV) or "").strip()
    if override:
        roots.append(Path(override).expanduser())
    # <repo>/src/personalclaw/artifacts/build.py → <repo>
    roots.append(Path(__file__).resolve().parents[3])
    roots.append(config_dir() / "artifacts" / ".toolchain")
    return roots


def _esbuild_in(root: Path) -> Path | None:
    for rel in _ESBUILD_RELPATHS:
        candidate = root / rel
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_toolchain() -> Toolchain:
    """Find an installed toolchain, or refuse legibly. NEVER installs anything.

    The refusal is the honest answer for a wheel install with no Node.js on the
    box: there is nothing to build with, and reaching the network to fix that is
    not this route's decision to make.
    """
    probed: list[str] = []
    for root in toolchain_candidates():
        probed.append(str(root))
        esbuild = _esbuild_in(root)
        if esbuild is None:
            continue
        if not all((root / rel).is_file() for rel in _RUNTIME_RELPATHS):
            continue
        return Toolchain(root=root, esbuild=esbuild)
    raise ArtifactBuildError(
        "no React build toolchain",
        "a react artifact is bundled with an already-installed esbuild plus react and "
        "react-dom, and none of the probed directories has all three in its node_modules: "
        f"{', '.join(probed)}. The build never installs anything, so it cannot fetch them.",
        f"run `npm ci` in a PersonalClaw source checkout, or install esbuild + react + "
        f"react-dom under a directory of your own and set {TOOLCHAIN_ROOT_ENV} to it.",
    )


def build_argv(toolchain: Toolchain, entry: Path, out_js: Path, metafile: Path) -> list[str]:
    """The one build command, owned entirely by core.

    The artifact contributes source at *entry* and nothing else — no build script,
    no plugin, no argument. That is what makes "the build cannot reach the
    network" a property of this function rather than a hope about a user's
    ``package.json``: ``esbuild --bundle`` resolves every import on disk, and no
    install verb appears here.

    Classic JSX (``React.createElement``) mirrors the in-chat harness's Babel
    ``preset-react`` default, so a body written for the preview bundles unchanged.

    ``--metafile`` is not diagnostics — it is the second half of the no-network
    property, and it exists because the first half is not enough. MEASURED with
    esbuild 0.25.12: a body containing ``import x from 'https://esm.sh/lodash'``
    builds with **rc 0 and no warning at any log level**, and the URL is left in
    the bundle as an external import. The metafile is the only place that says so
    (``external: true``), so :func:`_refuse_externals` reads it and turns a silent
    off-origin reference into a reported failure.
    """
    return [
        str(toolchain.esbuild),
        str(entry),
        "--bundle",
        f"--outfile={out_js}",
        f"--metafile={metafile}",
        "--format=iife",
        "--platform=browser",
        "--target=es2020",
        "--jsx=transform",
        "--jsx-factory=React.createElement",
        "--jsx-fragment=React.Fragment",
        '--define:process.env.NODE_ENV="production"',
        "--minify",
        "--log-level=warning",
        "--color=false",
    ]


def external_imports(metafile: Path) -> list[str]:
    """Every import the bundler left OUTSIDE the bundle, per its own metafile.

    An absent or unreadable metafile yields ``[]``: it is written by the bundler we
    just ran successfully, and a build must not fail because a diagnostics file was
    unreadable. The refusal below is a second gate over an already-successful
    build, not the build's only correctness check.
    """
    try:
        meta = json.loads(metafile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("react build metafile unreadable: %s", metafile, exc_info=True)
        return []
    if not isinstance(meta, dict):
        return []
    out: list[str] = []
    for spec in (meta.get("inputs") or {}).values():
        if not isinstance(spec, dict):
            continue
        for imp in spec.get("imports") or []:
            if isinstance(imp, dict) and imp.get("external"):
                out.append(str(imp.get("path", "?")))
    return out


def entry_module(source: str) -> str:
    """The module esbuild is pointed at: the runtime bindings, then the body, then the mount.

    The body is spliced into module scope rather than imported, because the kind's
    contract is "define a top-level ``App``" — there is no export to import. The
    globals are assigned as well as bound so a body reaching for ``window.React``
    (the preview contract) finds it.
    """
    return f"""\
import * as React from 'react';
import * as ReactDOMClient from 'react-dom/client';

globalThis.React = React;
globalThis.ReactDOM = ReactDOMClient;

// ── artifact source (verbatim) ──
{source}
// ── end artifact source ──

(function () {{
  var host = document.getElementById('root');
  function fail(err) {{
    var text = String((err && err.message) || err);
    var pre = document.createElement('pre');
    pre.className = 'artifact-build-error';
    pre.textContent = text;
    if (host) {{ host.textContent = ''; host.appendChild(pre); }}
  }}
  try {{
    var Comp = (typeof App !== 'undefined' && App) || globalThis.App || null;
    if (!Comp) {{
      throw new Error('No component found. Define a top-level function named App.');
    }}
    class Boundary extends React.Component {{
      constructor(p) {{ super(p); this.state = {{ err: null }}; }}
      static getDerivedStateFromError(err) {{ return {{ err: err }}; }}
      render() {{
        if (this.state.err) {{
          return React.createElement(
            'pre',
            {{ className: 'artifact-build-error' }},
            String(this.state.err.message || this.state.err),
          );
        }}
        return this.props.children;
      }}
    }}
    ReactDOMClient.createRoot(host).render(
      React.createElement(Boundary, null, React.createElement(Comp)),
    );
  }} catch (e) {{
    fail(e);
  }}
}})();
"""


def entry_document(title: str, *, with_css: bool) -> str:
    """The served ``index.html``.

    Every reference is relative to the artifact's own directory — there is no CDN
    URL anywhere in it, which is what lets the served page satisfy PEP-8's
    ``default-src 'none'`` / ``script-src 'self'`` fence with nothing blocked. The
    stylesheet link appears only when the build actually emitted CSS, so the page
    never requests a file that does not exist.
    """
    css_link = f'\n<link rel="stylesheet" href="./{BUNDLE_CSS}">' if with_css else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(title)}</title>{css_link}
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 16px;
    font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px; line-height: 1.5;
    -webkit-font-smoothing: antialiased;
  }}
  img, svg, canvas, video {{ max-width: 100%; height: auto; }}
  .artifact-build-error {{ white-space: pre-wrap; font-family: monospace; font-size: 13px; }}
</style>
</head>
<body>
<div id="root"></div>
<script src="./{BUNDLE_JS}" defer></script>
</body>
</html>
"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _kill_tree(proc: "asyncio.subprocess.Process") -> None:
    """SIGKILL the build's whole session, falling back to the leader alone.

    The group kill is the point: see :func:`_run_esbuild`'s note — killing only the
    leader leaves a grandchild holding the stdout pipe and the "timeout" becomes as
    long as the runaway build.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        return
    except (ProcessLookupError, PermissionError, OSError):
        # No such group (it already exited), or a platform that refuses the lookup.
        pass
    try:
        proc.kill()
    except ProcessLookupError:  # pragma: no cover - it exited as we killed it
        pass


async def _run_esbuild(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
) -> tuple[int, str]:
    """Spawn the bundler under the ``build`` resource ceiling. Returns ``(rc, output)``.

    The ONLY spawn in this module, and it goes through
    :func:`personalclaw.sandbox.create_subprocess_limited` with
    :data:`~personalclaw.sandbox.PROFILE_BUILD`: bundling arbitrary artifact
    source is precisely the agent-influenced build this profile exists for. The
    environment is the allowlisted child env (so the child inherits no proxy or
    credential variables), and a child that outlives *timeout* is killed and
    reported rather than awaited forever.

    ``start_new_session`` is what makes the timeout REAL, and it was measured: a
    bundler killed by pid leaves its own children holding the inherited stdout
    pipe, and ``Process.wait()`` does not return until that pipe closes — a 1s
    timeout over a ``sleep 30`` took 30s to raise. Its own session makes the whole
    tree killable in one :func:`os.killpg`, which took 1.06s for the same case.
    """
    from personalclaw.sandbox import create_subprocess_limited

    proc = await create_subprocess_limited(
        *argv,
        profile=PROFILE_BUILD,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        env=build_child_env(
            site="artifact-react-build",
            # NO_COLOR keeps escape codes out of the sentence a user reads; the two
            # npm variables make an accidental package-manager invocation offline
            # rather than merely unlikely.
            extra={"NO_COLOR": "1", "npm_config_offline": "true", "npm_config_audit": "false"},
        ),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_tree(proc)
        await proc.wait()
        raise ArtifactBuildError(
            "the React build timed out",
            f"the bundler was still running after {timeout:.0f}s and was stopped, so no "
            "bundle was produced.",
            "shrink the component or split it up, then deploy again; a build that needs "
            "longer than this is doing more than a widget should.",
        ) from None
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


async def build_react_artifact(
    *,
    slug: str,
    source: str,
    files_root: Path,
    title: str = "",
    timeout: float = BUILD_TIMEOUT_SECS,
) -> BuildResult:
    """Bundle *source* into static files under *files_root*. Raises :class:`ArtifactBuildError`.

    Build-once-serve-static: on success *files_root* holds an ``index.html`` and a
    ``bundle.js`` (plus ``bundle.css`` when the component imported styles) and
    nothing else changes. On ANY failure *files_root* is left exactly as it was —
    a half-built app is never published, and the caller gets a sentence saying
    why.
    """
    body = source or ""
    if not body.strip():
        raise ArtifactBuildError(
            "nothing to build",
            "this react artifact has an empty body, so there is no component to bundle.",
            "save some JSX defining a top-level `App` component, then deploy again.",
        )
    if len(body.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ArtifactBuildError(
            "the React source is too large to build",
            f"the body is over the {MAX_SOURCE_BYTES // 1024} KiB a deployable widget is "
            "allowed to be.",
            "split the component up, or move the data it embeds into a separate artifact.",
        )

    toolchain = resolve_toolchain()
    work = Path(tempfile.mkdtemp(prefix=f"pc-artifact-build-{slug}-"))
    try:
        entry = work / _ENTRY_MODULE
        entry.write_text(entry_module(body), encoding="utf-8")
        # Symlink rather than copy: node_modules is enormous, and the symlink is what
        # makes `import 'react'` resolve from the toolchain root with no install step.
        try:
            (work / "node_modules").symlink_to(toolchain.node_modules, target_is_directory=True)
        except OSError as exc:
            raise ArtifactBuildError(
                "could not prepare the React build",
                f"linking the toolchain's node_modules into the build directory failed "
                f"({exc.__class__.__name__}: {exc}).",
                "check that the toolchain directory is readable and that the temporary "
                "directory allows symlinks.",
            ) from exc

        out_js = work / "out" / BUNDLE_JS
        out_js.parent.mkdir(parents=True, exist_ok=True)
        metafile = work / "out" / "meta.json"
        argv = build_argv(toolchain, entry, out_js, metafile)
        rc, output = await _run_esbuild(argv, cwd=work, timeout=timeout)
        if rc != 0:
            logger.warning("react artifact build failed for %s (rc=%s): %s", slug, rc, output)
            raise ArtifactBuildError(
                "the React build failed",
                f"the bundler exited {rc}: {_bundler_tail(output)}",
                "fix the reported line in the artifact's JSX and deploy again; imports must "
                "resolve to react, react-dom, or the artifact's own files (a URL import "
                "cannot be bundled — the build has no network access).",
            )
        if not out_js.is_file():
            raise ArtifactBuildError(
                "the React build produced no bundle",
                f"the bundler exited 0 but wrote no {BUNDLE_JS}: {_bundler_tail(output)}",
                "deploy again; if it keeps happening, check the gateway log for the full "
                "bundler output.",
            )
        left_out = external_imports(metafile)
        if left_out:
            raise ArtifactBuildError(
                "the React build left an import unbundled",
                "the bundler could not include "
                f"{', '.join(sorted(set(left_out))[:3])} in the bundle, so the deployed page "
                "would have to fetch it at runtime — which the build has no network access "
                "for and the served page's CSP forbids.",
                "import only react, react-dom, or the artifact's own files; a URL import "
                "cannot be bundled, so inline what you need instead.",
            )
        js = out_js.read_bytes()
        if len(js) > MAX_BUNDLE_BYTES:
            raise ArtifactBuildError(
                "the React bundle is too large to serve",
                f"the build emitted {len(js) // 1024} KiB, over the "
                f"{MAX_BUNDLE_BYTES // 1024} KiB a deployed widget may be.",
                "drop the heaviest dependency or split the component up.",
            )
        out_css = out_js.with_name(BUNDLE_CSS)
        css = out_css.read_bytes() if out_css.is_file() else None
        return _publish(files_root, js, css, title=title or slug, warnings=output)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _bundler_tail(output: str) -> str:
    """The last useful lines of bundler output, or an honest admission of silence."""
    lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
    return _clip(" / ".join(lines[-4:])) if lines else "it printed nothing."


def _publish(
    files_root: Path,
    js: bytes,
    css: bytes | None,
    *,
    title: str,
    warnings: str,
) -> BuildResult:
    """Write the built files into the artifact's served directory.

    Reached only with a bundle in hand, which is what keeps a failed build from
    replacing a working deployment. Only the three built names are touched: a stale
    ``bundle.css`` from an earlier build is removed (otherwise the page would link
    a stylesheet the current component never asked for), and any other file the
    user put in ``webapp/`` survives.
    """
    files_root.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(files_root / BUNDLE_JS, js)
    if css is not None:
        atomic_write_bytes(files_root / BUNDLE_CSS, css)
    else:
        stale = files_root / BUNDLE_CSS
        if stale.is_file():
            stale.unlink()
    atomic_write(files_root / ENTRY_HTML, entry_document(title, with_css=css is not None))
    written = [ENTRY_HTML, BUNDLE_JS] + ([BUNDLE_CSS] if css is not None else [])
    return BuildResult(
        entry=ENTRY_HTML,
        files=written,
        bundle_bytes=len(js),
        warnings=_clip(warnings or ""),
    )
