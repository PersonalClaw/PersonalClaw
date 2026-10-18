"""The source-tree checks split from
``tests/test_personalclaw_stays_inside_its_home.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import source_index
from personalclaw_stays_inside_its_home_shared import _sites

import personalclaw

SRC = Path(personalclaw.__file__).resolve().parent


_SERVICE = "`personalclaw service install` writes where the OS looks for it"


_CLAUDE_CODE = "onboarding_import/sources/claude_code.py"


_CODEX = "onboarding_import/sources/codex.py"


_IMPORT_COMMON = "onboarding_import/sources/common.py"


_IMPORT = "Bring your setup over: reads another tool's own files, to import from them"


#: Every function outside :mod:`personalclaw.outside_home` that names a location in the user's real
#: home, and why that is not PersonalClaw keeping its own data there. A new entry needs a reason a
#: reviewer would accept; PersonalClaw storing or reading its own things is never one of them.
ALLOWED: dict[tuple[str, str], str] = {
    ("config/loader.py", "default_config_dir"): "the home itself is ~/.personalclaw by default",
    ("security.py", "_build_sensitive_regex"): "a guard: knows what under HOME to refuse",
    ("security.py", "SensitivePaths.__init__"): "a guard: knows what under HOME to refuse",
    ("security.py", "_credential_folders"): "a guard: knows what under HOME to refuse",
    ("security.py", "system_subtrees"): "a guard: the running account's own home is exempt",
    ("sandbox.py", "_build_launcher_script"): "the OS sandbox profile confines the home",
    ("sandbox.py", "_build_seatbelt_profile"): "the OS sandbox profile confines the home",
    ("sandbox.py", "_owners_own_fence"): "the OS sandbox profile denies writes to what runs as her",
    ("owner_only.py", "owners_own"): "a guard: knows what under HOME runs as the owner, to refuse",
    ("sandbox_providers/lima.py", "_host_mount"): "the sandbox VM's host mount, a sandbox setting",
    ("loop/validation.py", "workspace_write_target_errors"): "a guard: HOME is no workspace",
    ("write_scope.py", "problem"): "a guard: the home folder itself is no file a job changes",
    ("agent.py", "_apply_user_agent_hooks"): "a guard: a configured hooks folder must sit in HOME",
    ("command_paths.py", "named_paths"): "a guard: reads ~ and $HOME in a command as a shell does",
    ("run_bounds.py", "_expand_leading"): "a guard: reads $HOME in a path a command writes",
    ("protected_folders.py", "home_folders"): "a guard: the home folder a delete always asks about",
    ("protected_folders.py", "_expanded"): "a guard: reads ~ and $HOME in a path a delete removes",
    ("home_paths.py", "home"): "writes a path in the home from ~, as the agent and owner read it",
    ("acp/cli_resolve.py", "_node_manager_bin_globs"): "finds an agent CLI the owner installed",
    ("env.py", "augmented_path"): "finds MCP server binaries the owner installed",
    ("ffmpeg_binary.py", "_folders"): "finds ffmpeg where the owner installed it",
    ("service/macos.py", "<module>"): _SERVICE,
    ("service/macos.py", "render_plist"): _SERVICE,
    ("service/linux.py", "render_unit"): _SERVICE,
    (_CLAUDE_CODE, "resolve_root"): _IMPORT,
    (_CLAUDE_CODE, "global_config_path"): _IMPORT,
    (_CLAUDE_CODE, "config_dir_of"): _IMPORT,
    (_CODEX, "resolve_root"): _IMPORT,
    (_CODEX, "_shared_skills"): _IMPORT,
    (_IMPORT_COMMON, "on_this_machine"): _IMPORT,
    ("packs/external_formats.py", "default_dest_dir"): "an export the owner starts",
    ("dashboard/handlers/files.py", "_default_browse_dir"): "the folder picker, the owner choosing",
    ("dashboard/handlers/terminal.py", "default_terminal_cwd"): "the owner's own terminal",
}


#: The one module that names places outside the home for PersonalClaw's own use.
HELPER = "outside_home.py"


def home_sites() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for py, tree in source_index.package_trees():
        rel = py.relative_to(SRC).as_posix()
        if rel.startswith("skills/bundled/"):
            continue
        for scope in _sites(tree):
            out.add((rel, scope))
    return out


def test_only_the_helper_names_a_place_in_the_real_home_for_personalclaw():
    sites = home_sites()
    stray = sorted(s for s in sites if s not in ALLOWED and s[0] != HELPER)
    assert not stray, (
        "these name a location in the user's real home. PersonalClaw's own data lives in "
        "config_dir(); a place outside it is declared in personalclaw/outside_home.py, where the "
        f"owner allows it. Anything else needs an ALLOWED entry with its reason: {stray}"
    )


def test_every_allowed_site_still_exists_and_the_helper_is_where_the_places_are():
    sites = home_sites()
    assert sorted(set(ALLOWED) - sites) == [], "an ALLOWED entry names nothing now; remove it"
    assert any(path == HELPER for path, _ in sites), "the helper names no place: the rail is blind"
