#!/usr/bin/env python3
"""Publication-hygiene check over the tracked tree — is every published path fit to publish?

This repository is PUBLIC. `git ls-files` is, exactly, the list of things the world gets,
and `git rm` removes a path from the tree but never from history. So the only cheap moment
to refuse an unfit path is BEFORE it lands, and the only thing that can refuse it on every
PR is a rail.

The rules live in ``publication-hygiene-baseline.json`` (hand-authored policy, each rule
carrying its own rationale); this module only MEASURES the tree against them. That split is
deliberate and is the one thing not to "simplify":

⚠️  THERE IS NO REGENERATE MODE, ON PURPOSE. The sibling rails in this repo
    (``config-baseline``, ``inert-surface``, ``docs-lint``, the three structural ratchets)
    are generated censuses of populations that legitimately move, so each ships a generator
    and a shrink-only ratchet. A publication denylist does not move: a match is a defect, and
    a "regenerate the baseline" verb would exist only to bless one. The escape hatch is the
    reviewed ``allowed`` list, which costs a rationale a stranger reading the public repo
    would accept.

⚠️  AND THIS RAIL SHIPS AT ZERO, which is the opposite of the structural ratchets' "ship at
    the measured population, never at zero" rule — deliberately, because the situation is
    the inverse of the one that rule warns about. That rule protects against a never-run
    gate given teeth at zero reding a whole tree of pre-existing decay at once. Here the
    decay was REMOVED in the same change that added the rail (``temp-screenshots/`` deleted,
    ``scratch/`` renamed, two ``/Users/<maintainer>`` leaks scrubbed), so zero IS the measured
    population. Any nonzero result is a genuine regression, not a backlog.

Four rule families: denied PATH shapes, a ceiling on tracked BINARY size, absolute paths into
a real HOME directory, and OFFICE DOCUMENTS that carry custom properties, a sensitivity label
or someone's identity in their metadata. The last one ships at zero for the same reason as the
others: its defect was removed in the change that added it. The home-path rule also reads the
XML parts of an office document, because those parts are text every reader of the document
gets, however compressed the file that carries them.

⚠️  NO RULE HERE MATCHES A VOCABULARY. Whether a text names something private (an
    organisation's internal system, a person, a ticket, a private plan) is not a question a
    list of words can answer. A list in this public repository publishes the names it keeps
    out, and so does a list of their digests, which anyone can hash a dictionary of guesses
    against; a list kept anywhere else cannot run where contributors and CI run; and no list
    is ever complete. Keeping such names out is a contributor practice, written in
    ``AGENTS.md`` and ``CONTRIBUTING.md``. Every rule below judges structure instead: a
    path's shape, a file's size, and, for a home path or an office document's identity
    field, an allowlist of declared placeholders rather than a list of the names to keep out.

Run it directly for the report::

    python3 scripts/check_publication_hygiene.py        # exit 0 iff the tree is clean
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Read cap per file for the content rule. The longest real-home path we need to see is a
#: few hundred bytes; reading whole multi-megabyte sources to find one would make the rail
#: slow enough that someone switches it off. The largest tracked text file is well under it.
_CONTENT_READ_CAP = 2_000_000


def baseline_path() -> Path:
    """The committed policy file. Named to match the sibling ``*-baseline.json`` rails."""
    return REPO_ROOT / "publication-hygiene-baseline.json"


def load_baseline() -> dict:
    """Parse the committed policy. Raises rather than defaulting: a rail that silently
    falls back to an empty rule set when its policy file is unreadable is a rail that
    reports PASS for the rest of the repository's life."""
    return json.loads(baseline_path().read_text(encoding="utf-8"))


def tracked_files(root: Path | None = None) -> list[str]:
    """Every tracked path, as a repo-relative POSIX string — i.e. exactly what a clone gets.

    ``-z`` because a published repo may legitimately contain a path with a space, and
    line-splitting ``git ls-files`` would silently drop the tail of one.
    """
    root = root or REPO_ROOT
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _allowed_pairs(baseline: dict) -> set[tuple[str, str]]:
    """``(path, rule)`` pairs the policy deliberately exempts. Keyed on BOTH so an exemption
    granted for one rule cannot silently cover a different defect in the same file."""
    return {(e["path"], e["rule"]) for e in baseline.get("allowed", [])}


def path_rule_violations(paths: list[str], baseline: dict) -> list[str]:
    """Every tracked path matching a denied path pattern, minus the reviewed exemptions."""
    allowed = _allowed_pairs(baseline)
    found = []
    for rule in baseline["path_rules"]:
        pattern = re.compile(rule["pattern"])
        for path in paths:
            if pattern.search(path) and (path, rule["name"]) not in allowed:
                found.append(f"{rule['name']}: {path}")
    return sorted(found)


