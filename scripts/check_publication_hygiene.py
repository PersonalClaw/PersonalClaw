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
    the measured population, never at zero" ruling — deliberately, because the situation is
    the inverse of the one that ruling warns about. That ruling protects against a never-run
    gate given teeth at zero reding a whole tree of pre-existing decay at once. Here the
    decay was REMOVED in the same change that added the rail (``temp-screenshots/`` deleted,
    ``scratch/`` renamed, two ``/Users/<maintainer>`` leaks scrubbed), so zero IS the measured
    population. Any nonzero result is a genuine regression, not a backlog.

Five rule families: denied PATH shapes, a ceiling on tracked BINARY size, absolute paths into
a real HOME directory, OFFICE DOCUMENTS that carry custom properties, a sensitivity label or
someone's identity in their metadata, and INTERNAL REFERENCES — the name, host, identifier or
phrase of a system that is not public. The last two ship at zero for the same reason as the
others: the defects were removed in the change that added each. The two content rules also
read the XML parts of an office document, because those parts are text every reader of the
document gets, however compressed the file that carries them.

⚠️  THE INTERNAL-REFERENCE VOCABULARY IS PRIVATE, AND NO FORM OF IT IS PUBLISHED. A list in
    this public repository would publish the very names it exists to keep out of it, and a
    list of digests does too: with the salt beside it, anyone can hash a dictionary of likely
    names and confirm each entry offline — a salt defeats a lookup table, not a guess. So the
    names live in a plain-text file outside every repository, which the
    ``PERSONALCLAW_PRIVATE_DENYLIST`` environment variable names (its format is
    :func:`parse_private_denylist`'s). Where the variable is not set — CI, a fork, a
    contributor's clone — this one rule is SKIPPED with a one-line notice and every other rule
    runs; the maintainer's landing runs it with the list. The matcher stays public, and its
    tests prove every kind against a list of invented words.

    With the list, the rule refuses an ENCODED entry too: an MD5, SHA-1 or SHA-2 digest of one
    in hex or base64, a digest salted with a salt the list names (the salts a list of these
    digests was ever published under), or the entry's own base64 or hex. Each is as readable
    as the plaintext to anyone holding a dictionary, and each is how a denylist that means
    well puts the names back.

Run it directly for the report::

    python3 scripts/check_publication_hygiene.py              # exit 0 iff the tree is clean
    PERSONALCLAW_PRIVATE_DENYLIST=/path/to/list python3 scripts/check_publication_hygiene.py
        # the same, with the internal-reference rule
    python3 scripts/check_publication_hygiene.py --scan PATH...
        # the internal-reference rule over files OUTSIDE the tree — an unpacked wheel, a PR
        # body or release note saved to a file, anything that is about to be published. It
        # needs the private list: without one it checks nothing, and says so
    python3 scripts/check_publication_hygiene.py --digest KIND TEXT
        # the planning-id rule's policy line for TEXT (KIND: work-item-prefix, plan-name or
        # plan-word)

The policy file also holds ``planning_id_rule``: the private half of the planning-id census,
known to the tree as salted digests. That rule is measured by
``scripts/generate_planning_id_baseline.py``, not here, because it is the one exception to
"ships at zero": the tree still carries a known population of planning ids, so it is a
shrink-only ratchet with its own committed census.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
import zlib
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Read cap per file for the content rules. The longest real-home path we need to see is a
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

    The one reader both content rules share. Binary files are skipped by the NUL heuristic:
    their bytes are compressed or structured, and a word "found" in a PNG's pixels would be
    noise that teaches everyone to ignore the rail. An office document is the exception: its
    bytes are a zip of XML text that every reader of the document sees, so each XML part is
    read too, as ``<path>!<part>``, under the office-document rule's caps.
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


# ── internal references ──────────────────────────────────────────────────────────────────

#: The environment variable that names the PRIVATE denylist. It is read in exactly one place,
#: :func:`private_denylist`, which every entry point calls, so "is the rule on?" has one answer.
PRIVATE_DENYLIST_ENV = "PERSONALCLAW_PRIVATE_DENYLIST"

#: The one line an entry point prints when the variable is not set. The skip is deliberate —
#: the vocabulary cannot be published, so a clone without it cannot run this rule — and it is
#: said out loud, because a rule that quietly did nothing would read as a rule that passed.
INTERNAL_REFERENCE_SKIPPED = (
    f"publication-hygiene: internal-reference rule skipped: {PRIVATE_DENYLIST_ENV} is not set "
    "(its vocabulary is private; every other rule ran)"
)

#: The kinds a private-list line may name.
DENYLIST_ENTRY_KINDS = ("word", "phrase", "host", "code", "id")

#: The candidate kinds the matcher compares: the entry kinds, and for each compound kind
#: (phrase, host, code, id) a ``-head`` twin — one WORD every match of it must contain, derived
#: from the entries when the list is read. A file none of whose words is a head never runs that
#: kind's pattern, which keeps a whole-tree scan to one tokenising pass (the four full-text
#: patterns were most of the cost, and nearly every file carries no head at all).
INTERNAL_REFERENCE_KINDS = (
    "word",
    "phrase-head",
    "phrase",
    "host-head",
    "host",
    "code-head",
    "code",
    "id-head",
    "id",
)

#: ``kind -> canonical candidates``, one key per :data:`INTERNAL_REFERENCE_KINDS`, plus
#: ``salt``: the salts a digest of an entry must never be made with.
Denylist = Mapping[str, frozenset[str]]

_DIGITS = "0123456789"
#: A word: a maximal run of ASCII letters and digits in the case-folded text, so
#: ``snake_case``, ``kebab-case`` and ``dotted.names`` fold into the words a reader sees.
_WORD = re.compile(r"[a-z0-9]+")
#: The same run, case preserved — so a mixed-case run can also be split into its CamelCase
#: parts (``FooService`` is read as ``fooservice``, ``foo`` and ``service``).
_RUN = re.compile(r"[A-Za-z0-9]+")
_CAMEL_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
#: A dotted, host-like run. Every suffix of two or more labels is a candidate, so a denied
#: host also denies each of its subdomains. Anchored at a label start: unanchored, the engine
#: retries from every character inside every word, which made the scan quadratic per word.
#: A dot may precede the run, so a wildcard family (``*.example.org``) is still read.
_HOST = re.compile(r"(?<![a-z0-9-])[a-z0-9-]+(?:\.[a-z0-9-]+)+")
#: A catalogue code: letters, an optional hyphen, one to three digits. Not followed by
#: ``.<digit>``, so a version string (``pkg-1.6.0``) is never read as a code. Digits fold to
#: ``9`` (count-preserving), so one entry covers a whole numbered family.
_CODE = re.compile(r"(?<![a-z0-9])([a-z]{2,6})(-?)([0-9]{1,3})(?![a-z0-9]|\.[0-9])")
#: A document-store id: a three-letter prefix, ``_``, then 14 mixed-case base62 characters
#: (shape ``m14``) or 8 lowercase-and-digit characters (shape ``l8``). Only PREFIX and SHAPE
#: are compared, so one entry covers every id the store issues. Matched case-sensitively.
_ID = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>[a-z]{3})_(?:"
    r"(?P<m14>(?=[a-z0-9]*[A-Z])(?=[A-Za-z]*[0-9])(?=[A-Z0-9]*[a-z])[A-Za-z0-9]{14})"
    r"|(?P<l8>(?=[a-z]*[0-9])(?=[0-9]*[a-z])[a-z0-9]{8})"
    r")(?![A-Za-z0-9])"
)
#: How far apart two words may sit and still read as one phrase: a space or a hyphen, or a
#: line break with a comment marker and indentation between them in a wrapped comment.
_PHRASE_GAP = 12

