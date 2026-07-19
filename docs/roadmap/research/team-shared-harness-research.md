# Team-Shared / Multi-Tenant Agentic Harnesses — Internet Research (2025-2026)

**Status:** RESEARCH (deep-research harness run, persisted 2026-07-14)
**Question:** How are teams and products solving shared/multi-tenant agentic AI harnesses in 2025-2026 — across five angles: (1) team-shared memory/knowledge backends; (2) shared scheduled-job/trigger repositories with teammate-visible/claimable automations; (3) multi-tenant task-tracking integrations as agent-pluggable providers; (4) how existing harnesses handle the personal-vs-team boundary (identity, per-user permissioning, approval routing); (5) evolving a single-tenant local-first entity store (JSON/SQLite/FAISS) into optionally-remote provider-backed entities without losing local-first operation.
**Method:** adversarially-verified fan-out research. 23 sources fetched, 115 claims extracted, 25 verified (24 confirmed / 1 killed), synthesized to 11 findings. Every surviving finding below was unanimously confirmed by independent verifier votes (3-0). Companion doc: `multi-tenancy-entity-audit.md` (the in-repo code audit this research pairs with).

---

## Executive Summary

Across 2025-2026, teams solving shared/multi-tenant agentic harnesses converge on **namespace- and ownership-based isolation with server-authoritative, per-user identity — not CRDT merging**. Memory layers (Zep/Graphiti's `group_id` namespacing, Letta's shared memory blocks) provide partition-scoped sharing with deliberately primitive conflict handling (last-writer-wins full rewrites, append-only ops, designated-owner conventions, block-level rather than per-agent permissions), while managed identity/user/thread multi-tenancy is monetized in commercial layers (Zep) rather than shipped in OSS cores. Scheduling/automation platforms (Temporal, n8n) share the same shape: shared infrastructure with per-tenant task queues or projects, tenant isolation enforced by application routing logic or per-project RBAC rather than platform credentials, and known failure modes when ownership transfers revoke sharing. Task trackers answer the personal-vs-team boundary most concretely: Linear models agents as restricted first-class "app user" identities with delegation-not-ownership semantics (a human stays accountable), admin-gated team-scoped installation, and a centrally hosted OAuth 2.1 MCP server; Atlassian mirrors this with per-user OAuth 2.1 consent as the default plus an admin-opt-in service-identity path; Google's reference architecture prescribes hard per-tenant datastores (no shared vector store) with end-user identity propagation into shared MCP servers plus agent-layer IAM checks.

Notably, angle (5) — evolving a single-tenant local-first entity store into optionally-remote shared entities — received **no direct confirmed coverage**: the surviving evidence *implies* the ecosystem's answer is server-authoritative identity plus ownership/claim conventions rather than offline CRDT merge, but that is an inference from adjacent evidence, not a verified finding (see Caveats).

---

## Angle 1 — Team-Shared Memory/Knowledge Backends

### F1. Zep/Graphiti: one primitive — `group_id` namespacing — serves both tenant isolation and team sharing *(confidence: high, 3-0 unanimous)*

**Claim:** Zep/Graphiti implements team-shared and multi-tenant agent memory with a single primitive: `group_id` namespacing, where nodes/edges sharing a `group_id` form an isolated graph inside one backend, and the same mechanism is explicitly positioned for both per-customer isolation and team-shared graph spaces.

**Evidence:** Docs state verbatim: "Nodes and edges with the same group_id form a cohesive, isolated graph" with use-case bullets "Multi-tenant applications: Isolate data between different customers or organizations" AND "Team collaboration: Allow different teams to work with their own graph spaces" (`tenant_{tenant_id}` example). Corroborated at code level: `add_episode(group_id)` docstring = "An id for the graph partition the episode is a part of"; some drivers map group_id to a separate database. Caveat: this is namespace-scoped partitioning, not a hardened DB-level security boundary.

**Sources:** https://help.getzep.com/graphiti/core-concepts/graph-namespacing · https://github.com/getzep/graphiti

### F2. Graphiti's MCP server exposes namespacing but not per-user identity; managed multi-tenancy is the commercial layer *(confidence: high, 3-0 unanimous)*

