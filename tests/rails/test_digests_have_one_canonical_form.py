"""A SHA-256 of compact JSON is ``ledger.hashing.canonical_digest``, or a form kept on purpose.

Shared contracts §2.4 gives every digest a yes is bound to one form, spelled once in
``ledger/hashing.py``. A second spelling drifts where nobody looks: ``ensure_ascii``'s default
escapes text the canonical form writes as itself, so the same value digests two ways as soon as it
holds a name in another language. So this rail fails on a SHA-256 taken of
``json.dumps(..., separators=(",", ":"))`` anywhere but ``ledger/hashing.py``: in the function that
serializes it, or through a function of the same module that returns it.

Some such digests predate the form and are kept as built, because bytes already recorded or
already handed out depend on them. Each is listed in :data:`KEPT` with why, by the function whose
``json.dumps`` it is; a new one costs its reason. A digest the owner's yes is sealed to through a
grant book (``owner_grants.seal`` over a server's or a runner's definition) is taken in another
module, so this rail does not see it: §2.4 keeps that seal as built.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import source_index

HOME = "src/personalclaw/ledger/hashing.py"

#: The compact-JSON digests kept as built, each with why its bytes must not change.
KEPT: dict[str, str] = {
    "src/personalclaw/computer_use/types.py:fingerprint_of": (
        "a window's 32-character structure fingerprint, held across calls to catch a stale index"
    ),
    "src/personalclaw/durability/numbered_rows.py:_identity_of": (
        "a row's identity, written into every numbered table and matched across homes when a sync"
        " merges them: in row order, its own rule for bytes, 32 characters"
    ),
    "src/personalclaw/durability/shards.py:canonical_json": (
        "a shard's line is the bytes the file holds, and its digest is the file's (§2.4's file"
        " digest), recorded in every export's manifest"
    ),
    "src/personalclaw/evals/judge_bench.py:canonical_json": (
        "a fixture set's identity, pinned in recorded runs: the scenarios' rule, ASCII escaped"
    ),
    "src/personalclaw/evals/retrieval_bench.py:sha256": (
        "a benchmark's subject hash, pinned in recorded runs so a score change is attributed"
    ),
    "src/personalclaw/evals/scenarios.py:canonical_json": (
        "a scenario's identity, pinned in recorded runs and the gate's subsets, ASCII escaped"
    ),
    "src/personalclaw/inbound/bridge.py:digest_of": (
        "the 16-character catalogue fingerprint a client caches and polls against"
    ),
    "src/personalclaw/knowledge/restructure.py:_digest": (
        "a restructure preview's 32-character token, which the confirm sends back"
    ),
    "src/personalclaw/skills/overlays.py:refinement_id": (
        "the 16-character id of a stored refinement record, ASCII escaped"
    ),
    "src/personalclaw/stale_write.py:revision_of": (
        "the 16-character stale-write revision every editable row serves and a save sends back in"
        " If-Match; ASCII escaped so any string encodes"
    ),
    "src/personalclaw/triggers/dispatch.py:payload_hash": (
        "an event's dedup key, held in the spool and the dedup window across a restart"
    ),
    "src/personalclaw/turn_checkpoints.py:_record_folder": (
        "the digest of a folder record's compressed bytes, which names its file"
    ),
    "src/personalclaw/workflows/project_export.py:_canonical": (
        "the digests in an exported project's manifest, which its import checks"
    ),
}


def _is_compact_dumps(node: ast.AST) -> bool:
    if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "dumps"):
        return False
    for keyword in node.keywords:
        if keyword.arg == "separators" and isinstance(keyword.value, ast.Tuple):
            return [getattr(e, "value", None) for e in keyword.value.elts] == [",", ":"]
    return False


def _own(fn: ast.AST) -> list[ast.AST]:
    """The nodes of *fn*'s own body: a function defined inside it is its own."""
    nodes, todo = [], list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        nodes.append(node)
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            todo.extend(ast.iter_child_nodes(node))
    return nodes


def _name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        owner = func.value.id if isinstance(func.value, ast.Name) else ""
        return f"{owner}.{func.attr}" if owner else func.attr
    return ""


def _origins(expr: ast.AST, bound: dict[str, set[str]], here: str, returners: set[str]) -> set[str]:
    """The functions whose compact JSON *expr* is made of (the JSON, its bytes, a compressed
    copy): *here* for a ``json.dumps`` in it or a name bound to one, a helper for a call of it."""
    origins: set[str] = set()
    for node in ast.walk(expr):
        if _is_compact_dumps(node):
            origins.add(here)
        elif isinstance(node, ast.Name) and node.id in bound:
            origins |= bound[node.id]
        elif isinstance(node, ast.Call) and _name(node.func) in returners:
            origins.add(_name(node.func))
    return origins


def _bound(fn: ast.AST, returners: set[str]) -> dict[str, set[str]]:
    """Each name *fn* binds to compact JSON, with the functions it came from."""
    bound: dict[str, set[str]] = {}
    changed = True
    while changed:
        changed = False
        for node in _own(fn):
            if isinstance(node, ast.Assign):
                origins = _origins(node.value, bound, fn.name, returners)
                for target in node.targets:
                    if isinstance(target, ast.Name) and origins - bound.get(target.id, set()):
                        bound[target.id] = bound.get(target.id, set()) | origins
                        changed = True
    return bound