#: The digests the encoded-forms check tries on every spelling of every entry, in hex and in
#: base64: the ones a denylist written to "keep the names out" reaches for.
_DIGEST_ALGORITHMS = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512")
#: A hex run long enough to be a digest, or the hex of a four-letter name. Compared whole and
#: case-folded, so a digest is found in a list, a set, a JSON array or an ``0x`` literal.
_HEX_RUN = re.compile(r"[0-9A-Fa-f]{8,}")
#: A base64 run (standard or URL-safe alphabet), compared whole with its padding stripped.
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{4,}")


class PrivateDenylistError(Exception):
    """The private denylist is named but cannot be used: unreadable, malformed or empty.

    Never a reason to skip. A maintainer who set the variable asked for the rule, and a typo
    in a path or a line must not turn "checked" into "silently passed".
    """


def internal_reference_digest(kind: str, candidate: str, salt: str) -> str:
    """The salted digest of one canonical candidate: how the planning-id rule knows its
    vocabulary, and one of the encoded forms this rule refuses for a private entry."""
    return hashlib.sha256(f"{salt}\0{kind}\0{candidate}".encode("utf-8")).hexdigest()


def _words(text: str) -> set[str]:
    """Every word of *text*, case-folded: each whole run, plus the CamelCase parts of a
    mixed-case one. Only a run with an uppercase letter after its first is split, so the
    split costs nothing on ordinary prose."""
    runs = set(_RUN.findall(text))
    words = {run.lower() for run in runs}
    for run in runs:
        tail = run[1:]
        if tail != tail.lower() and not run.isupper():
            words.update(part.lower() for part in _CAMEL_PART.findall(run))
    return words


