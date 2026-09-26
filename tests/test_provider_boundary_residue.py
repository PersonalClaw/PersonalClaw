"""Anti-regrowth rail: no NEW vendor residue creeps into core (plan 32).

The provider boundary keeps core provider-agnostic — vendor-specific logic lives in
app bundles. A few vendor-shaped surfaces are deliberate keeps (secret patterns can't be
renamed without breaking the control; the CRED_SLACK_* key names are what existing
installs hold; the OpenAI-*compatible* wire clients are rows 1–2 of the architecture
doc's judgment table). This sweep pins that set:

- It scans every core ``src/personalclaw/**/*.py`` for ACTIONABLE residue —
  vendor SDK imports (``import openai`` / ``import slack_sdk`` / ``from slack ...``)
  and vendor credential/secret literals (``SLACK_*`` env/cred keys, ``xox`` token
  patterns).
- Every file with such a hit MUST be listed in
  ``docs/architecture/provider-boundary-keeps.txt`` (the machine-checked keeps
  table). A hit in an unlisted file fails the test (regrowth). A listed file that
  no longer has a hit also fails (stale entry — keep the table honest).

It deliberately does NOT flag vendor *words* in docstrings/comments/prose: core
legitimately documents the reference channel ("Socket-Mode lives in the
slack-channel app"). Only imports + credential/secret literals are residue.

**#3500 — the vendor set was one vendor wide, so the doc and the rail disagreed.**
``_SDK_MODULES`` held only the channel SDKs. Core imports the ``openai`` SDK in eight
places across four modules, and every one of them is a *declared* exception in
``docs/architecture/provider-boundary.md`` rows 1–2 — declared, and therefore believed
enforced, while this sweep could not see them at all. So the tenet had a documented
boundary and a rail that were looking at different things: the sweep was green on a tree
with eight unchecked vendor SDK imports, and nothing would have gone red if a ninth
arrived in a module row 1–2 does not cover. Widening the set turns those rows into an
enforced allowlist. The set is DERIVED, not guessed — see ``_SDK_MODULES``.

🪤 **Why the import check is AST-based and the literal checks are not.** A rail that
greps raw source counts the comment explaining the fix as the defect. The vendor-SDK
check therefore reads ``ast`` Import nodes, so a ``# import openai`` in a comment or an
``import openai`` line inside a docstring — both of which a line-anchored regex matches —
cannot flag a file. A regex is kept only as the fallback for text that does not parse as
Python, which is how the deliberately case-obfuscated probes below still fire. The
credential/secret patterns stay textual on purpose: one existing keep
(``config/safety.py``) is a vendor key name inside ``_meta`` HELP TEXT, i.e. prose is
exactly what that half is meant to catch.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_CORE = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
_KEEPS_FILE = (
    Path(__file__).resolve().parents[1] / "docs" / "architecture" / "provider-boundary-keeps.txt"
)
_DOC_FILE = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "provider-boundary.md"

#: Vendor SDK top-level import names core must not reach for. DERIVED, so the set has a
#: reason to be exactly this and a rule for growing it — a guessed list of plausible
#: vendor names would read as authoritative while being arbitrary. Two sources, both in
#: the tree:
#:
#: 1. ``pyproject.toml``'s provider-SDK extras, each of which exists precisely because the
#:    SDK is NOT a core dependency: ``[anthropic]`` → ``anthropic``, ``[openai]`` →
#:    ``openai``, ``[bedrock]`` → ``boto3``, ``[slack]`` → ``slack_sdk``,
#:    ``[tts]`` → ``huggingface_hub``.
#: 2. Vendor SDK distributions a bundled/first-party app declares in its manifest
#:    ``dependencies.pythonDependencies`` (``apps/app_manager.py::_install_python_deps``
#:    installs them into ``<home>/app-python``): ``openai``, ``anthropic``, ``boto3``,
#:    ``qdrant-client`` → ``qdrant_client``, ``huggingface-hub``, ``slack-sdk``.
#:
#: Plus three the provider-boundary doc names as bundle-resident by construction and
#: whose absence from core is the whole point of a case study or a row: ``ollama`` (the
#: 1002-LOC client that left core for ``apps/ollama-models``), ``chromadb`` (named beside
#: Qdrant in the vector-store row, where core "ships **no** vector-store client"), and
#: ``telegram``/``discord`` (CHANNEL-EXPANSION's next channels, already pinned here before
#: either exists — a vendor set that only lists what already leaked is not a rail).
#:
#: To add a vendor: ship its extra or its app-manifest dependency, then add it here.
_SDK_MODULES = (
    "openai",
    "anthropic",
    "boto3",
    "botocore",
    "slack_sdk",
    "slack",
    "telegram",
    "discord",
    "ollama",
    "qdrant_client",
    "chromadb",
    "huggingface_hub",
)

# Actionable-residue patterns are case-insensitive (NOT plain vendor words in prose):
#  - a vendor credential-key literal (SLACK_*_TOKEN, PERSONALCLAW_OWNER via SLACK pairing)
#  - a Slack token-shape detection pattern (xox...)
# The credential-key patterns end at TOKEN so suffixed prose identifiers such as
# _setup_slack_tokens stay clean while CRED_SLACK_* constant names still match.
_LITERAL_PATTERNS = [
    re.compile(r"SLACK_[A-Z_]*TOKEN\b", re.IGNORECASE),
    re.compile(r"SLACK_USER_TOKEN\b", re.IGNORECASE),
    re.compile(r"xox\[?[bpas]", re.IGNORECASE),
]

#: Fallback for text that is not parseable Python (the case-obfuscated probes). Never
#: reached for a real core module, so a comment/docstring in core cannot match it.
_SDK_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(?:" + "|".join(_SDK_MODULES) + r")\b",
    re.MULTILINE | re.IGNORECASE,
)


def _core_files() -> list[Path]:
    out: list[Path] = []
    for p in sorted(_CORE.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out.append(p)
    return out


def _sdk_import_lines(text: str) -> list[tuple[int, str]]:
    """``(lineno, vendor)`` for every real vendor-SDK import — AST nodes, not text.

    Comments and docstrings hold no Import node, so the prose documenting a keep can
    never be counted as the keep (§ the module docstring's trap note)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [(0, "?")] if _SDK_IMPORT_RE.search(text) else []
    wanted = {m.lower() for m in _SDK_MODULES}
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            root = name.split(".", 1)[0].lower()
            if root in wanted:
                found.append((getattr(node, "lineno", 0), root))
    return found


def _has_residue(text: str) -> bool:
    return bool(_sdk_import_lines(text)) or any(pat.search(text) for pat in _LITERAL_PATTERNS)


def _keeps() -> set[str]:
    """Repo-relative paths listed in the keeps file (ignoring comments/blanks)."""
    paths: set[str] = set()
    for line in _KEEPS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # "<path> — <judgment>"
        path = line.split("—", 1)[0].strip()
        if path:
            paths.add(path)
    return paths


def _rel(p: Path) -> str:
    return str(p.relative_to(_CORE.parents[1]))  # relative to repo root (src/...)


def test_no_new_vendor_residue_outside_keeps():
    """Every core file carrying vendor credential/secret residue is a listed keep."""
    keeps = _keeps()
    offenders: list[str] = []
    for f in _core_files():
        if _has_residue(f.read_text(encoding="utf-8")):
            rel = _rel(f)
            if rel not in keeps:
                offenders.append(rel)
    assert not offenders, (
        "New provider residue in core (vendor SDK import or credential/secret "
        "literal) outside the keeps table:\n"
        + "\n".join(f"  {o}" for o in sorted(offenders))
        + "\nMove the vendor logic into an app bundle, or — if it is a genuine "
        "secret-detection/credential-key keep — add it to "
        "docs/architecture/provider-boundary-keeps.txt with a judgment."
    )


def test_keeps_table_has_no_stale_entries():
    """Every listed keep still contains residue — a stale entry (the file was cleaned)
    must be removed so the table stays an exact mirror of reality."""
    stale: list[str] = []
    for rel in _keeps():
        p = _CORE.parents[1] / rel
        if not p.is_file() or not _has_residue(p.read_text(encoding="utf-8")):
            stale.append(rel)
    assert not stale, "Stale keeps entries (no residue found — remove them):\n" + "\n".join(
        f"  {s}" for s in sorted(stale)
    )


def test_sweep_has_teeth(tmp_path):
    """The anti-regrowth proof: a fresh core module with a vendor SDK import IS
    detected by the residue patterns (so real regrowth would fail the sweep)."""
    fake = tmp_path / "sneaky.py"
    fake.write_text("import slack_sdk\n\nx = 1\n", encoding="utf-8")
    assert _has_residue(fake.read_text(encoding="utf-8")), (
        "residue patterns failed to catch an injected `import slack_sdk` — the "
        "sweep would not catch real regrowth"
    )
    clean = tmp_path / "fine.py"
    clean.write_text('"""A channel transport (e.g. the slack-channel app)."""\n', encoding="utf-8")
    assert not _has_residue(
        clean.read_text(encoding="utf-8")
    ), "residue patterns wrongly flagged a docstring vendor mention (prose is not residue)"


#: Core modules holding a vendor SDK import, measured for #3500: the four OpenAI-protocol
#: clients that rows 1–2 of ``docs/architecture/provider-boundary.md`` declare as deliberate
#: keeps. A FLOOR, not a ceiling — it exists so a broken walk or a vendor set that stopped
#: matching cannot read as a clean tree. Eight import statements across these four files.
#: (Row 1 also names ``llm/anthropic.py``, which imports NO SDK — it speaks the wire format
#: over ``httpx`` — so it is correctly absent from the keeps table, which the stale-entry
#: rail would otherwise reject.)
_SDK_IMPORT_FILE_FLOOR = 4


def test_every_declared_vendor_sdk_is_actually_detected():
    """The vacuity control for ``_SDK_MODULES``: each derived vendor is really matched.

    A negated or narrowed sweep that cannot match is indistinguishable from a clean tree,
    and that is exactly what #3500 measured — the set held only the channel SDKs, so eight
    ``import openai`` statements in core were invisible and the sweep was green.
    """
    missed = [
        v
        for v in _SDK_MODULES
        if not (_has_residue(f"import {v}\n") and _has_residue(f"from {v} import thing\n"))
    ]
    assert not missed, (
        "these declared vendor SDKs are not detected by the sweep, so the rail is blind to "
        f"them: {missed}"
    )


def test_the_openai_wire_clients_are_enforced_keeps_not_merely_declared_ones():
    """#3500. Rows 1–2 of the architecture doc are now a MACHINE-CHECKED allowlist.

    The doc has always enumerated the OpenAI-protocol clients as deliberate in-core
    exceptions, but this sweep read a vendor set that did not contain ``openai`` — so the
    declared exception list and the rail that was supposed to enforce it were looking at
    different things, and core imported the ``openai`` SDK in eight places under a green
    sweep. This names those sites and requires each of their files to be a listed keep, so
    a ninth import in a module the doc does not cover goes red.
    """
    sites: dict[str, list[tuple[int, str]]] = {}
    for f in _core_files():
        found = _sdk_import_lines(f.read_text(encoding="utf-8"))
        if found:
            sites[_rel(f)] = found
    assert len(sites) >= _SDK_IMPORT_FILE_FLOOR, (
        f"the sweep found vendor SDK imports in only {len(sites)} core files, below the "
        f"{_SDK_IMPORT_FILE_FLOOR} measured for #3500 — the walk or the vendor set stopped "
        f"matching, so a clean result here means nothing. Found: {sorted(sites)}"
    )
    keeps = _keeps()
    unlisted = sorted(rel for rel in sites if rel not in keeps)
    assert not unlisted, (
        "these core files import a vendor SDK and are not in the keeps table:\n"
        + "\n".join(
            f"  {rel}  " + ", ".join(f"line {ln} ({v})" for ln, v in sites[rel]) for rel in unlisted
        )
        + "\nEither move the vendor client into an app bundle, or record the judgment in "
        "docs/architecture/provider-boundary-keeps.txt and the table in "
        "docs/architecture/provider-boundary.md."
    )


def test_the_import_check_cannot_count_its_own_explanation(tmp_path):
    """§ the module docstring's trap: a rail that greps raw source flags the comment that
    documents the fix, so the commit fixing the defect is the commit the rail reds.

    The vendor-SDK half reads ``ast`` Import nodes, so prose about a vendor SDK — in a
    ``#`` comment or inside a docstring, both of which a line-anchored regex matches — is
    not residue. Asserted with the positive control beside it: the same file gains a REAL
    import and is then caught, so this is not a detector that simply stopped working.
    """
    prose = tmp_path / "documented.py"
    prose.write_text(
        '"""Row 2 of the boundary table.\n\n'
        "An app bundle does the equivalent of::\n\n"
        "    import openai\n\n"
        'so core ships the protocol client only.\n"""\n\n'
        "# import openai  ← never do this at module scope\n"
        "VALUE = 1\n",
        encoding="utf-8",
    )
    assert not _sdk_import_lines(prose.read_text(encoding="utf-8")), (
        "the vendor-SDK check counted a docstring/comment mention as an import — that is "
        "the rail's bug, and it reds the very commit that documents a keep"
    )
    prose.write_text(
        prose.read_text(encoding="utf-8") + "\n\ndef go():\n    import openai\n    return openai\n",
        encoding="utf-8",
    )
    assert _sdk_import_lines(prose.read_text(encoding="utf-8")), (
        "the check missed a REAL function-local `import openai`, so the clean result above "
        "proves nothing"
    )


#: ``module.SYMBOL`` citations in the judgment table, e.g. ``constants.APP_LOGGER_ROOTS``.
_DOC_SYMBOL_RE = re.compile(r"`([a-z][a-z0-9_]*)\.([A-Z][A-Z0-9_]{2,})`")


def _judgment_table_rows() -> list[str]:
    """Only the ``|``-delimited rows of the judgment table — NOT the surrounding prose.

    Deliberate, and it is the whole reason this rail is safe to write: the doc's prose now
    *records the removed row by name*, so a rail that scanned the file whole would flag the
    sentence explaining the fix as the defect — the trap the module docstring describes and
    that the tree has recorded four times. A rail measures the ruling, not the account of
    how the ruling changed."""
    return [
        ln
        for ln in _DOC_FILE.read_text(encoding="utf-8").splitlines()
        if ln.lstrip().startswith("|")
    ]


def _unresolvable_symbol_citations(rows: list[str]) -> list[str]:
    """``module.SYMBOL`` cites in *rows* that no core module actually defines."""
    bad: list[str] = []
    for ln in rows:
        for module, symbol in _DOC_SYMBOL_RE.findall(ln):
            if symbol.endswith("_"):  # a `CRED_SLACK_*`-style family, not one name
                continue
            candidates = [_CORE / f"{module}.py", *_CORE.rglob(f"{module}.py")]
            defining = [
                p
                for p in candidates
                if p.is_file()
                and re.search(rf"^{re.escape(symbol)}\s*[:=]", p.read_text(encoding="utf-8"), re.M)
            ]
            if not defining:
                bad.append(f"{module}.{symbol}")
    return bad


def test_the_judgment_table_cites_no_symbol_that_does_not_exist():
    """#3500. The table's whole purpose is to be authoritative about what core may touch,
    so a citation that does not resolve is worse than no citation — it reads as a ruling
    and answers wrongly in both directions.

    Measured: the table listed ``constants.APP_LOGGER_ROOTS`` as a deliberate core keep
    while ``constants.py`` has no such name, and in the same row called apps registering
    their own logger roots "deliberately not built yet" while ``loggerRoots`` +
    ``apps.catalog.installed_logger_roots()`` do exactly that for two live consumers. The
    row is gone; this keeps the next one honest.
    """
    rows = _judgment_table_rows()
    assert rows, "no judgment-table rows parsed out of provider-boundary.md — fix the parser"
    # The zero below is only meaningful if the checker can fire, so make it fire (§15: write
    # the control before trusting the selector's silence).
    control = _unresolvable_symbol_citations(["| `constants.NOT_A_REAL_CONSTANT` | core | x |"])
    assert control == ["constants.NOT_A_REAL_CONSTANT"], (
        f"the citation checker cannot detect a symbol that does not exist ({control}), so a "
        f"clean result on the real table proves nothing"
    )
    unresolvable = _unresolvable_symbol_citations(rows)
    assert not unresolvable, (
        "the provider-boundary judgment table cites core symbols that do not exist:\n"
        + "\n".join(f"  {c}" for c in sorted(unresolvable))
        + "\nCorrect or remove the row — a stale exception list reads as a ruling."
    )


def test_residue_patterns_are_case_insensitive_without_matching_prose_identifiers():
    """Case-obfuscated residue is caught without flagging suffixed prose identifiers."""
    probes = [
        "IMPORT SLACK_SDK",
        "from Slack_SDK import x",
        "slack_bot_token",
        "Slack_Bot_Token",
        "XOXB-abc",
        "XoxB-abc",
        "import slack_sdk",
        "SLACK_BOT_TOKEN",
        "xoxb-abc",
    ]
    missed = [probe for probe in probes if not _has_residue(probe)]
    assert not missed, "case-insensitive residue probes escaped the sweep:\n" + "\n".join(
        f"  {probe}" for probe in missed
    )
    assert not _has_residue(
        "_setup_slack_tokens"
    ), "credential-key patterns wrongly flagged a suffixed prose identifier"
