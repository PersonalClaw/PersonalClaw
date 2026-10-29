"""Architecture docs state facts about the tree; these legs hold them to it.

A reader follows a doc's claim into the code. When the claim drifts (a count the code no
longer has, a module the tree no longer holds), the doc misleads exactly the reader who
trusted it, and nothing else notices. Each leg compares one kind of claim with the tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import source_index

from personalclaw.sandbox import _STANDARD_DIRS
from personalclaw.shell_denylist import BUILTIN_DENIED_COMMAND_PATTERNS

pytestmark = pytest.mark.security


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs" / "architecture"

#: A stated size of the built-in command denylist ("116 shell patterns").
_PATTERN_COUNT = re.compile(r"(\d+)\s+shell patterns")

#: A backticked file path, in the three forms the docs write one: package-relative
#: (`personalclaw/surface_layers.py`, resolved under `src/`), and repo-relative under
#: `src/personalclaw/` or `web/src/`. Prose mentions without backticks are not claims a
#: reader is sent to open, so they are not matched.
_FILE_PATH = re.compile(
    r"`((?:src/)?personalclaw/[\w/.-]+\.py|web/src/[\w/.-]+\.(?:tsx?|css|mjs|js))`"
)

#: A section, from its heading to the next heading of any level.
_SECTION = re.compile(r"(?m)^(?=#{1,6} )")


def _claims(text: str) -> str:
    """*text* less every section whose first line is ``Status: planned``.

    Such a section (the shared contracts' convention, its §0.3) says what is still to be built,
    so a file it names is the plan's, not a claim about the tree. The change that builds the
    section removes the line, and from then on the files it names are held to the tree.
    """
    kept = []
    for section in _SECTION.split(text):
        head, _, body = section.partition("\n")
        if not (head.startswith("#") and body.lstrip("\n").startswith("Status: planned\n")):
            kept.append(section)
    return "".join(kept)


def test_the_security_doc_states_the_denylists_real_size():
    """The security doc's denylist count is the count the gateway enforces.

    It said 112 while the packaged baseline held 116.
    """
    stated = _PATTERN_COUNT.findall((DOCS_DIR / "security.md").read_text(encoding="utf-8"))
    assert stated, "security.md no longer states the denylist's size: rework or drop this leg"
    expected = len(BUILTIN_DENIED_COMMAND_PATTERNS)
    assert [int(n) for n in stated] == [expected] * len(stated), (
        f"security.md says {stated} shell patterns; "
        f"BUILTIN_DENIED_COMMAND_PATTERNS holds {expected}"
    )


def test_the_security_doc_names_what_the_standard_sandbox_level_hides():
    """What the security doc says the `standard` level hides is that level's list.

    It listed `~/.aws` and `~/.kube` among the folders the sandbox hides, and the `standard`
    level the agent's shell runs at hides neither.
    """
    names = [f"`~/{d}`" for d in _STANDARD_DIRS]
    listed = ", ".join(names[:-1]) + " and " + names[-1]
    sentence = f"The `standard` level hides {listed}."
    text = " ".join((DOCS_DIR / "security.md").read_text(encoding="utf-8").split())
    assert sentence in text, f"security.md does not say {sentence!r}"


def test_every_file_an_architecture_doc_names_is_in_the_tree():
    """Every backticked file path in ``docs/architecture/*.md`` resolves.

    The widgets doc sent readers to ``personalclaw/dashboard/surface_layers.py``, which does
    not exist: the module is ``src/personalclaw/surface_layers.py``.
    """
    named: list[tuple[str, str]] = []
    docs = [p for p in source_index.docs_files() if p.parent == DOCS_DIR and p.suffix == ".md"]
    for doc in docs:
        named += [(doc.name, m) for m in _FILE_PATH.findall(_claims(source_index.text(doc)))]
    # A pattern that stopped matching would pass this leg while checking nothing.
    assert len(named) >= 40, f"the scan found only {len(named)} file paths in the docs"
    missing = [
        f"{doc}: `{rel}`"
        for doc, rel in named
        if not (REPO_ROOT / ("src/" + rel if rel.startswith("personalclaw/") else rel)).is_file()
    ]
    assert not missing, "a doc names a file the tree does not have:\n" + "\n".join(missing)


def test_a_planned_section_may_name_a_file_still_to_be_built():
    """The same name is a claim in a built section and the plan's in a planned one."""
    named = "See `personalclaw/not_yet_built.py`.\n"
    built = "### 1.1 Built\n\n" + named
    planned = "### 1.2 Planned\n\nStatus: planned\n\n" + named
    assert _FILE_PATH.findall(_claims(built + planned)) == ["personalclaw/not_yet_built.py"]
    assert _FILE_PATH.findall(_claims(planned)) == []
