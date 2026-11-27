"""Install instructions written in prose, read like the code they stand for (#3852).

A skill's instructions are carried out by the agent, and a README's or a prerequisite's by the
person reading them, so a line that pipes a download into a shell is the same act there as in a
script. The install scanner (``supply_chain.SkillScanner``) reads three surfaces of prose with
the execution rules a script gets, each in its own band:

* :data:`INSTRUCTIONS`: a skill's ``SKILL.md`` and the Markdown files it links. The agent carries
  them out, so each rule keeps the severity it has in a script.
* :data:`SKILL_DOCS`: any other Markdown under a skill's folder. The agent reads it only when it
  is linked, so each finding there is a band lower.
* :data:`PROSE`: a README and each prerequisite's ``how`` in ``app.json``. A person carries them
  out and sees the review first, so a finding there is a warning at most, never terminal.

Besides the script rules that run a download or a decoded payload (``remote_exec_pipe``,
``obfuscated_exec``, ``pipe_to_shell``), :data:`RULES` holds the install tricks that live mostly
in prose. The two phrase rules (``fetch_and_follow``, ``credential_in_notes``) sit in the lowest
band, so they never block an install. The paste sites ``paste_site_link`` knows are data, in
``scan_paste_hosts.json`` beside the rules' glosses, so the list changes without a code change.
"""

from __future__ import annotations

import json
import posixpath
import re
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

INSTRUCTIONS = "instructions"
SKILL_DOCS = "skill_docs"
PROSE = "prose"
SURFACES = (INSTRUCTIONS, SKILL_DOCS, PROSE)

#: The script rules that also read prose: a download or a decoded payload handed to a shell.
EXECUTION_RULES = ("remote_exec_pipe", "obfuscated_exec", "pipe_to_shell")

PASTE_HOSTS_PATH = Path(__file__).with_name("scan_paste_hosts.json")

_BANDS = ("low", "warning", "dangerous")


@lru_cache(maxsize=1)
def paste_hosts() -> tuple[str, ...]:
    """The hosts a link to counts as a paste site's. Raises when the packaged list is missing
    or empty: a rule that quietly knows no host is a rule that never fires."""
    try:
        raw = json.loads(PASTE_HOSTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{PASTE_HOSTS_PATH} is missing or unreadable: {exc}") from exc
    hosts = tuple(sorted({str(h).lower() for h in raw.get("hosts", []) if str(h).strip()}))
    if not hosts:
        raise RuntimeError(f"{PASTE_HOSTS_PATH} lists no paste host")
    return hosts


def _paste_link() -> re.Pattern[str]:
    alternatives = "|".join(re.escape(h) for h in paste_hosts())
    return re.compile(
        rf"\bhttps?://(?:[a-z0-9-]+\.)*(?:{alternatives})(?=[/:?#\s)\]\"'>`]|$)", re.I
    )


_VERB_GAP = r"[^\n.]{0,60}?"

#: The install tricks prose carries, each ``(rule, pattern, band in a script's terms)``.
RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # Removing or blanking the quarantine mark on a download (or every extended attribute), or
    # switching the check off, skips what macOS checks before it first runs something downloaded.
    (
        "quarantine_strip",
        re.compile(
            r"\bxattr\s+(?:-\w+\s+)*-\w*[dw]\w*\s+(?:-\w+\s+)*[\"']?com\.apple\.quarantine\b"
            r"|\bxattr\s+(?:-\w+\s+)*-\w*c\w*(?=\s|$)"
            r"|\bspctl\s+(?:--master-disable|--global-disable|--disable)\b"
        ),
        "warning",
    ),
    # An archive opened with a password given beside it: content packed so a scan of the
    # download sees nothing inside.
    (
        "password_archive",
        re.compile(
            r"\bunzip\s+(?:-\w+\s+)*-P\s*\S+"
            r"|\b7z[az]?\s+[ex]\b[^\n]*?\s-p\S*"
            r"|\bunrar\s+[ex]\b[^\n]*?\s-p\S+"
        ),
        "warning",
    ),
    ("paste_site_link", _paste_link(), "warning"),
    # An order to fetch text from somewhere and do what it says: what is carried out is decided
    # by whoever controls that text, after the review.
    (
        "fetch_and_follow",
        re.compile(
            rf"\b(?:fetch|read|open|download|load|visit|get)\b{_VERB_GAP}"
            rf"(?:https?://\S+|\bthe\s+(?:url|link|page|file)\b){_VERB_GAP}"
            r"\b(?:and|then)\s+(?:follow|obey|do|execute|run|carry\s+out)\b[^\n.]{0,40}?"
            r"\b(?:instructions?|steps|commands?|what\s+it\s+says)\b"
            r"|\bfollow\s+(?:the\s+)?(?:instructions|steps)\s+(?:at|from|on)\s+https?://",
            re.I,
        ),
        "low",
    ),
    # An order to keep a credential where it is read back as text: the agent's memory or notes.
    (
        "credential_in_notes",
        re.compile(
            r"\b(?:save|store|keep|write|put|remember|record|paste)\b[^\n.]{0,40}?"
            r"\b(?:token|api[ _-]?key|password|passphrase|secret|credentials?|private\s+key)\b"
            r"[^\n.]{0,40}?\b(?:in|to|into)\s+(?:your\s+|the\s+|a\s+)?"
            r"(?:memory|memories|notes?|notebook|scratchpad|memory\.md|notes\.md)\b",
            re.I,
        ),
        "low",
    ),
)


