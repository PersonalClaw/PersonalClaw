# Plan: Security Legibility — Make the Strongest Story Checkable

**Status:** DESIGNED — deepened 2026-07-18 (initial PROPOSED 2026-07-18 from the pre-launch investigation & owner alignment review)
**Created:** 2026-07-18
**Wave:** 0 — launch-gating trust surface. Deep security *features* are explicitly out of scope (SECURITY-HARDENING, Wave 4 by owner decision).
**Depends on:** nothing. DISCOVERABILITY-LAUNCH republishes these artifacts on the website; OSS-OPERATIONS links SECURITY.md from the hygiene set.
**Scope:** make the existing security architecture externally legible and verifiable — disclosure surface, public threat model mapped to the OWASP Agentic Top 10 (2026), honest-limitations statements. **Soul guardrail:** documentation and disclosure only — no new enforcement mechanisms, no control changes, no rewording of in-product security copy (protocol rule: security surfaces are copy-sensitive). A control gap discovered while writing goes to SECURITY-HARDENING's candidate list via a DISCOVERY ledger entry — never fixed inline here.

---

## Context (verified 2026-07-18)

Controls inventory (all existing, all documented internally in `docs/architecture/security.md`): 4 auth modes with loopback-forced `none`; app-scoped 1-hour tokens injected by the reverse proxy that strips owner credentials; 112 denied + 52 suspicious command patterns merged at read time; OS child sandbox with credential env denylist; one egress chokepoint (`net/`) with named policies + user host-policy layering; `fence_untrusted` content fencing; supply-chain scanner with clean/warning(consent)/dangerous(terminal) verdicts and the scanned-bytes==installed-bytes invariant; HMAC-chained SEL; single YOLO trust state with TTL; credential-excluding exports. Honest-limitation facts to publish: ACP-under-YOLO tool gating rides system-prompt framing, not rails (`task_modes.py`, documented internally); the app `network` permission is declaration-only (disclosed at install consent). Missing externally: `SECURITY.md` (neither repo), any public threat model, any OWASP mapping.

## Design

- **SECURITY.md (both repos, near-identical):** report privately via GitHub Security Advisories (the "Report a vulnerability" button — owner must enable it); supported-versions table (latest minor only, pre-1.0); response expectation ("acknowledge ≤7 days, fix-or-plan ≤30 for confirmed issues" — solo-maintainer-honest numbers); scope-in (RCE, auth bypass, sandbox/scanner/egress bypass, token leakage) / scope-out (self-YOLO footguns, issues requiring an already-compromised host, hardening *requests* → normal issues).
- **`docs/security/threat-model.md`:** trust-boundary diagram (owner ↔ agent ↔ tools; core ↔ apps; gateway ↔ channels/inbound; install pipeline ↔ sources) + the ASI mapping table (below) + honest-limitations section + "what we deliberately don't defend against" (physical access, compromised OS, the owner's own YOLO choices — each with rationale). Source material is an *editing* job from `security.md` + the security-and-guardrails learnings file; no new claims may be invented — every row cites its module.
- **ASI mapping table rows (control ↔ category ↔ code):** ASI01 goal hijack → fencing + approval modes + memory-recall framing (`security.py::fence_untrusted`, `dashboard/handlers/memory.py`); ASI02 tool misuse → deny/suspicious patterns + task modes + sandbox (`security.py`, `sandbox.py`); ASI03 identity/privilege → app-scoped tokens + proxy credential stripping + permission middleware (`dashboard/handlers/apps.py::api_app_proxy`, `token_auth.py`); ASI04 supply chain → quarantine/scan/verdict gate + integrity invariant (`supply_chain.py`, `apps/app_manager.py`); ASI05 code execution → command screening + OS sandbox + env denylist; ASI06 memory/context poisoning → fenced recall + propose-don't-write learning + temporary/incognito modes; ASI07 inter-agent comms → *forward-pointer to plans 41/24 discipline (fail-closed inbound, fencing at ingestion)*; ASI08 cascading failures → breakers/budgets (AUTONOMY-GUARDRAILS, mark "in progress" honestly until it lands); ASI09 trust exploitation → approval surfaces + expiring YOLO + consent-gated installs; ASI10 rogue agents → SEL audit + kill switches (+ incident flag once guardrails land).
- **README security section (3 sentences + links)** and cross-links from the Store's install-consent docs.

## Contracts & artifacts (doc-artifact plan — no code contracts; structures pinned)

- **Artifacts produced (exact paths):** `SECURITY.md` (both repos), `docs/security/limitations.md`, `docs/security/threat-model.md`, README "Security" section. No source code, no config, no schema.
- **The one structured format — the ASI mapping table** (`threat-model.md`): columns `| ASI category | control | code citation (file:path) | status |` where status ∈ {`enforced`, `in progress (plan N)`, `documented limitation`}. **A row may say `enforced` ONLY with a resolvable `file:path` citation** — this is the plan's anti-fabrication rail (a cheaper model cannot invent a control it can't cite). Every claim traces to `docs/architecture/security.md` or the security-and-guardrails learnings file — inventing capabilities is escalation trigger E1.
- **Conventions:** none beyond markdown; the honest-limitations wording is copy-sensitive (owner sign-off, owner task 2).
- **Integration points:** consumed by DISCOVERABILITY (36 republishes threat-model on the site) + OSS-OPERATIONS (37 links SECURITY.md). Feeds SECURITY-HARDENING (47) candidate list via DISCOVERY entries — never inline fixes.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Disclosure surface

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Write SECURITY.md per Design (core repo), with the advisory-channel link, versions table, response expectations, scope-in/out lists | create `SECURITY.md` | all four sections present; zero invented capability claims (cross-check each against `docs/architecture/security.md`) |
| T1.2 | Apps-repo SECURITY.md: same disclosure channel; scope adjusted (app bundles, scanner interaction); links back to core | apps repo: create `SECURITY.md` | mirrors core process; app-specific scope stated |
| T1.3 | Honest-limitations section drafted (ACP-YOLO framing-not-rails with `task_modes.py` citation; `network` declaration-only with the install-consent context; both in the security doc's own internal voice — quote it, don't soften it) | `docs/security/limitations.md` (new dir) | both limitations stated with code citations; wording approved path flagged for owner review (owner task 2) |
| V1 | Validation: from a stranger's seat — repo page shows the security policy; the "Report a vulnerability" path works end to end (owner task 1 must be done first); every link in the three files resolves | — | walkthrough clean |