**Claim:** Graphiti's official MCP server exposes multi-tenancy to AI assistants via group management (`group_id` namespacing, `--group-id` defaulting to 'main') with no built-in per-user identity; managed multi-tenant operation — governed per-user/entity graphs, built-in users/threads/message storage — is explicitly delegated to the commercial Zep layer ("Build your own" for OSS Graphiti).

**Evidence:** mcp_server/README: "Group Management: Organize and manage groups of related data with group_id filtering"; "--group-id: Set a namespace for the graph (optional). If not provided, defaults to 'main'". README's Zep-vs-Graphiti table: Zep "Manages vast numbers of per-user/entity context graphs with governance" and "Built-in users, threads, and message storage" vs Graphiti "Build and query individual context graphs" / "Build your own". Caveats: the MCP server is labeled "experimental" though shipped in the official repo; the table is also vendor upsell positioning.

**Sources:** https://github.com/getzep/graphiti

### F3. Letta: shared memory = shared-state blocks attached to multiple agents, not message passing *(confidence: high, 3-0)*

**Claim:** Letta implements team/multi-agent shared memory as shared-state coordination rather than message passing: memory blocks are attached to multiple agents by block ID, and writes by one agent are immediately visible to all attached agents (after the writer's turn completes).

**Evidence:** Docs: "Create a block, then attach it to multiple agents using block_ids" ("Same block = shared memory"); "When one agent updates the block, all others see the change immediately"; framing is Letta's own: "real-time coordination without explicit agent-to-agent messaging." Verifier confirmed against live docs plus the ORM source. Caveat: visibility requires the writing agent to finish its turn.

**Sources:** https://docs.letta.com/guides/core-concepts/memory/shared-memory · https://docs.letta.com/guides/agents/multi-agent-shared-memory

### F4. Letta's conflict/permission model: ownership conventions, append-only safety, LWW data loss admitted — no CRDTs, no per-agent ACLs *(confidence: high, 3-0 unanimous across all three merged claims)*

**Claim:** Letta's conflict and permission model for shared memory is ownership-convention-based, not CRDT- or ACL-based: `memory_rethink` (full rewrite) is documented as last-writer-wins with lost updates under concurrency; the recommended pattern is to designate one owner agent for heavy edits while others use append-only `memory_insert` (the only fully concurrent-safe op); and the sole permission control is a block-wide `read_only` flag — no per-agent or per-user write permissions exist.

**Evidence:** Concurrency table: `memory_rethink` "No (last-writer-wins)", `memory_insert` "Yes (append-only)", `memory_replace` "Mostly (fails if target string changed)"; anti-pattern: simultaneous `memory_rethink` "leads to lost updates"; "Recommended pattern: designate one agent (or sleep-time memory) as the owner for heavy edits; others append." Permissions: "Read-only applies to the entire block, not per-agent." Ground truth: `read_only` is a column on the block table itself; the blocks_agents join table has no permission columns. Letta's own skills repo restates: "Last-writer-wins, no merge logic. Highest risk of data loss." Caveats: pages sit under the "V1 SDK (legacy)" nav; the newer Agent SDK adds a separate git-backed MemFS mechanism; `read_only` is marked deprecated in the response model with no shown replacement.

**Sources:** https://docs.letta.com/guides/core-concepts/memory/shared-memory · https://github.com/letta-ai/skills/blob/main/letta/agent-development/references/concurrency.md · https://github.com/letta-ai/letta

---

## Angle 2 — Shared Scheduling / Trigger Repositories

### F5. Temporal: shared-infrastructure-first, application-authoritative tenancy — task queues per tenant, isolation in routing logic not credentials *(confidence: high, 3-0 unanimous across all three merged claims)*

**Claim:** Temporal's multi-tenant scheduling guidance is shared-infrastructure-first and application-authoritative: the top-recommended pattern is dedicated task queues per tenant within one shared Namespace (a single Worker polls many tenant queues; scales to thousands of tenants), with tenant isolation "mostly enforced by your application and worker routing logic rather than by Temporal credentials" and noisy-neighbor mitigation (per-tenant rate limiting) left to the application. Namespace-per-tenant is the strongest credential boundary but needs a dedicated Worker pool per customer (min 2 for HA), is not cost-effective at scale, is deemed manageable below ~50 tenants, and hits a 10,000-Namespace platform ceiling.

**Evidence:** Doc labels "Task queues per tenant (Recommended)", "Can handle thousands of tenants per Namespace", cons include "Need to prevent 'noisy neighbor' issues at the worker level". RBAC corroboration: Namespace-level permission is the finest credential granularity — credentials genuinely cannot distinguish tenants sharing a Namespace. Namespace-per-tenant: "Requires a new Worker pool deployment for each customer (minimum 2 per Namespace for high availability)", "manageable for fewer than 50 tenants", table "Scale ceiling: 10,000 (Namespace limit)". Caveats: the doc hedges with "mostly" (a custom self-hosted Authorizer could add finer logic); the "hard" ceiling and ~50 qualifier carry documented qualifiers the merged claim slightly flattens.

**Sources:** https://docs.temporal.io/production-deployment/multi-tenant-patterns · https://docs.temporal.io/cloud/users · https://docs.temporal.io/self-hosted-guide/security

### F6. n8n: projects + per-project RBAC; documented failure mode — ownership transfer revokes all sharing *(confidence: high, 3-0 unanimous)*

**Claim:** n8n shares automations across a team via 'projects' grouping workflows and credentials with per-project RBAC (a user can hold different roles in different projects), and ownership of a workflow/credential is transferable — but transfer revokes all existing individual sharing, a documented failure mode that can silently break other workflows depending on the shared resource.

**Evidence:** Docs verbatim: "a single user can have different roles in different projects, giving them different levels of access" (Project Admin/Editor/Viewer roles); warning box "Moving revokes sharing": "Moving workflows or credentials removes all existing sharing" and "workflows may stop working if the credentials they need aren't available in the target project." Caveat: project RBAC is paid-tier only (Pro/Enterprise), not Community edition.

**Sources:** https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/organize-work-in-projects

---

## Angle 3 — Multi-Tenant Task Tracking as Agent-Pluggable Providers

### F7. Atlassian Rovo MCP: per-user OAuth 2.1 consent by default; admin-opt-in service identity for headless agents *(confidence: high, 3-0 unanimous across all three merged claims)*

**Claim:** Atlassian's Rovo MCP Server ties agent access to shared Jira/Confluence state to individual user identity by default: OAuth 2.1 with interactive consent is the primary mechanism, positioned as "fine-grained, user-level consent and context" (with companion docs stating actions respect users' existing access controls); for headless agents/bots/CI, an admin-opt-in API-token path exists in two variants — personal tokens via Basic auth and service-account API keys as Bearer tokens where available — giving teams a distinct service-identity route.

