"""Untrusted-input caps for every office parser, defined ONCE (#2747).

`documents/`'s parsers hand raw bytes to python-docx / openpyxl / python-pptx, each of
which opens the archive itself — so none of `doc_parser.py`'s zip-bomb posture reached
them and nothing bounded the parse. The measured consequence: a **56 KB** `.docx` holding
200,000 one-character paragraphs cost **153 s** of CPU, and at that compression ratio the
16 MiB write cap admits ≈ 12 hours of it.

This module is the one place the caps live, for the reason the plan's guardrail gave —
"reuse the existing zip-bomb defenses' posture rather than inventing new caps, and cap
parsed block count". It holds no `documents.*` import on purpose: every parser imports
*it*, so a dependency the other way would be a cycle.

## The two kinds of cap, and why they refuse differently

**Archive caps are a REFUSAL.** :func:`assert_archive_within_limits` sums the *actual*
decompressed size of the XML parts — regardless of what the ZIP header declares, exactly
as ``doc_parser._read_zip_entry`` does — before a single byte reaches a document library.
A package over the cap raises :class:`DocumentTooLarge`, which the artifact route turns
into a ``document_too_large`` 413. You cannot half-open a ZIP, so there is no honest
partial answer here.

**Structural caps TRUNCATE and say so.** A block/cell/slide cap appends a ``size_limit``
`LossItem` and stops walking. A raise would tell the user "your document is broken"; the
loss report tells them "too big, here is what fit" through the machinery the module
already has for saying what it dropped.

## The way OUT (a cap with no escape is the outage)

A refused document is still fully available: ``GET /api/artifacts/{slug}/raw`` serves the
original bytes to the same browser, unredacted, one route over. The refusal message says
so. What is lost is the *editor*, not the file — and the refusal only triggers where
opening the editor would have cost minutes of CPU and gigabytes of RAM anyway.

## Where the numbers come from (measured against a known-clean corpus, not chosen)

Calibrated against every real ``.docx``/``.xlsx``/``.pptx`` on the development machine
plus this repo's fixtures — Word-authored specifications, Excel-authored financial models,
the bundled deck — and then against SYNTHETIC-BUT-ORDINARY documents at the top of the
plausible range, because the real corpus alone would have picked caps far too tight:

====================  =============  ==============  =====================================
cap                   largest real   plausible-max   worst case measured under the cap
====================  =============  ==============  =====================================
XML part 50 MB        438 KB         33 MB           600k-cell sheet: 5.4 s load, 743 MB
XML total 100 MB      908 KB         33 MB           —
archive 65,536 parts  43 parts       2,046 parts     0.04 s to scan 32,768 parts
20,000 blocks         251            —               7.7 s on the 200k-paragraph attack
500,000 cells         7,280          200,000         8.2 s / 743 MB on a 600k-cell sheet
1,000 slides          12             —               4.4 s on a 1,005-slide deck
====================  =============  ==============  =====================================

**The corpus overturned two numbers a plausible reading would have picked.**

* An ordinary 2,000 x 100 spreadsheet (200,000 cells — a perfectly normal export) carries
  **10.9 MB of XML in one part**, and a 600,000-cell one carries **33 MB**. A part cap of
  8 MB, which looks generous beside the 438 KB largest real part, refuses both. So the cap
  stays at ``doc_parser``'s shipped 50 MB, which is what the guardrail asked for anyway.
* Conversely one real document IS refused, and that is the correct outcome: an 8.6 MB
  business ``.xlsx`` carries a 92 MB ``xl/pivotCache/pivotCacheRecords1.xml`` the model
  never reads. Measured — ``parse_xlsx`` takes **223 s** and peaks at **4.7 GB** to
  produce 7,280 cells. Refusing it and pointing at ``…/raw`` beats serving it.

**Residual, stated rather than hidden.** A 50 MB part shaped like that pivot cache would
still cost roughly 48 s and 2.5 GB (extrapolated from the measured 92 MB case, not
measured directly — pivot caches are per-byte pathological in openpyxl in a way worksheet
XML is not). That is bounded and survivable, and the parse already runs off the event loop
(``asyncio.to_thread``), where today's behaviour is unbounded: the issue measured a 16 MiB
upload at roughly 12 hours and 23 GB.
"""

from __future__ import annotations

import io
import zipfile

# ── Archive caps: a REFUSAL, checked before any document library sees the bytes ──

#: Entries in the package. Not here to bound THIS module's scan — measured at 0.04 s for
#: 32,768 members, so the scan is free — but to bound what the document libraries do when
#: handed an archive of a million one-byte members. Sized so the SLIDE cap is what stops an
#: absurd deck (a 1,000-slide deck measured 2,046 parts, so a refusal here would pre-empt
#: the truncation that is the honest answer): 32x that, 1,500x the largest real (43 parts).
MAX_ARCHIVE_ENTRIES = 65_536

