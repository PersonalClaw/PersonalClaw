# Plan: Agent Packs & Portable Bundles — Share a Composed PersonalClaw

**Status:** PROPOSED — created 2026-07-13 from research synthesis, promoted from backlog
**Created:** 2026-07-13
**Wave:** 4 (pre-publication) — this is the distribution story for a product heading toward open publication; it consumes entity stores that Waves 1-3 stabilize (workflow templates, unified triggers, the flywheel lifecycle, project secrets) and is the sharing channel for flywheel-earned templates. Slices 1-2 (format + export/import core) have no hard engine dependency and could pull forward if publication accelerates.
**Depends on:** softly on WORKFLOWS-V2 (template store shape), WORKFLOWS-V2-AUTOMATION-SUBSTRATE (`triggers.json` unified store — pack triggers import as *staged/disabled* either way), WORK-R19 (per-project secrets store — the requirements manifest re-satisfies against it), LEARNING-FLYWHEEL (`{source, computedHash}` lock convention on imported templates). None is a blocker: every dependency has a today-shaped fallback named in §2/§3.
**Scope:** one portable bundle format (`.pclaw` packs) with dependency-closure export, redaction, and non-forgeable provenance; a leaves-first transactional importer with fresh-id cross-ref rewriting and a referential-integrity linter; connector configure-or-substitute + post-install setup-skill interview; pack kinds (Domain OS, agent/roster, prompt-card, one-link setup); a multi-tool OUTBOUND exporter to external harness formats; an agentskills.io-style INBOUND catalog importer; and project-fingerprint auto-surfacing that *proposes* packs.

---

## Research Integration (2026-07-13)

- **NEW-12** (installable bundles of composed configurations: identity, config subset, skills, workflow templates, triggers, MCP connector declarations, app wiring; dependency-closure `.pclaw` export/import; fresh-id cross-ref rewriting; leaves-first import with rollback; referential-integrity linter; credentials/memories NEVER ship + requirements manifest; deny-list secret redaction; schema-versioned forward-compat; connector catalog with configure-or-substitute; post-install setup-skill interview; Domain OS packs; prompt-card importer) → §1-§5, sources `hermes-agent` (profile distributions, `distribution.yaml`, `env_requires`, distribution-owned paths, credentials-never-ship), `anthropic-financial-services` (vertical bundles, leaves-first manifest resolution, connector catalog, check.py ref-integrity linting, version-bump-on-touch), `omnivoice-studio` (`.ovsvoice`: integer `schema_version`, best-effort forward import, non-forgeable provenance recomputed from bundle contents, zip-slip defense, mid-import rollback, inspect-without-writing), `mattpocock-skills` (setup-skill convention binding abstract roles to the environment), `milesdeutscher-x-post` (Domain OS / prompt-card genre).
- **NEW-12 amendment (a)** (agent-pack/roster-pack installs: catalog JSON + persona markdown + optional scenario runbooks with staged rosters, slug-resolution-checked on install, one-click team deploy) → §4.2, source `agency-agents` (divisions.json / runbooks.json / activation staging).
- **NEW-12 amendment (b)** (one-link setup export/import: skills+prompts+workflow templates+app configs, never secrets, single shareable manifest with per-resource hashes) → §4.4, sources `different-ai-openwork`, `tryfriday`.
- **NEW-12 amendment (c)** (multi-tool export rendering AgentDefinitions/skills into external harness formats via a format/installKind/dest contract) → §5, source `agency-agents` (tools.json).
- **NEW-12 amendment (d)** (inbound half: agentskills.io-style importer pulling external skill catalogs into the skill store; project-fingerprint auto-surfacing that scans workspace file patterns, confidence-scores a project type, and proposes the matching pack) → §6, §7, sources `hermes-agent` (skills federation, ~150-skill hub), `openclaw` (~13.7k catalog), `skillclaw`, `openjarvis`.

---

## Overview

PersonalClaw installs apps and skills separately but **nothing bundles a coherent domain**. Verified starting points (recon 2026-07-12, providers + persistence briefs):

