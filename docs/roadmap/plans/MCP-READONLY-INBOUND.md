# Plan: MCP Read-Only Inbound — The Curated Query Surface, Extracted and Landed Early

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner-approved extraction from EXTERNAL-ACCESS)
**Created:** 2026-07-18
**Wave:** 0/1 — pulled forward from Wave 3. "Point your IDE / other agents at your assistant" must not wait behind the five-dialect inbound program.
**Depends on:** nothing hard (AUTONOMY-GUARDRAILS' incident flag is honored if present; a config kill switch suffices until it lands). EXTERNAL-ACCESS inherits this substrate rather than building its §1.
**Scope:** ONE fail-closed, loopback-default, bearer-gated HTTP mount inside the existing gateway process, serving a hand-curated **read-only** MCP tool table. EXTERNAL-ACCESS §3 + the *minimum viable* slice of its §1. **Soul guardrail:** query-only with no path to writes, by construction — no generic passthrough to `_aggregated_call_tool`; an inbound request can never trigger an install, migration, config write, or store mutation. When in doubt whether a capability belongs here or in EXTERNAL-ACCESS, it belongs in EXTERNAL-ACCESS. The protocol handler is deliberately minimal (three JSON-RPC methods, no SSE streaming) — resist SDK-frameworkization.

---

## Context (code recon, 2026-07-18)

- **No inbound MCP exists:** `mcp_core.py` serves tools only via a *stdio* loop for the ACP child (`mcp_shared.run_mcp_stdio_loop`); its JSON-RPC helpers (`_read_message`, `respond`, `call_tool_with_logging`) are transport-coupled to stdio — the HTTP handler reuses the *shapes* (JSON-RPC 2.0 framing, tool result envelopes), not the loop.
- **Middleware chain** (`dashboard/server.py:1318-1353`): `no_cache` → (`csrf` + `token_auth` | `_dev_user`) → `app_permission` → `sel_audit`. **Exemption precedent exists:** `POST /api/hooks/agent` is middleware-exempt with its own constant-time `_verify_hook_token` — the inbound mount follows exactly this pattern (locate the exemption mechanism inside `token_auth.py`/server wiring and extend it; do not invent a second one).
- **The read paths to adapt (verified signatures):** memory `MemoryService.recall_with_provenance(query_text, limit)` (+ `record_recall`; restriction gating for temporary/incognito lives on the recall API path — T2.x verifies where and reuses it); knowledge `knowledge/retrieval.py::search(query, limit, include_archived=False)`; tasks `tasks/registry.py::list_all_tasks / get_task / search_tasks` (async façades); session-archive reads are redacted via `history.py` redaction helpers (the sessions_search tool rides that, never raw files); `/api/status` handler for `status()`.
- **Security substrate to compose:** `fence_untrusted(text, *, source)` (`security.py:672`), SEL (`sel.py::SecurityEventLog`), `save_credential` (.env 0600), `mcp` extra already exists for *clients* — this plan adds **no dependency** (hand-rolled 3-method JSON-RPC).
- The gateway is one aiohttp app — the mount is route registration, not a second listener.

## Design

### The module: `src/personalclaw/inbound/` (new)

- `auth.py` — token load (`PERSONALCLAW_INBOUND_MCP_TOKEN` via credential store), ≥32-byte validation, constant-time compare (`hmac.compare_digest`), loopback peer check (reject non-loopback unless `inbound.mcp.allow_remote` AND `inbound.public_url` set with exact-Host match; forwarded headers untrusted).
- `caps.py` — request caps (64 KiB body, 30 s deadline, token-bucket 1 rps sustained/burst 20/4 concurrent, result caps 100 items / 2 MiB) as module constants with config overrides; `Cache-Control: no-store` on every response.
- `audit.py` — one JSONL line per request to `~/.personalclaw/inbound_audit.jsonl` `{ts, surface:"mcp", route/tool, status, bytes_in/out, duration_ms, refused_reason}`, 2×-cap trim (mirror `notifications.jsonl` trim mechanics); auth failures/cap breaches additionally → SEL.
- `mcp_http.py` — `POST /mcp`: JSON-RPC 2.0 over HTTP; methods `initialize` (protocol version + server info + capabilities `{tools:{}}`), `tools/list`, `tools/call`; `GET /mcp` → 405 (no SSE stream — spec-permitted); unknown methods → JSON-RPC error, SEL-logged. Batch requests rejected (fail-closed simplicity).
- `tools.py` — the hand-curated table (below); one `_wrap_result(text, tool)` helper applies `fence_untrusted(..., source="inbound:mcp")` + the fixed data-not-instructions preamble to EVERY textual result — a new tool physically cannot skip fencing.
- **Enablement (fail-closed):** mount refuses at startup (explicit log line) when: token absent/short/equal to dashboard token or `X-Internal-Secret`; `inbound.mcp.enabled` false/missing/corrupt (**missing reads DISABLED — inbound OFF is the safe state, stated in-code so nobody "fixes" it**). Kill switches: config flag (PATCH-editable, unmounts on next config read), incident flag honored when guardrails land. CLI: `personalclaw inbound token create mcp` (generates 32-byte urlsafe, stores via `save_credential`, prints once).

### v1 tool table (each a thin adapter, all read-only)

| Tool | Backs onto | Notes |
|---|---|---|
| `memory_recall(query, limit≤20)` | `MemoryService.recall_with_provenance` | honors temporary/incognito restrictions (reuse the recall API's gate — verify location T2.1); memory.db = harness mechanics (stated in description) |
| `knowledge_search(query, limit≤20)` | `knowledge/retrieval.py::search` | knowledge.db = the user's items; the memory/knowledge boundary stated in both descriptions |
| `tasks_list(status?, project?)` / `task_get(id)` | `tasks/registry.py` façades | write façades not imported at all |
| `sessions_search(query, limit≤10)` | session archive search via `history.py` redacted readers | redaction mandatory; returns titles/snippets/ids, never raw transcripts |
| `status()` | the `/api/status` handler's data fn | uptime, version, counters — no config values |

## Contracts & Interfaces (this plan OWNS the inbound substrate; plan 24 INHERITS it — [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md) §1.3 landmine #3)

### C1 — `src/personalclaw/inbound/` package API

```python
# auth.py
def load_surface_token(surface: str) -> str | None: ...   # from credential store PERSONALCLAW_INBOUND_<SURFACE>_TOKEN
def verify_bearer(surface: str, presented: str) -> bool: ...   # hmac.compare_digest; False if token <32B or == dashboard token
def peer_allowed(request, surface: str) -> bool: ...       # loopback unless <surface>.allow_remote AND public_url exact-Host

# caps.py
@dataclass(frozen=True)
class Caps: body_bytes:int=65536; deadline_s:int=30; rps:float=1.0; burst:int=20; concurrent:int=4; max_items:int=100; max_result_bytes:int=2*1024*1024
def check_rate(client_key: str) -> bool: ...              # token bucket; False → caller returns 429 + Retry-After

# audit.py
def audit(surface: str, *, route: str, status: int, bytes_in: int, bytes_out: int, duration_ms: int, refused: str = "") -> None: ...
# → ~/.personalclaw/inbound_audit.jsonl (trim 2×); auth/cap/killswitch failures ALSO → sel() (§3.3)

# tools.py
def wrap_result(text: str, tool: str) -> dict: ...        # fence_untrusted(source="inbound:mcp") + fixed preamble; EVERY tool result goes through this
TOOLS: dict[str, ToolSpec]                                 # the curated table (C3)
```

### C2 — JSON-RPC 2.0 over HTTP (`mcp_http.py`, `POST /mcp`)

| Method | Request params | Response |
|---|---|---|
| `initialize` | `{protocolVersion, clientInfo}` | `{protocolVersion, serverInfo:{name:"personalclaw",version}, capabilities:{tools:{}}}` |
| `tools/list` | `{}` | `{tools:[{name, description, inputSchema(JSON-Schema)}]}` |
| `tools/call` | `{name, arguments}` | `{content:[{type:"text", text}], isError?}` — text is `wrap_result`'d |

`GET /mcp` → 405. Batch arrays → JSON-RPC error `-32600`. Unknown method → `-32601` + SEL. Errors use JSON-RPC error objects (NOT the §2.2 HTTP envelope — this is a JSON-RPC surface). Every request: audit line; cap/auth failure → SEL.

### C3 — The 5-tool table (each `ToolSpec = {description, inputSchema, handler}`; handlers are thin adapters over §3.9 read paths)

| name | inputSchema (required) | backs onto | guard |
|---|---|---|---|
| `memory_recall` | `{query:str, limit:int≤20}` | `MemoryService.recall_with_provenance` | honors temporary/incognito restriction (T2.1 verifies the gate location) |
| `knowledge_search` | `{query:str, limit:int≤20}` | `knowledge/retrieval.search` | — |
| `tasks_list` | `{status?:str, project?:str}` | `tasks/registry.list_all_tasks` | read-only façade only |
| `task_get` | `{id:str}` | `tasks/registry.get_task` | — |
| `sessions_search` | `{query:str, limit:int≤10}` | archive search + `history.py` redaction | redaction MANDATORY |
| `status` | `{}` | `/api/status` data fn | no config values |

(`status` makes 5 tools + the aggregate; the table lists 6 rows because `tasks_list`/`task_get` are one backing area.) Arg validation: out-of-range `limit` clamped; unknown args → JSON-RPC `-32602` invalid-params.

### C4 — Enablement (fail-CLOSED, §2.7) + config
Mount only if: `load_surface_token("mcp")` ≥32 bytes AND `inbound.mcp.enabled` truthy. Missing/corrupt `enabled` → **disabled** (stated in-code). Config additions (5-point, §2.1): `inbound.mcp.enabled: bool`, `inbound.mcp.allow_remote: bool`, `inbound.public_url: str`. Kill switch = config flag (PATCH, unmounts next read) + incident flag when guardrails land. CLI: `personalclaw inbound token create mcp [--rotate]`.

### Integration points
- **Calls:** the hooks-endpoint auth-exemption mechanism (T1.1 locates it in `token_auth.py`/`server.py` — extend, don't fork), `fence_untrusted`, `sel()`, `save_credential`, the §3.9 read paths, `config_dir`/`atomic_write`.
- **Called by:** external MCP clients (IDEs, agents) over loopback; **plan 24** mounts its four other dialects on this same `inbound/` package and generalizes single-token → per-client identity (never re-designs C1/C4).
- **Storage owned:** `inbound_audit.jsonl`; credential `PERSONALCLAW_INBOUND_MCP_TOKEN`.
- **Route added:** `POST /mcp` (middleware-exempt, own bearer gate — the `/api/hooks/agent` precedent).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Substrate + mount

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Locate the hooks-endpoint auth-exemption mechanism (`token_auth.py` + `server.py` wiring for `/api/hooks/agent`); extend it to `/mcp` with an in-code comment citing this plan; record the mechanism in the Execution log | `src/personalclaw/dashboard/token_auth.py`, `dashboard/server.py` | `/mcp` reachable without dashboard token; every other route still enforced (existing auth tests green) |
| T1.2 | `inbound/auth.py`: token load/validate/compare + loopback check per Design; unit tests incl. short-token refusal, dashboard-token-equality refusal, non-loopback rejection, forwarded-header ignored | create `src/personalclaw/inbound/{__init__,auth}.py`, `tests/test_inbound_auth.py` | all refusal paths tested; timing-safe compare used |
| T1.3 | Config: `inbound.mcp.{enabled,allow_remote}` + `inbound.public_url` wired through the FULL round-trip contract (dataclass+_meta, load, to_dict, PATCH allowlist) — missing/corrupt `enabled` reads False with a warning log | `src/personalclaw/config/loader.py`, roundtrip test auto-covers | `test_config_roundtrip.py` green; corrupt-file fixture reads disabled |
| T1.4 | `caps.py` + `audit.py` per Design; token-bucket unit-tested; audit trim mirrors notifications trim | `src/personalclaw/inbound/{caps,audit}.py`, tests | 429 with `Retry-After` on burst; audit line schema matches Design; trim proven |
| T1.5 | `mcp_http.py`: the three methods + 405 GET + batch rejection + deadline enforcement; mount in server app factory **only when enablement passes** (refusal = one explicit log line naming the failing condition) | `src/personalclaw/inbound/mcp_http.py`, `dashboard/server.py` | with no token: gateway boots, log shows refusal, `/mcp` 404s; with token: `initialize` round-trips against `curl` |
| T1.6 | CLI `personalclaw inbound token create mcp` (+ `--rotate`); docs stub in `docs/reference/cli.md` | `src/personalclaw/cli.py` | token printed once; stored 0600; rotation invalidates the old |
| V1 | Validation: boot with token → `curl` initialize/tools-list (empty table yet) → kill switch flip unmounts within one config read → SEL shows auth-failure on bad bearer → audit file populates | — | all observed; ledger written |

### Session 2 — Tool table + validation

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Verify the memory-restriction gate location (temporary/incognito) on the recall path; wire `memory_recall` through it; result via `_wrap_result` | `src/personalclaw/inbound/tools.py` (+ Execution log note on gate location) | incognito-fixture session's suppressed memories never appear in tool output (test) |
| T2.2 | `knowledge_search`, `tasks_list`, `task_get` adapters with arg validation (limits clamped, unknown args → JSON-RPC invalid-params) | `inbound/tools.py`, tests | happy path + clamp + bad-args tests green |
| T2.3 | `sessions_search` over redacted archive readers; assert redaction by fixture (a seeded token-like string in a session never appears in output) | `inbound/tools.py`, tests | redaction test green |
| T2.4 | `status()` adapter; `tools/list` returns the five with descriptions carrying the memory-vs-knowledge boundary sentences | `inbound/tools.py` | descriptions match Design table notes verbatim |
| T2.5 | `_wrap_result` fencing helper + a meta-test: every registered tool's output passes through it (iterate table, assert fenced markers present) | `inbound/tools.py`, tests | meta-test fails if a new tool skips fencing (fixture-verified) |
| T2.6 | Guide: `docs/guides/use-from-your-ide.md` — token creation, client config snippets (generic MCP client JSON), loopback caveat, kill switch | new doc | a reader connects a real MCP client from the doc alone |
| V2 | Validation as a user: connect a real MCP-enabled client (IDE or `mcp` CLI) from the same machine; exercise all five tools; trip the rate cap; flip the kill switch mid-session; verify SEL + audit trails | — | every behavior matches design; ledger written |

## Owner tasks (real world)

1. **Validation client:** have one MCP-capable client on the machine for V2 (your IDE's MCP config, or any MCP inspector tool) — 10 min setup.
2. Decide default posture in docs: this plan ships **loopback-only, disabled-until-token** — confirm you want the guide to mention `allow_remote` at all pre-EXTERNAL-ACCESS (recommendation: document it as "exists, discouraged until the hardened inbound layer lands").

## Risks & open questions

- **Protocol drift:** MCP streamable-HTTP evolves; the three-method subset is stable core. If a client demands SSE, that's EXTERNAL-ACCESS's generalization — E6, not scope creep here.
- **Open:** per-client identity is deliberately absent (single token). If the owner wants two clients distinguishable in audit before EXTERNAL-ACCESS lands, the audit line's UA field is the interim answer — noted in the guide.
