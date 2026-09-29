"""The agent's shell, and every path guard, refuses the sign-in another tool keeps.

Measured through the Tools page's Try it on the shipped container image: `head -c 30
~/.codex/auth.json` returned the start of Codex's login file, while `~/.aws/*` and `~/.ssh/*` were
refused. The onboarding importer had always treated that file as a credential and never opened it;
nothing the agent reaches knew. The same held for every agent CLI and tool PersonalClaw works with:
Claude Code's login, Gemini CLI's Google sign-in and the API key it reads from its `.env`, the
GitHub and GitLab CLIs' tokens, Hugging Face's.

Each tool keeps its sign-in in a folder of its own that a variable can move, and each is found here
the way the tool finds it (checked against each tool's own source). The default folder stays refused
beside a moved one, since a login can still sit there.
"""

from __future__ import annotations

import pytest

from personalclaw.hooks import validate_file_path
from personalclaw.security import is_sensitive_bash_command, is_sensitive_path

CREDENTIAL = "Blocked: command accesses sensitive credential path"

#: Each tool's sign-in, where it keeps it when no variable moves its folder.
DEFAULT_SIGN_INS = (
    ".codex/auth.json",
    ".codex/.credentials.json",
    ".claude/.credentials.json",
    ".config/claude/.credentials.json",
    ".gemini/oauth_creds.json",
    ".gemini/.env",
    ".gemini/mcp-oauth-tokens.json",
    ".gemini/a2a-oauth-tokens.json",
    ".cache/.gemini/mcp-oauth-tokens.json",
    ".config/gh/hosts.yml",
    ".config/glab-cli/config.yml",
    "Library/Application Support/glab-cli/config.yml",
    ".cache/huggingface/token",
    ".cache/huggingface/stored_tokens",
)

_MOVERS = (
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "GEMINI_CLI_HOME",
    "GH_CONFIG_DIR",
    "GLAB_CONFIG_DIR",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "HF_HOME",
    "HF_TOKEN_PATH",
)


@pytest.fixture
def user(tmp_path, monkeypatch):
    home = tmp_path / "user"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "data"))
    for name in _MOVERS:
        monkeypatch.delenv(name, raising=False)
    return home


def test_the_measured_read_of_codexs_login_is_refused(user):
    (user / ".codex").mkdir()
    (user / ".codex" / "auth.json").write_text('{ "OPENAI_API_KEY": null }', encoding="utf-8")
    assert is_sensitive_bash_command("head -c 30 ~/.codex/auth.json") == CREDENTIAL


@pytest.mark.parametrize("sign_in", DEFAULT_SIGN_INS)
def test_every_tools_sign_in_is_refused_by_the_shell_and_the_file_guard(sign_in, user):
    path = user / sign_in
    assert is_sensitive_bash_command(f"cat '{path}'") == CREDENTIAL, sign_in
    assert is_sensitive_bash_command(f"cat ~/'{sign_in}'") == CREDENTIAL, sign_in
    assert is_sensitive_path(str(path)), sign_in
    assert validate_file_path(str(path)) is None, "the dashboard's and file tools' reads refuse it"


@pytest.mark.parametrize(
    ("variable", "folder", "file"),
    [
        ("CODEX_HOME", "codex-home", "auth.json"),
        ("CLAUDE_CONFIG_DIR", "claude-config", ".credentials.json"),
        ("GEMINI_CLI_HOME", "gemini-home", ".gemini/oauth_creds.json"),
        ("GEMINI_CLI_HOME", "gemini-home", ".gemini/.env"),
        ("GH_CONFIG_DIR", "gh-config", "hosts.yml"),
        ("GLAB_CONFIG_DIR", "glab-config", "config.yml"),
        ("XDG_CONFIG_HOME", "xdg-config", "gh/hosts.yml"),
        ("XDG_CONFIG_HOME", "xdg-config", "glab-cli/config.yml"),
        ("HF_HOME", "hf-home", "token"),
        ("HF_HOME", "hf-home", "stored_tokens"),
        ("XDG_CACHE_HOME", "xdg-cache", "huggingface/token"),
    ],
)
def test_a_moved_sign_in_is_found_where_its_tool_looks(
    variable, folder, file, user, tmp_path, monkeypatch
):
    moved = tmp_path / folder
    monkeypatch.setenv(variable, str(moved))
    assert is_sensitive_bash_command(f"cat {moved / file}") == CREDENTIAL
    assert is_sensitive_path(str(moved / file))


def test_an_explicit_hugging_face_token_file_is_refused(user, tmp_path, monkeypatch):
    token = tmp_path / "somewhere" / "hf.token"
    monkeypatch.setenv("HF_TOKEN_PATH", str(token))
    assert is_sensitive_bash_command(f"cat {token}") == CREDENTIAL


def test_the_default_folder_stays_refused_beside_a_moved_one(user, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    assert is_sensitive_bash_command("cat ~/.codex/auth.json") == CREDENTIAL


def test_a_claude_code_config_folder_inside_the_home_is_covered(user, tmp_path):
    """The Claude Code app runs its sessions with a config folder of their own inside the home, so
    the file Claude Code signs in from sits there: refused by its name."""
    inside = tmp_path / "data" / "cc-config" / ".credentials.json"
    assert is_sensitive_bash_command(f"cat {inside}") == CREDENTIAL
    assert is_sensitive_bash_command(
        "cat ../cc-config/.credentials.json", cwd=tmp_path / "data" / "workspace"
    )


def test_a_sign_in_a_provider_app_declares_is_refused(user, monkeypatch):
    """A subscription provider app declares where its CLI keeps the sign-in it rides; the agent may
    not read what PersonalClaw itself reads only once the owner allows it."""
    from personalclaw.llm import subscription_credentials as sc

    source = sc.SubscriptionSource(
        id="example-cli",
        login_hint="sign in with `example login` first",
        credential_files=("~/.example-cli/session.json",),
        token_path=("token",),
    )
    monkeypatch.setattr(sc, "_SOURCES", {"example-cli": source})
    assert is_sensitive_bash_command("cat ~/.example-cli/session.json") == CREDENTIAL
    assert is_sensitive_path(str(user / ".example-cli" / "session.json"))


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.codex/config.toml",
        "ls -la ~/.codex",
        "cat ~/.claude/settings.json",
        "cat ~/.gemini/settings.json",
        "cat ~/.config/gh/config.yml",
        "cat ./auth.json",
        "cat src/i18n/en/auth.json",
        "cat inventory/hosts.yml",
        "cat deploy/config.yml",
        "cat ~/.cache/huggingface/hub/models--x/config.json",
        "gh auth status",
        "codex login status",
        "ssh -i ~/.ssh/id_ed25519 example.com",
    ],
)
def test_ordinary_work_with_those_tools_passes(command, user, tmp_path):
    """The names that say nothing on their own (`auth.json`, `hosts.yml`, `config.yml`) are refused
    only where their tool keeps them; the rest of each tool's folder stays readable."""
    assert is_sensitive_bash_command(command, cwd=tmp_path / "project") is None, command