**Evidence:** Support docs verbatim: "uses OAuth 2.1 as its primary authentication mechanism" with "token validation and user context enrichment", chosen when "a user is present and can complete an interactive consent flow" / "You want fine-grained, user-level consent and context"; API-token section lists exactly `Authorization: Basic <base64(email:api_token)>` and `Authorization: Bearer <api_key>` ("where available") for "backend services, CI/CD pipelines, bots, and automated agents." GitHub README: "every action respects the user's existing access controls." Caveats: API-token auth requires org-admin enablement; some tools are unavailable under token auth; the auth page itself does not explicitly state the server enforces the user's exact Jira/Confluence permissions (companion getting-started docs do).

**Sources:** https://support.atlassian.com/atlassian-rovo-mcp-server/docs/authentication-and-authorization/ · https://developer.atlassian.com/ · https://github.com/atlassian/atlassian-mcp-server

### F8. Linear: agents are restricted "app user" identities with delegation-not-ownership; human stays accountable *(confidence: high, 3-0 unanimous across all three merged claims)*

**Claim:** Linear answers the personal-vs-team boundary by making agents a distinct restricted identity class with human-anchored accountability: agents are 'app users' that behave like workspace members (@-mentionable, delegable issues, commenting) but cannot sign in, access admin functionality, or manage users; assigning an issue to an agent triggers delegation (a distinct `delegate` field, not `assignee`) so the human assignee remains responsible; installation is admin-gated and team-scoped (teams chosen at install, any user with team access can then interact) — an implicit approval gate with coarse team-level rather than per-user permissioning.

