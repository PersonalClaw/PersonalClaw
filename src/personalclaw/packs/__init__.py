"""Portable ``.pclaw`` packs — the shareable bundle format (AGENT-PACKS §1-§2).

A pack is a ZIP of *capability configuration only* — skills, workflow templates,
prompts and agents — that one user can hand to another. The existential risk is a
secret riding along, so redaction is TWO independent layers, defence-in-depth:

* the STRUCTURAL layer (:mod:`packs.deny`) never opens a secret/user-data file — the
  exporter's readers are an allowlist of the §1 component stores, and every read is
  routed through a home-relative deny check that fails CLOSED;
* the CONTENT layer (:func:`packs.build.build_pack`) scans every component's text with
  ``security.redact`` + ``guardrails.scan_outbound`` and BLOCKS any credential-bearing
  component rather than shipping it redacted.

Session 1 (this atom) is the format + export core: the dependency-closure walker, the
two redaction layers, and a look-before-write :func:`preview_pack`. The import side, the
connector catalog and the export UI/route are later AP atoms.
"""

from personalclaw.packs.build import (
    BlockedComponent,
    PackComponent,
    PackPreview,
    Requirement,
    build_pack,
    preview_pack,
)

__all__ = [
    "BlockedComponent",
    "PackComponent",
    "PackPreview",
    "Requirement",
    "build_pack",
    "preview_pack",
]
