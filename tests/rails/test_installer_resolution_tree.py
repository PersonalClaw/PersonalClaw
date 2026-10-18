"""The source-tree checks split from
``tests/test_installer_resolution.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from pathlib import Path

import installer_resolution_shared
import source_index

env = installer_resolution_shared.env


_SPAWN_FUNCS = {"run", "Popen", "call", "check_call", "check_output", "create_subprocess_exec"}


_INSTALLER_ENVS = ("installer_env(", "installer_cache_env(", "_pip_env(")


_INSTALLER_ARGV_FUNCS = {
    "install_argv",
    "checkout_install_argv",
    "prefix_install_argv",
    "env_install_argv",
}


def _callee(node) -> str:
    import ast

    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _runs_an_installer(call, installer_argvs: set[str]) -> bool:
    import ast

    args = list(call.args)
    if args and isinstance(args[0], (ast.List, ast.Tuple)):
        args = list(args[0].elts)
    if args and isinstance(args[0], ast.Starred):
        args = [args[0].value]
    if args and isinstance(args[0], ast.Name) and args[0].id in installer_argvs:
        return True
    words = [
        a.value if isinstance(a, ast.Constant) else getattr(a, "id", None)
        for a in args
        if isinstance(a, (ast.Constant, ast.Name))
    ]
    if words and words[0] in ("npm", "npx"):
        return True
    return any(words[i : i + 2] == ["-m", "pip"] for i in range(len(words)))


def installer_spawns() -> dict[tuple[str, str], bool]:
    """``(file, function) -> whether its spawns pass the installer environment``, each spawn
    attributed to the innermost function around it."""
    import ast

    import personalclaw

    src = Path(personalclaw.__file__).resolve().parent
    found: dict[tuple[str, str], bool] = {}
    for py, tree in source_index.package_trees():
        rel = py.relative_to(src).as_posix()
        if rel.startswith("skills/bundled/"):
            continue
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def enclosing(node):
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return node
            return tree

        def qualname(node) -> str:
            names = []
            while node is not tree:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(node.name)
                node = parents[node]
            return ".".join(reversed(names)) or "<module>"

        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or _callee(call) not in _SPAWN_FUNCS:
                continue
            fn = enclosing(call)
            argvs = {
                target.id
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and _callee(node.value) in _INSTALLER_ARGV_FUNCS
                for target in node.targets
                if isinstance(target, ast.Name)
            }
            if not _runs_an_installer(call, argvs):
                continue
            env = next((kw.value for kw in call.keywords if kw.arg == "env"), None)
            sources = [env] if env is not None else []
            if isinstance(env, ast.Name):  # `env=env`: what the function assigned it
                sources = [
                    node.value
                    for node in ast.walk(fn)
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == env.id for t in node.targets)
                ]
            ok = bool(sources) and all(
                any(tag in ast.unparse(value) for tag in _INSTALLER_ENVS) for value in sources
            )
            site = (rel, qualname(fn))
            found[site] = found.get(site, True) and ok
    return found


def test_every_install_or_build_personalclaw_runs_keeps_no_cache_outside_the_home():
    spawns = installer_spawns()
    missing = sorted(site for site, ok in spawns.items() if not ok)
    assert not missing, (
        "these run pip, uv or npm without `_installer.installer_env()` or "
        f"`installer_cache_env()`, so they fill the user's own caches: {missing}"
    )


def test_the_rail_sees_every_known_install_site():
    """The vacuity floor: a detector that stopped recognising a spawn would pass above."""
    assert {
        ("apps/app_python.py", "_pip_install"),
        ("cli_server.py", "_install"),
        ("gateway.py", "GatewayOrchestrator._check_missing_deps"),
        ("dashboard/handlers/updates.py", "_upgrade_wheel"),
        ("checkout_update.py", "_install"),
        ("frontend.py", "build_frontend_async"),
        ("acp/cli_resolve.py", "_npm_global_root"),
        ("acp/cli_resolve.py", "provision_acp_adapter"),
    } <= set(installer_spawns())