#: Actual decompressed bytes of a SINGLE XML part. The number is ``doc_parser``'s
#: ``_MAX_ZIP_ENTRY`` verbatim — the guardrail said reuse the posture, not invent one.
MAX_XML_PART_BYTES = 50 * 1024 * 1024

#: Actual decompressed bytes summed over ALL XML parts. Deliberately 2x the per-part cap
#: so a document of many small parts is never refused by the total when no single part
#: would have failed — a 400-slide deck is many parts, and charging it the same total as
#: one enormous worksheet would refuse the shape the per-part cap was written to allow.
#: Largest real total measured (excluding the pivot-cache file): 908 KB.
MAX_XML_TOTAL_BYTES = 100 * 1024 * 1024

#: Parts whose bytes are XML this or a document library will parse. Media (images, fonts,
#: embedded objects) is deliberately OUT of the XML budget: it is large in legitimate
#: documents and cheap to skip, so charging it would refuse an ordinary photo deck.
_XML_SUFFIXES = (".xml", ".rels")

# ── Structural caps: TRUNCATE and append a ``size_limit`` loss ──

#: Blocks (paragraphs, tables, lists, images) a ``.docx`` parse will emit.
MAX_BLOCKS = 20_000

#: Cells a ``.xlsx`` parse will emit, summed across sheets.
MAX_CELLS = 500_000

#: Slides a ``.pptx`` parse will emit.
MAX_SLIDES = 1_000


class DocumentTooLarge(Exception):
    """An office package exceeds an archive cap, so it was never handed to a parser.

    Its own type rather than ``ValueError`` because the wire distinguishes the two: a
    refusal is ``document_too_large`` (413, "too big — read the raw bytes instead"),
    while anything else out of a parser is ``model_parse_failed`` (400, "this is not a
    document of that kind"). Telling a user their spreadsheet is corrupt when it is
    merely enormous is the failure this separation exists to prevent.
    """


def assert_archive_within_limits(data: bytes) -> None:
    """Refuse *data* if its XML exceeds the archive caps. Returns ``None`` when fine.

    Reads each XML part with a BOUNDED read and stops at the first breach, so the check
    costs O(cap) rather than O(file) and cannot itself be the denial of service.

    A ``data`` that is not a readable ZIP returns quietly: "this is not an office
    document" is the parser's error to raise, with its own wire code, and pre-empting it
    here would report a corrupt upload as an oversized one.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError, ValueError):
        return
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise DocumentTooLarge(
                f"the package holds {len(infos)} parts; this build opens at most "
                f"{MAX_ARCHIVE_ENTRIES}"
            )
        total = 0
        for info in infos:
            if not info.filename.lower().endswith(_XML_SUFFIXES):
                continue
            actual = _actual_size(archive, info)
            if actual > MAX_XML_PART_BYTES:
                raise DocumentTooLarge(
                    f"part {info.filename!r} decompresses to more than "
                    f"{_mb(MAX_XML_PART_BYTES)} of XML"
                )
            total += actual
            if total > MAX_XML_TOTAL_BYTES:
                raise DocumentTooLarge(
                    f"the package decompresses to more than "
                    f"{_mb(MAX_XML_TOTAL_BYTES)} of XML in total"
                )


def _actual_size(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> int:
    """The REAL decompressed size of one part, measured to at most one byte over the cap.

    ``info.file_size`` is the header's CLAIM and a hostile archive is free to lie in it —
    the same reason ``doc_parser._read_zip_entry`` reads the bytes rather than trusting
    the declaration. Reading in chunks and stopping one byte past the cap keeps this
    check's own cost and peak memory bounded, so the guard cannot become the outage.
    """
    seen = 0
    with archive.open(info) as part:
        while seen <= MAX_XML_PART_BYTES:
            chunk = part.read(1 << 20)
            if not chunk:
                break
            seen += len(chunk)
    return seen


def _mb(value: int) -> str:
    return f"{value // (1024 * 1024)} MB"


def truncation_detail(unit: str, kept: int, dropped_hint: str = "") -> str:
    """The one sentence all three parsers use for a structural cap, so a caller reading
    a ``size_limit`` loss sees the same shape whichever format produced it."""
    tail = f" {dropped_hint}" if dropped_hint else ""
    return (
        f"this document has more than {kept} {unit} and this build parses {kept}; "
        f"the rest were not read.{tail} Read the original bytes through the artifact's "
        "raw route to get the whole file."
    )