def _digests(files: Iterable[tuple[str, ast.Module]]) -> list[tuple[str, int, str]]:
    """Each function outside the one home whose compact JSON a SHA-256 is taken of, as
    ``(module, line, function)``: the function that calls ``json.dumps``, whether it takes the
    SHA-256 itself or a function of its module takes it of what it returns."""
    found = set()
    for rel, tree in files:
        if rel == HOME:
            continue
        functions = [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        by_name = {f.name: f for f in reversed(functions)}
        sha = {"hashlib.sha256", "sha256"}
        # A function of this module that takes a SHA-256 hashes what it is given too.
        hashers = sha | {
            f.name
            for f in functions
            if any(isinstance(n, ast.Call) and _name(n.func) in sha for n in _own(f))
        }
        returners = {
            f.name
            for f in functions
            if any(
                isinstance(n, ast.Return)
                and n.value is not None
                and f.name in _origins(n.value, _bound(f, set()), f.name, set())
                for n in _own(f)
            )
        }
        for fn in functions:
            bound = _bound(fn, returners)
            # A hash object fed in steps: ``h = hashlib.sha256()`` then ``h.update(...)``.
            fed = {
                f"{t.id}.update"
                for n in _own(fn)
                if isinstance(n, ast.Assign)
                and isinstance(n.value, ast.Call)
                and _name(n.value.func) in sha
                for t in n.targets
                if isinstance(t, ast.Name)
            }
            for node in _own(fn):
                if isinstance(node, ast.Call) and _name(node.func) in (hashers | fed) - {fn.name}:
                    for arg in node.args:
                        for origin in _origins(arg, bound, fn.name, returners):
                            found.add((rel, by_name[origin].lineno, origin))
    return sorted(found)


def _findings(files: Iterable[tuple[str, ast.Module]]) -> list[str]:
    return [
        f"{rel}:{line}: {name} takes a SHA-256 of compact JSON"
        for rel, line, name in _digests(files)
        if f"{rel}:{name}" not in KEPT
    ]


def _package() -> list[tuple[str, ast.Module]]:
    """The package's modules that can hold such a digest. One needs a ``json.dumps`` with
    ``separators`` and a SHA-256 taken in the same module, so a module whose text names either not
    at all is passed over unwalked: walking every function of every module took past the rails
    job's 30 seconds a test."""
    trees = source_index.package_trees()
    assert len(trees) > 500, f"walked {len(trees)} modules — vacuous"
    return [
        (source_index.rel(Path(path)), tree)
        for path, tree in trees
        if _may_hold(source_index.text(Path(path)))
    ]


def _may_hold(text: str) -> bool:
    """Whether a module of *text* can hold such a digest: a compact ``json.dumps`` names
    ``separators``, and every SHA-256 :func:`_digests` follows names ``sha256``."""
    return "separators" in text and "sha256" in text


def test_every_compact_json_digest_is_the_canonical_one_or_kept() -> None:
    assert _findings(_package()) == []


def test_every_kept_digest_is_still_one() -> None:
    """A kept entry whose function no longer takes such a digest is an exception nothing uses."""
    digests = {f"{rel}:{name}" for rel, _, name in _digests(_package())}
    assert set(KEPT) <= digests, f"kept but no longer a digest: {sorted(set(KEPT) - digests)}"


#: A module holding one digest of each shape the rail follows, and one compact JSON never hashed.
PLANTED = (
    "import hashlib, json\n"
    "def seal(value):\n"
    '    text = json.dumps(value, sort_keys=True, separators=(",", ":"))\n'
    "    return hashlib.sha256(text.encode()).hexdigest()\n"
    "def _form(value):\n"
    '    return json.dumps(value, separators=(",", ":"))\n'
    "def _sha(data):\n"
    "    return hashlib.sha256(data).hexdigest()\n"
    "def key(value):\n"
    "    return _sha(_form(value).encode())\n"
    "def wire(value):\n"
    '    return json.dumps(value, separators=(",", ":"))\n'
    "def stepped(value):\n"
    "    h = hashlib.sha256()\n"
    '    h.update(json.dumps(value, separators=(",", ":")).encode())\n'
    "    return h.hexdigest()\n"
)


def test_a_planted_digest_is_caught() -> None:
    planted = ast.parse(PLANTED)
    assert _findings([("src/personalclaw/planted.py", planted)]) == [
        "src/personalclaw/planted.py:2: seal takes a SHA-256 of compact JSON",
        "src/personalclaw/planted.py:5: _form takes a SHA-256 of compact JSON",
        "src/personalclaw/planted.py:13: stepped takes a SHA-256 of compact JSON",
    ]
    assert _findings([(HOME, planted)]) == [], "the one home is where the form is spelled"


def test_a_module_passed_over_could_hold_none() -> None:
    """The scan walks only a module whose text names both ``separators`` and ``sha256``. A module
    missing either holds no digest the walk would find, so passing it over drops nothing."""
    assert _may_hold(PLANTED)
    for word, other in (("separators", "spaced"), ("sha256", "md5")):
        text = PLANTED.replace(word, other)
        assert not _may_hold(text), word
        assert _findings([("src/personalclaw/planted.py", ast.parse(text))]) == [], word
