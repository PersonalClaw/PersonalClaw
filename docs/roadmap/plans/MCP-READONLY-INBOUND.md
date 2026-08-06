# MCP-READONLY-INBOUND

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/MRI.md`](../atomic/MRI.md) as 5 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: MCP Read-Only Inbound — The Curated Query Surface, Extracted and Landed Early

**Status:** IN PROGRESS — Session 1 (transport + auth + caps + audit) shipped 2026-07-28; **Session 2
(six curated read-only tools + the fencing meta-test) shipped 2026-07-28**; the 2026-07-29
protocol-currency amendment (G1.1-G1.4, `2024-11-05` → `2025-06-18` with real negotiation) shipped
2026-07-30. `src/personalclaw/inbound/` is mounted from `dashboard/server.py` and `InboundConfig`
round-trips; `inbound/tools.py` registers `memory_recall`, `knowledge_search`, `tasks_list`,
`task_get`, `sessions_search` and `status`, each wrapped by `wrap_result` fencing.
**REMAINING:** T2.6 (`docs/guides/use-from-your-ide.md` — the log defers it to the owner's client run)
and V2's real-MCP-client validation. Status corrected 2026-08-04 by code audit (this line said
Session 2 was not started). Deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18;
owner-approved extraction from EXTERNAL-ACCESS)

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

---

## Execution log

### 2026-07-28 — Session 1 (T1.1–T1.6, V1): DONE

The substrate landed with an empty tool table, as Session 1 intends: transport,
auth, caps, audit and the kill switch are all provable on their own, so Session 2's
five tools arrive on ground already verified.

**T1.1 — auth exemption mechanism (as instructed: extended, not forked).** The
precedent is `token_auth.py`'s `_BYPASS_EXACT` set — the same set that exempts
`POST /api/hooks/agent`. Added `/mcp` to it with an in-code comment citing this
plan. The route is exempt from the *dashboard* token, not from authentication: it
authenticates itself with its own surface bearer token, checked per request.

**T1.2 — `inbound/auth.py`.** Two independent gates, both required: a valid surface
bearer token AND an allowed peer. Kept separate deliberately — a peer check is not
authentication (a local port forwarder makes remote traffic arrive as 127.0.0.1),
and a token alone would make the surface reachable from anywhere the port is.
`hmac.compare_digest` for the compare; the peer address comes from the transport's
peername and never from a header, since `X-Forwarded-For` is attacker-settable on a
directly-reachable port. Refusals return a *reason string* rather than a bool, so a
refusal can name its failing condition.

**T1.3 — config, and a fail-open bug caught by writing the test.** Wired
`inbound.mcp.{enabled,allow_remote}` + `inbound.public_url` through the full
round-trip contract. The first draft used `bool(...)` and carried a comment claiming
it was fail-closed. It wasn't: `bool("false")` is `True`, so a config value of the
*string* `"false"` — or any other garbage — would have turned an inbound network
surface ON. Added `_expose_flag()` next to the existing `_guard_flag()`: same idea,
opposite polarity. A guard's ambiguity must fail ON (keep protecting); an exposure
flag's ambiguity must fail OFF. Only an explicit true-spelling opens the surface,
proven by a 13-case parametrized test. `allow_remote` and `public_url` are
deliberately absent from the PATCH allowlist — widening network exposure should not
be one mis-click in a browser.

**T1.4 — caps + audit.** Token bucket (1 rps sustained, burst 20) so an IDE panel
that fires several calls at once isn't punished for a trivial average rate;
`Retry-After` is floored at 1 second because `Retry-After: 0` invites a retry storm.
Result truncation is *visible* — a silent cut would let a caller believe it had the
whole answer. Audit is its own JSONL rather than a slice of the SEL: the SEL answers
"what was refused", this answers "what did that client actually do yesterday",
including the boring successful reads. Refusals write to both.

**T1.5 — transport.** `POST /mcp` with `initialize`/`tools/list`/`tools/call`; `GET`
405s (no SSE — a long-lived stream is a second lifecycle for no v1 benefit, and the
spec permits POST-only); batches refused outright rather than half-supported, since
one authenticated batch multiplies into N handler invocations and complicates every
cap. Check order is enablement → peer → token → rate → concurrency → body → parse.
Enablement is re-read per request, which is what makes the config flag a real kill
switch instead of a boot-time decision.

**T1.6 — CLI.** `personalclaw inbound token create|show mcp [--rotate]`. The token
prints once at creation and `show` never reveals it — a bearer credential the CLI
can re-read is one an unattended process can also exfiltrate, and rotation is cheap.

**V1 — validated as a user against a real gateway** (isolated dev home, port 10027):

| Checked | Result |
|---|---|
| token present, flag OFF | `POST /mcp` → **404**; mount refusal logged naming `inbound.mcp.enabled is off` |
| flag ON, valid bearer | `initialize` round-tripped: protocol `2024-11-05`, serverInfo `personalclaw 0.1.2` |
| no / wrong bearer | **401** both |
| `GET /mcp` | **405** |
| batch / malformed JSON / unknown method / unknown tool | JSON-RPC `-32600` / `-32700` / `-32601` / `-32601` |
| burst of 26 | exactly 20 × 200 then 429 with `Retry-After: 1` |
| kill switch flipped mid-flight | next request 404 — **no restart** |
| audit + SEL | 37 audit rows, every refusal carrying its reason; all 15 refusals mirrored to the SEL |
| reusing `.local_secret` as the token | refused (`token must not equal the dashboard/internal secret`) |

**DISCOVERY (logging).** The refusal line is emitted at INFO on
`personalclaw.inbound.mcp_http`, which the gateway's configured level filters out of
its log file. The line is correct and test-asserted via `caplog`, but an operator
running the packaged gateway will not see it. Left as-is rather than promoting the
level unilaterally: the *observable* refusal an operator needs is already there (the
404 plus the audited reason), and log-level policy is a gateway-wide decision, not
this surface's to make. Worth a deliberate pass if the gateway ever gains a
per-logger level map.

**Deferred to Session 2, by design:** the five tools. `TOOLS` is empty and
`tools/list` returns `[]`. The fencing wrapper (`wrap_result`) is already in place
and already the *only* representable way a result reaches a caller — handlers return
text and the dispatcher does the wrapping — so a Session 2 tool cannot skip
untrusted-data fencing even by accident. T2.5's meta-test still lands with the
tools.

**Tests:** `tests/test_inbound_mcp.py`, 63 cases (token lifecycle, mount gating,
transport, peer policy, caps, fencing, audit, config wiring). Named for the surface
rather than the plan's suggested `test_inbound_auth.py` since it covers all six
modules. Full suite green; lint clean.

### 2026-07-28 — Session 2 (T2.1–T2.5, V2 partial): DONE

The six-tool table (`memory_recall`, `knowledge_search`, `tasks_list`, `task_get`,
`sessions_search`, `status`) on the Session-1 substrate. Read-only by CONSTRUCTION:
every handler calls a read path and returns text, there is no dispatcher to a generic
tool surface, so no inbound request can reach a write, an install, or a config change.

**T2.1 — the memory restriction gate: located, and it does NOT apply here.** The plan
says "verify the gate location and reuse it". Verified: the temporary/incognito
restriction is not inside `recall_with_provenance` — it is `_blocks_reads_session` at
the dashboard endpoint, which reads an `X-Session-Key` header and asks whether *that
session* may read memory. It is strictly per-session (a temporary session is denied its
own context so the thread starts blank), not an instance-wide memory lock. An inbound
MCP call is a separate caller with no session, so no session's restriction can apply.
Blocking inbound recall whenever some unrelated temporary chat happened to be open
would misread the mechanism and make an IDE's lookups fail for invisible reasons. What
gates this surface is its own switch: the endpoint is unmounted unless the owner enabled
it and minted a token. Recorded in the handler docstring, because "the gate is honored"
and "the gate does not apply" look identical in a diff.

**T2.3 — a REAL credential leak, found by the redaction test.** `sessions_search`
correctly routes through `redact_credentials`… which did not recognize LLM provider API
keys at all. Its pattern set covered AWS, Slack and PEM blocks; an `sk-ant-api03-…` key
pasted into a chat passed straight through. Added Anthropic, OpenAI (classic + project),
GitHub and Google patterns to `_CREDENTIAL_PATTERNS` in `security.py`. This fixes every
surface that redacts on the way out, not just this one — the same function guards
session-archive reads and dashboard titles. Verified all five new shapes redact, the
pre-existing AWS/Slack ones still do, and benign text plus short strings are untouched.

**Two of my own tests were wrong before the code was.** Both "leak" assertions searched
for a word that also appears in the ECHOED QUERY (`No conversations matched
'pineapple'.`), so they would have passed or failed for the wrong reason. Rewritten to
assert on the session key and to require the session actually be FOUND first —
a redaction test that passes because nothing matched proves nothing.

**T2.5 fencing meta-test** iterates the REAL `TOOLS` table rather than a fixture list,
so a tool added later that bypassed `wrap_result` fails it. A companion test asserts the
arg table covers every registered tool, so a new tool cannot be silently skipped by the
meta-test itself.

**Arg validation (§C3):** unknown args are NAMED and refused (a typo'd `quesry` that
silently returned everything looks like a bug in the answer, not the call); wrong types
refused, including `True` for a limit, since bool is an int in Python and a
flag-by-mistake should be told; out-of-range limits CLAMP, because optimism is not an
error and the cap is ours to enforce anyway.

**T2.4 boundary descriptions:** the memory/knowledge tools each name the other, so a
model reaching for "the user's documents" does not get the assistant's internal recall.
Test-locked.

**V2 status:** exercised over the real transport end-to-end with `curl` — `tools/list`
advertises all six, `status`/`memory_recall`/`sessions_search`/`tasks_list` return real
fenced data, and bad args come back as JSON-RPC `-32602`. The plan's V2 also asks for a
real MCP *client* (an IDE or inspector) to drive it; that needs an interactive client
this session cannot install, so it stays an owner task — the protocol surface itself is
proven.

**T2.6 (`docs/guides/use-from-your-ide.md`) NOT written** — it documents connecting a
real client, and writing client-config instructions nobody has executed would be
guessing. It belongs with the owner's V2 client validation.

Tests: 30 new cases in `tests/test_inbound_mcp.py` (82 total). Full suite 8731 passed
(no fallout from the shared `security.py` change); lint clean.

---

## Amendment (2026-07-29 — owner-approved: protocol-revision currency)

**Provenance.** An ecosystem research pass (2026-07-28/29) found that MCP revision **`2026-07-28` makes the protocol stateless, deleting `Mcp-Session-Id`** — and that at least one shipping vendor (via an undocumented endpoint) negotiates `2025-06-18` **with** session IDs and is therefore on the wrong side of that change.

**The good news, verified against code: PersonalClaw is already on the right side by construction.** This surface is stateless today, not by luck but by an explicit design decision recorded in its own module docstring:
- **No session IDs at all.** `grep -rn "Mcp-Session-Id" src/` returns **zero**. Nothing in this surface issues, reads, or persists a session identifier.
- **POST-only, no SSE stream.** `inbound/mcp_http.py:6` — *"**No SSE stream** (`GET /mcp` → 405). The spec permits a POST-only server"* — implemented at `:78-81` returning a typed 405 explaining the posture.

So the stateless-spec transition costs this plan **nothing structural**. What it does expose is a currency problem:

**`PROTOCOL_VERSION = "2024-11-05"`** (`inbound/mcp_http.py:34`), returned in the `initialize` result at `:191`. That revision is now several behind. A client negotiating a modern revision may either refuse or silently degrade, and the value is a **wire contract** — per INTEGRATION-ARCHITECTURE §4.3 the inbound dialect wire contracts are **Tier S**, so this is a deliberate, tested bump rather than an edit.

**Why bump now:** the surface is deliberately minimal (six read-only tools, no SSE, no sessions), which is the cheapest this will ever be to verify. Conformance work grows with surface area, and EXTERNAL-ACCESS (24) is specified to *widen* this same `inbound/` package — bumping before it widens means one conformance pass instead of two.

**Contract for the bump (an executor must not improvise these):**
- Advertise a revision this surface **actually conforms to**, verified clause-by-clause against the published spec for the chosen revision — not the newest string available. If a mandatory clause of the newest revision is unimplementable within this plan's read-only scope, advertise the newest revision that IS fully satisfied and record the reason in the execution log.
- **Preserve every existing security property**: fail-closed enablement, the dedicated token that must differ from the dashboard token, loopback-only unless `allow_remote` + `public_url`, read-only by construction, and `fence_untrusted` on every result. A protocol bump is not a licence to touch the auth or fencing posture (that would be escalation **E4**).
- **Stay stateless.** Do not add session handling to satisfy an older client. The absence of `Mcp-Session-Id` is now aligned with where the spec is going; re-introducing it would be a regression dressed as compatibility.
- Keep the POST-only posture and its typed 405. The spec permits it and `mcp_http.py:6` documents the choice.
- Version negotiation must **fail legibly**: a client requesting an unsupported revision gets a typed error naming what this surface speaks, not a silent partial handshake.

### Amendment task table (extends this plan; run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| G1.1 | Clause-by-clause conformance review of the current surface against the target revision(s); record the chosen revision + any deliberately-unmet clause and its reason in the execution log | `docs/roadmap/plans/MCP-READONLY-INBOUND.md` (log), notes | the chosen revision is justified in writing, with any gap named rather than discovered later by a client |
| G1.2 | Bump `PROTOCOL_VERSION` to the reviewed revision and align the `initialize` result; **no session handling added**; POST-only + typed 405 preserved | `src/personalclaw/inbound/mcp_http.py:34`, `:191`, tests | `initialize` advertises the reviewed revision; `grep Mcp-Session-Id src/` still returns zero (regression test); `GET /mcp` still 405s |
| G1.3 | Version-negotiation error path: an unsupported requested revision returns a typed JSON-RPC error naming the supported revision(s), per §2.2's stable-code discipline | `inbound/mcp_http.py`, tests | a request for an unsupported revision (older or newer) fails legibly, never with a partial handshake |
| G1.4 | Regression-lock the security posture across the bump: existing fail-closed enablement, token-distinctness, loopback-gating, read-only, and `fence_untrusted` tests all still pass unmodified — **if a test needed editing to accommodate the bump, stop (E4)** | `tests/test_inbound_mcp.py` | the full existing inbound test suite passes with zero edits to security assertions |
| VG | Validation as a user: with the surface enabled on an isolated dev home, connect a real modern MCP client end-to-end, list the six tools, invoke each, and confirm results arrive fenced; confirm a client requesting an unsupported revision gets the typed error; confirm remote access is still refused without `allow_remote` + `public_url` | — | holds |

### Note for EXTERNAL-ACCESS (24)
Plan 24 widens this `inbound/` package rather than re-designing it (INTEGRATION-ARCHITECTURE §1.3 landmine 3). It should **inherit the revision chosen here** rather than negotiating its own, so every dialect on the shared substrate speaks one protocol revision.

- 2026-07-30 — **DONE (amendment G1.1–G1.4): protocol revision bumped `2024-11-05` → `2025-06-18`,
  with real negotiation.**

  **G1.1 — the conformance review IS the deliverable, so it lives in the code** (a comment block
  above `PROTOCOL_VERSION`, where the next person to touch the constant will read it) rather than
  only here. Verified clause by clause against what this surface actually does, per the plan's
  "advertise a revision this surface conforms to, not the newest string available":
  - **Streamable HTTP, POST-only** — the spec permits it; `GET` → 405 unchanged.
  - **Stateless** — no `Mcp-Session-Id` issued or required, which is where the spec is heading.
  - **`initialize`/`tools/list`/`tools/call`** implemented with the revision's result shapes;
    `capabilities` advertises only `tools`, which is all this surface has.
  - **No batching** — 2025-06-18 **removed** JSON-RPC batching, so this surface's long-standing
    refusal became *conformant* rather than a deviation. That is one of the two reasons this
    revision is the right target.
  - **Origin / DNS-rebinding guidance** — satisfied more strictly than asked: `peer_allowed`
    gates on the TRANSPORT peer (never a forgeable header) and a non-loopback peer additionally
    needs `allow_remote` **and** an exact `Host` match against the owner-declared `public_url`.
    An `Origin` check would be strictly weaker.

  **Deliberately unmet clauses of LATER drafts, recorded as the plan requires:** anything needing
  server→client requests (elicitation, sampling) or a resumable event stream is out of scope for
  a read-only POST-only surface, and OAuth authorization is expressly this plan's non-goal (the
  surface uses a dedicated bearer distinct from the dashboard token). Advertising a revision that
  mandates those would be a false claim — which is what the review exists to prevent.

  **G1.3 — negotiation now fails legibly.** The old handler **ignored** `params.protocolVersion`
  and returned its own string regardless, so a client pinning an unknown revision got a
  *successful handshake* and then failed later on a call whose shape it expected to differ. Now:
  a supported request is **echoed back** (the session runs under the revision the client asked
  for, not our preference), and an unsupported one — newer or older — returns a typed
  `-32602` naming what this server speaks. `2024-11-05` stays supported because already-configured
  clients pin it and the only difference that matters (batching) was never supported here, so
  honoring it is honest rather than a compatibility shim.

  **G1.4 — the E4 stop condition held: all 82 pre-existing inbound tests pass with ZERO edits.**
  No security assertion needed accommodating, which was the signal that this bump stays inside its
  scope. Fail-closed enablement, token distinctness, loopback gating, read-only, and
  `fence_untrusted` are all untouched.

  **ARCC was NOT queried — the MCP server is unavailable in this session.** Standard practice
  applied instead: no auth/fencing/exposure code was modified, the change is confined to a version
  constant plus a negotiation branch INSIDE the already-authenticated handler, and the existing
  security suite was used as the regression lock.

  **Two test bugs of mine, both caught before commit:** the session-handling grep matched the
  module's own comment explaining that it has *no* sessions (making the lock unsatisfiable while
  looking like a real failure — it now scans code lines only), and a duplicate 405 test omitted
  the token that `mount()` requires. Also renamed a test that overclaimed a "type check": an int
  `protocolVersion` is rejected because its string form isn't a supported revision, not by a
  dedicated type guard — the test now says so.

  **Validated as a user** against a live surface (isolated dev home, port 10746, never :10000):
  handshake with no version → `2025-06-18`; a client pinning `2024-11-05` got `2024-11-05` back
  (not overridden); `2099-01-01` got `-32602 unsupported protocolVersion '2099-01-01'; this server
  speaks 2025-06-18, 2024-11-05`; `tools/list` returned all six tools; `GET` → **405**; **zero**
  session headers on any response. Posture re-verified live: **no token → 401 before any protocol
  reasoning** (so negotiation can't be an unauthenticated probe), wrong token → 401, a batch →
  "batch requests are not supported", and a `tools/call` result still arrives wrapped in
  `<untrusted_content source=inbound:mcp:status>`. **0 gateway tracebacks.**

  **Gates:** `make lint` clean (mypy 554 files) · `make test` **9405 passed, 0 failed**.
  Tests: +16 revision/negotiation cases in `test_inbound_mcp.py` (98 in file).