def _is_binary(blob: bytes) -> bool:
    """A NUL byte in the first 8 KB — the same heuristic ``git diff`` uses to decide a file
    has no textual diff. Good enough, and it is the definition the size rule wants: the
    exemption exists for files a reader reads (source, docs, lockfiles), not for images."""
    return b"\0" in blob[:8192]


def _text_blobs(paths: list[str], root: Path) -> Iterator[tuple[str, str]]:
    """``(path, text)`` for every tracked TEXT file, read up to the cap, decoded leniently.

    What the home-path rule reads. Binary files are skipped by the NUL heuristic: their bytes
    are compressed or structured, and a home path "found" in a PNG's pixels would be noise that
    teaches everyone to ignore the rail. An office document is the exception: its bytes are a
    zip of XML text that every reader of the document sees, so each XML part is read too, as
    ``<path>!<part>``, under the office-document rule's caps.
    """
    for path in paths:
        full = root / path
        # A tracked symlink whose target is absent resolves to nothing: no content to read.
        if not full.is_file():
            continue
        blob = full.read_bytes()[:_CONTENT_READ_CAP]
        if not _is_binary(blob):
            yield path, blob.decode("utf-8", "replace")
        elif blob.startswith(_ZIP_MAGIC):
            for part, text in office_part_texts(full.read_bytes()):
                yield f"{path}!{part}", text


