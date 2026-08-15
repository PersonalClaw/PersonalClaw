# AGENT-PACKS — atomic plans

**Source plan:** [`AGENT-PACKS`](../plans/AGENT-PACKS.md)  
**Code:** `AP`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `AP-1` | ⬜ | Pack format + export core (dependency-closure walker + two-layer redaction) | — | build_pack(seeds) writes a schema_version=1 .pclaw ZIP: the §2.1 edge table resolves declared references with unresolvable edges demoted to requirements rows; structural layer (shared packs/deny.py, extends portability.EXPORT_EXCLUDE) never opens .env/.local_secret/memory.db/knowledge.db/sessions; content layer (security.redact + guardrails scan wrapper) BLOCKS any credential-bearing component; export preview tree renders before writing; golden-pack fixture round-trip greps clean of planted canary secrets. |
| `AP-2` | ⬜ | Import core: inspect-without-write, leaves-first commit, rollback, ref-integrity lint, scan | `AP-1` | import runs inspect (dry-run, no writes) then quarantine→integrity recompute→packs/lint.py ref-integrity lint→leaves-first commit with per-write journal at packs/.installing/<id>.json; a DANGEROUS-pattern skill is refused regardless of consent and WARNING needs explicit consent; fault-injected mid-import unwinds every journaled write to byte-identical pre-import state; skills commit through PackMarketplace→install_guarded producing .pclaw-lock.json; fresh-id rewriting on parsed objects (never raw bytes); triggers land disabled and config_subset staged; every step SEL-audited. |
| `AP-3` | ⬜ | Requirements resolution + connector catalog (configure/substitute/skip) + setup-skill + PacksConfig | `AP-2` | connector_catalog.json store seeded; each connectors.json declaration resolves via configure (credential collected, server written through providers/mcp_instances)/substitute (same-category rewrite)/skip; skipped deps degrade with machine-readable connector_missing:<name>; setup/SKILL.md installs through the guarded path and surfaces as a re-runnable Finish-setup chip; new PacksConfig (skill_catalogs, fingerprint_enabled, connector_catalog_url) wired through dataclass _meta, load(), to_dict(), _EDITABLE_CONFIG+FE with test_config_roundtrip green. |
| `AP-4` | ⬜ | Pack kinds: agent/roster packs, prompt-card importer, bundled Domain OS packs, one-link serialization | `AP-2`, `AP-3` | Personal CFO + Health OS bundled first-party packs export→wipe→import on a fresh PERSONALCLAW_HOME with skills locked, template runnable, digest trigger DISABLED, connector configure-or-substitute prompt, and setup interview binding a folder; roster pack imports rendering persona markdown into config agents{} with every runbook/catalog slug lint-resolved (a broken slug blocks import naming the exact unresolved ref) and only the always tier one-click-deploys; prompt-card importer fences pasted input, maps to typed PromptTemplate/WorkflowDef/AgentDefinition through the proposal review flow; one-link JSON serialization imports through the same §3 pipeline. |
| `AP-5` | ✅ | Outbound multi-tool export (ExternalFormat + 3 renderers + byte-identical golden tests) | `AP-1` | packs/external_formats.py ExternalFormat(name,installKind,dest,render) contract + Claude Code agents / Cursor rules / SKILL.md renderers export a PersonalClaw agent into a ~/.claude/agents/<slug>.md that Claude Code actually loads; a per-format golden-file test proves byte-identical rendering across runs; §2.2 content redaction runs on rendered output; export lands in a user-chosen directory only after explicit dest confirmation. |
| `AP-6` | ⬜ | Inbound skill-catalog importer (CatalogMarketplace via install_guarded chokepoint) | `AP-3` | packs/catalog_marketplace.py CatalogMarketplace(SkillsMarketplace) registers each configured catalog (packs.skill_catalogs) on get_default_skills_registry at COMMUNITY tier; fetch() pulls files via net.fetch under the CONNECTOR egress profile returning SkillDetail so install_guarded does quarantine/scan/commit/lock; installing a catalog skill produces a standard .pclaw-lock.json and passes verify_skill_integrity (zero chokepoint bypass); Skills store gains a source filter + per-source counts; a large index browses client-side without entering the agent budget until install. |
| `AP-7` | ⬜ | Project-fingerprint auto-surfacing + pack update flow + pack store FE + validation sweep | `AP-3`, `AP-4` | packs/fingerprint.py zero-LLM scanner over Project.workspace_dir matches declared fingerprints on project-create and on-demand only; a Terraform-shaped dir surfaces a propose-only pack card with confidence + the §3.1 inspect report; rejecting once is remembered per (project,pack) and never re-nags; packs.fingerprint_enabled=false stops scanning entirely; pack update overwrites only pack_owned components and skips computedHash-drifted user-edited copies with a visible drift note; pack store/detail FE ships and the export→wipe→import round-trip validation sweep on a second PERSONALCLAW_HOME passes. |