**Evidence:** Docs verbatim: "Agents, also known as 'app users', behave similar to other users in a workspace... Agents cannot sign in to the app, access admin functionality or manage users"; "The human assignee remains responsible for the issue, even after delegation to an agent"; "App users are installed and managed by workspace admins... Once installed, any user with access to the selected teams can interact with the agent." API corroboration: delegation sets `delegate` not `assignee`, gated by `app:assignable` scope; changelog entries Apr–Jul 2026 show the model is current. Caveats: an issue can be delegated with no human assignee (per a 2026-04-16 changelog fix), so the accountability guarantee presumes an assignee exists; the Agents API carries a Developer Preview label; team access is admin-editable post-install.

**Sources:** https://linear.app/docs/agents-in-linear · https://linear.app/developers/agents · https://linear.app/blog/building-our-agent-interaction-sdk

### F9. Linear's hosted MCP server: identity is server-authoritative, not harness-managed *(confidence: high, 3-0 unanimous)*

**Claim:** Linear's official MCP server makes identity server-authoritative rather than harness-managed: it is a centrally hosted remote service at https://mcp.linear.app/mcp authenticating individual users via OAuth 2.1 with dynamic client registration (verified live: RFC 7591 registration endpoint, PKCE S256, 401 unauthenticated), and it supports permission-scoped agent access — Bearer tokens acting as a non-human 'app' actor (OAuth `actor=app` / client_credentials for agents and service accounts) or read-only access via a restricted API key.

**Evidence:** Docs: server "is centrally hosted and managed", "uses OAuth 2.1 with dynamic client registration"; FAQ: supports `Authorization: Bearer <yourtoken>` "to interact with the MCP server as an `app` user, provide read-only access through a restricted API key." Verifier probed the endpoint: `.well-known/oauth-authorization-server` returns live metadata with registration_endpoint and resource binding. Developer docs: `actor=app` "should be used for agents and service accounts"; API keys restrictable to Read/Write/Admin and specific teams. Caveat: read-only enforcement derives from key restrictions at creation time (platform-level), not a separate MCP-layer permission system.

**Sources:** https://linear.app/docs/mcp · https://linear.app/developers/oauth-2-0-authentication · https://linear.app/docs/api-and-webhooks

---

## Angle 4 — Personal-vs-Team Boundary in Agentic Architectures

### F10. Google's multi-tenant agentic reference architecture: per-tenant datastores, NO shared vector store; layered per-user permissioning *(confidence: high, 3-0 unanimous)*

**Claim:** Google's reference architecture for multi-tenant agentic AI (last reviewed 2026-06-18) rejects shared memory/vector stores across tenants: each tenant gets a dedicated RAG datastore (BigQuery or AlloyDB) only that tenant's agents can access (PAB policies + VPC Service Controls), with sharing limited to the routing hub, governance hub, and optionally model endpoints and MCP servers; per-user permissioning on shared state is layered — the agent verifies the user's identity and IAM role bindings before any data access, atop zero-trust IAP ingress and a dynamically maintained tenant-registry router.

**Evidence:** Doc verbatim: "To maintain strict data sovereignty, only the tenant agents can access this data"; "the agent verifies the user's identity and IAM role bindings"; IAP "enforces a zero-trust model to verify user identity and context before any request reaches the application." Verifier searched the doc for shared memory/vector/session components and found none crossing tenants (only per-tenant Memorystore for rate limiting). Independent production walkthrough (Sakura Sky) confirms the per-tenant datastore + PAB/VPC-SC pattern while criticizing gaps (DR, CMEK, identity propagation detail). Caveat: prescriptive reference architecture, not a report of one deployed system; the agent-layer check is one defense-in-depth layer among several.

**Sources:** https://docs.cloud.google.com/architecture/multi-tenant-agentic-ai-system · https://www.sakurasky.com/blog/multi-tenant-agentic-ai-google-cloud/

### F11. Shared MCP servers: end-user identity propagation + backend authorization, not per-tenant server copies *(confidence: high, 3-0)*

**Claim:** The emerging pattern for MCP servers shared across tenants/users is end-user identity propagation plus backend authorization, not per-tenant server copies: Google's architecture requires securely propagating the end-user identity from the tenant agent to the shared MCP server, which enforces fine-grained access control on the backend; per-tenant 'local' MCP deployment is the maximum-isolation alternative for regulated data. This aligns with the MCP authorization spec (OAuth 2.1 resource server, RFC 8707 audience binding, raw token passthrough forbidden).

