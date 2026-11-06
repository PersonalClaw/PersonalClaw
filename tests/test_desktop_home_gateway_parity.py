"""The desktop app reaches its home's running gateway the way a command does — held to the Python.

``desktop/homeGateway.js`` is a Node port of ``home_gateway.reach`` and ``HomeGateway.sign_in``:
the runtime record, the identity read with its nonce, the home's id, the gateway's proof that it
holds the home's local secret, and the sign-in. Two implementations of one handshake are two things
to drift, and a drift here is silent: the app would refuse every real gateway (a proof it computes
differently never matches), or the app's start would stop reading the exit a served home gives.

So this reads the JS side's constants and the vectors ``desktop/test/homeGateway.test.js`` holds
its functions to, and checks each against what this side computes. Parsed from source rather than
executed, the ``test_desktop_install_kind.py`` precedent: node is not a test dependency here, and
the desktop suite executes the JS.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from personalclaw import gateway_base, home_gateway
from personalclaw.auth.lifetimes import DEFAULT_BROWSER_SESSION_TTL_SECS
from personalclaw.dashboard.token_auth import parse_duration

REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP = REPO_ROOT / "desktop"


def _source(relative: str) -> str:
    path = DESKTOP / relative
    assert path.is_file(), f"desktop/{relative} is missing"
    return path.read_text(encoding="utf-8")


def _js_string(source: str, name: str) -> str:
    match = re.search(rf'const {name} = "([^"]*)";', source)
    assert match, f'the JS declares no `const {name} = "…";`'
    return match.group(1)


def test_the_proof_is_made_over_the_same_context() -> None:
    assert _js_string(_source("homeGateway.js"), "PROOF_CONTEXT") == home_gateway.PROOF_CONTEXT


def test_the_app_reaches_the_gateway_at_the_address_a_command_does() -> None:
    assert _js_string(_source("homeGateway.js"), "LOOPBACK_HOST") == home_gateway.LOOPBACK_HOST


def test_the_app_reads_the_files_the_gateway_writes() -> None:
    source = _source("homeGateway.js")
    assert _js_string(source, "RUNTIME_FILE") == gateway_base.RUNTIME_FILE
    assert _js_string(source, "SECRET_FILE") == ".local_secret"


def test_the_app_signs_in_for_the_ready_lines_own_lifetime() -> None:
    """The ready line's session lasts ``auth.session_ttl``, 30 days unless it is set, so an app on a
    gateway it found lasts as long as one on the gateway it starts."""
    ttl = parse_duration(_js_string(_source("homeGateway.js"), "SIGN_IN_TTL"))
    assert ttl == DEFAULT_BROWSER_SESSION_TTL_SECS


def test_the_vectors_the_js_is_held_to_are_this_sides_answers() -> None:
    tests = _source("test/homeGateway.test.js")
    proofs = re.findall(
        r'\{ secret: "([^"]*)", nonce: "([0-9a-f]{32})", port: (\d+), proof: "([0-9a-f]{64})" \}',
        tests,
    )
    assert len(proofs) >= 2, "the JS test carries no proof vectors — this rail is now blind"
    for secret, nonce, port, proof in proofs:
        assert home_gateway.gateway_proof(secret, nonce, int(port)) == proof, (secret, nonce, port)
    ids = re.findall(r'\{ resolved: "([^"]+)", id: "([0-9a-f]{16})" \}', tests)
    assert ids, "the JS test carries no home id vectors — this rail is now blind"
    for resolved, id_ in ids:
        assert home_gateway.home_id(resolved) == id_
        assert hashlib.sha256(resolved.encode()).hexdigest()[:16] == id_


def test_the_app_reads_the_exit_a_served_home_gives() -> None:
    match = re.search(r"const HOME_SERVED_EXIT = (\d+);", _source("gatewayLost.js"))
    assert match, "desktop/gatewayLost.js declares no HOME_SERVED_EXIT"
    assert int(match.group(1)) == gateway_base.HOME_SERVED_EXIT


def test_the_app_reads_the_exit_and_the_stamp_a_newer_home_gives() -> None:
    """An older app's gateway, refused a home a newer PersonalClaw served, exits with a code of its
    own, and the row names the version the home's stamp holds."""
    match = re.search(r"const HOME_NEWER_EXIT = (\d+);", _source("gatewayLost.js"))
    assert match, "desktop/gatewayLost.js declares no HOME_NEWER_EXIT"
    assert int(match.group(1)) == gateway_base.HOME_NEWER_EXIT
    assert _js_string(_source("homeGateway.js"), "VERSION_FILE") == gateway_base.VERSION_FILE


def test_the_app_passes_no_rollback_on_to_its_gateway() -> None:
    """The one variable that lets an older version claim a newer home is the owner's word to one
    start; the app's own gateway never inherits it (``desktop/gatewayEnv.js``)."""
    match = re.search(
        r"const NOT_PASSED_ON = Object\.freeze\(\[([^\]]*)\]\);", _source("gatewayEnv.js")
    )
    assert match, "desktop/gatewayEnv.js declares no NOT_PASSED_ON"
    assert f'"{gateway_base.ROLL_BACK_ENV}"' in match.group(1)


def test_the_app_resolves_its_home_where_its_gateway_does(monkeypatch) -> None:
    """``appHome`` in ``desktop/gatewayEnv.js`` resolves ``PERSONALCLAW_HOME`` as the loader does,
    so the app reads its gateway's runtime record and local secret in the folder the gateway writes
    them to; a home kept as written would be a second folder, and the app's record watch would hold
    the windows' sign-in back for good. Held to the vectors ``desktop/test/gatewayEnv.test.js``
    checks ``appHome`` against. The process ``HOME`` is set too: ``~`` expands against it, not
    against the environment handed in."""
    from personalclaw.config import loader

    vectors = re.findall(
        r'\{ home: "([^"]*)", named: "([^"]*)", resolved: "([^"]*)" \}',
        _source("test/gatewayEnv.test.js"),
    )
    assert len(vectors) >= 5, "the JS test carries no home vectors — this rail is now blind"
    assert any(named.startswith("~") for _, named, _ in vectors), "no vector expands a ~"
    assert any(
        resolved.endswith("/.personalclaw") and named for _, named, resolved in vectors
    ), "no vector is a refused home"
    for home, named, resolved in vectors:
        monkeypatch.setenv("HOME", home)
        env = {"HOME": home, "PERSONALCLAW_HOME": named}
        assert loader.resolve_config_dir(env) == Path(resolved), (home, named)


def test_a_home_linked_into_a_system_tree_is_refused_once_resolved(tmp_path, monkeypatch) -> None:
    """The loader refuses a system tree on the resolved path, as ``appHome`` does
    (``desktop/test/gatewayEnv.test.js``: a link into ``/usr``)."""
    from personalclaw.config import loader

    (tmp_path / "into-usr").symlink_to("/usr")
    monkeypatch.setenv("HOME", "/h")
    env = {"HOME": "/h", "PERSONALCLAW_HOME": str(tmp_path / "into-usr" / "pc")}
    assert loader.resolve_config_dir(env) == Path("/h/.personalclaw")
    assert "into-usr" in _source("test/gatewayEnv.test.js"), "the JS side holds no such link"