## Atom scopes

### `AP-1` — Pack format + export core (dependency-closure walker + two-layer redaction)

**Status:** todo

§1 The .pclaw Pack Format; §2.1 Dependency-closure walker; §2.2 Deny-list secret redaction. Session 1. Soft: reads WORKFLOWS-V2 template store via per-kind adapter reading whichever shape exists on disk.

**Done when:** build_pack(seeds) writes a schema_version=1 .pclaw ZIP: the §2.1 edge table resolves declared references with unresolvable edges demoted to requirements rows; structural layer (shared packs/deny.py, extends portability.EXPORT_EXCLUDE) never opens .env/.local_secret/memory.db/knowledge.db/sessions; content layer (security.redact + guardrails scan wrapper) BLOCKS any credential-bearing component; export preview tree renders before writing; golden-pack fixture round-trip greps clean of planted canary secrets.

### `AP-2` — Import core: inspect-without-write, leaves-first commit, rollback, ref-integrity lint, scan

**Status:** todo

§3.1 Pipeline; §3.2 Referential-integrity linter; §3.5 Trust scan-everything-tier-by-origin; §8 skills path (PackMarketplace→install_guarded). Session 2. Success criteria 2, 3.

**Done when:** import runs inspect (dry-run, no writes) then quarantine→integrity recompute→packs/lint.py ref-integrity lint→leaves-first commit with per-write journal at packs/.installing/<id>.json; a DANGEROUS-pattern skill is refused regardless of consent and WARNING needs explicit consent; fault-injected mid-import unwinds every journaled write to byte-identical pre-import state; skills commit through PackMarketplace→install_guarded producing .pclaw-lock.json; fresh-id rewriting on parsed objects (never raw bytes); triggers land disabled and config_subset staged; every step SEL-audited.

### `AP-3` — Requirements resolution + connector catalog (configure/substitute/skip) + setup-skill + PacksConfig

**Status:** todo

§3.3 Connector catalog + configure-or-substitute; §3.4 Post-install setup-skill interview; §8 four config wiring points; §9 stores. Session 3. Soft: re-satisfies against WORK-R19 secrets store when present, save_credential fallback until then.

**Done when:** connector_catalog.json store seeded; each connectors.json declaration resolves via configure (credential collected, server written through providers/mcp_instances)/substitute (same-category rewrite)/skip; skipped deps degrade with machine-readable connector_missing:<name>; setup/SKILL.md installs through the guarded path and surfaces as a re-runnable Finish-setup chip; new PacksConfig (skill_catalogs, fingerprint_enabled, connector_catalog_url) wired through dataclass _meta, load(), to_dict(), _EDITABLE_CONFIG+FE with test_config_roundtrip green.

### `AP-4` — Pack kinds: agent/roster packs, prompt-card importer, bundled Domain OS packs, one-link serialization

**Status:** todo

§4.1 Domain OS packs; §4.2 Agent/roster packs; §4.3 Prompt-card importer; §4.4 / §2.3 One-link setup export. Session 4. Success criteria 1, 4. Soft: WORK-R16 roster slug refs, LEARNING-FLYWHEEL {source,computedHash} locks.