def _code_shape(letters: str, hyphen: str, digits: str) -> str:
    return f"{letters}{hyphen}{'9' * len(digits)}"


def _id_shape(match: re.Match[str]) -> str:
    return f"{match.group('prefix')}_{'m14' if match.group('m14') else 'l8'}"


def denylist_entries(kind: str, text: str) -> list[tuple[str, str]]:
    """The canonical ``(kind, candidate)`` pairs one private-list line denies: the entry
    itself and, for a compound kind, the head word it is only ever checked behind.

    *kind* is ``word``, ``phrase`` (two words), ``host`` (denies its subdomains too),
    ``code`` (``ab-12``: denies every code of that letters-and-digit-count shape) or ``id``
    (one id: denies every id of that prefix and shape). A refusal never repeats the text —
    it is a private entry, and the caller names its line instead.
    """
    low = text.strip().lower()
    words = _WORD.findall(low)
    if kind == "word":
        if words != [low]:
            raise ValueError("a word is one run of letters and digits")
        return [("word", low)]
    if kind == "phrase":
        if len(words) != 2:
            raise ValueError("a phrase is exactly two words")
        return [("phrase", " ".join(words)), ("phrase-head", words[0])]
    if kind == "host":
        labels = low.split(".")
        if not _HOST.fullmatch(low) or len(labels) < 2 or not _WORD.findall(labels[-2]):
            raise ValueError("a host is a dotted host name")
        return [("host", low), ("host-head", _WORD.findall(labels[-2])[0])]
    if kind == "code":
        code = _CODE.fullmatch(low)
        if not code:
            raise ValueError("a code is letters, an optional hyphen and 1-3 digits")
        return [("code", _code_shape(*code.groups())), ("code-head", code.group(1))]
    if kind == "id":
        ident = _ID.fullmatch(text.strip())
        if not ident:
            raise ValueError("an id is abc_ and 14 mixed-case or 8 lowercase base62 characters")
        return [("id", _id_shape(ident)), ("id-head", ident.group("prefix"))]
    raise ValueError(f"unknown kind {kind!r} ({', '.join(DENYLIST_ENTRY_KINDS)})")


def parse_private_denylist(text: str, source: str = "the private denylist") -> Denylist:
    """Read a private denylist: one entry per line, its KIND, whitespace, then its TEXT::

        # a comment; blank lines are ignored too
        word    <one run of letters and digits>
        phrase  <two words>
        host    <a.dotted.host>      (denies every subdomain too)
        code    <ab-12>              (denies every code of that shape: digits fold to 9s)
        id      <abc_Ab3dE5gH7jK9mN> (denies every id of that prefix and shape)
        salt    <a salt>             (no digest of an entry may be made with it)

    Each entry is folded exactly as the matcher folds the text it reads, so it denies what a
    reader would find; a salt is taken as written. Raises :class:`PrivateDenylistError` naming
    ``source:line`` for a malformed line, and for a list with no entry at all: an empty list
    would deny nothing, and the rule would pass every tree while reporting that it ran.
    """
    denied: dict[str, set[str]] = {kind: set() for kind in (*INTERNAL_REFERENCE_KINDS, "salt")}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        try:
            if len(parts) != 2:
                raise ValueError("a line is KIND, whitespace, then the TEXT to deny")
            pairs = [tuple(parts)] if parts[0] == "salt" else denylist_entries(*parts)
        except ValueError as exc:
            raise PrivateDenylistError(f"{source}:{number}: {exc}") from None
        for entry_kind, candidate in pairs:
            denied[entry_kind].add(candidate)
    if not any(denied[kind] for kind in DENYLIST_ENTRY_KINDS):
        raise PrivateDenylistError(f"{source} holds no entry")
    return {kind: frozenset(candidates) for kind, candidates in denied.items()}