- **Apps platform is the nearest host and the install-discipline template.** `apps/app_manager.py:install()` stages in quarantine FIRST, scans via `supply_chain.default_scanner.scan(staged, tier)` with `_tier_for_origin` (builtin/registry→OFFICIAL/local+external→COMMUNITY), refuses DANGEROUS unconditionally, requires consent on WARNING, records provenance `source_ref` in `installed.json`, and commits via atomic swap with `.{name}.rollback` crash recovery. `AppManifest` (`apps/manifest.py:557`) already carries `mcpServers{}`, `prompts[]`, `crons[]`, `dependencies{marketplace{mcp,skills,agents}}`, and `PROVIDER_TYPES` (manifest.py:453) guarded by `test_manifest_types_match_handlers`.
- **Skill installs have the hardened chokepoint to reuse, not re-derive.** `skills/marketplace.py:install_guarded()` (:345): fetch → quarantine → `scan_dir` at marketplace tier → commit the EXACT scanned bytes (TOCTOU closed) → `.pclaw-lock.json` `{id, source, trust_tier, verdict, per-file sha256, installed_at}` → SEL audit at every step; `verify_skill_integrity` diffs disk vs lock. New skill sources plug in as `SkillsMarketplace` implementations registered on `get_default_skills_registry()` — install_guarded supplies quarantine/scan/lock for free.
- **The entities a pack composes live in knowable stores:** workflow templates at `~/.personalclaw/workflows/<name>/` today (WORKFLOWS-V2 moves them to versioned `templates/<slug>/vN.json` — the exporter reads whichever exists); prompts at `~/.personalclaw/prompts/*.yaml` (`PromptTemplate`); agents in `config.json agents{}` (the `agent` extension type is an `EntitySeamHandler` whose source_of_truth IS config — `providers/registry.py:364`; there is **no SOUL.md-style identity file** — "identity" in a pack means AgentDefinition entries + a persona markdown rendered into them, §4.2); triggers in `crons.json`/`hooks.json`/`event_triggers.json` today, `triggers.json` post-substrate; MCP servers in `~/.personalclaw/mcp.json` (multi-instance seam `providers/mcp_instances.py`); app settings at `~/.personalclaw/apps/{name}/data/config.json` (`ProviderSettings`).
- **Secrets/redaction primitives exist:** `portability.py:EXPORT_EXCLUDE` (:38) is the deny-list precedent (.env, .local_secret, sel_hmac.key, telemetry_salt, session_map.json, pid files); `security.py:redact()` (:658) catches credential-shaped strings; `save_credential()` (config/loader.py:255) is where re-satisfied requirements land.
- **Approved neighbors this plan must NOT duplicate:** **WORK-R15** (WORK-CONTAINERS §1.7) owns the *project* export/import contract — manifest ZIP, per-entity sha256, path-safety validation (reject `../`, absolute paths, symlinks, null bytes — the `snapshot.py:_data_filter` posture), `imported-N` collision slots, optional AES-GCM, secrets-never-travel. This plan **reuses that exact contract as the container layer** (§1) and scopes itself to what R15 does not do: packs are *capability compositions* (skills+templates+agents+connectors+config), not project state. **DURABILITY-AND-SYNC** owns backup/portability of *your own* state between *your own* machines; packs are for *sharing with others* — different threat model (recipient is untrusted-to-you and you are untrusted-to-recipient), hence redaction + provenance + import scanning here. **LEARNING-FLYWHEEL** already specifies `{source, computedHash}` locks on pinned shared/imported templates — the importer stamps exactly that.

**The soul guardrail:** a pack is files in a ZIP, installed by one person onto one machine, reviewable in a text editor. No registry service, no accounts, no signing infrastructure (provenance is recomputed-from-contents, not PKI), no pack "store" backend — the catalog is a JSON file fetched over `net.fetch`. Everything a pack proposes (triggers, agents, config changes) lands **staged/disabled and human-enabled** — propose-don't-write applied to distribution.

---

## 1. The `.pclaw` Pack Format

A pack is a ZIP with a top-level `pack.json` manifest — the `.ovsvoice` checklist adopted wholesale, layered on WORK-R15's container contract:

```
mypack.pclaw
├── pack.json                    # manifest (below)
├── skills/<slug>/SKILL.md ...   # skill dirs, verbatim install_guarded-compatible file sets
├── templates/<slug>.json        # workflow templates (WorkflowDef JSON; v1 SOP dirs exported as-is)
├── prompts/<slug>.yaml          # PromptTemplate YAML
├── agents/<slug>.md             # persona markdown (frontmatter + body → AgentDefinition, §4.2)
├── agents/catalog.json          # roster catalog (labels/icons/colors/activation), amendment (a)
├── triggers/<slug>.json         # trigger declarations — ALWAYS import disabled
├── connectors.json              # MCP connector DECLARATIONS (no credentials): {name, transport, url|command, category, auth: {required, kind, env_hint}}
├── app_config/<app>.json        # non-secret settings overlays for named apps
├── config_subset.json           # dotted-path config proposals (validated against _EDITABLE_CONFIG on import)
└── setup/SKILL.md               # optional post-install interview skill (§3.4)
```

`pack.json`:

```json
{
  "schema_version": 1,
  "name": "personal-cfo", "version": "1.2.0", "displayName": "Personal CFO",
  "description": "...", "author": "...", "license": "MIT",
  "pclaw_requires": ">=0.9",
  "components": [ {"kind": "skill|template|prompt|agent|trigger|connector|app_config|config", "id": "<slug>", "path": "...", "sha256": "...", "depends_on": ["kind:slug", ...]} ],
  "requirements": [ {"kind": "credential|connector|app|local_model", "id": "...", "description": "...", "required": true, "env_hint": "OPENAI_API_KEY"} ],
  "pack_owned": ["skills/cfo-*", "templates/*"],
  "provenance": {"exported_by_version": "...", "exported_at": "...", "content_hash": "<sha256 over sorted component hashes>"}
}
```

Load-bearing rules, each traceable to a verified source mechanism:

- **Integer `schema_version` with best-effort forward import** (.ovsvoice): a pack from a future schema imports what it understands, flags `schema_version_ahead=true` in the report, never hard-fails on unknown manifest keys.
- **Non-forgeable provenance**: manifest flags are advisory; anything trust-bearing is **recomputed from actual bundle contents on import** (per-file sha256 re-hashed, `content_hash` re-derived). A pack claiming components it doesn't contain fails the integrity check, not the reader's trust.
- **Credentials and memories NEVER ship** — structurally, not by discipline: the exporter has no code path that reads `.env`, `memory.db`, `knowledge.db`, session JSONLs, or `sel_hmac.key` (§2.2). What ships instead is the `requirements` manifest, re-satisfied on import (§3.3). This is Hermes's "`auth.json` and `.env` are never part of a distribution" plus its `env_requires` interview, verbatim.
- **`pack_owned` paths** (Hermes `distribution_owned`): on pack *update*, only pack-owned entities are overwritten; user-modified copies are skipped with a drift note (the skills-lock `computedHash` tells us which is which — the LEARNING-FLYWHEEL lock convention doing double duty).
- **Path safety = WORK-R15's contract**, reused not reimplemented: member names never build paths directly (zip-slip defense), reject traversal/absolute/symlink/NUL, extract to unique tmp with janitor cleanup.

**Memory vs Knowledge boundary (explicit):** packs carry **harness capability configuration only**. `memory.db` content (harness mechanics) never exports — a recipient's assistant must not inherit the author's episodic/semantic memory. `knowledge.db` items (the user's personal documents/files/photos) never export either — they are personal data, not capability. The ONLY learning-adjacent thing a pack may carry is a *skill* (already a shareable file entity behind install_guarded). Nothing in this plan writes to `memory.db` or `knowledge.db`.

---

## 2. Export — Dependency Closure + Redaction

### 2.1 Dependency-closure walker

`packs/export.py:build_pack(seed_entities) -> PackBuild`. Starting from user-selected seeds (e.g. "the Personal CFO template + its skills"), the walker follows *declared* references only — each edge is a real, greppable reference in today's stores:

| Edge | Where the reference lives (verified) |
|---|---|
| template → agent slug | WORK-R16 roster slugs in template `agent:` fields (WORK-CONTAINERS §3: templates reference agents by slug, drift-checked) |
| template → skill | template prose/`runtime_hints` skill mentions; post-flywheel, the template's declared skill deps |
| template → prompt use-case | `active_prompts.json`-style `provider:prompt_name` refs |
| trigger → template/prompt | trigger action config (`run-workflow`/`run-prompt` action payloads) |
| skill → connector | SKILL.md frontmatter MCP-tool mentions (advisory; missing ones become `requirements` rows) |
| anything → app | app-owned prompts/settings (`~/.personalclaw/apps/{name}/data/config.json`) become `app_config` components + an `app` requirement (the app itself is NOT embedded — it stays an App Store install, declared as a requirement) |

Unresolvable edges don't block export — they demote to `requirements` rows (the recipient satisfies or substitutes them). The closure is rendered as a **preview tree in the export UI** before writing anything, mirroring `GET /personas/inspect`'s look-before-write idiom on the import side.

### 2.2 Deny-list secret redaction — two layers

1. **Structural layer:** the exporter's store readers are an allowlist of the §1 component stores. `.env`, `.local_secret`, `sel_hmac.key`, `telemetry_salt`, `session_map.json`, `memory*.db`, `knowledge.db`, `sessions/`, `security_events.jsonl` are not merely excluded — they are **never opened** (extends `portability.py:EXPORT_EXCLUDE` into a shared `packs/deny.py` constant both modules import, so the lists can't drift).
2. **Content layer:** every text component is scanned with `security.py:redact()` + the key-shaped-string patterns from AUTONOMY-GUARDRAILS §2.2's scan wrapper (shared rules, not a fork) before zipping. A hit **blocks export of that component** (never silently rewrites someone's prose) with a per-finding report — the author fixes or excludes. `connectors.json` entries are schema-constrained to have NO value-bearing auth fields (only `auth: {required, kind, env_hint}`), so a credential can't ride a connector declaration even intentionally.
3. App-config overlays are filtered against each app's `settingsSchema`: any field marked secret/`x-meta: credential` in the Draft-07 schema is dropped and demoted to a `requirements` row.

