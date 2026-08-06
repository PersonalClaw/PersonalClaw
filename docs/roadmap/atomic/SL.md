# SECURITY-LEGIBILITY — atomic plans

**Source plan:** [`SECURITY-LEGIBILITY`](../plans/SECURITY-LEGIBILITY.md)  
**Code:** `SL`  
**Source status:** done

Decomposed SECURITY-LEGIBILITY (SL) into 7 atoms, all status=done. Fully-shipped doc-artifact plan (S1 disclosure surface + S2 public threat model, 2026-07-22): core+apps SECURITY.md, limitations.md, threat-model.md with a citation-gated OWASP-Agentic table, README security section. All dependency edges are intra-plan; no blocking cross-plan deps (outbound only, to SECURITY-HARDENING / DISCOVERABILITY / OSS-OPERATIONS). No PR numbers in the log — plan tracked by date, not PR.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `SL-1` | ✅ | Core repo SECURITY.md (disclosure channel, versions, response expectations, scope in/out) | — | Core SECURITY.md exists with all four sections (private GitHub advisory channel, latest-0.x-minor versions table, 7-day ack / 30-day fix-or-plan expectations stated as expectations not SLAs, scope-in/scope-out lists); zero invented capability claims (each cross-checked against docs/architecture/security.md) |
| `SL-2` | ✅ | Apps-repo SECURITY.md (app-bundle-scoped, routes platform issues to core) | `SL-1` | Apps-repo SECURITY.md mirrors the core disclosure process with app-specific scope (malicious bundle / scanner-evasion / under-declared manifest in scope; consented 'warning' install out, 'dangerous' override in) and links back to core |
| `SL-3` | ✅ | docs/security/limitations.md — honest limitations in the architecture's own voice | — | New docs/security/limitations.md states both limitations with code citations (ACP-under-YOLO gates via system-prompt framing not rails, task_modes.py; app 'network' permission declaration-only, apps/permissions.py::can_use_network); owner sign-off received on the copy-sensitive wording |
| `SL-4` | ✅ | V1 validation + enable GitHub Private Vulnerability Reporting on both repos | `SL-1`, `SL-2`, `SL-3` | PVR confirmed enabled:true on both repos; the 'Report a vulnerability' path works end-to-end; every link across the three Session-1 files resolves; make lint green |
| `SL-5` | ✅ | docs/security/threat-model.md — five trust boundaries + 10-row ASI mapping table + 'don't defend against' section | `SL-3` | threat-model.md carries five trust boundaries each with a resolvable module citation, a 10-row ASI table where every 'enforced' row has a grep-verified file:path and non-enforced rows are marked 'in progress (plan N)' or 'documented limitation', a 'verified against main@<commit>' line, and a 'what we deliberately don't defend against' section consistent with limitations.md; citation-drift fixes applied (apps/app_manager.py::install, supply_chain.py::SkillScanner/Verdict, quarantine-staging phrasing) |
| `SL-6` | ✅ | README Security section + bidirectional cross-links across both repos | `SL-1`, `SL-2`, `SL-5` | README 'Security' section (posture + threat-model link + private-disclosure link) added; cross-links added into docs/architecture/security.md header and apps-repo docs/third-party-install.md; all links resolve both directions |
| `SL-7` | ✅ | V2 skeptical-outsider read-through + DISCOVERY ledger to SECURITY-HARDENING | `SL-5`, `SL-6` | Full threat model read as a skeptical outsider with every enforced claim traced to grep-verified code and non-enforced rows plan-marked; all cross-repo links resolve; DISCOVERY entries filed as SECURITY-HARDENING (plan 47) candidates (stale install_guarded citation, byte-integrity-as-quarantine-staging TOCTOU test, ASI07/ASI08 unlanded controls) with ZERO inline fixes to security.md/code; make lint green |

## Atom scopes

### `SL-1` — Core repo SECURITY.md (disclosure channel, versions, response expectations, scope in/out)

**Status:** done

Design 'SECURITY.md (both repos)'; Task breakdown Session 1 T1.1

**Done when:** Core SECURITY.md exists with all four sections (private GitHub advisory channel, latest-0.x-minor versions table, 7-day ack / 30-day fix-or-plan expectations stated as expectations not SLAs, scope-in/scope-out lists); zero invented capability claims (each cross-checked against docs/architecture/security.md)

### `SL-2` — Apps-repo SECURITY.md (app-bundle-scoped, routes platform issues to core)

**Status:** done

Design 'SECURITY.md (both repos)'; Task breakdown Session 1 T1.2

**Done when:** Apps-repo SECURITY.md mirrors the core disclosure process with app-specific scope (malicious bundle / scanner-evasion / under-declared manifest in scope; consented 'warning' install out, 'dangerous' override in) and links back to core

### `SL-3` — docs/security/limitations.md — honest limitations in the architecture's own voice

**Status:** done

Design 'docs/security/threat-model.md' honest-limitations; Task breakdown Session 1 T1.3; Owner task 2

**Done when:** New docs/security/limitations.md states both limitations with code citations (ACP-under-YOLO gates via system-prompt framing not rails, task_modes.py; app 'network' permission declaration-only, apps/permissions.py::can_use_network); owner sign-off received on the copy-sensitive wording

### `SL-4` — V1 validation + enable GitHub Private Vulnerability Reporting on both repos

**Status:** done

Task breakdown Session 1 V1; Owner tasks 1

**Done when:** PVR confirmed enabled:true on both repos; the 'Report a vulnerability' path works end-to-end; every link across the three Session-1 files resolves; make lint green

### `SL-5` — docs/security/threat-model.md — five trust boundaries + 10-row ASI mapping table + 'don't defend against' section

**Status:** done

Design 'docs/security/threat-model.md' + ASI mapping table rows; Contracts & artifacts (the one structured format); Task breakdown Session 2 T2.1, T2.2, T2.3

**Done when:** threat-model.md carries five trust boundaries each with a resolvable module citation, a 10-row ASI table where every 'enforced' row has a grep-verified file:path and non-enforced rows are marked 'in progress (plan N)' or 'documented limitation', a 'verified against main@<commit>' line, and a 'what we deliberately don't defend against' section consistent with limitations.md; citation-drift fixes applied (apps/app_manager.py::install, supply_chain.py::SkillScanner/Verdict, quarantine-staging phrasing)

### `SL-6` — README Security section + bidirectional cross-links across both repos

**Status:** done

Design 'README security section'; Contracts & artifacts (Integration points); Task breakdown Session 2 T2.4

**Done when:** README 'Security' section (posture + threat-model link + private-disclosure link) added; cross-links added into docs/architecture/security.md header and apps-repo docs/third-party-install.md; all links resolve both directions

### `SL-7` — V2 skeptical-outsider read-through + DISCOVERY ledger to SECURITY-HARDENING

**Status:** done

Task breakdown Session 2 V2; Risks & open questions (claim drift); Execution log DISCOVERY→plan 47

**Done when:** Full threat model read as a skeptical outsider with every enforced claim traced to grep-verified code and non-enforced rows plan-marked; all cross-repo links resolve; DISCOVERY entries filed as SECURITY-HARDENING (plan 47) candidates (stale install_guarded citation, byte-integrity-as-quarantine-staging TOCTOU test, ASI07/ASI08 unlanded controls) with ZERO inline fixes to security.md/code; make lint green