def band_on(surface: str, band: str) -> str:
    """The band a finding of *band* (its severity in a script) has on prose *surface*: the same
    in a skill's instructions, one lower in its other docs, at most ``warning`` in prose a
    person reads. Never below ``low``: a finding is told, whatever surface it is on."""
    rank = _BANDS.index(band)
    if surface == SKILL_DOCS:
        rank = max(0, rank - 1)
    elif surface == PROSE:
        rank = min(rank, _BANDS.index("warning"))
    return _BANDS[rank]


#: The files read as prose an install carries out: Markdown and plain text.
PROSE_SUFFIXES = (".md", ".markdown", ".txt")

#: Every way Markdown names another file: inline (`[t](path)`, `[t](<a path>)`), reference
#: definitions (`[1]: path`), autolinks (`<path>`) and HTML (`href="path"`, `src="path"`).
_LINK_FORMS = (
    re.compile(r"\]\(\s*(?:<([^>\n]+)>|([^)\s]+))"),
    re.compile(r"^[ \t]{0,3}\[[^\]\n]+\]:[ \t]*(?:<([^>\n]+)>|(\S+))", re.M),
    re.compile(r"<([^<>\s]+)>"),
    re.compile(r"\b(?:href|src)\s*=\s*[\"']([^\"'\n]+)[\"']", re.I),
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def _links(text: str, base: str) -> set[str]:
    """The tree paths of the prose files *text*'s links name, resolved from folder *base* and
    percent-decoded. A link with a scheme, a bare anchor, or one that leaves the tree names
    none."""
    out: set[str] = set()
    for form in _LINK_FORMS:
        for match in form.finditer(text):
            target = unquote(next(g for g in match.groups() if g).strip())
            target = target.split("#", 1)[0].split("?", 1)[0]
            if not target or _SCHEME.match(target) or target.startswith("/"):
                continue
            rel = posixpath.normpath(posixpath.join(base, target))
            if rel.startswith("..") or not rel.lower().endswith(PROSE_SUFFIXES):
                continue
            out.add(rel)
    return out


def surfaces(texts: dict[str, str]) -> dict[str, str]:
    """Every prose file of a tree (*texts*, each Markdown or text file's path to its text) and
    the surface it is read on. A ``SKILL.md`` and every file it reaches by a link, link after
    link and wherever it sits in the tree, are :data:`INSTRUCTIONS` (a link is matched to a
    file however its case differs); a README is :data:`PROSE`; any other file is
    :data:`SKILL_DOCS` under a skill's folder and :data:`PROSE` outside one. None is left out."""
    skills = sorted(rel for rel in texts if PurePosixPath(rel).name.lower() == "skill.md")
    by_case = {rel.casefold(): rel for rel in texts}
    out: dict[str, str] = {}
    reach = list(skills)
    while reach:
        rel = reach.pop()
        if rel in out:
            continue
        out[rel] = INSTRUCTIONS
        for target in _links(texts[rel], posixpath.dirname(rel)):
            found = target if target in texts else by_case.get(target.casefold())
            if found is not None and found not in out:
                reach.append(found)
    folders = [posixpath.dirname(rel) for rel in skills]
    for rel in texts:
        if rel in out:
            continue
        inside = any(not folder or rel.startswith(folder + "/") for folder in folders)
        readme = PurePosixPath(rel).stem.lower() == "readme"
        out[rel] = SKILL_DOCS if inside and not readme else PROSE
    return out


def refusal_lines(findings: Iterable[Any], gloss: Callable[[str], str], *, lead: str = "") -> str:
    """What a refusal says about the install instructions that made it, after *lead*: for each
    dangerous line, its file and line, why it is refused (*gloss*, the rule's plain words), then
    what to do. ``""`` when none of the dangerous findings is in install instructions, so a
    refusal for a script reads as it always did."""
    dangerous = [
        f
        for f in findings
        if getattr(f, "surface", "") in SURFACES
        and str(getattr(f.severity, "value", f.severity)) == "dangerous"
    ]
    if not dangerous:
        return ""
    said = [
        f"{f.path or 'the instructions'} {f.evidence} — {gloss(f.rule) or f.rule}"
        for f in dangerous[:3]
    ]
    if len(dangerous) > 3:
        said.append(f"and {len(dangerous) - 3} more")
    return lead + " ".join(said) + " Remove the skill, or update it to a version without that line."


def prerequisite_hows(manifest_text: str | None) -> list[tuple[str, str]]:
    """Each prerequisite an ``app.json`` declares, ``(name, how)``, for those with a ``how``.
    Read from the manifest as written, so the scan names exactly what consent shows."""
    if not manifest_text:
        return []
    try:
        data = json.loads(manifest_text)
    except ValueError:
        return []
    requires = data.get("requires") if isinstance(data, dict) else None
    if not isinstance(requires, list):
        return []
    return [
        (str(entry.get("name") or f"#{i + 1}"), entry["how"])
        for i, entry in enumerate(requires)
        if isinstance(entry, dict) and isinstance(entry.get("how"), str) and entry["how"].strip()
    ]


def prerequisite_path(name: str) -> str:
    """The path a finding in prerequisite *name*'s ``how`` is told by."""
    return f"app.json requires {name}: how"
