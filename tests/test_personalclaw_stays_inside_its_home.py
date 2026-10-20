"""PersonalClaw reads and writes inside its home, and anywhere else only where the owner allowed.

Vision tenet 3: all data lives under one PersonalClaw home. Measured on ``main`` before this change,
with ``HOME`` pointed at a scratch folder:

* ``~/.agents/skills`` was a skill root on every read AND the default install target, and deleting
  a skill ``rmtree``'d the first folder of that name it found, which could be the owner's own
  skill in ``~/.agents/skills``;
* a session restart and every MCP sync merged PersonalClaw's servers into ``~/.mcp.json``;
* the workspace defaulted to ``~/workplace/personalclaw-workspace``, created on first use;
* a subscription provider read another CLI's sign-in file (``~/.claude/.credentials.json``) with
  nobody having said it may;
* the Hugging Face token cascade read ``~/.cache/huggingface/token``;
* the Files page offered PersonalClaw's own data folder (``config.json``, ``mcp.json``…) as an
  editable root.

Now a place outside the home is declared once in :mod:`personalclaw.outside_home`, is off until the
owner allows it in Settings → Security, and is read-only there. A rail below fails on new code that
names a location in the real home anywhere else.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from personalclaw_stays_inside_its_home_shared import _sites


@pytest.fixture
def real_home(tmp_path, monkeypatch) -> Path:
    """The user's HOME as PersonalClaw sees it: a scratch folder, so nothing here is the owner's."""
    home = tmp_path / "real-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("HF_HOME", "HF_TOKEN_PATH", "XDG_CACHE_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)
    return home


def _home() -> Path:
    from personalclaw.config.loader import config_dir

    return config_dir()


def _allow(*place_ids: str) -> None:
    """What the Settings toggle writes: the allowed places, in config.json."""
    path = _home() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    data.setdefault("security", {})["outside_home"] = list(place_ids)
    path.write_text(json.dumps(data), encoding="utf-8")


def _skill(root: Path, name: str, *, installed_by_personalclaw: bool = False) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} helper\n---\n\n# {name}\n", encoding="utf-8"
    )
    if installed_by_personalclaw:
        (d / ".pclaw-lock.json").write_text(json.dumps({"source": "skills.sh"}), encoding="utf-8")
    return d


# ── the rail: a location in the real home is named in one place ──────────────────────────────


@pytest.mark.parametrize(
    "code, caught",
    [
        ("from pathlib import Path\nx = Path.home() / '.x'", True),
        ("import os\nx = os.path.expanduser('~/.x')", True),
        ("from pathlib import Path\nx = Path('~/.x').expanduser()", True),
        ("import os\nx = os.environ['HOME']", True),
        ("import os\nx = os.environ.get('HOME', '/')", True),
        ("import os\nx = os.getenv('HOME')", True),
        ("import os\nx = os.path.expanduser(user_input)", False),
        ("from pathlib import Path\nx = Path(user_input).expanduser()", False),
        ("x = 'Path.home() in a string'", False),
        ("import os\nx = os.environ.get('HOMEBREW_PREFIX')", False),
    ],
)
def test_the_rail_sees_each_way_to_name_the_home(code, caught):
    assert bool(_sites(ast.parse(code))) is caught


# ── (a) ~/.agents/skills: not read until allowed, never written, never deleted from ──────────


def test_a_skill_in_the_shared_agents_folder_is_not_read_until_allowed(real_home):
    from personalclaw.skills.loader import SkillsLoader
    from personalclaw.skills.marketplace import skill_discovery_paths

    shared = real_home / ".agents" / "skills"
    _skill(shared, "shared-one")
    assert "shared-one" not in {s["name"] for s in SkillsLoader().list_skills()}
    assert shared not in skill_discovery_paths()

    _allow("agent-skills")
    assert "shared-one" in {s["name"] for s in SkillsLoader().list_skills()}
    assert shared in skill_discovery_paths()


def test_what_a_session_touched_in_the_shared_folder_is_attributed_only_when_allowed(real_home):
    from personalclaw.inbound.capture_store import _skill_roots

    shared = real_home / ".agents" / "skills"
    shared.mkdir(parents=True)
    assert shared not in _skill_roots()
    _allow("agent-skills")
    assert shared in _skill_roots()


class _Market:
    """A marketplace that serves one benign skill."""

    def __init__(self):
        from personalclaw.skills.marketplace import SkillDetail, SkillsMarketplace

        class _M(SkillsMarketplace):
            def search(self, query, limit=20):
                return []

            def fetch(self, skill_id):
                body = f"---\nname: {skill_id}\ndescription: helps\n---\n\n# {skill_id}\n"
                return SkillDetail(
                    id=skill_id, name=skill_id, files=[{"path": "SKILL.md", "contents": body}]
                )

            @property
            def marketplace_type(self):
                return "fake"

            @property
            def trust_tier(self):
                return "community"

        self.cls = _M


def _json_request(body: dict | None = None, match: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.match_info = match or {}
    req.rel_url.query = {}
    req.get = lambda *_a, **_k: "owner"
    req.json = AsyncMock(return_value=body or {})
    return req


@pytest.mark.asyncio
async def test_a_skill_installs_into_the_home(real_home, monkeypatch):
    from personalclaw.dashboard.handlers.skills import api_skills_install
    from personalclaw.skills import marketplace

    registry = marketplace.SkillsRegistry()
    registry.register("fake", _Market().cls())
    monkeypatch.setattr(marketplace, "get_default_skills_registry", lambda: registry)

    resp = await api_skills_install(_json_request({"id": "helper", "marketplace": "fake"}))
    assert resp.status == 201, resp.body
    assert (_home() / "skills" / "helper" / "SKILL.md").is_file()
    assert not (real_home / ".agents").exists()


@pytest.mark.asyncio
async def test_deleting_a_skill_never_touches_one_outside_the_home(real_home):
    from personalclaw.dashboard.handlers.skills import api_skills_delete

    theirs = _skill(real_home / ".agents" / "skills", "their-skill")
    _allow("agent-skills")
    resp = await api_skills_delete(_json_request(match={"name": "their-skill"}))
    assert resp.status == 409, resp.body
    assert "outside PersonalClaw's home" in json.loads(resp.body)["error"]
    assert (theirs / "SKILL.md").is_file()

    mine = _skill(_home() / "skills", "their-skill")
    resp = await api_skills_delete(_json_request(match={"name": "their-skill"}))
    assert resp.status == 200, resp.body
    assert not mine.exists() and (theirs / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_an_allowed_shared_skill_is_listed_read_only_and_never_changed(real_home):
    from personalclaw.dashboard.handlers.skills import api_skills_list
    from personalclaw.skills.loader import SkillsLoader

    theirs = _skill(real_home / ".agents" / "skills", "theirs")
    before = (theirs / "SKILL.md").read_text(encoding="utf-8")
    _allow("agent-skills")

    resp = await api_skills_list(_json_request())
    rows = {r["key"]: r for r in json.loads(resp.body)}
    assert rows["theirs"]["source"] == "shared" and rows["theirs"]["type"] == "read-only"

    loader = SkillsLoader()
    assert loader.load_skill("theirs") is not None
    assert loader.update_skill("theirs", before.replace("helper", "rewritten")) is False
    assert loader.delete_skill("theirs") is False
    assert (theirs / "SKILL.md").read_text(encoding="utf-8") == before


def test_skills_personalclaw_installed_in_the_shared_folder_come_home_once(real_home):
    from personalclaw.outside_home import settle_previous_locations

    shared = real_home / ".agents" / "skills"
    installed = _skill(shared, "installed-here", installed_by_personalclaw=True)
    owners = _skill(shared, "owners-own")
    before = sorted(p.relative_to(shared).as_posix() for p in shared.rglob("*"))

    settle_previous_locations()

    home_skills = _home() / "skills"
    assert (home_skills / "installed-here" / "SKILL.md").is_file()
    assert (home_skills / "installed-here" / ".pclaw-lock.json").is_file()
    assert not (home_skills / "owners-own").exists()
    assert sorted(p.relative_to(shared).as_posix() for p in shared.rglob("*")) == before
    assert installed.is_dir() and owners.is_dir()

    # Once: a later PersonalClaw-shaped folder there is the owner's business, not ours.
    _skill(shared, "later", installed_by_personalclaw=True)
    settle_previous_locations()
    assert not (home_skills / "later").exists()


# ── (b) ~/.mcp.json is not written ─────────────────────────────────────────────────────────


def _server_in_home() -> None:
    (_home() / "mcp.json").write_text(
        json.dumps({"mcpServers": {"notes": {"command": "notes-mcp", "args": []}}}),
        encoding="utf-8",
    )


def _restart_request():
    state = MagicMock()
    state.sessions = MagicMock()
    state.sessions._lock = asyncio.Lock()
    state.sessions._sessions = {}
    request = MagicMock(spec=web.Request)
    request.app = {"state": state}
    return request


@pytest.mark.asyncio
async def test_a_session_restart_leaves_the_users_mcp_json_alone(real_home):
    from personalclaw.dashboard.handlers.sessions import api_sessions_restart

    _server_in_home()
    with patch(
        "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
        new_callable=AsyncMock,
        return_value=0,
    ):
        await api_sessions_restart(_restart_request())
    assert not (real_home / ".mcp.json").exists()


@pytest.mark.asyncio
async def test_an_mcp_sync_leaves_the_users_mcp_json_alone(real_home):
    from personalclaw.dashboard.handlers.mcp import api_mcp_sync

    _server_in_home()
    with patch(
        "personalclaw.dashboard.handlers.sessions._reset_all_sessions",
        new_callable=AsyncMock,
        return_value=0,
    ):
        await api_mcp_sync(_restart_request())
    assert not (real_home / ".mcp.json").exists()


# ── (c) the workspace is in the home ────────────────────────────────────────────────────────


def test_the_workspace_defaults_to_the_homes_own_workspace_folder(real_home):
    from personalclaw.config.loader import default_workspace_dir, workspace_root

    assert workspace_root() == _home() / "workspace"
    assert default_workspace_dir() == str((_home() / "workspace").resolve())
    assert not (real_home / "workplace").exists()


def test_an_old_default_workspace_pointer_is_dropped_once_and_a_chosen_one_kept(real_home):
    from personalclaw.config.loader import workspace_root
    from personalclaw.outside_home import settle_previous_locations

    pointer = _home() / "workspace_dir"
    pointer.write_text(str(real_home / "workplace" / "personalclaw-workspace") + "\n", "utf-8")
    settle_previous_locations()
    assert not pointer.exists()
    assert workspace_root() == _home() / "workspace"

    # Chosen again after the move, it sticks: the old default is only let go of once.
    pointer.write_text(str(real_home / "workplace" / "personalclaw-workspace") + "\n", "utf-8")
    settle_previous_locations()
    assert pointer.exists()

    chosen = real_home / "projects"
    pointer.write_text(str(chosen) + "\n", "utf-8")
    assert workspace_root() == chosen


def test_setup_keeps_the_default_without_writing_a_pointer(real_home, monkeypatch):
    from personalclaw import cli_setup

    monkeypatch.setattr(cli_setup, "_ask", lambda prompt: "")
    cli_setup._setup_workspace_dir()
    assert not (_home() / "workspace_dir").exists()
    assert not (real_home / "workplace").exists()


# ── (d) another CLI's sign-in is read only once allowed ─────────────────────────────────────


def test_a_subscription_sign_in_is_not_read_until_allowed(real_home):
    from personalclaw.llm import subscription_credentials as sub

    creds = real_home / ".example" / ".credentials.json"
    creds.parent.mkdir(parents=True)
    creds.write_text(json.dumps({"oauth": {"accessToken": "tok-123"}}), encoding="utf-8")
    source = sub.SubscriptionSource(
        id="example-cli",
        login_hint="sign in with `example login` first",
        credential_files=("~/.example/.credentials.json",),
        token_path=("oauth", "accessToken"),
    )
    sub.register_subscription_source(source)
    try:
        auth = sub.resolve_subscription_credential("example-cli")
        assert not auth.logged_in and auth.secret == ""
        assert "Settings → Security" in auth.reason, auth.reason

        _allow("sign-in:example-cli")
        auth = sub.resolve_subscription_credential("example-cli")
        assert auth.logged_in and auth.secret == "tok-123"
    finally:
        sub._SOURCES.pop("example-cli", None)


# ── (f) the machine-wide Hugging Face folder ────────────────────────────────────────────────


def test_the_hugging_face_cli_sign_in_is_not_read_until_allowed(real_home, monkeypatch):
    from personalclaw.local_models import hf_token

    token_file = real_home / ".cache" / "huggingface" / "token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("fake-hf-token-machine_wide_token_1234", encoding="utf-8")
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert hf_token._read_hf_cli_file() == ""
    _allow("huggingface-cache")
    assert hf_token._read_hf_cli_file() == "fake-hf-token-machine_wide_token_1234"


def test_an_app_gets_the_hugging_face_folder_read_only_and_only_once_allowed(real_home):
    """``sdk.util.outside_home_path`` is how an installable app reaches the folder.

    Nothing in this repo called it, so no test showed that an app is told ``None`` until the owner
    allows the place, or that the path it then gets refuses every write, through the folder and
    through any child of it.
    """
    from personalclaw.sdk.util import outside_home_path

    model = real_home / ".cache" / "huggingface" / "hub" / "model.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"weights")

    assert outside_home_path("huggingface-cache") is None

    _allow("huggingface-cache")
    folder = outside_home_path("huggingface-cache")
    assert folder == real_home / ".cache" / "huggingface"
    assert (folder / "hub" / "model.bin").read_bytes() == b"weights"
    with pytest.raises(PermissionError):
        (folder / "hub" / "model.bin").write_bytes(b"replaced")
    with pytest.raises(PermissionError):
        (folder / "hub" / "model.bin").open("wb")
    with pytest.raises(PermissionError):
        (folder / "hub" / "model.bin").unlink()
    with pytest.raises(PermissionError):
        (folder / "downloads").mkdir()
    assert model.read_bytes() == b"weights"
    assert not (real_home / ".cache" / "huggingface" / "downloads").exists()
    assert outside_home_path("no-such-place") is None


# ── PersonalClaw's data folder is not an editable Files root ────────────────────────────────


def test_the_files_page_does_not_offer_personalclaws_data_folder(real_home):
    from personalclaw.dashboard.handlers.files import _dashboard_roots, _validate_dashboard_path

    home = str(_home().resolve())
    roots = dict((rp, label) for label, rp in _dashboard_roots())
    assert home not in roots, roots
    (_home() / "config.json").write_text("{}", encoding="utf-8")
    assert _validate_dashboard_path(str(_home() / "config.json")) is None
    workspace = _home() / "workspace"
    workspace.mkdir(exist_ok=True)
    notes = workspace / "notes.md"
    assert _validate_dashboard_path(str(notes)) == os.path.realpath(notes)


# ── the Settings surface ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settings_lists_each_place_and_whether_it_is_allowed(real_home):
    from personalclaw.dashboard.handlers.core import api_security_outside_home

    _allow("agent-skills")
    resp = await api_security_outside_home(make_mocked_request("GET", "/"))
    places = {p["id"]: p for p in json.loads(resp.body)["places"]}
    assert places["agent-skills"]["allowed"] is True
    assert places["agent-skills"]["paths"] == [str(real_home / ".agents" / "skills")]
    assert places["huggingface-cache"]["allowed"] is False
    assert places["huggingface-cache"]["paths"] == [str(real_home / ".cache" / "huggingface")]


def test_allowing_a_place_is_a_loosening_the_owner_confirms():
    from personalclaw.config.editable import _EDITABLE_CONFIG

    spec = _EDITABLE_CONFIG["security.outside_home"]
    assert spec["type"] == "str_list"
    assert spec["security"].loosens([], ["agent-skills"])
    assert not spec["security"].loosens(["agent-skills"], [])