def load_private_denylist(path: Path) -> Denylist:
    """:func:`parse_private_denylist` over the file at *path*."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PrivateDenylistError(f"{path} cannot be read: {exc}") from None
    return parse_private_denylist(text, str(path))


def private_denylist(environ: Mapping[str, str] | None = None) -> Denylist | None:
    """The private denylist the environment names, or ``None`` when it names none — the one
    case in which the internal-reference rule is skipped. An empty value counts as unset."""
    value = (os.environ if environ is None else environ).get(PRIVATE_DENYLIST_ENV, "").strip()
    if not value:
        return None
    return load_private_denylist(Path(value).expanduser())


def denylist_size(denylist: Denylist) -> int:
    """How many entries the list holds (heads are derived, so they are not counted)."""
    return sum(len(denylist[kind]) for kind in DENYLIST_ENTRY_KINDS)


def _spellings(candidate: str) -> set[str]:
    """The spellings an encoder plausibly started from: the canonical lower-case candidate,
    its upper, capitalised and title-cased twins, for a phrase its words run together or joined
    by a hyphen or an underscore — and each with the trailing newline ``echo`` adds."""
    joined = {candidate}
    if " " in candidate:
        joined |= {candidate.replace(" ", sep) for sep in ("", "-", "_")}
    cased = {c for text in joined for c in (text, text.upper(), text.capitalize(), text.title())}
    return cased | {text + "\n" for text in cased}


def _b64(raw: bytes) -> set[str]:
    """*raw* in base64, standard and URL-safe, padding stripped."""
    return {
        base64.b64encode(raw).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="),
    }


def encoded_forms(
    denylist: Denylist,
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """``(hex forms, base64 forms)``: each maps an encoded token to ``(encoding, kind)``.

    Hex forms are lower-case (a hex run is compared case-folded); base64 forms are compared as
    written. Heads are included: a head digested on its own is still the name.
    """
    hex_forms: dict[str, tuple[str, str]] = {}
    b64_forms: dict[str, tuple[str, str]] = {}
    salts = denylist.get("salt", frozenset())
    for kind in INTERNAL_REFERENCE_KINDS:
        for candidate in denylist[kind]:
            for salt in salts:
                hex_forms.setdefault(
                    internal_reference_digest(kind, candidate, salt), ("salted sha256", kind)
                )
            for spelling in _spellings(candidate):
                raw = spelling.encode("utf-8")
                hex_forms.setdefault(raw.hex(), ("hex", kind))
                for token in _b64(raw):
                    b64_forms.setdefault(token, ("base64", kind))
                for algorithm in _DIGEST_ALGORITHMS:
                    digest = hashlib.new(algorithm, raw).digest()
                    hex_forms.setdefault(digest.hex(), (algorithm, kind))
                    for token in _b64(digest):
                        b64_forms.setdefault(token, (f"base64 {algorithm}", kind))
    return hex_forms, b64_forms


class InternalReferenceMatcher:
    """Finds the private denylist's references in text: as written, and encoded.

    Each file's candidates are checked with set arithmetic against the denied sets, so the
    per-file cost is the tokenising, not a Python call per word.
    """

    def __init__(self, denylist: Denylist) -> None:
        self._denied = {
            kind: frozenset(denylist.get(kind, ())) for kind in (*INTERNAL_REFERENCE_KINDS, "salt")
        }
        self._hex_forms, self._b64_forms = encoded_forms(self._denied)

    def _denied_among(self, kind: str, candidates: set[str]) -> set[str]:
        """The subset of *candidates* the denylist refuses."""
        return candidates & self._denied[kind]

    def references(self, text: str) -> list[tuple[str, str]]:
        """Every ``(kind, surface)`` denied reference in *text*, sorted, once each."""
        low = text.lower()
        words = _words(text)
        found = {("word", w) for w in self._denied_among("word", words)}
        heads = self._denied_among("phrase-head", words)
        if heads:
            pairs = _phrase_pattern(sorted(heads)).finditer(low)
            phrases = {f"{m.group(1)} {m.group(2)}" for m in pairs}
            found.update(("phrase", p) for p in self._denied_among("phrase", phrases))
        if self._denied_among("host-head", words):
            for run in set(_HOST.findall(low)):
                labels = run.split(".")
                suffixes = {".".join(labels[start:]) for start in range(len(labels) - 1)}
                denied = self._denied_among("host", suffixes)
                if denied:
                    found.add(("host", max(denied, key=len)))
        letters = {w.rstrip(_DIGITS) for w in words if w[-1] in _DIGITS}
        if self._denied_among("code-head", words | letters):
            codes = {(_code_shape(*m), "".join(m)) for m in set(_CODE.findall(low))}
            denied = self._denied_among("code", {shape for shape, _ in codes})
            found.update(("code", surface) for shape, surface in codes if shape in denied)
        if self._denied_among("id-head", words):
            ids = {(_id_shape(m), m.group(0)) for m in _ID.finditer(text)}
            denied = self._denied_among("id", {shape for shape, _ in ids})
            found.update(("id", surface) for shape, surface in ids if shape in denied)
        return sorted(found)

    def located(self, text: str) -> list[tuple[int, str, str]]:
        """``(line, kind, surface)`` for every denied reference in *text*.

        A clean text (the overwhelming case) costs one pass. Only a text that carries a
        reference is read again, line by line, to say WHERE. A phrase is the one kind that can
        straddle a line break (a wrapped comment), so it is placed on its first word's line.
        """
        hits = self.references(text)
        if not hits:
            return []
        located = {
            (number, kind, surface)
            for number, line in enumerate(text.splitlines(), 1)
            for kind, surface in self.references(line)
        }
        placed = {(kind, surface) for _, kind, surface in located}
        low = text.lower()
        for kind, surface in hits:
            if kind == "phrase" and (kind, surface) not in placed:
                for match in _wrapped_phrase(surface).finditer(low):
                    located.add((low.count("\n", 0, match.start()) + 1, kind, surface))
        return sorted(located)

    def encodings(self, text: str) -> list[tuple[str, str, str]]:
        """Every ``(encoding, kind, token)`` in *text* that is a denied entry ENCODED — a
        digest of it, or its base64 or hex — sorted, once each."""
        found = set()
        for run in set(_HEX_RUN.findall(text)):
            hit = self._hex_forms.get(run.lower())
            if hit:
                found.add((*hit, run))
        for run in set(_BASE64_RUN.findall(text)):
            hit = self._b64_forms.get(run)
            if hit:
                found.add((*hit, run))
        return sorted(found)

    def located_encodings(self, text: str) -> list[tuple[int, str, str, str]]:
        """``(line, encoding, kind, token)`` for every encoded entry in *text*; one pass for a
        clean text, as :meth:`located` does."""
        if not self.encodings(text):
            return []
        return sorted(
            {
                (number, *hit)
                for number, line in enumerate(text.splitlines(), 1)
                for hit in self.encodings(line)
            }
        )


def _phrase_pattern(heads: list[str]) -> re.Pattern[str]:
    """A head word followed, within the phrase gap, by the next word (captured by lookahead
    so that word can itself start the next phrase)."""
    alternation = "|".join(map(re.escape, heads))
    return re.compile(rf"(?<![a-z0-9])({alternation})(?=[^a-z0-9]{{1,{_PHRASE_GAP}}}([a-z0-9]+))")


def _wrapped_phrase(phrase: str) -> re.Pattern[str]:
    """Where a two-word phrase sits when a line break falls between its words."""
    head, tail = (re.escape(part) for part in phrase.split(" ", 1))
    return re.compile(rf"(?<![a-z0-9]){head}(?=[^a-z0-9]{{1,{_PHRASE_GAP}}}{tail}(?![a-z0-9]))")


def _shown(token: str) -> str:
    """An encoded token as a finding quotes it: enough to find it, not a wall of hex."""
    return token if len(token) <= 20 else token[:20] + "…"


def _path_findings(
    matcher: InternalReferenceMatcher, name: str, path: str, where: str
) -> list[str]:
    """The rule's findings for a published PATH (a file name is published as its content is):
    *path* is what is read, *where* is how a finding names the file."""
    return [
        *(
            f"{name}: {where} — its PATH names a denied {kind} ({surface!r})"
            for kind, surface in matcher.references(path)
        ),
        *(
            f"{name}: {where} — its PATH carries the {encoding} of a denied {kind} "
            f"({_shown(token)!r})"
            for encoding, kind, token in matcher.encodings(path)
        ),
    ]


def _text_findings(matcher: InternalReferenceMatcher, name: str, path: str, text: str) -> list[str]:
    """The rule's findings for a published TEXT, one per (line, reference)."""
    return [
        *(
            f"{name}: {path}:{line} names a denied {kind} ({surface!r})"
            for line, kind, surface in matcher.located(text)
        ),
        *(
            f"{name}: {path}:{line} carries the {encoding} of a denied {kind} ({_shown(token)!r})"
            for line, encoding, kind, token in matcher.located_encodings(text)
        ),
    ]


