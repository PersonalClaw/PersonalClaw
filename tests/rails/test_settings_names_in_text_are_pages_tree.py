"""A Settings name the gateway or the CLI puts in front of a person is a page that is there (#5897).

Settings' forty pages folded into seventeen. The links moved with them and the words did not: the
Doctor's finding said "Open Settings → Doctor" and a refused fetch said "clear incident mode in
Settings → Guardrails", naming pages that had become sections of Health and Security.

So every string literal under ``src/personalclaw`` that writes "Settings →" (or "›") names a page by
its label and a section, when it names one, by its heading on that page:
"Settings → Security → Guardrails", and so does every string of a bundled app's or workflow's
manifest, which the app shows as a setting's help. The strings are read from each module's tree
and each manifest's JSON, never a module's comments; docstrings and bare string statements are
documentation, not output, and keep the names they had (their rewrite is its own item).
``settingsNamesInCopyArePages.test.ts``, beside the pages in ``web/src/pages/settings``, holds the
app's own copy to the same names.

The names come from the two tables the app draws Settings from, each row read by its exact shape:
``settingsPages.ts``'s pages and ``SettingsPage.tsx``'s sections. Planted strings below must be
caught, and the scan must find the names the tree holds today, so a walker that reads nothing
cannot pass.
"""

from __future__ import annotations

import ast
import json
import re

import source_index
from rails_repo import REPO

PAGES_TS = REPO / "web/src/pages/settings/settingsPages.ts"
SECTIONS_TSX = REPO / "web/src/pages/settings/SettingsPage.tsx"

#: A page row of ``SETTINGS_PAGES``, as the file writes each one.
PAGE_ROW = re.compile(
    r"^\s*\{ id: '([a-z-]+)', label: '([^']+)', group: '[^']+', span: \d, sections: \[([^\]]*)\]",
    re.M,
)
#: A section row of ``SECTIONS``, as the file writes each one.
SECTION_ROW = re.compile(r"^\s*\{ id: '([a-z-]+)', label: '([^']+)', icon: ", re.M)

ARROW = re.compile(r"Settings ?[→›] ?")
NEXT_ARROW = re.compile(r" ?[→›] ?")
#: The operating system's own Settings, which a sentence about the microphone or reduced motion
#: names: "System Settings" on a Mac and the Settings app on Windows.
OS_SETTINGS = re.compile(r"(\bSystem |\bWindows,? )$")


def pages() -> dict[str, list[str]]:
    """Each page's label, with the headings of the sections it draws."""
    sections = dict(SECTION_ROW.findall(source_index.text(SECTIONS_TSX)))
    rows = PAGE_ROW.findall(source_index.text(PAGES_TS))
    return {
        label: [sections.get(sid, f"(no section {sid})") for sid in re.findall(r"'([a-z-]+)'", ids)]
        for _id, label, ids in rows
    }


def _ends(rest: str) -> bool:
    return rest == "" or not rest[0].isalnum()


def wrong_name(text: str, at: int, known: dict[str, list[str]]) -> str | None:
    """What follows "Settings →" in *text* at *at*, read as a page and maybe a section of it: None
    when it is one, else the words it names."""
    rest = text[at:]
    for page in sorted(known, key=len, reverse=True):
        if not rest.startswith(page) or not _ends(rest[len(page) :]):
            continue
        after = rest[len(page) :]
        arrow = NEXT_ARROW.match(after)
        if not arrow:
            return None
        named = after[arrow.end() :]
        if _names(named, known[page]):
            return None
        # A deeper name that is no section's is a heading on the page, which these tables do not
        # hold: "Settings → Usage → Model prices". One that is another page's section is misfiled.
        others = [s for p, ss in known.items() if p != page for s in ss]
        return f"{page} → {named[:40]}" if _names(named, others) else None
    return rest[:40] or "(nothing: the name is filled in at run time)"


def _names(text: str, names: list[str]) -> bool:
    """Whether *text* starts with one of *names*, the whole name."""
    return any(text.startswith(n) and _ends(text[len(n) :]) for n in names)