def oversized_binaries(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Tracked BINARY files over the ceiling. Text is exempt — see the rule's rationale."""
    root = root or REPO_ROOT
    rule = baseline["binary_size_rule"]
    cap = rule["max_bytes"]
    found = []
    for path in paths:
        full = root / path
        # A tracked symlink whose target is absent resolves to nothing; it is not a blob
        # that weighs on a clone, so it is not this rule's business.
        if not full.is_file():
            continue
        size = full.stat().st_size
        if size <= cap:
            continue
        if _is_binary(full.read_bytes()[:8192]):
            found.append(f"{rule['name']}: {path} is {size} bytes (ceiling {cap})")
    return sorted(found)


def real_home_paths(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Absolute paths into a home directory whose owner name is not a declared placeholder.

    Reports one line per (file, name) rather than per occurrence: the fix is always "stop
    naming that person", and forty lines for one file buries the other files.
    """
    root = root or REPO_ROOT
    rule = baseline["real_home_path_rule"]
    pattern = re.compile(rule["pattern"])
    placeholders = set(rule["placeholder_home_names"])
    found = set()
    for path, text in _text_blobs(paths, root):
        for name in pattern.findall(text):
            if name not in placeholders:
                found.add(f"{rule['name']}: {path} names home directory '{name}'")
    return sorted(found)


# ── office documents ─────────────────────────────────────────────────────────────────────

#: Extensions of the office formats. A tracked file carrying one must be readable as an office
#: document or the rule refuses it: a format it cannot open is a format it cannot vouch for.
#: Formats are recognised by CONTENT as well, so a document renamed to anything else is still
#: read; extensions that other files also use (``.dot`` for a graph, ``.pot`` for a
#: translation template, ``.key`` for a private key) are left to that content check.
OFFICE_EXTENSIONS = frozenset(
    # Office Open XML documents, workbooks, presentations and drawings.
    ".docx .docm .dotx .dotm .xlsx .xlsm .xlsb .xltx .xltm .xlam".split()
    + ".pptx .pptm .potx .potm .ppsx .ppsm .ppam .sldx .sldm .thmx".split()
    + ".vsdx .vsdm .vssx .vssm .vstx .vstm".split()
    # OpenDocument, packaged and flat.
    + ".odt .ott .ods .ots .odp .otp .odg .otg .odf .odc .odb .fodt .fods .fodp .fodg".split()
    # The binary formats before those, rich text, and other word processors' packages.
    + ".doc .xls .xlt .ppt .pps .vsd .rtf .pages .numbers".split()
)

_ZIP_MAGIC = b"PK\x03\x04"
#: The compound-document container of the binary office formats, of mail items and of
#: embedded objects. It keeps the author and custom properties in property sets this rule does
#: not parse, so a compound document is refused rather than trusted.
_COMPOUND_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_RTF_MAGIC = b"{\\rtf"
_ODF_MIMETYPE = b"application/vnd.oasis.opendocument."
_ODF_OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_ODF_META_NS = "urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
#: The parts read as XML: the package's own markup. The rest are media or binary, except that a
#: part which is itself a package or a compound document is an embedded document.
_OFFICE_XML_SUFFIXES = (".xml", ".rels", ".vml", ".rdf")
#: Caps on what one document can make the rule read, so a crafted archive cannot stall a run:
#: bytes per part, parts per package, and packages nested inside packages.
_OFFICE_PART_CAP = 8_000_000
_OFFICE_MAX_PARTS = 2_000
_OFFICE_MAX_DEPTH = 3
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    zlib.error,
    OSError,
    EOFError,
    RuntimeError,
    NotImplementedError,
    ValueError,
)

#: Elements whose TEXT names a person or an organisation: a package's creator, last-modified-by,
#: company and manager, a spreadsheet comment's author, and an OpenDocument's initial creator,
#: creator (also written on each tracked change and annotation), creator initials and
#: printed-by.
_IDENTITY_ELEMENTS = frozenset(
    {
        "creator",
        "lastModifiedBy",
        "Company",
        "Manager",
        "author",
        "initial-creator",
        "creator-initials",
        "sender-initials",
        "printed-by",
    }
)
#: Attributes that name a person wherever they sit: a revision's or comment's author and
#: initials, the account id recorded for a signed-in author, and a shared workbook's user.
_IDENTITY_ATTRIBUTES = frozenset({"author", "initials", "userId", "userName"})
#: Elements that describe a person, whose ``name`` or ``displayName`` is that person's name: a
#: presentation's comment authors, a workbook's threaded-comment people and a shared
#: workbook's users.
_PERSON_ELEMENTS = frozenset({"cmAuthor", "author", "person", "userInfo"})
_PERSON_NAME_ATTRIBUTES = frozenset({"name", "displayName"})
#: Root namespaces of a part that holds CUSTOM properties: the package's custom-properties part
#: (transitional and strict) and the metadata part a document library attaches to its files.
_CUSTOM_PROPERTY_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
        "http://purl.oclc.org/ooxml/officeDocument/customProperties",
        "http://schemas.microsoft.com/office/2006/metadata/properties",
    }
)
#: Relationship types that attach custom properties, or a sensitivity label, to a package.
_CUSTOM_PROPERTY_RELATIONSHIPS = ("/custom-properties", "/customProperties")
_LABEL_RELATIONSHIPS = ("/classificationlabels",)
#: A sensitivity label is written as custom properties whose names start with this marker, or
#: into a label part of its own.
_LABEL_MARKER = "msip_label"
_LABEL_PART = "docmetadata/labelinfo.xml"
#: RTF's document-information destinations that name a person or an organisation, its
#: custom-properties destination, and its revision table (one author per entry).
_RTF_IDENTITY = re.compile(r"\{\\(?:\*\\)?(author|operator|company|manager)(?![a-z])\s?([^{}]*)\}")
_RTF_CUSTOM = re.compile(r"\{\\\*\\userprops(?![a-z])")
_RTF_REVISION_TABLE = re.compile(r"\{\\\*\\revtbl(?![a-z])((?:\s*\{[^{}]*\})*)")
_RTF_REVISION_ENTRY = re.compile(r"\{([^{}]*)\}")


def _local(name: str) -> str:
    """The local part of an ElementTree ``{namespace}name``."""
    return name.rsplit("}", 1)[-1]


def _namespace(name: str) -> str:
    """The namespace of an ElementTree ``{namespace}name``; empty when it has none."""
    return name[1:].split("}", 1)[0] if name.startswith("{") else ""


def office_kind(blob: bytes) -> str | None:
    """What kind of office document *blob* is, judged by its CONTENT: ``package`` (Office Open
    XML or OpenDocument), ``flat`` (a single-file OpenDocument, or one part of one), ``rtf``
    or ``compound``; ``None`` for anything else, a zip that is not an office package included.
    """
    if blob.startswith(_COMPOUND_MAGIC):
        return "compound"
    if blob.startswith(_RTF_MAGIC):
        return "rtf"
    if blob.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names:
                    return "package"
                if "mimetype" in names:
                    with archive.open("mimetype") as fh:
                        if fh.read(len(_ODF_MIMETYPE)) == _ODF_MIMETYPE:
                            return "package"
        except _ZIP_ERRORS:
            return None
        return None
    head = blob[:4096]
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<") and _ODF_OFFICE_NS.encode() in head:
        try:
            for _, element in ElementTree.iterparse(io.BytesIO(blob), events=("start",)):
                return "flat" if _namespace(element.tag) == _ODF_OFFICE_NS else None
        except ElementTree.ParseError:
            return None
    return None


def _read_part(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes | None:
    """A part's bytes, or ``None`` when it cannot be read within the part cap: an encrypted,
    corrupt or oversized member. The size is what the read yields, never what the member's
    header claims, so a header that lies about its size cannot get past the cap."""
    try:
        with archive.open(info) as fh:
            data = fh.read(_OFFICE_PART_CAP + 1)
    except _ZIP_ERRORS:
        return None
    return data if len(data) <= _OFFICE_PART_CAP else None


def _xml_problems(part: str, data: bytes, placeholders: frozenset[str]) -> list[str]:
    """The problems in one XML part. Fields are judged by NAME, never by searching for a
    person: a list of names to keep out would publish the names."""
    where = f" ({part})" if part else ""
    found = []
    if _LABEL_MARKER in data.decode("utf-8", "replace").lower():
        found.append(f"carries a sensitivity label{where}")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return [*found, f"has a part that is not well-formed XML{where}"]
    if _namespace(root.tag) in _CUSTOM_PROPERTY_NAMESPACES:
        found.append(f"carries custom properties{where}")
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = _local(element.tag)
        if tag == "user-defined" and _namespace(element.tag) == _ODF_META_NS:
            found.append(f"carries custom properties{where}")
        if tag == "Relationship":
            relationship = element.get("Type", "")
            if relationship.endswith(_CUSTOM_PROPERTY_RELATIONSHIPS):
                found.append(f"attaches custom properties{where}")
            if relationship.endswith(_LABEL_RELATIONSHIPS):
                found.append(f"attaches a sensitivity label{where}")
        if tag in _IDENTITY_ELEMENTS and "".join(element.itertext()).strip() not in placeholders:
            found.append(f"names someone in <{tag}>{where}")
        for key, value in element.attrib.items():
            attribute = _local(key)
            named = attribute in _IDENTITY_ATTRIBUTES or (
                tag in _PERSON_ELEMENTS and attribute in _PERSON_NAME_ATTRIBUTES
            )
            if named and value.strip() not in placeholders:
                found.append(f"names someone in <{tag} {attribute}>{where}")
    return found


def _package_problems(blob: bytes, placeholders: frozenset[str], depth: int) -> list[str]:
    """The problems in every part of one package, and of every package embedded in it."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        infos = [info for info in archive.infolist() if not info.is_dir()]
    except _ZIP_ERRORS:
        return ["is not a readable package"]
    if len(infos) > _OFFICE_MAX_PARTS:
        return [f"has more parts than this rule reads ({len(infos)} > {_OFFICE_MAX_PARTS})"]
    found = []
    for info in infos:
        part = info.filename
        data = _read_part(archive, info)
        if data is None:
            found.append(f"has a part this rule cannot read ({part})")
            continue
        if part.lower() == _LABEL_PART:
            found.append(f"carries a sensitivity label ({part})")
        if part.lower().endswith(_OFFICE_XML_SUFFIXES):
            found += _xml_problems(part, data, placeholders)
        elif data.startswith(_COMPOUND_MAGIC):
            found.append(f"embeds a compound document this rule cannot read ({part})")
        elif data.startswith(_ZIP_MAGIC) and office_kind(data) == "package":
            if depth >= _OFFICE_MAX_DEPTH:
                found.append(f"nests documents deeper than this rule reads ({part})")
            else:
                inner = _package_problems(data, placeholders, depth + 1)
                found += [f"{problem} inside {part}" for problem in inner]
    return found


def _rtf_problems(text: str, placeholders: frozenset[str]) -> list[str]:
    """The problems in an RTF document's information group, custom properties and revision
    table."""
    found = [
        f"names someone in \\{field}"
        for field, value in _RTF_IDENTITY.findall(text)
        if value.strip() not in placeholders
    ]
    if _RTF_CUSTOM.search(text):
        found.append("carries custom properties (\\userprops)")
    for table in _RTF_REVISION_TABLE.findall(text):
        entries = (
            entry.strip().rstrip(";").strip() for entry in _RTF_REVISION_ENTRY.findall(table)
        )
        if any(entry not in placeholders for entry in entries):
            found.append("names someone in its revision table")
    if _LABEL_MARKER in text.lower():
        found.append("carries a sensitivity label")
    return found


def office_document_problems(blob: bytes, rule: dict, *, claimed: bool = False) -> list[str]:
    """Why *blob* is unfit to publish as an office document, one phrase each, sorted.

    Empty when it is fit, and when it is not an office document at all — unless *claimed*
    (its extension names an office format), because a file that says it is a document and
    cannot be read as one is exactly the file this rule cannot vouch for. A phrase names the
    field or the part, never its value: the report is published as well.
    """
    placeholders = frozenset(rule["placeholder_values"])
    kind = office_kind(blob)
    if kind is None:
        found = ["is not readable as the office document its extension names"] if claimed else []
    elif kind == "compound":
        found = ["is a compound document, whose author and custom properties this rule cannot read"]
    elif kind == "rtf":
        found = _rtf_problems(blob.decode("latin-1"), placeholders)
    elif kind == "flat":
        found = _xml_problems("", blob, placeholders)
    else:
        found = _package_problems(blob, placeholders, depth=0)
    return sorted(set(found))


def office_part_texts(blob: bytes, depth: int = 0) -> Iterator[tuple[str, str]]:
    """``(part, text)`` for every XML part of an office package, and of the packages embedded
    in it (as ``<part>!<inner part>``), read under the office rule's caps; nothing for any
    other blob."""
    if office_kind(blob) != "package":
        return
    archive = zipfile.ZipFile(io.BytesIO(blob))
    for info in [info for info in archive.infolist() if not info.is_dir()][:_OFFICE_MAX_PARTS]:
        data = _read_part(archive, info)
        if data is None:
            continue
        if info.filename.lower().endswith(_OFFICE_XML_SUFFIXES):
            yield info.filename, data.decode("utf-8", "replace")
        elif depth < _OFFICE_MAX_DEPTH and data.startswith(_ZIP_MAGIC):
            for inner, text in office_part_texts(data, depth + 1):
                yield f"{info.filename}!{inner}", text


def _may_be_office(head: bytes) -> bool:
    """Whether a file's first bytes could open an office document of any kind."""
    if head.startswith((_ZIP_MAGIC, _COMPOUND_MAGIC, _RTF_MAGIC)):
        return True
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return stripped.startswith(b"<") and _ODF_OFFICE_NS.encode() in head


def office_documents(paths: list[str], baseline: dict, root: Path | None = None) -> list[str]:
    """Tracked office documents that carry custom properties, a sensitivity label or someone's
    identity, or that cannot be read — see the rule's rationale. One line per problem, because
    each is its own field to clear."""
    root = root or REPO_ROOT
    rule = baseline["office_document_rule"]
    found = []
    for path in paths:
        full = root / path
        # A tracked symlink whose target is absent resolves to nothing: no document to read.
        if not full.is_file():
            continue
        claimed = PurePosixPath(path).suffix.lower() in OFFICE_EXTENSIONS
        with full.open("rb") as fh:
            head = fh.read(4096)
        if not claimed and not _may_be_office(head):
            continue
        for problem in office_document_problems(full.read_bytes(), rule, claimed=claimed):
            found.append(f"{rule['name']}: {path} {problem}")
    return sorted(found)


def violations(root: Path | None = None) -> list[str]:
    """Every publication-hygiene defect in the tracked tree, sorted and grouped by rule
    family. Empty list == the tree is fit to publish."""
    baseline = load_baseline()
    paths = tracked_files(root)
    return [
        *path_rule_violations(paths, baseline),
        *oversized_binaries(paths, baseline, root),
        *real_home_paths(paths, baseline, root),
        *office_documents(paths, baseline, root),
    ]


def _report(found: list[str], clean: str, baseline: dict) -> int:
    if not found:
        print(clean)
        return 0
    print(f"publication-hygiene: FAIL ({len(found)} finding(s))")
    for line in found:
        print(f"  - {line}")
    # The office-document rule's fix is to clear a field, not to delete or move the path, so it
    # carries its own advice.
    office = baseline["office_document_rule"]
    prefix = f"{office['name']}:"
    if any(line.startswith(prefix) for line in found):
        print("\n" + office["how_to_fix"])
    if any(not line.startswith(prefix) for line in found):
        print("\n" + baseline["how_to_fix_a_red"])
    return 1


def main(argv: list[str] | None = None) -> int:
    """Print the report; exit ``0`` iff the tracked tree carries nothing unfit to publish.

    It takes no arguments, and refuses any: an option this script once had must fail loudly,
    not run the whole-tree check and report a PASS for something it never looked at."""
    argparse.ArgumentParser(description=__doc__.split("\n", 1)[0]).parse_args(argv)
    clean = f"publication-hygiene: PASS ({len(tracked_files())} tracked paths, 0 unfit)"
    return _report(violations(), clean, load_baseline())


if __name__ == "__main__":
    sys.exit(main())