def internal_references(
    paths: list[str], denylist: Denylist, baseline: dict, root: Path | None = None
) -> list[str]:
    """Tracked paths and text that name a non-public system — see the rule's rationale.

    One line per (file, line, reference): unlike a home path, each occurrence is its own
    sentence to rewrite, so the line is the useful unit. There is no exemption mechanism for
    this rule — a match is always a defect, fixed by stating the principle instead.
    """
    root = root or REPO_ROOT
    name = baseline["internal_reference_rule"]["name"]
    matcher = InternalReferenceMatcher(denylist)
    found = [line for path in paths for line in _path_findings(matcher, name, path, path)]
    for path, text in _text_blobs(paths, root):
        found += _text_findings(matcher, name, path, text)
    return sorted(found)


def scan_files(targets: list[Path]) -> list[tuple[Path, str]]:
    """``(file, shown path)`` for every file under *targets*: a directory is walked and each
    file is shown relative to it; a file stands for itself."""
    files = []
    for target in targets:
        if target.is_dir():
            files += [(p, p.relative_to(target).as_posix()) for p in sorted(target.rglob("*"))]
        else:
            files.append((target, target.name))
    return [(file, shown) for file, shown in files if file.is_file()]


def scan_paths(targets: list[Path], denylist: Denylist, baseline: dict | None = None) -> list[str]:
    """The internal-reference rule over files that are NOT the tracked tree: an unpacked
    wheel, a PR body or release note saved to a file. Every file is read WHOLE (a minified
    bundle can outgrow the tree's read cap); binaries are skipped, as in the tree."""
    baseline = baseline or load_baseline()
    name = baseline["internal_reference_rule"]["name"]
    matcher = InternalReferenceMatcher(denylist)
    found = []
    for file, shown in scan_files(targets):
        found += _path_findings(matcher, name, shown, str(file))
        blob = file.read_bytes()
        if not _is_binary(blob):
            found += _text_findings(matcher, name, str(file), blob.decode("utf-8", "replace"))
    return sorted(found)