def _documentation(tree: ast.Module) -> set[int]:
    """The ids of the string nodes that are documentation: docstrings and bare string statements."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            out.add(id(node.value))
    return out


def names_in(tree: ast.Module, known: dict[str, list[str]]) -> list[tuple[int, str | None]]:
    """Every "Settings →" in the module's string literals: its line, and what it names wrongly."""
    docs = _documentation(tree)
    out: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docs:
            continue
        for m in ARROW.finditer(node.value):
            if OS_SETTINGS.search(node.value[: m.start()]):
                continue
            out.append((node.lineno, wrong_name(node.value, m.end(), known)))
    return sorted(out, key=lambda found: found[0])


#: The bundled manifests whose strings the app shows: an app's settings and a workflow's inputs.
MANIFESTS = ("app.json", "workflow.json")


def manifest_names(text: str, known: dict[str, list[str]]) -> list[str | None]:
    """Every "Settings →" in a manifest's string values, and what each names wrongly."""
    out: list[str | None] = []

    def walk(value: object) -> None:
        if isinstance(value, str):
            for m in ARROW.finditer(value):
                if not OS_SETTINGS.search(value[: m.start()]):
                    out.append(wrong_name(value, m.end(), known))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(json.loads(text))
    return out


def test_the_tables_read_whole() -> None:
    """Every section the pages name is one the sections table defines, each on one page, and every
    section the table defines is on a page: so neither table read in part."""
    defined = SECTION_ROW.findall(source_index.text(SECTIONS_TSX))
    rows = PAGE_ROW.findall(source_index.text(PAGES_TS))
    on_pages = [sid for _id, _label, ids in rows for sid in re.findall(r"'([a-z-]+)'", ids)]
    assert sorted(on_pages) == sorted(sid for sid, _label in defined), "the tables disagree"
    assert len(set(on_pages)) == len(on_pages), "a section is on two pages"
    # Vacuity floor: the seventeen pages and forty sections of #5897.
    assert (
        len(rows) >= 17 and len(defined) >= 40
    ), f"read {len(rows)} pages, {len(defined)} sections"


def test_a_planted_manifest_name_is_read_from_its_values() -> None:
    known = pages()
    text = json.dumps(
        {"settings": [{"help": "bind one in Settings → Models"}, {"help": "Settings → Health"}]}
    )
    assert manifest_names(text, known) == ["Models", None]


def test_a_planted_wrong_name_is_caught_and_a_right_one_is_not() -> None:
    known = pages()
    src = (
        'A = "Open Settings → Health to run it."\n'
        'B = "clear incident mode in Settings → Guardrails."\n'
        'C = f"Settings › Security › Doctor for {x}"\n'
        'D = "Settings → Devices & Access → Companion apps"\n'
        'G = "Settings → Usage → Model prices"\n'
        'E = "turn it on in System Settings → Privacy & Security"\n'
        'F = f"Settings → {page}"\n'
        "def g():\n"
        '    """Settings → Doctor, in a docstring."""\n'
        "    # Settings → Guardrails, in a comment\n"
    )
    found = names_in(ast.parse(src), known)
    assert found == [
        (1, None),
        (2, "Guardrails."),
        (3, "Security → Doctor for "),
        (4, None),
        (5, None),
        (7, "(nothing: the name is filled in at run time)"),
    ]


def test_every_settings_name_the_gateway_and_cli_write_is_a_page() -> None:
    known = pages()
    found = [
        (source_index.rel(path), line, wrong)
        for path, tree in source_index.package_trees()
        if ARROW.search(source_index.text(path))
        for line, wrong in names_in(tree, known)
    ] + [
        (source_index.rel(path), 0, wrong)
        for path in source_index.package_any_files()
        if path.name in MANIFESTS and ARROW.search(source_index.text(path))
        for wrong in manifest_names(source_index.text(path), known)
    ]
    # Vacuity floor: the strings and manifests name a Settings page in 277 places today.
    assert len(found) >= 277, f"found {len(found)} Settings names: the scan is reading little"
    wrong = [f"{rel}:{line}  Settings → {name}" for rel, line, name in found if name is not None]
    assert not wrong, "strings naming a Settings page that is not there:\n" + "\n".join(wrong)
