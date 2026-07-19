"""P1: vendor-neutral ACP CLI launch-argv resolver.

Pins the resolution precedence (env override → PATH → node-manager globs → npx
fallback → None) and the ``.js → [node, script]`` shebang-dodge. The resolver is
CLI-agnostic: every test drives it with synthetic env-var/bin names so nothing
here knows about any specific vendor CLI.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from personalclaw.acp.cli_resolve import (
    is_npx_fallback,
    node_argv_for_script,
    provision_acp_adapter,
    resolve_acp_cli,
    resolve_node_ge,
)


def _make_exec(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_node_argv_for_script_prefixes_node_for_js():
    argv = node_argv_for_script("/some/where/adapter.js")
    assert len(argv) == 2
    assert argv[0].endswith("node") or argv[0] == "node"
    assert argv[1] == "/some/where/adapter.js"


def test_node_argv_for_script_passes_through_native():
    assert node_argv_for_script("/usr/local/bin/test-cli") == ["/usr/local/bin/test-cli"]


def test_env_override_single_path(monkeypatch, tmp_path):
    target = tmp_path / "my-acp"
    _make_exec(target)
    monkeypatch.setenv("MY_ACP_BIN", str(target))
    assert resolve_acp_cli(env_var="MY_ACP_BIN", bin_names=["unused"], npm_pkg="pkg") == [str(target)]


def test_env_override_js_path_gets_node(monkeypatch, tmp_path):
    target = tmp_path / "my-acp.js"
    target.write_text("// js entry\n")
    monkeypatch.setenv("MY_ACP_BIN", str(target))
    argv = resolve_acp_cli(env_var="MY_ACP_BIN", bin_names=["unused"], npm_pkg=None)
    assert argv is not None
    assert argv[0].endswith("node") or argv[0] == "node"
    assert argv[1] == str(target)


def test_env_override_full_argv_honoured_verbatim(monkeypatch):
    monkeypatch.setenv("MY_ACP_BIN", "node /opt/foo.js --flag value")
    assert resolve_acp_cli(env_var="MY_ACP_BIN", bin_names=["x"], npm_pkg=None) == [
        "node",
        "/opt/foo.js",
        "--flag",
        "value",
    ]


def test_path_lookup(monkeypatch, tmp_path):
    """A bin name present on the (fake) PATH resolves to it."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    target = bindir / "fake-cli-acp"
    _make_exec(target)
    monkeypatch.delenv("MY_ACP_BIN", raising=False)
    monkeypatch.setenv("PATH", str(bindir))
    argv = resolve_acp_cli(env_var="MY_ACP_BIN", bin_names=["fake-cli-acp"], npm_pkg=None)
    assert argv == [str(target)]


def test_npx_fallback_when_unresolved(monkeypatch):
    """Unresolvable bin + npm_pkg supplied → npx -y <pkg>."""
    monkeypatch.delenv("MY_ACP_BIN", raising=False)
    # Empty PATH so nothing (incl. node-manager globs that need real dirs) matches.
    monkeypatch.setenv("PATH", "")
    argv = resolve_acp_cli(
        env_var="MY_ACP_BIN", bin_names=["definitely-absent-xyz"], npm_pkg="@scope/pkg"
    )
    assert argv is not None
    assert argv[-3:] == ["npx", "-y", "@scope/pkg"] or argv[-2:] == ["-y", "@scope/pkg"]
    assert "@scope/pkg" in argv


def test_none_when_unresolved_and_no_pkg(monkeypatch):
    monkeypatch.delenv("MY_ACP_BIN", raising=False)
    monkeypatch.setenv("PATH", "")
    # Point HOME at an empty dir so node-manager globs find nothing.
    monkeypatch.setenv("HOME", os.devnull + "_nope")
    assert (
        resolve_acp_cli(env_var="MY_ACP_BIN", bin_names=["definitely-absent-xyz"], npm_pkg=None)
        is None
    )


def test_node_manager_glob(monkeypatch, tmp_path):
    """A bin installed under a fake ~/.nvm node version dir is resolved."""
    home = tmp_path / "home"
    nvm_bin = home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
    nvm_bin.mkdir(parents=True)
    target = nvm_bin / "globbed-acp"
    _make_exec(target)
    monkeypatch.delenv("MY_ACP_BIN", raising=False)
    monkeypatch.setenv("PATH", "")  # force the glob path
    monkeypatch.setenv("HOME", str(home))
    argv = resolve_acp_cli(env_var="MY_ACP_BIN", bin_names=["globbed-acp"], npm_pkg=None)
    assert argv == [str(target)]


# ── adapter provisioning helpers (durable codex-acp fix) ─────────────────────

def test_is_npx_fallback_detects_npx_argv():
    assert is_npx_fallback(["npx", "-y", "@scope/pkg"]) is True
    assert is_npx_fallback(["/opt/homebrew/bin/npx", "-y", "@scope/pkg"]) is True
    # A real on-disk adapter (steps 1-3) is NOT the fallback.
    assert is_npx_fallback(["/Users/x/.nvm/versions/node/v20/bin/codex-acp"]) is False
    assert is_npx_fallback(["node", "/some/adapter.js"]) is False
    assert is_npx_fallback(None) is False
    assert is_npx_fallback([]) is False


def test_resolve_node_ge_picks_new_enough(monkeypatch, tmp_path):
    """A Node whose --version reports >= min_major is returned; an older one isn't."""
    home = tmp_path / "home"
    # A fake nvm node that prints v22 (>= 20).
    new_bin = home / ".nvm" / "versions" / "node" / "v22.1.0" / "bin"
    new_bin.mkdir(parents=True)
    node = new_bin / "node"
    node.write_text("#!/bin/sh\necho v22.1.0\n")
    node.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")  # force the glob path
    # Isolate from the HOST's real node: _node_manager_bin_globs() hardcodes common
    # global prefixes (/opt/homebrew/bin, /usr/local/bin) that HOME can't override,
    # so on a machine with homebrew node the resolver would return that instead of
    # our fake. Pin the glob set to ONLY the fake nvm bin dir.
    import personalclaw.acp.cli_resolve as cli_resolve
    monkeypatch.setattr(cli_resolve, "_node_manager_bin_globs", lambda: [str(new_bin)])
    assert resolve_node_ge(min_major=20) == str(node)
    # A higher bar than the installed version → None.
    assert resolve_node_ge(min_major=99) is None


def test_provision_disabled_returns_none(monkeypatch, tmp_path):
    """PERSONALCLAW_ACP_NO_PROVISION=1 → never installs (returns None when absent)."""
    monkeypatch.setenv("PERSONALCLAW_ACP_NO_PROVISION", "1")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert provision_acp_adapter("@scope/never-installed-xyz", ["never-installed-xyz"]) is None
