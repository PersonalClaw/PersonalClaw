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

AP-1 is the format + export core: the dependency-closure walker, the two redaction layers,
and a look-before-write :func:`preview_pack`. AP-2 (this atom) is the import inverse:
:func:`inspect_pack` (dry-run, no writes) and :func:`import_pack` (leaves-first commit with
a journaled rollback, referential-integrity lint, and scan-by-origin trust). The connector
catalog and the export/import UI routes are later AP atoms.
"""

from personalclaw.packs.build import (
    BlockedComponent,
    PackComponent,
    PackPreview,
    Requirement,
    build_pack,
    preview_pack,
)
from personalclaw.packs.connectors import (
    MISSING_PREFIX,
    CatalogEntry,
    ConnectorResolution,
    ConnectorResolutionError,
    catalog_by_category,
    catalog_lookup,
    load_catalog,
    missing_marker,
    resolve_connector,
    resolve_for_import,
    resolve_requirements,
    seed_catalog,
)
from personalclaw.packs.import_ import (
    ImportPlan,
    PackImportRefused,
    PackMarketplace,
    PlannedComponent,
    import_pack,
    inspect_pack,
)
from personalclaw.packs.installed import InstalledPack, load_installed, record_install
from personalclaw.packs.lint import LintFinding, LintReport, lint_pack

__all__ = [
    "MISSING_PREFIX",
    "BlockedComponent",
    "CatalogEntry",
    "ConnectorResolution",
    "ConnectorResolutionError",
    "ImportPlan",
    "InstalledPack",
    "LintFinding",
    "LintReport",
    "PackComponent",
    "PackImportRefused",
    "PackMarketplace",
    "PackPreview",
    "PlannedComponent",
    "Requirement",
    "build_pack",
    "catalog_by_category",
    "catalog_lookup",
    "import_pack",
    "inspect_pack",
    "lint_pack",
    "load_catalog",
    "load_installed",
    "missing_marker",
    "preview_pack",
    "record_install",
    "resolve_connector",
    "resolve_for_import",
    "resolve_requirements",
    "seed_catalog",
]