**Evidence:** Doc verbatim: "You securely propagate the end-user identity from the agent in the tenant project to the shared MCP server" and "the shared MCP server uses the propagated user identity to enforce fine-grained access control on the backend system"; "Local MCP servers offer maximum isolation and they can handle highly sensitive or regulated data access." Verifier cross-checked the vendor-neutral MCP 2025-06-18 authorization spec: servers MUST validate audience-bound end-user tokens; passthrough of raw tokens is prohibited, so propagation must use exchanged/separately-issued tokens.

**Sources:** https://docs.cloud.google.com/architecture/multi-tenant-agentic-ai-system · https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization

---

## Angle 5 — Local-First → Optionally-Remote Evolution

**No surviving direct claims.** This is the research's most important negative result: the specific question of evolving a single-tenant local-first entity store (JSON/SQLite/FAISS on disk) into optionally-remote shared entities — sync protocols, CRDT-vs-LWW offline conflict resolution, small-team identity federation — produced **no confirmed findings**. Local-first sync platforms were fetched (ElectricSQL writes guide, Ink & Switch Keyhive, PowerSync update-conflict handling, SQLite Sync) but no claims about *agent-harness* entity stores survived verification.

The synthesis's inference (flagged as inference, not finding): the ecosystem's answer is **server-authoritative identity plus ownership/claim conventions rather than offline CRDT merge** — Letta ships LWW-with-admitted-data-loss and recommends designated-owner + append-only conventions instead of merge logic (F4); Temporal pushes isolation into application routing over shared authoritative infrastructure (F5); Linear/Atlassian make the *server* the identity authority (F7-F9); Google prescribes hard per-tenant stores (F10). Nobody in the verified corpus applies Automerge/Loro/Yjs-class CRDTs to shared agent state.

---

## Refuted Claims (do not rely on these)

1. **(0-3, killed)** "Graphiti's isolation is application-enforced query scoping rather than hard tenancy: searches restrict scope by passing group_id (e.g. derived as `f"tenant_{tenant_id}"`), and cross-namespace access has no built-in support — the docs instruct applications to run multiple queries and merge results themselves." — The characterization of Graphiti's *cross-namespace* behavior (run-multiple-queries-and-merge instruction) did not survive verification. (Source under test: https://help.getzep.com/graphiti/core-concepts/graph-namespacing)

## Caveats (verbatim from synthesis)