### 2.3 One-link setup export (amendment b)

The same `PackBuild`, serialized as a single JSON manifest (base64-embedded small files, URL references + sha256 for large ones) behind one shareable link/file — "here is my setup" without the ZIP ceremony. Same redaction, same schema_version, same importer (§3); it is a *serialization* of a pack, not a second format. Per-resource hashes make partial fetch verifiable.

---

## 3. Import — Leaves-First, Fresh IDs, Rollback, Lint

### 3.1 Pipeline (one transaction shape, borrowed from proven code)

```
fetch/open → inspect (dry-run report, NO writes — the /personas/inspect pattern)
→ quarantine extract (WORK-R15 path-safety) → integrity recompute (§1)
→ referential-integrity lint (§3.2) → supply-chain scan (§3.5)
→ requirements resolution (§3.3, interactive) → leaves-first commit → lock stamping
→ post-install setup skill (§3.4)
```

- **Leaves-first commit** (the `deploy-managed-agent.sh` topology, resolved against `depends_on`): connectors and skills first, then prompts, then agents, then templates, then triggers — so every cross-reference a later component makes already resolves when it lands. **Rollback:** every written path is journaled to `~/.personalclaw/packs/.installing/<id>.json` before writing (the app_manager `.rollback` idiom); any mid-import failure unwinds every journaled write. No partial packs.
- **Fresh-id cross-ref rewriting:** every imported entity gets a fresh local id/slug when it collides (`imported-N` slots, the WORK-R15 convention), and the importer rewrites all intra-pack references to the fresh ids in one pass over the parsed components (never string-replace over raw bytes). Server-generated ids, never manifest-trusted ids — the .ovsvoice rule.
- **Lock stamping:** every installed component gets the flywheel's `{source, computedHash}` lock; skills additionally get their standard `.pclaw-lock.json` because they commit **through `install_guarded`'s file-writer path** (`install_skill_files`, which re-scans per file — defense-in-depth inherited, not reimplemented). A `PackMarketplace` (§8) adapts pack skill dirs to the `SkillDetail{name, files}` shape so the existing chokepoint does the work.
- **Triggers import DISABLED, always.** A pack cannot arm automation on install — the user enables each trigger from the Automations page, where AUTONOMY-GUARDRAILS profiles/budgets apply. Today they land in `crons.json`/`hooks.json` with `enabled: false`; post-substrate, in `triggers.json` staged. `config_subset.json` entries are likewise **proposals**: validated against `_EDITABLE_CONFIG` (dashboard/handlers/core.py:363) and presented as an accept/skip checklist — never applied wholesale (a pack cannot edit config fields the user couldn't edit through the UI).

### 3.2 Referential-integrity linter

`packs/lint.py` — the check.py pattern, run at export AND import: every `depends_on` resolves to a bundled component or a `requirements` row; every agent slug referenced by a template resolves (the WORK-R16 drift check applied to pack scope); every skill mentioned in template/agent prose (backtick-hyphenated-identifier regex, the check.py trick) is bundled or declared; JSON/YAML parse-lints every component (Anthropic's own repo shipped a broken .mcp.json — parse-lint everything); prompt variables referenced by triggers exist. Lint failures block export hard and block import with an override only for WARNING-class findings (mirroring the scanner's force semantics).

### 3.3 Connector catalog + configure-or-substitute

A first-class **connector catalog** — a JSON file (`~/.personalclaw/connector_catalog.json`, seeded with a bundled starter set, user-extendable, optionally refreshed from a published URL via `net.fetch` under the CONNECTOR egress profile): entries `{name, category, transport, url|command, auth: {kind, env_hint}, description}`. At import, each pack `connectors.json` declaration resolves through it:

- **Configure:** the declared connector exists in the catalog (or is self-contained) → the UI collects the required credential (stored via `save_credential()` → `.env`, or the WORK-R19 project secrets store once it lands — the requirements manifest re-satisfies against whichever exists) and writes the server into `~/.personalclaw/mcp.json` through the existing `providers/mcp_instances.py` seam (which already guards against injectable keys).
- **Substitute:** the user picks a different catalog entry of the same `category` (e.g. their own search MCP instead of the pack author's) — the importer rewrites the pack's connector references to the substitute.
- **Skip:** the requirement is recorded unmet; dependent components install anyway and **degrade with a machine-readable reason** (`connector_missing:<name>` — the degraded-completion idiom WORKFLOWS-V2 already blessed), visible on the pack's detail page.

### 3.4 Post-install setup-skill interview

A pack may ship `setup/SKILL.md` — the mattpocock `/setup-*-skills` convention: a user-invoked skill (installed through the same guarded path as every other skill) that interviews the user to bind abstract roles to their environment ("which folder holds your finance CSVs?", "which channel should weekly digests go to?") and writes the answers into the pack's app_config overlays / prompt variables via existing APIs. It is a *skill*, so it runs under normal tool approval — no new execution surface. The importer surfaces it as a one-click "Finish setup" chip on the pack card; skippable, re-runnable.

### 3.5 Trust: scan everything, tier by origin

Pack content passes `default_scanner` at the tier `_tier_for_origin` maps for its source (URL install → COMMUNITY, the bundled starter packs → BUILTIN). DANGEROUS refuses always; WARNING needs explicit consent — identical semantics to apps and skills, one shared verdict vocabulary. Every step SEL-audited (`sel.py`), same as skill installs.

---

## 4. Pack Kinds (what actually ships)

All four are **the same format** — kinds differ only in which components dominate and which bundled starter packs we author.

### 4.1 Domain OS packs

The flagship kind: **Personal CFO** (budget-review template + spending-digest trigger [disabled] + finance skills + a finance-category connector requirement + a CFO agent persona) and **Health OS** (checkup-cadence triggers + journaling template + health skills). We author these two as bundled first-party packs — they are the acceptance test for the whole mechanism (§Success 1) and the reference for third-party authors. Personal-scale: each is <20 components.

### 4.2 Agent packs / roster packs (amendment a)

`agents/catalog.json` (the agency-agents divisions.json shape: `{slug, name, description, label, icon, color, activation}`) + one persona markdown per agent + optional scenario runbooks with **staged rosters** (`activation: always | phase-N | as-needed`). Import path honors the real seam: AgentDefinitions live in `config.json agents{}` (EntitySeamHandler source-of-truth), so persona markdown renders into config agent entries (frontmatter → profile fields, body → system-prompt content), **slug-resolution-checked on install** (every runbook/catalog slug must resolve to a bundled persona — lint §3.2), and the WORK-R16 generated roster projection picks them up with zero extra wiring. "One-click team deploy" = install the pack, enable the `always` roster tier; staged tiers surface as suggestions in the relevant project/workflow context, not auto-enabled.

### 4.3 Prompt-card importer

A small converter for the viral OS-prompt genre (milesdeutscher-style "life OS" prompt cards): paste a prompt card (markdown/text) → an LLM pass (`one_shot_completion(use_case="background")`, output_type-constrained per AUTONOMY-GUARDRAILS §2.4) maps it onto `{PromptTemplate | WorkflowDef skeleton | AgentDefinition}` → the result enters the normal **proposal review** flow (rendered diff, accept/edit/reject) before any store writes. Pasted card text is fenced with `fence_untrusted` before the LLM sees it — a prompt card is attacker-controlled input. This is deliberately an importer *into* existing entities, not a fifth entity kind.

### 4.4 One-link setup export

§2.3 — same importer, JSON serialization, per-resource hashes.

---

## 5. Multi-Tool OUTBOUND Export (amendment c)

Render PersonalClaw AgentDefinitions and skills into external harness formats, per the proven agency-agents `tools.json` contract — **an export surface, not a sync system** (one-shot render; drift is the recipient tool's problem):

```python
# packs/external_formats.py
@dataclass(frozen=True)
class ExternalFormat:
    name: str            # "claude-code-agents" | "cursor-rules" | "skill-md"
    installKind: str     # per-agent | roster | plugin
    dest: str            # "~/.claude/agents/{slug}.md" | ".cursor/rules" | "<dir>/skills/{slug}/SKILL.md"
    render: Callable[[Entity], list[RenderedFile]]
```

- v1 formats: **Claude Code agents** (`~/.claude/agents/<slug>.md`, frontmatter name/description/tools), **Cursor rules** (roster-kind single `.cursorrules`), **SKILL.md dirs** (skills exported near-verbatim — they already ARE the agentskills.io shape).
- Byte-identical-per-format guarantee (the tools.json rule): two targets share a `format` only if rendered output is identical — enforced by a golden-file test per format.
- Redaction (§2.2 content layer) runs on rendered output too; exports land in a user-chosen directory, never auto-installed into another tool's config without an explicit dest confirmation.
- **Boundary note:** NEW-13's "PClaw as context provider" (routed-context CLAUDE.md adapters) is a *live context* surface and stays with NEW-13; this section renders *entities* (agents/skills), one-shot. Complementary, not overlapping.

---

## 6. INBOUND Skill-Catalog Importer (amendment d, first half)

The inbound half of distribution — and the cheapest slice, because the chokepoint already exists:

- `packs/catalog_marketplace.py:CatalogMarketplace(SkillsMarketplace)` — one class, N instances: each configured catalog (agentskills.io-style index URL, a GitHub "tap" repo with `skills/<slug>/SKILL.md`, a `/.well-known/skills/index.json` site) registers as a named marketplace on `get_default_skills_registry()` with `trust_tier=COMMUNITY`. `fetch(skill_id)` pulls files via `net.fetch` under the CONNECTOR egress profile and returns `SkillDetail{name, files}` — **`install_guarded` then does everything else** (quarantine, scan, TOCTOU-closed commit, `.pclaw-lock.json`, SEL). Zero new install machinery.
- Catalog list lives in config (`packs.skill_catalogs: list[{name, url, kind}]` — four wiring points, §8); the Skills store UI gains a source filter + per-source counts. Search is client-side over the fetched index (personal-scale; no search service).
- Scale honesty: a 13.7k-entry catalog imports its *index* (name/description rows) for browse/search; skills fetch lazily on install. The index is data, never context — nothing enters the agent's budget until a skill is actually installed and surfaced by the existing `skills/surfacing.py` machinery.

---

## 7. Project-Fingerprint Auto-Surfacing (amendment d, second half)

Packs get *discovered*, not just installed:

- `packs/fingerprint.py`: a deterministic, zero-LLM scanner over a project's workspace dir (the `Project.workspace_dir` binding, `tasks/models.py:316`) matching declared fingerprints — file-pattern rules each pack/catalog entry may carry (`{"globs": ["*.tf"], "signals": ["provider \"aws\""], "confidence": 0.9}` → "terraform project"). Runs on project creation and on-demand ("Suggest packs" button), never on a background loop in v1 (AUTOMATION-SUBSTRATE can later give it a trigger kind; the function is trigger-shaped from day one).
- A match **proposes** — a card in the project hub / pack store ("This looks like a Terraform project — the `infra-ops` pack matches, 0.9 confidence, here's what it would install") with the full §3.1 inspect report behind it. Never auto-installs, never auto-enables. Rejections are remembered per (project, pack) so it never re-nags — the mattpocock `.out-of-scope` prior-rejection rule.
- Disposition note: PROACTIVE-ASSISTANT owns *digest*-surface proposals; this proposal renders in the pack store and project hub, and (once PROACTIVE-ASSISTANT lands) can additionally fold into its digest as one more section — same propose-only card either way.

---

## 8. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** Packs are a lifecycle/composition layer over existing entity stores — the same stance as "no space provider type" (`providers/registry.py:555`) and AUTONOMY-GUARDRAILS' no-type precedent. Nothing here registers through `_TypeHandler`s; `PROVIDER_TYPES` (manifest.py:453) is untouched.
- **Skills path:** inbound catalogs and pack-bundled skills both enter through `SkillsMarketplace` implementations registered on `get_default_skills_registry()` (`skills/marketplace.py`) → `install_guarded` → `.pclaw-lock.json`. The pack importer's skill leg is literally a marketplace adapter (`PackMarketplace`, `fetch()` reads the quarantined pack dir).
- **Action providers:** this plan adds **none**, so `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) is unchanged — restated because pack-shipped *triggers* reference action providers by name, and the importer's lint (§3.2) validates every referenced provider name against that frozenset at import time (a pack referencing an absent provider imports the trigger disabled + flagged, matching hook_create's own rejection semantics).
- **MCP connectors:** written through `providers/mcp_instances.py` onto `~/.personalclaw/mcp.json` (the existing multi-instance repoint seam with its injectable-key guard); app-shipped servers keep using manifest `mcpServers{}` — packs declare, apps embed.
- **Apps:** never embedded in packs; declared as `requirements` rows resolved against the App Store install flow (`app_manager.install`, trust tiers intact). App settings overlays write through `ProviderSettings` files, filtered by `settingsSchema` secret markers (§2.2.3).
- **Config: the FOUR wiring points**, per the recon gotcha. New `PacksConfig` dataclass (beside `SkillsConfig`, config/loader.py:854): `skill_catalogs` (list — each element field gets `_meta(label, help)`, the list[dataclass] precedent from `ProjectionRuleConfig`), `fingerprint_enabled` (default true, guard-flag-safe parse per AUTONOMY-GUARDRAILS §5), `connector_catalog_url`. Wired through (a) dataclass `_meta` (schema reachability tests), (b) `AppConfig.load()` explicit field-by-field mapping (loader.py:1638-1802 — omission = silent drop), (c) `to_dict()` new top-level section (:1930), (d) `_EDITABLE_CONFIG` (core.py:363) + FE for the runtime-editable subset (fingerprint toggle, catalog list).
- **Egress:** all remote fetches (catalog indexes, pack URLs, one-link manifests) go through `net.fetch` with the CONNECTOR profile via `egress_policy_for` — never hand-rolled aiohttp (persistence recon rule).
- **SEL:** export, import, requirement-satisfaction, and every scan verdict log to `sel.py:SecurityEventLog`, same as skill installs.
- **Snapshot/durability:** `~/.personalclaw/packs/` (installed-pack ledger + locks) and `connector_catalog.json` register in DURABILITY-AND-SYNC's inventory (that plan's §1 owns coverage; this plan just adds its stores to the inventory when both land — no private allowlist).
- **Memory vs Knowledge:** restated as an invariant — no pack component kind maps to `memory.db` or `knowledge.db` content; the exporter cannot read either (§2.2 structural layer); the importer writes to neither.

---

## 9. Data Model & Stores

| Store | File (`~/.personalclaw/`) | Format | Notes |
|---|---|---|---|
| Installed-pack ledger | `packs/installed.json` | JSON `{pack_id: {version, source_ref, components[], requirements_state, installed_at}}` | atomic_write; the app `installed.json` idiom |
| Import journal | `packs/.installing/<id>.json` | JSON write-journal | rollback source; janitor-cleaned |
| Connector catalog | `connector_catalog.json` | JSON | seeded bundled set; user-extendable; optional URL refresh |
| Fingerprint rejections | `packs/fingerprint_rejections.json` | JSON `{(project_id, pack_id): decided_at}` | never re-nag |
| Component locks | per-entity (skills: `.pclaw-lock.json`; others: flywheel `{source, computedHash}`) | — | drift detection for pack updates |
| Config | `config.json` → `packs` section | `PacksConfig` | four wiring points (§8) |

---

## Disposition & Dependency Notes

| Adjacent work | Relationship |
|---|---|
| **WORK-R15** (project export/import, approved) | **REUSED as the container layer** — path safety, sha256 manifest, `imported-N` slots, tmp-extract janitor, optional AES-GCM. This plan adds no second ZIP-validation stack; packs and project exports share `_data_filter`-posture code. Scope split: R15 = project *state*; packs = *capability composition*. |
| **DURABILITY-AND-SYNC** (approved) | Orthogonal: backup/sync of your own state vs sharing with others. Pack stores register in its inventory (§8). Its shard format is NOT the pack format (shards are lossless self-state; packs are redacted compositions). |
| **LEARNING-FLYWHEEL** (approved) | Its `{source, computedHash}` lock on imported templates is the drift primitive pack updates rely on (§1 pack_owned); its proposal queue is the review surface the prompt-card importer (§4.3) and fingerprint proposals (§7) feed. Packs are "the sharing channel for flywheel-earned templates" — a promoted template exports like any other. |
| **WORK-R19 / WF2-R14** (secrets, approved) | Requirements re-satisfy against the keychain-backed store when present, `.env` via `save_credential()` until then. Packs never ship either. |
| **AUTOMATION-SUBSTRATE** (approved) | Pack triggers target whichever store exists (dual-write shim until `triggers.json`); always disabled on import; its never-throw trigger validation is the model for §3.1's lenient component parse. |
| **AUTONOMY-GUARDRAILS** (approved) | Shared scan rules (§2.2), guard-flag parse tenet (§8 config), profiles/budgets governing any pack-shipped trigger the user enables. |
| **NEW-13** (Platform Legibility, sibling backlog plan) | §5 renders entities outbound; NEW-13 owns live context adapters + the first-party SKILL.md. No shared code beyond the redaction pass. |
| **Hermes multi-profile isolation** (research) | Deliberately NOT adopted — PersonalClaw stays single-home, single-user; packs compose INTO the one instance rather than forking instances. Named here so it isn't re-proposed. |

---

## Implementation Effort

**~6 sessions.**

- **Session 1 — format + export core (§1, §2.1-2.2):** `pack.json` schema + integer versioning; dependency-closure walker over the real stores; structural + content redaction layers (shared `packs/deny.py` with portability.py); export preview UI; golden-pack fixture tests.
- **Session 2 — import core (§3.1-3.2, §3.5):** inspect-without-write; quarantine + integrity recompute; referential-integrity linter; leaves-first commit with write-journal rollback; fresh-id rewriting; `PackMarketplace` adapter through `install_guarded`; trigger/config staging semantics; SEL wiring.
- **Session 3 — requirements + connectors + setup (§3.3-3.4):** connector catalog store + seeded set; configure-or-substitute-or-skip flow through `mcp_instances`; requirement re-satisfaction against `save_credential()`/secrets store; degraded-reason plumbing; setup-skill convention + "Finish setup" chip; `PacksConfig` through all four wiring points.
- **Session 4 — pack kinds (§4):** agent/roster pack import (catalog.json + persona→`agents{}` rendering + slug lint + staged-roster surfacing); prompt-card importer (fenced input, typed output, proposal review); author + ship Personal CFO and Health OS bundled packs; one-link serialization.
- **Session 5 — outbound + inbound interop (§5, §6):** `ExternalFormat` contract + 3 renderers + byte-identical golden tests; `CatalogMarketplace` + config-driven catalog list + Skills store source filter; lazy index handling.
- **Session 6 — fingerprinting + polish (§7):** fingerprint scanner + pack-side fingerprint declarations + propose-only cards + rejection memory; pack update flow (pack_owned overwrite, drift skip); pack store/detail FE; as-a-user validation sweep (export→wipe→import round-trip on a second `PERSONALCLAW_HOME`).

Sessions 1-2 are the keel; 3-6 each ship independently behind it.

---

## Risks

| Risk | Mitigation |
|---|---|
| Secret leakage in a shared pack (the existential one) | Structural never-opened deny-list + content scan that BLOCKS (never silently rewrites) + schema-filtered app configs + connector declarations schema-banned from carrying values; export preview shows every file; round-trip test greps the golden pack for planted canary secrets |
| Malicious pack (prompt-injected skill, hostile trigger, exfil connector) | Everything scanned at COMMUNITY tier via the existing scanner; DANGEROUS non-overridable; triggers/config land disabled/staged; connectors need explicit credential entry; egress chokepoint governs all fetches; SEL audit trail |
| Cross-ref rewriting corrupts entities | Rewrites happen on parsed component objects, never raw bytes; linter re-runs post-rewrite pre-commit; leaves-first order means a failed rewrite aborts before dependents land; journal rollback covers partial commits |
| Store-shape drift under this plan (workflows v1→v2, triggers unification) | Exporter/importer read through small per-kind adapters keyed on what exists on disk; component `kind` is stable while the backing store moves; golden-pack round-trip test runs in CI to catch drift |
| Format ossification (schema_version 1 forever wrong) | Best-effort forward import + advisory unknown keys means v2 can add fields without breaking v1 importers; the .ovsvoice legacy-shadow trick reserved if a breaking change ever ships |
| Fingerprinting feels like nagware | Deterministic rules only, on-create/on-demand only, propose-only, per-(project,pack) rejection memory, one config kill switch |
| Scope creep toward a pack registry service | Hard line in §Overview soul guardrail: catalogs are fetched JSON files; publishing is "put the .pclaw somewhere" (Hermes: "publishing is just a git push"); no server component, ever, in this plan |
| Silent config drop (four-wiring-points gotcha) | Explicit checklist §8; schema reachability tests enforce `_meta`; list-element `_meta` per the ProjectionRuleConfig precedent |

---

## Success Criteria

1. **Domain OS round-trip:** export the bundled Personal CFO pack from instance A, import on a fresh `PERSONALCLAW_HOME`: skills land locked, the template runs, the digest trigger exists DISABLED, the connector requirement prompts configure-or-substitute, the setup skill interview binds the finance folder — and grepping the `.pclaw` for any string from A's `.env`, `memory.db`, or session files finds nothing (canary-verified).
2. A pack containing a skill with a planted DANGEROUS pattern is refused at import regardless of consent; a WARNING pack requires explicit consent; both verdicts and every commit step appear in the SEL.
3. Mid-import failure (fault-injected at the template leg) rolls back every already-written component — `packs/installed.json`, the skills dir, `prompts/`, and `agents{}` are byte-identical to pre-import.
4. A roster pack installs with every runbook slug resolving (lint-proven); a deliberately broken slug blocks import with the exact unresolved reference named; the `always` tier deploys in one click and `phase-N` agents stay dormant until surfaced.
5. An external agentskills.io-style catalog registers as a marketplace; installing one of its skills produces a standard `.pclaw-lock.json` and passes `verify_skill_integrity` — proving zero bypass of the existing chokepoint.
6. `ExternalFormat` renders a PersonalClaw agent into a working `~/.claude/agents/<slug>.md` that Claude Code actually loads, and the golden-file test proves byte-identical rendering across runs.
7. Creating a project over a Terraform-shaped directory surfaces a propose-only pack card with confidence + inspect report; rejecting it once means it never reappears for that project; disabling `packs.fingerprint_enabled` stops scanning entirely.
8. Updating an installed pack overwrites only `pack_owned` components; a user-edited pack skill (computedHash drift) is skipped with a visible drift note, never clobbered.