**Done when:** Personal CFO + Health OS bundled first-party packs export→wipe→import on a fresh PERSONALCLAW_HOME with skills locked, template runnable, digest trigger DISABLED, connector configure-or-substitute prompt, and setup interview binding a folder; roster pack imports rendering persona markdown into config agents{} with every runbook/catalog slug lint-resolved (a broken slug blocks import naming the exact unresolved ref) and only the always tier one-click-deploys; prompt-card importer fences pasted input, maps to typed PromptTemplate/WorkflowDef/AgentDefinition through the proposal review flow; one-link JSON serialization imports through the same §3 pipeline.

### `AP-5` — Outbound multi-tool export (ExternalFormat + 3 renderers + byte-identical golden tests)

**Status:** done

§5 Multi-Tool OUTBOUND Export (amendment c). Session 5 (first half). Success criterion 6. Reuses §2.2 content-layer redaction.

**Done when:** packs/external_formats.py ExternalFormat(name,installKind,dest,render) contract + Claude Code agents / Cursor rules / SKILL.md renderers export a PersonalClaw agent into a ~/.claude/agents/<slug>.md that Claude Code actually loads; a per-format golden-file test proves byte-identical rendering across runs; §2.2 content redaction runs on rendered output; export lands in a user-chosen directory only after explicit dest confirmation.

**DONE.** `packs/external_formats.py` ships the `ExternalFormat(name, installKind, dest, render)`
contract, the three v1 renderers (`claude-code-agents` per-agent, `cursor-rules` roster,
`skill-md` plugin) and an all-or-nothing writer: render → containment-check every path → §2.2
scan → clobber-check → write. Redaction imports `packs.build._scan_component` rather than
forking it, so the content rules stay shared; a hit blocks the whole batch before any file
exists. Containment is checked twice (slug validation in the renderer, resolved-path
validation in the writer) and a file we did not write — recognised by a constant provenance
trailer — is never overwritten. `tests/fixtures/external_formats_golden/` pins per-format
bytes; `tests/test_packs_external_formats.py` renders twice per format and diffs the golden.

Two deliberate calls. (1) The exported agent frontmatter carries `name`/`description`
(+ `model` when pinned) but NOT `tools`: an `AgentDefinition` has no tool-allowlist field, so
any value there would be invented data that silently narrows the exported agent — declared
skills go in the body instead, where an unknown key cannot break the recipient's parse.
(2) No HTTP route or CLI yet — the pack store/detail FE is `AP-7`'s scope; the registry's
readers today are `get_format`/`export_entities` plus the `personalclaw.packs` package export.
Format conformance is asserted by parsing the rendered frontmatter with a real YAML loader;
**no external binary was executed**.

### `AP-6` — Inbound skill-catalog importer (CatalogMarketplace via install_guarded chokepoint)

**Status:** todo

§6 INBOUND Skill-Catalog Importer (amendment d, first half); §8 skills path. Session 5 (second half). Success criterion 5. Needs PacksConfig.skill_catalogs.

**Done when:** packs/catalog_marketplace.py CatalogMarketplace(SkillsMarketplace) registers each configured catalog (packs.skill_catalogs) on get_default_skills_registry at COMMUNITY tier; fetch() pulls files via net.fetch under the CONNECTOR egress profile returning SkillDetail so install_guarded does quarantine/scan/commit/lock; installing a catalog skill produces a standard .pclaw-lock.json and passes verify_skill_integrity (zero chokepoint bypass); Skills store gains a source filter + per-source counts; a large index browses client-side without entering the agent budget until install.

### `AP-7` — Project-fingerprint auto-surfacing + pack update flow + pack store FE + validation sweep

**Status:** todo

§7 Project-Fingerprint Auto-Surfacing (amendment d, second half); §1 pack_owned update flow; §9 fingerprint_rejections store; pack store/detail FE. Session 6. Success criteria 7, 8.

**Done when:** packs/fingerprint.py zero-LLM scanner over Project.workspace_dir matches declared fingerprints on project-create and on-demand only; a Terraform-shaped dir surfaces a propose-only pack card with confidence + the §3.1 inspect report; rejecting once is remembered per (project,pack) and never re-nags; packs.fingerprint_enabled=false stops scanning entirely; pack update overwrites only pack_owned components and skips computedHash-drifted user-edited copies with a visible drift note; pack store/detail FE ships and the export→wipe→import round-trip validation sweep on a second PERSONALCLAW_HOME passes.