1. Source composition skews heavily toward vendor primary documentation — accurate for stated design and admitted limitations (Letta's last-writer-wins, Temporal's credential gap, n8n's sharing-revocation warning are all against-interest admissions), but almost no independent production failure stories or postmortems survived verification; the request for 'failure stories' is only partially satisfied by vendor-documented anti-patterns.
2. Research dimension (5) produced NO surviving direct claims; the "server-authoritative + ownership/claim conventions over CRDTs" implication is an inference from adjacent evidence, not a verified finding. Likewise, no claims survived on OpenClaw/Claude-Code-class harnesses, LangGraph/CrewAI, Devin, MS Copilot agents, or Google Agentspace specifically — the Google finding is a GCP reference architecture, not Agentspace.
3. One claim was refuted (see above) — do not rely on that characterization of Graphiti's cross-namespace behavior.
4. Time-sensitivity: Graphiti's MCP server is labeled experimental; Letta's cited pages sit under a "V1 SDK (legacy)" section while a newer Agent SDK adds a separate git-backed MemFS mechanism; Linear's Agents API is Developer Preview; Atlassian's API-token path shipped ~Feb 2026; the MCP authorization spec (2025-06-18 revision) is still evolving.
5. Scope/licensing footnotes: n8n project RBAC requires paid tiers; Atlassian API-token auth requires org-admin enablement; Temporal's 10,000-Namespace "hard ceiling" and "<50 tenants" figures carry documented qualifiers the merged claims slightly flatten; Graphiti group_id isolation is namespace partitioning, not a hardened security boundary.

## Open Questions

1. How do OpenClaw/Claude-Code-class personal-agent harnesses and orchestration frameworks (LangGraph, CrewAI, Letta Cloud, Devin, Copilot agents, Agentspace) concretely implement the personal-vs-team boundary today — no claims about their shared-cron/trigger repositories or claimable automations survived verification, so it remains unknown whether **anyone** ships teammate-visible/claimable scheduled jobs for personal agents. (If true, PersonalClaw's shared-trigger repository would be first-of-kind.)
2. What concrete sync protocols are being used in practice to evolve local-first agent entity stores (JSON/SQLite/FAISS) into optionally-remote shared stores — are CRDT libraries (Automerge/Loro/Yjs) actually applied to agent state anywhere, or does everyone go straight to server-authoritative APIs with LWW, as Letta and Temporal's patterns suggest?
3. Given the MCP spec's prohibition on raw token passthrough, what token-exchange implementations do small teams (without enterprise IdPs) actually use to propagate end-user identity into shared MCP servers — Google's architecture prescribes it but hand-waves the mechanism (a gap its independent reviewer also flagged)?
4. Are there documented production incidents of cross-tenant leakage or lost updates in shared agent memory at scale (e.g., group_id scoping bugs, concurrent memory_rethink data loss) — verification found only vendor-admitted theoretical limitations, no real postmortems.

## Source Register (23 fetched)

| Source | Quality | Angle | Claims |
|---|---|---|---|
| https://help.getzep.com/graphiti/core-concepts/graph-namespacing | primary | shared-agent-memory-backends | 5 |
| https://docs.letta.com/guides/core-concepts/memory/shared-memory | primary | shared-agent-memory-backends | 5 |
| https://deepwiki.com/mem0ai/mem0/7.4-organizations-and-projects | secondary | shared-agent-memory-backends | 5 |
| https://rhumb.dev/blog/multi-tenant-mcp-server-design | blog | shared-agent-memory-backends | 5 |
| https://github.com/getzep/graphiti | primary | shared-agent-memory-backends | 5 |
| https://www.falkordb.com/blog/graphiti-falkordb-multi-agent-performance/ | blog | shared-agent-memory-backends | 5 |
| https://github.com/openclaw/openclaw/issues/26370 | forum | multi-tenant-scheduling-and-triggers | 5 |
| https://docs.temporal.io/production-deployment/multi-tenant-patterns | primary | multi-tenant-scheduling-and-triggers | 5 |
| https://clawgo.net/mastering-openclaw-multi-user-setup-in-no-time/ | unreliable | multi-tenant-scheduling-and-triggers | 5 |
| https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/organize-work-in-projects | primary | multi-tenant-scheduling-and-triggers | 5 |
| https://community.n8n.io/t/multi-tenant-n8n-workflows-with-shared-logic-but-isolated-state/295495 | forum | multi-tenant-scheduling-and-triggers | 5 |
| https://docs.cloud.google.com/architecture/multi-tenant-agentic-ai-system | primary | multi-tenant-scheduling-and-triggers | 5 |
| https://dev.to/stacklok/token-delegation-and-mcp-server-orchestration-for-multi-user-ai-systems-3gbi | blog | mcp-task-provider-integrations | 5 |
| https://support.atlassian.com/atlassian-rovo-mcp-server/docs/authentication-and-authorization/ | primary | mcp-task-provider-integrations | 5 |
| https://linear.app/docs/agents-in-linear | primary | mcp-task-provider-integrations | 5 |
| https://linear.app/docs/mcp | primary | mcp-task-provider-integrations | 5 |
| https://www.langchain.com/blog/custom-authentication-and-access-control-in-langgraph | primary | harness-personal-vs-team-boundary | 5 |
| https://accuroai.co/blog/microsoft-copilot-permissions-sprawl-m365-data-leak | blog | harness-personal-vs-team-boundary | 5 |
| https://learn.microsoft.com/en-us/entra/agent-id/ | primary | harness-personal-vs-team-boundary | 5 |
| https://electric.ax/docs/guides/writes | primary | local-first-to-multi-tenant-sync | 5 |
| https://www.inkandswitch.com/keyhive/notebook/ | primary | local-first-to-multi-tenant-sync | 5 |
| https://docs.powersync.com/handling-writes/handling-update-conflicts | primary | local-first-to-multi-tenant-sync | 5 |
| https://www.sqlite.ai/sqlite-sync | primary | local-first-to-multi-tenant-sync | 5 |

**Run stats:** 5 angles · 23 sources fetched · 115 claims extracted · 25 verified (24 confirmed, 1 killed, 0 unverified) · 11 findings after synthesis · 105 agent calls.