#: The planning-id rule's kinds, and the one spelling each is digested in.
PLANNING_ID_KINDS = ("work-item-prefix", "plan-name", "plan-word")
_PLANNING_SHAPES = {
    "work-item-prefix": re.compile(r"[A-Z][A-Z0-9]{1,5}"),
    "plan-name": re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+"),
    "plan-word": re.compile(r"[A-Za-z][A-Za-z0-9]{1,15}"),
}


def _planning_candidate(kind: str, text: str) -> str:
    """*text* as the planning-id rule digests it: a prefix as written, a plan name or word folded
    to lower case."""
    if not _PLANNING_SHAPES[kind].fullmatch(text):
        raise ValueError(f"not a {kind}: {text!r}")
    return text if kind == "work-item-prefix" else text.lower()


def planning_id_line(kind: str, text: str, salt: str) -> str:
    """The ``KIND DIGEST`` line to add to ``planning_id_rule.denied`` so that *text* counts.

    *kind* is ``work-item-prefix`` (a work-item prefix as written, in capitals), ``plan-name``
    (a hyphenated plan name, any case) or ``plan-word`` (a one-word plan name). An
    internal-reference kind is refused: that vocabulary is private, and a digest of it in this
    repository would publish it.
    """
    if kind in DENYLIST_ENTRY_KINDS:
        raise ValueError(
            f"{kind!r} is an internal-reference kind, and that vocabulary is private: add "
            f"'{kind} TEXT' to the file {PRIVATE_DENYLIST_ENV} names, never a digest to this "
            "repository"
        )
    if kind not in PLANNING_ID_KINDS:
        raise ValueError(f"unknown kind {kind!r} ({', '.join(PLANNING_ID_KINDS)})")
    return (
        f"{kind} {internal_reference_digest(kind, _planning_candidate(kind, text.strip()), salt)}"
    )


