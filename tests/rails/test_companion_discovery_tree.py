"""The source-tree checks split from
``tests/test_companion_discovery.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import companion_discovery_shared

_isolate = companion_discovery_shared._isolate


def test_pairing_cannot_depend_on_discovery():
    """Structural: the auth layer must not import discovery, so it can never gate pairing.

    "Degradable" is only true if it cannot be made untrue by a later edit. If enrollment
    ever imported this module, turning discovery off would become a way to break pairing.
    """
    import ast
    import pathlib

    from personalclaw.auth import enrollment

    tree = ast.parse(pathlib.Path(enrollment.__file__).read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules += [a.name for a in node.names]
    assert not [m for m in modules if "companion" in m], modules
