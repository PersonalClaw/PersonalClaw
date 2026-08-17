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
a journaled rollback, referential-integrity lint, and scan-by-origin trust). AP-5
(:mod:`packs.external_formats`) is the OUTBOUND direction — rendering an entity into another
tool's own format, where the same content layer runs on the rendered bytes. The pack store
UI is a later AP atom.
"""

from personalclaw.packs.build import (
    BlockedComponent,
    PackComponent,
    PackPreview,
    Requirement,
    build_pack,
    preview_pack,
)
from personalclaw.packs.bundled import (
    BundledPack,
    BundledPackError,
    build_bundled,
    bundled_packs,
    get_bundled,
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
from personalclaw.packs.external_formats import (
    EXTERNAL_FORMATS,
    ExportBlocked,
    ExportRefused,
    ExportResult,
    ExportSkill,
    ExternalFormat,
    RenderedFile,
    default_dest_dir,
    export_entities,
    export_preview,
    format_names,
    get_format,
)
from personalclaw.packs.import_ import (
    ImportPlan,
    PackImportRefused,
    PackMarketplace,
    PlannedComponent,
    import_pack,
    inspect_pack,
)
from personalclaw.packs.installed import (
    BindingError,
    InstalledPack,
    bind_answer,
    load_installed,
    record_install,
)
from personalclaw.packs.lint import LintFinding, LintReport, lint_pack
from personalclaw.packs.onelink import (
    OneLinkError,
    import_onelink,
    inspect_onelink,
    materialize,
    to_onelink,
)
from personalclaw.packs.prompt_cards import (
    PromptCardError,
    build_entity,
    convert_card,
    import_prompt_card,
    install_accepted_prompt_card,
    is_prompt_card_proposal,
)
from personalclaw.packs.roster import (
    ACTIVATION_ALWAYS,
    RosterEntry,
    Runbook,
    deploy_roster,
    lint_roster,
    load_roster,
    parse_catalog,
    parse_runbooks,
)

__all__ = [
    "ACTIVATION_ALWAYS",
    "EXTERNAL_FORMATS",
    "MISSING_PREFIX",
    "BindingError",
    "BlockedComponent",
    "BundledPack",
    "BundledPackError",
    "CatalogEntry",
    "ConnectorResolution",
    "ConnectorResolutionError",
    "ExportBlocked",
    "ExportRefused",
    "ExportResult",
    "ExportSkill",
    "ExternalFormat",
    "ImportPlan",
    "InstalledPack",
    "LintFinding",
    "LintReport",
    "OneLinkError",
    "PackComponent",
    "PackImportRefused",
    "PackMarketplace",
    "PackPreview",
    "PlannedComponent",
    "PromptCardError",
    "RenderedFile",
    "Requirement",
    "RosterEntry",
    "Runbook",
    "bind_answer",
    "build_bundled",
    "build_entity",
    "build_pack",
    "bundled_packs",
    "catalog_by_category",
    "catalog_lookup",
    "convert_card",
    "default_dest_dir",
    "deploy_roster",
    "export_entities",
    "export_preview",
    "format_names",
    "get_bundled",
    "get_format",
    "import_onelink",
    "import_pack",
    "import_prompt_card",
    "inspect_onelink",
    "inspect_pack",
    "install_accepted_prompt_card",
    "is_prompt_card_proposal",
    "lint_pack",
    "lint_roster",
    "load_catalog",
    "load_installed",
    "load_roster",
    "materialize",
    "missing_marker",
    "parse_catalog",
    "parse_runbooks",
    "preview_pack",
    "record_install",
    "resolve_connector",
    "resolve_for_import",
    "resolve_requirements",
    "seed_catalog",
    "to_onelink",
]