def violations(root: Path | None = None, *, denylist: Denylist | None) -> list[str]:
    """Every publication-hygiene defect in the tracked tree, sorted and grouped by rule
    family. Empty list == the tree is fit to publish.

    *denylist* is REQUIRED, so no caller can leave the internal-reference rule out by
    forgetting it: pass :func:`private_denylist`'s answer, and ``None`` only where that is it.
    """
    baseline = load_baseline()
    paths = tracked_files(root)
    return [
        *path_rule_violations(paths, baseline),
        *oversized_binaries(paths, baseline, root),
        *real_home_paths(paths, baseline, root),
        *office_documents(paths, baseline, root),
        *(internal_references(paths, denylist, baseline, root) if denylist is not None else ()),
    ]


def _report(found: list[str], clean: str, baseline: dict) -> int:
    if not found:
        print(clean)
        return 0
    print(f"publication-hygiene: FAIL ({len(found)} finding(s))")
    for line in found:
        print(f"  - {line}")
    # A rule whose fix is not "delete or move the path" carries its own advice.
    advised = (baseline["office_document_rule"], baseline["internal_reference_rule"])
    for rule in advised:
        if any(line.startswith(f"{rule['name']}:") for line in found):
            print("\n" + rule["how_to_fix"])
    prefixes = tuple(f"{rule['name']}:" for rule in advised)
    if any(not line.startswith(prefixes) for line in found):
        print("\n" + baseline["how_to_fix_a_red"])
    return 1


def main(argv: list[str] | None = None) -> int:
    """Print the report; exit ``0`` iff nothing checked is unfit to publish."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan", nargs="+", type=Path, metavar="PATH")
    mode.add_argument("--digest", nargs=2, metavar=("KIND", "TEXT"))
    args = parser.parse_args(argv)
    baseline = load_baseline()
    if args.digest:
        kind, text = args.digest
        try:
            line = planning_id_line(kind, text, baseline["planning_id_rule"]["digest_salt"])
        except ValueError as exc:
            parser.error(str(exc))
        print(line)
        return 0
    try:
        denylist = private_denylist()
    except PrivateDenylistError as exc:
        print(f"publication-hygiene: FAIL (the private denylist is unusable: {exc})")
        return 1
    if denylist is None:
        print(INTERNAL_REFERENCE_SKIPPED)
    checked = (
        ""
        if denylist is None
        else f"; {denylist_size(denylist)} private internal-reference entries"
    )
    if args.scan:
        scanned = len(scan_files(args.scan))
        if denylist is None:
            print(f"publication-hygiene: SKIPPED ({scanned} files, nothing checked)")
            return 0
        found = scan_paths(args.scan, denylist, baseline)
        return _report(
            found,
            f"publication-hygiene: PASS ({scanned} files scanned, 0 found{checked})",
            baseline,
        )
    found = violations(denylist=denylist)
    clean = f"publication-hygiene: PASS ({len(tracked_files())} tracked paths, 0 unfit{checked})"
    return _report(found, clean, baseline)


if __name__ == "__main__":
    sys.exit(main())