### Session 2 — The public threat model

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Trust-boundary section: enumerate the five boundaries with one paragraph + the crossing-controls list each (source: architecture docs only) | create `docs/security/threat-model.md` | five boundaries; every named control has a module citation |
| T2.2 | ASI mapping table exactly per Design rows, with status column (`enforced` / `in progress (plan N)` / `documented limitation`) — no row may claim `enforced` without a code citation | `docs/security/threat-model.md` | ten rows; citations resolve (spot-check by grep); in-progress rows name their plan |
| T2.3 | "What we deliberately don't defend against" + rationale paragraphs | `docs/security/threat-model.md` | section present; consistent with limitations.md |
| T2.4 | README "Security" section (3 sentences: posture, threat-model link, disclosure link); cross-link from `docs/architecture/security.md` header and third-party-install docs (apps repo) | `README.md`, `docs/architecture/security.md`, apps repo `docs/third-party-install.md` | links resolve both directions |
| V2 | Validation: read the full threat model as a skeptical outsider; every claim traceable to code or explicitly marked in-progress/limitation; DISCOVERY entries filed for any control gap noticed (→ SECURITY-HARDENING candidates), with zero inline fixes | — | ledger reflects the read-through |

## Owner tasks (real world)

1. **Enable GitHub private vulnerability reporting** on both repos (Settings → Code security → Private vulnerability reporting) — required before V1; ~2 min per repo.
2. **Review and sign off the honest-limitations wording** (T1.3) and the response-expectation numbers in SECURITY.md — these are public commitments in your name.
3. When DISCOVERABILITY-LAUNCH S2 lands, confirm the threat model page renders on the site (it becomes a marketing asset).

## Risks & open questions

- **Risk — claim drift:** the threat model can rot as controls evolve; mitigation: the doc carries a "verified against commit" line, and SECURITY-HARDENING S4's review re-verifies it. A CI freshness check is deliberately NOT added (docs-drift automation beyond the stability inventory is out of scope — ratchet only on observed rot).
- **Open:** publish response-time expectations as targets vs. commitments — default text says "expectations, not SLAs" explicitly; owner may harden it.
