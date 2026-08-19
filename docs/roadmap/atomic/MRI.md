# MCP-READONLY-INBOUND — atomic plans

**Source plan:** [`MCP-READONLY-INBOUND`](../plans/MCP-READONLY-INBOUND.md)  
**Code:** `MRI`  
**Source status:** in_progress



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `MRI-1` | ✅ | Inbound substrate + fail-closed mount (auth exemption, auth.py, config round-trip, caps+audit, JSON-RPC transport, CLI token) | — | With no token: gateway boots, refusal log names the failing condition, POST /mcp 404s and every other route still auth-enforced (existing auth tests green). With token+flag ON: initialize round-trips via curl; test_config_roundtrip green with corrupt-enabled reading disabled; burst of 26 -> 20x200 then 429 with Retry-After>=1; kill-switch flip unmounts within one config read (no restart); bad bearer -> 401 mirrored to SEL; inbound_audit.jsonl populates; token stored 0600 and prints once, rotation invalidates old. |
| `MRI-2` | ✅ | Six-row curated read-only tool table + arg validation + fencing meta-test | `MRI-1` | tools/list advertises memory_recall, knowledge_search, tasks_list, task_get, sessions_search, status with memory-vs-knowledge boundary sentences; each returns real fenced (<untrusted_content source=inbound:mcp:...>) data; incognito-suppressed memories never appear (gate located as per-session and shown not to apply); sessions_search redaction test green (seeded credential/token-like string never in output); unknown args -> JSON-RPC -32602, wrong types refused, out-of-range limits clamp; meta-test iterating real TOOLS fails if any tool skips wrap_result; arg-coverage test asserts every registered tool is covered. |
| `MRI-3` | ✅ | Protocol-currency amendment: bump 2024-11-05 -> 2025-06-18 with clause-by-clause conformance, legible version negotiation, security regression lock | `MRI-1`, `MRI-2` | initialize advertises 2025-06-18 (echoes a supported requested revision back, still honors pinned 2024-11-05); grep Mcp-Session-Id src/ returns zero (regression-locked); GET /mcp still 405; an unsupported requested revision (older or newer) returns typed -32602 naming supported revisions, never a partial handshake; all pre-existing inbound security tests pass with ZERO edits (E4 stop condition held); make lint clean + full suite green. |
| `MRI-5` | ✅ | V2 owner validation: drive the surface from a real MCP-enabled client end-to-end | `MRI-2`, `MRI-3` | A real MCP-enabled client (IDE MCP config or mcp CLI) on the same machine connects over loopback, exercises all six tools, trips the rate cap, flips the kill switch mid-session, and SEL + inbound_audit.jsonl trails match design; remote access still refused without allow_remote + public_url; validation ledger written. (Owner-gated: requires an MCP client installed on the machine.) |
| `MRI-4` | ✅ | Guide: docs/guides/use-from-your-ide.md (token creation, client-config snippets, loopback caveat, kill switch) | `MRI-2`, `MRI-5` | docs/guides/use-from-your-ide.md exists with token-creation steps, generic MCP-client JSON config snippets, the loopback caveat, and the kill switch; a reader can connect a real MCP client from the doc alone. Written after the client run so instructions are executed, not guessed. |

## Atom scopes

### `MRI-1` — Inbound substrate + fail-closed mount (auth exemption, auth.py, config round-trip, caps+audit, JSON-RPC transport, CLI token)

**Status:** done

Session 1 — Substrate + mount (T1.1-T1.6, V1)

**Done when:** With no token: gateway boots, refusal log names the failing condition, POST /mcp 404s and every other route still auth-enforced (existing auth tests green). With token+flag ON: initialize round-trips via curl; test_config_roundtrip green with corrupt-enabled reading disabled; burst of 26 -> 20x200 then 429 with Retry-After>=1; kill-switch flip unmounts within one config read (no restart); bad bearer -> 401 mirrored to SEL; inbound_audit.jsonl populates; token stored 0600 and prints once, rotation invalidates old.

### `MRI-2` — Six-row curated read-only tool table + arg validation + fencing meta-test

**Status:** done

Session 2 — Tool table + validation (T2.1-T2.5)

**Done when:** tools/list advertises memory_recall, knowledge_search, tasks_list, task_get, sessions_search, status with memory-vs-knowledge boundary sentences; each returns real fenced (<untrusted_content source=inbound:mcp:...>) data; incognito-suppressed memories never appear (gate located as per-session and shown not to apply); sessions_search redaction test green (seeded credential/token-like string never in output); unknown args -> JSON-RPC -32602, wrong types refused, out-of-range limits clamp; meta-test iterating real TOOLS fails if any tool skips wrap_result; arg-coverage test asserts every registered tool is covered.

### `MRI-3` — Protocol-currency amendment: bump 2024-11-05 -> 2025-06-18 with clause-by-clause conformance, legible version negotiation, security regression lock

**Status:** done

Amendment (2026-07-29 owner-approved) — G1.1-G1.4, VG

**Done when:** initialize advertises 2025-06-18 (echoes a supported requested revision back, still honors pinned 2024-11-05); grep Mcp-Session-Id src/ returns zero (regression-locked); GET /mcp still 405; an unsupported requested revision (older or newer) returns typed -32602 naming supported revisions, never a partial handshake; all pre-existing inbound security tests pass with ZERO edits (E4 stop condition held); make lint clean + full suite green.

### `MRI-5` — V2 owner validation: drive the surface from a real MCP-enabled client end-to-end

**Status:** todo

Session 2 — V2 + Owner tasks (real-world validation client)

**Done when:** A real MCP-enabled client (IDE MCP config or mcp CLI) on the same machine connects over loopback, exercises all six tools, trips the rate cap, flips the kill switch mid-session, and SEL + inbound_audit.jsonl trails match design; remote access still refused without allow_remote + public_url; validation ledger written. (Owner-gated: requires an MCP client installed on the machine.)

### `MRI-4` — Guide: docs/guides/use-from-your-ide.md (token creation, client-config snippets, loopback caveat, kill switch)

**Status:** todo

Session 2 — T2.6

**Done when:** docs/guides/use-from-your-ide.md exists with token-creation steps, generic MCP-client JSON config snippets, the loopback caveat, and the kill switch; a reader can connect a real MCP client from the doc alone. Written after the client run so instructions are executed, not guessed.

