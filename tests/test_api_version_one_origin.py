"""PL-9 rail — the emitted, declared and negotiated API version share ONE origin.

Three surfaces name this number: the gateway EMITS it (``/api/manifest``'s
``apiVersion`` plus the two generated reference documents), the SPA DECLARES it
(``X-PersonalClaw-API-Version``), and the gate NEGOTIATES it. The failure this
rail exists to prevent is the cheap one: someone types the literal a second time —
in another module, in a TypeScript file, in a test — and the three quietly drift
until a client is refused for a version the server actually speaks, or waved
through for one it does not.

So the rail is not "the numbers happen to be equal today". It asserts there is
exactly ONE place each value is written down, and that every other surface reads
it from there. Adding a second hard-coded api-version literal anywhere under
``src/personalclaw`` or ``web/src`` reds this file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from personalclaw import api_version as av
from personalclaw import manifest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "personalclaw"
WEB = REPO / "web" / "src"

ORIGIN_PY = SRC / "api_version.py"
ORIGIN_TS = WEB / "lib" / "apiVersion.ts"
GATE_PY = SRC / "dashboard" / "api_version_gate.py"

#: A name is an api-version binding when it mentions "api version" in any casing
#: or separator style. Deliberately broad: the rail's job is to catch a *new*
#: spelling of the same number, and a near-miss name is exactly how one hides.
_NAME_RE = re.compile(r"api_?version", re.IGNORECASE)

#: The only two api-version names allowed to be bound to an integer literal, and
#: the only file allowed to bind them.
ALLOWED_PY_BINDINGS = {"API_VERSION", "MIN_SUPPORTED_API_VERSION"}
ALLOWED_TS_BINDINGS = {"CLIENT_API_VERSION"}


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _ts_files() -> list[Path]:
    return sorted(
        p for p in WEB.rglob("*") if p.suffix in {".ts", ".tsx"} and "node_modules" not in p.parts
    )


def _int_literal_bindings(tree: ast.AST) -> list[tuple[str, int]]:
    """Every ``NAME = <int>`` / ``NAME: T = <int>`` in a module, at any nesting."""
    out: list[tuple[str, int]] = []

    def _record(target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Constant):
            return
        if isinstance(value.value, bool) or not isinstance(value.value, int):
            return
        out.append((target.id, value.value))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                _record(t, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _record(node.target, node.value)
    return out


class TestSingleOriginPython:
    def test_only_api_version_py_binds_an_api_version_literal(self):
        offenders: list[str] = []
        for path in _py_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a syntax error is another test's red
                continue
            for name, value in _int_literal_bindings(tree):
                if not _NAME_RE.search(name):
                    continue
                if path == ORIGIN_PY and name in ALLOWED_PY_BINDINGS:
                    continue
                offenders.append(f"{path.relative_to(REPO)}: {name} = {value}")
        assert not offenders, (
            "an api-version integer literal is bound outside its one origin "
            f"({ORIGIN_PY.relative_to(REPO)}): " + "; ".join(offenders)
        )

    def test_the_origin_binds_both_window_ends_and_nothing_else(self):
        bound = {
            name
            for name, _ in _int_literal_bindings(ast.parse(ORIGIN_PY.read_text(encoding="utf-8")))
            if _NAME_RE.search(name)
        }
        assert bound == ALLOWED_PY_BINDINGS

    def test_manifest_re_exports_rather_than_re_declaring(self):
        # `from personalclaw.manifest import API_VERSION` must keep working (the
        # generated reference used it for years), but as a binding to the same
        # object — never a second literal.
        assert manifest.API_VERSION is av.API_VERSION


class TestSingleOriginTypeScript:
    def test_only_api_version_ts_binds_an_api_version_literal(self):
        # `=` covers `const CLIENT_API_VERSION = 1`; `:` covers an object-literal
        # `apiVersion: 1`. Neither matches the `apiVersion: number` type in the
        # `Manifest` interface, which carries no digit.
        pat = re.compile(r"([A-Za-z_$][\w$]*)\s*(?::\s*number)?\s*[:=]\s*(\d+)")
        offenders: list[str] = []
        for path in _ts_files():
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for name, value in pat.findall(line):
                    if not _NAME_RE.search(name):
                        continue
                    if path == ORIGIN_TS and name in ALLOWED_TS_BINDINGS:
                        continue
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}: {name} = {value}")
        assert not offenders, (
            "an api-version literal is bound outside the SPA's one declaration file "
            f"({ORIGIN_TS.relative_to(REPO)}); import CLIENT_API_VERSION instead: "
            + "; ".join(offenders)
        )

    def test_the_spa_declares_the_servers_number_and_header(self):
        text = ORIGIN_TS.read_text(encoding="utf-8")
        m = re.search(r"CLIENT_API_VERSION\s*=\s*(\d+)", text)
        assert m, "apiVersion.ts no longer declares CLIENT_API_VERSION"
        assert int(m.group(1)) == av.API_VERSION, (
            f"the SPA declares API version {m.group(1)} but the gateway speaks "
            f"{av.API_VERSION}; bump web/src/lib/apiVersion.ts in the same change"
        )
        h = re.search(r"API_VERSION_HEADER\s*=\s*'([^']+)'", text)
        assert h, "apiVersion.ts no longer declares API_VERSION_HEADER"
        assert h.group(1) == av.VERSION_HEADER, (
            f"the SPA declares its version in {h.group(1)!r} but the gate reads "
            f"{av.VERSION_HEADER!r} — the declaration would be silently ignored"
        )
        # The refusal CODE deliberately has no TypeScript mirror: `errText.ts`
        # already lifts `error.message` out of PL-8's envelope, so a `code`
        # constant here would be an export nothing imports.
        assert "API_VERSION_ERROR_CODE" not in text

    def test_the_api_client_actually_sends_the_declaration(self):
        # The SPA can declare the number in a module and still send nothing. The
        # shared header object every request helper spreads is the one place that
        # has to carry it; if this spread is dropped, the SPA is unversioned again
        # and the gate silently stops seeing it.
        api_ts = (WEB / "lib" / "api.ts").read_text(encoding="utf-8")
        assert "from './apiVersion'" in api_ts
        assert re.search(r"const SK = \{[^}]*\.\.\.apiVersionHeaders", api_ts), (
            "web/src/lib/api.ts's shared header object no longer spreads "
            "apiVersionHeaders — every request would go out undeclared"
        )


class TestSingleChokepoint:
    def test_negotiate_has_exactly_one_non_test_caller(self):
        callers: list[str] = []
        for path in _py_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    name = (
                        fn.id
                        if isinstance(fn, ast.Name)
                        else fn.attr if isinstance(fn, ast.Attribute) else ""
                    )
                    if name == "negotiate":
                        callers.append(str(path.relative_to(REPO)))
        assert callers == [str(GATE_PY.relative_to(REPO))], (
            "the version comparison must happen at ONE chokepoint; a second "
            f"comparison site is the defect this atom removed. Callers: {callers}"
        )

    def test_the_gate_is_installed_in_the_gateway_middleware_chain(self):
        # A gate that is importable but not installed ships inert — exactly how the
        # emitted constant shipped. The gateway's middleware list is written once,
        # explicitly, in server.py.
        server = (SRC / "dashboard" / "server.py").read_text(encoding="utf-8")
        chain = server.split("app.middlewares[:] = [", 1)
        assert len(chain) == 2, "server.py no longer declares one explicit middleware list"
        block = chain[1].split("]", 1)[0]
        assert "api_version_middleware()" in block, (
            "api_version_middleware() is not in the gateway's explicit middleware "
            "chain — the negotiation would never run"
        )

    def test_the_gate_holds_no_version_literal_of_its_own(self):
        bound = [
            name
            for name, _ in _int_literal_bindings(ast.parse(GATE_PY.read_text(encoding="utf-8")))
            if _NAME_RE.search(name)
        ]
        assert bound == []


class TestEmittedMatchesTheOrigin:
    @pytest.mark.asyncio
    async def test_manifest_emits_the_origin(self):
        doc = await manifest.build_manifest()
        assert doc["apiVersion"] == av.API_VERSION

    def test_generated_reference_docs_emit_the_origin(self):
        # The two checked-in reference documents are regenerated by
        # `python -m personalclaw.manifest_reference`; a bump that forgets to
        # regenerate them leaves a doc claiming the old version.
        for name in ("index.md", "tools.md"):
            text = (SRC / "reference" / name).read_text(encoding="utf-8")
            found = re.findall(r"manifest apiVersion (\d+)", text)
            assert found, f"reference/{name} no longer states the manifest apiVersion"
            assert {int(v) for v in found} == {av.API_VERSION}, (
                f"reference/{name} states apiVersion {found} but the origin is "
                f"{av.API_VERSION}; regenerate with "
                "`python -m personalclaw.manifest_reference`"
            )

    def test_the_wire_code_is_registered(self):
        from personalclaw.http_errors import HTTP_ERROR_CODES

        assert "api_version_unsupported" in HTTP_ERROR_CODES


class TestBumpRuleLivesInOnePlace:
    def test_the_origin_states_the_bump_rule(self):
        doc = av.__doc__ or ""
        assert "bump rule" in doc.lower()
        # Prose a person can apply: what bumps, and what deliberately does not.
        assert "Bump :data:`API_VERSION` when" in doc
        assert "Do NOT bump for" in doc

    def test_no_other_module_restates_it(self):
        # Two statements of a rule are two rules. Other modules may POINT at the
        # origin; none may re-derive what counts as a breaking wire change.
        offenders = []
        for path in _py_files():
            if path == ORIGIN_PY:
                continue
            text = path.read_text(encoding="utf-8")
            if "Do NOT bump for" in text or "Bump :data:`API_VERSION` when" in text:
                offenders.append(str(path.relative_to(REPO)))
        assert not offenders, f"the bump rule is restated outside its origin: {offenders}"
