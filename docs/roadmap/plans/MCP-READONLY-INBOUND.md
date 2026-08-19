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
V2's real-MCP-client validation ran 2026-08-17 (MRI-5, official SDK — **PARTIAL**: it found the
defects listed in its log, and Defect 4 — `allow_remote`/`public_url` inert for an MCP client — is
deliberately left as an owner scope call belonging to EXTERNAL-ACCESS). **T2.6
(`docs/guides/use-from-your-ide.md`) shipped 2026-08-18 (MRI-4)**, written after re-executing every
step against a live surface. **REMAINING:** no task row is outstanding; the one open item is
Defect 4, which is not this plan's to close. Status corrected 2026-08-04 by code audit (this line
said Session 2 was not started). Deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18;
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

## Contracts & Interfaces (this plan OWNS the inbound substrate; plan 24 INHERITS it — [AGENTS.md](../../../AGENTS.md) §1.3 landmine #3)

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

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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

**`PROTOCOL_VERSION = "2024-11-05"`** (`inbound/mcp_http.py:34`), returned in the `initialize` result at `:191`. That revision is now several behind. A client negotiating a modern revision may either refuse or silently degrade, and the value is a **wire contract** — inbound dialect wire contracts are a **stable surface**, so this is a deliberate, tested bump rather than an edit.

**Why bump now:** the surface is deliberately minimal (six read-only tools, no SSE, no sessions), which is the cheapest this will ever be to verify. Conformance work grows with surface area, and EXTERNAL-ACCESS (24) is specified to *widen* this same `inbound/` package — bumping before it widens means one conformance pass instead of two.

**Contract for the bump (an executor must not improvise these):**
- Advertise a revision this surface **actually conforms to**, verified clause-by-clause against the published spec for the chosen revision — not the newest string available. If a mandatory clause of the newest revision is unimplementable within this plan's read-only scope, advertise the newest revision that IS fully satisfied and record the reason in the execution log.
- **Preserve every existing security property**: fail-closed enablement, the dedicated token that must differ from the dashboard token, loopback-only unless `allow_remote` + `public_url`, read-only by construction, and `fence_untrusted` on every result. A protocol bump is not a licence to touch the auth or fencing posture (that would be escalation **E4**).
- **Stay stateless.** Do not add session handling to satisfy an older client. The absence of `Mcp-Session-Id` is now aligned with where the spec is going; re-introducing it would be a regression dressed as compatibility.
- Keep the POST-only posture and its typed 405. The spec permits it and `mcp_http.py:6` documents the choice.
- Version negotiation must **fail legibly**: a client requesting an unsupported revision gets a typed error naming what this surface speaks, not a silent partial handshake.

### Amendment task table (extends this plan; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

| ID | Task | Files | Done when |
|---|---|---|---|
| G1.1 | Clause-by-clause conformance review of the current surface against the target revision(s); record the chosen revision + any deliberately-unmet clause and its reason in the execution log | `docs/roadmap/plans/MCP-READONLY-INBOUND.md` (log), notes | the chosen revision is justified in writing, with any gap named rather than discovered later by a client |
| G1.2 | Bump `PROTOCOL_VERSION` to the reviewed revision and align the `initialize` result; **no session handling added**; POST-only + typed 405 preserved | `src/personalclaw/inbound/mcp_http.py:34`, `:191`, tests | `initialize` advertises the reviewed revision; `grep Mcp-Session-Id src/` still returns zero (regression test); `GET /mcp` still 405s |
| G1.3 | Version-negotiation error path: an unsupported requested revision returns a typed JSON-RPC error naming the supported revision(s), per §2.2's stable-code discipline | `inbound/mcp_http.py`, tests | a request for an unsupported revision (older or newer) fails legibly, never with a partial handshake |
| G1.4 | Regression-lock the security posture across the bump: existing fail-closed enablement, token-distinctness, loopback-gating, read-only, and `fence_untrusted` tests all still pass unmodified — **if a test needed editing to accommodate the bump, stop (E4)** | `tests/test_inbound_mcp.py` | the full existing inbound test suite passes with zero edits to security assertions |
| VG | Validation as a user: with the surface enabled on an isolated dev home, connect a real modern MCP client end-to-end, list the six tools, invoke each, and confirm results arrive fenced; confirm a client requesting an unsupported revision gets the typed error; confirm remote access is still refused without `allow_remote` + `public_url` | — | holds |

### Note for EXTERNAL-ACCESS (24)
Plan 24 (EXTERNAL-ACCESS) widens this `inbound/` package rather than re-designing it — the shared-inbound-surface convergence point. It should **inherit the revision chosen here** rather than negotiating its own, so every dialect on the shared substrate speaks one protocol revision.

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

### 2026-08-17 — MRI-5 (V2 + VG): the validation ledger — **PARTIAL, and it found three defects**

Driven with a **real MCP client**: the official Python SDK, `mcp 1.28.1`, over
`mcp.client.streamable_http.streamablehttp_client` → `ClientSession`. Isolated dev home,
loopback port 10424, never `~/.personalclaw`. **No handler was called directly** — every
result below came off the wire. Where a probe used raw HTTP instead of the SDK it says so.

**The headline: a stock MCP client could not connect at all.** Session 2's V2 note said "the
protocol surface itself is proven" on the strength of a `curl` drive. It was not. `curl` sends
whatever revision you type; a real client sends *its* default, and the SDK's is `2025-11-25`:

```
mcp.shared.exceptions.McpError: unsupported protocolVersion '2025-11-25';
  this server speaks 2025-06-18, 2024-11-05
```

The handshake died there. The client's own supported list is
`['2024-11-05','2025-03-26','2025-06-18','2025-11-25']` — it speaks our revision and would
have agreed instantly. **`2025-03-26` is also a real published revision this surface rejects.**

**DEVIATION — G1.3's `-32602` was wrong, and is replaced by a counter-offer.** The spec's
lifecycle clause is a MUST in *both* directions: same version if supported, "otherwise the
server MUST respond with another protocol version it supports", and the client SHOULD
disconnect if it can't use the answer. G1.3 reasoned that a mismatch "has to be said out
loud" — it does, and `protocolVersion` is the field that says it. The typed error was
strictly worse: it converts "we disagree about a revision" into "you cannot connect".
The reference implementation agrees (`mcp/server/session.py`: requested-if-supported else
latest). What G1.3 got right is kept: a supported request is ECHOED, never overridden.

**Clause-by-clause against the `done_when`:**

| Clause | Verdict |
|---|---|
| real MCP client connects over loopback | ✅ `initialize` → `protocolVersion 2025-06-18`, `capabilities {tools}`, `serverInfo personalclaw/0.1.3` — **only after the fix**; ❌ before it |
| exercises all six tools | ✅ all six, real rows, every one fenced |
| trips the rate cap | ✅ real client cut off after 19 calls; raw HTTP: 20 admitted, #21 → `429` `Retry-After: 1` |
| flips the kill switch mid-session | ✅ config flip mid-session → `McpError: Session terminated`, raw `404`; re-enable → `200` |
| SEL + `inbound_audit.jsonl` trails match design | ⚠️ they match **after** fixing one rejection that reached neither |
| remote refused without `allow_remote` + `public_url` | ⚠️ refused — but by the **dashboard CSRF middleware**, not by `peer_allowed`. See below |
| validation ledger written | ✅ this entry |

**All six tools, over the wire** (seeded through non-MCP paths first, so an empty answer
could not be mistaken for a working tool — marker `pomegranate`):

- `status {}` → `PersonalClaw 0.1.3 / tasks: available / memory: keyword search + entity
  graph / knowledge items: 1`
- `memory_recall {"query":"pomegranate","limit":5}` → `1 memory hit(s)` with the seeded episode
- `knowledge_search {"query":"pomegranate","limit":5}` → `1 knowledge item(s)`
- `sessions_search {"query":"pomegranate","limit":5}` → the transcript, and the planted
  `sk-ant-api03-…` key came back as **`[REDACTED: credential]`** — §C3 redaction confirmed live
- `tasks_list {"limit":10}` → `1 of 1 task(s)`
- `task_get {"id":"t-43575a76"}` → full record

Every result arrived inside `<untrusted_content source=inbound:mcp:<tool>>` with the
data-not-instructions preamble.

**Read-only: "no write tool exposed" vs "writes refused" — distinguished, because they look
identical from a happy path.** What was observed is **refusal by ABSENCE**, and it is worth
naming as such: there is no write-policy gate on this surface that inspects a request and
denies it. `tools/call task_create|memory_write|config_set` → `-32601 unknown tool 'task_create'`;
`logging/setLevel` — a genuine *mutating* MCP method — → `-32601 unknown method
'logging/setLevel'`; `resources/list`, `prompts/list` → same. `tasks_list {"title":"injected"}`
→ `-32602 unknown argument(s): title`. Raw HTTP `PUT`/`DELETE`/`PATCH /mcp` → `405`.
**Vacuity floor:** on that same live session a `status` read still returned `200`, and on the
same raw client `POST /mcp` still returned 6 tools — so the refusals were refusals, not a dead
connection. Absence is the stronger property; the point is that nothing here would stop a
future write tool added to `TOOLS`, so the guard is the table's hand-written shortness.

**Defect 2 — `TaskStatus.OPEN` crossed the boundary.** `tasks_list` returned
`- [TaskStatus.OPEN] t-43575a76: …` — a Python enum repr, handed to a model as though it were
the status vocabulary — while `priority` in the same handler rendered `medium`. Fixed
(`_enum_value`): now `- [open] …`, verified live.

**Defect 3 — one rejection reached NEITHER trail.** The audit file after a full drive carried
`unknown tool`, `rate limit`, `GET not supported` and `disabled: inbound.mcp.enabled is off`,
each mirrored to SEL as `outcome=denied` (13 rows, hash-chained). An **argument** rejection
recorded as a plain `200` with no reason, so it never reached SEL either — a caller probing
argument shapes left no denied trail at all, against this module's own docstring ("every
rejection is audited"). Fixed; verified live (`refused_reason: "invalid arguments"`, SEL 13→14).
The reason stays generic on purpose — the message embeds caller-supplied argument names.

**Defect 4 — `inbound.mcp.allow_remote` + `inbound.public_url` are INERT for a real MCP
client. NOT fixed: this is an owner scope call (E4), and it belongs to EXTERNAL-ACCESS (24).**
The first remote probe returned `403 CSRF check failed: request origin not allowed.` in **all
six** states — including the one that should have been allowed. That failed vacuity floor is
the finding: `csrf_middleware` (`dashboard/server.py:1680`) has **no path exemption**, so it
refuses every non-safe method before the route runs. `/mcp` is exempt from the dashboard
*token* check (`_BYPASS_EXACT`) but not from this one. `check_origin(require=True)` with no
`Origin` header returns True **only for a loopback peer** — and an MCP client is not a browser,
so it never sends `Origin`. Net effect: a remote MCP client is refused before `peer_allowed`
ever executes, no matter how `allow_remote`/`public_url` are set. Adding one allowed `Origin`
header — the only change between the two runs — moved every response from the CSRF text to the
inbound surface's own, which is what proves the diagnosis.

The security *outcome* is fine (remote fails closed, harder than designed). Two consequences
to carry forward: the knob is a promise the gateway cannot keep, and G1.1's conformance note
citing `peer_allowed` for the spec's DNS-rebinding guidance describes a branch that never runs
for a remote caller. Widening a CSRF control is not a validation atom's call.

**`peer_allowed` itself, once reachable, is correct** — exercised with a genuinely non-loopback
peer at **zero network exposure** by binding the probe gateway to `fe80::1%lo0` (an IPv6
link-local address that lives only on `lo0` yet is absent from the surface's loopback
allowlist; `lsof` confirmed nothing on `0.0.0.0`). Raw HTTP, since httpx cannot express an IPv6
scope id — sound here because `peer_allowed` runs at check 2, before any protocol reasoning:

| State | Result | Audited reason |
|---|---|---|
| `allow_remote` off | `403 {"error":"forbidden"}` | `non-loopback peer and allow_remote is off` |
| on, `public_url` unset | `403` | `allow_remote is on but inbound.public_url is unset` |
| on, `Host` ≠ `public_url` | `403` | `Host '[fe80::1]:10426' does not match inbound.public_url` |
| forged `Host`, `allow_remote` off | `403` | `non-loopback peer and allow_remote is off` |
| **both satisfied (vacuity floor)** | **`200`, tools=6** | — |
| allowed peer, wrong token | `401` | `bad or missing bearer token` |

A forged `Host` alone buys nothing, and the peer and token gates are independent as designed.

**NOT exercised, with reasons:** an IDE's own MCP config (the SDK client is the client this
plan's `done_when` names as the alternative, and it is what ran); `2025-03-26` acceptance (the
surface rejects it — recorded as a gap, not fixed, since widening `SUPPORTED_PROTOCOL_VERSIONS`
is a wire-contract decision); a *genuinely* off-host peer (deliberately avoided — the
`fe80::1%lo0` bind exercises the same branch without exposing a port).

**Also observed, not a defect:** a `429` tears the SDK's session down entirely
(`raise_for_status` in the streamable-http transport), so exceeding the burst costs an IDE its
connection, not just one call. HTTP-level rate limiting is spec-permitted, and MRI-1's
`done_when` specifies exactly this `429` + `Retry-After`, so the shape stays. Worth knowing
before EXTERNAL-ACCESS raises the traffic. Audit rows also omit the peer, so the trail cannot
say *which* client was limited.

**Gates:** `make lint` clean · `tests/test_inbound_mcp.py` **101 passed** (+3: the SDK-default
handshake, the enum boundary, the argument-refusal trail) · `test_agent_reference.py`,
`test_docs_lint_baseline.py`, `test_inert_surface_baseline.py`, `tests/security/` green.
All three fixes falsified by mutating the live line and observing the specific red.

- 2026-08-18 — **DONE (MRI-4 / T2.6): [`docs/guides/use-from-your-ide.md`](../../guides/use-from-your-ide.md)
  written AFTER executing every step it publishes**, on an isolated dev home
  (`PERSONALCLAW_HOME=<worktree>/.dev-home`, port 10521) with the official SDK (`mcp 1.28.1`).
  Linked from `README.md`'s documentation index. Nothing was published that was not run; the
  guide's own code block was extracted back out of the committed file and executed to prove it.

  **The counter-offer fix is on `main`** (`inbound/mcp_http.py`, the `initialize` branch +
  `SUPPORTED_PROTOCOL_VERSIONS`), so a reader with a current SDK connects with no pin: the client
  asked for its default `2025-11-25` and the session came up on the counter-offered `2025-06-18`,
  `serverInfo personalclaw 0.1.3`, all six tools listed, `status` returning fenced content. MRI-5's
  "a stock client could not handshake at all" is therefore closed for anyone on `main`.

  **Defect 4 independently REPRODUCED, and hardened in the doc from "caveat" to "hard limit".**
  With the knobs fully ON — `inbound.mcp.allow_remote true`, `inbound.public_url
  http://192.168.86.33:10521` matching the request `Host` exactly, a valid dedicated token, gateway
  bound `*:10521` — a non-loopback peer still gets `403 CSRF check failed: request origin not
  allowed.` while the identical loopback request gets `200`. The response *body* is what proves the
  layer: `csrf_middleware` emits that text, `peer_allowed` emits `{"error": "forbidden"}`. So the
  guide states plainly that off-machine access does not work and tells the reader not to spend an
  evening on the two knobs, rather than presenting remote as a configuration choice.

  **HARNESS TRAP worth carrying forward: do NOT measure this surface's CSRF behaviour under
  `PERSONALCLAW_AUTH_MODE=none`.** That mode swaps `csrf_middleware` + `token_auth_middleware` out
  for `_dev_user_middleware` (`dashboard/server.py`, the explicit `app.middlewares[:]` block), so the
  middleware under test is *absent*. A first probe in none-mode returned `200` for a foreign
  `Origin` and read as "no CSRF problem"; the same probe in default auth mode returned `403`. Any
  future inbound validation must run in the reader's auth mode, not the convenient one.

  **NEW measured finding — the enablement flag is live in ONE direction only, and the two `404`s
  are distinguishable.** `mount()` registers `/mcp` at gateway startup only, and only if the flag
  and token already pass; enablement is then re-checked per request. Measured: enabling under a
  running gateway that started disabled yields a bare **`404: Not Found`** (plain text, aiohttp's
  own — the route was never registered), and only a restart fixes it; disabling under a running
  gateway that started enabled yields the surface's **JSON `{"error": "not found"}`** on the next
  call with no restart; re-enabling from that state comes straight back, also with no restart. **My
  own first draft got this wrong** — it said "to turn it back on: set the flag to `true` and
  restart" and claimed the asymmetry was off-live/on-restart. Executing it falsified both
  sentences; corrected before commit. The body difference is now the guide's primary
  troubleshooting discriminator.

  **Rate limit measured** (MRI-5 recorded the SDK teardown; here is the mechanism): the 21st rapid
  call is the first refused — burst 20 — with `Retry-After: 1` and `{"error": "rate limited"}`. The
  `429` is HTTP-level, so `raise_for_status` in the SDK's streamable-http transport raises
  `httpx.HTTPStatusError` out of the transport's task group; it escapes the `async with` blocks
  entirely, so a per-call `except` cannot catch it and the session is gone, not the one call.

  **Also verified verbatim:** `personalclaw inbound token create mcp` (prints the bearer once,
  `0600` file), `inbound token show mcp` (confirms without revealing), a second `create` without
  `--rotate` refusing rather than clobbering, `--rotate` invalidating the old token to `401
  {"error": "unauthorized"}` while the new one works with no restart, `GET /mcp` → `405` as the
  mounted-and-healthy signal, and `task_create` → `-32601 unknown tool` (read-only by absence).
  The two protocol claims were closed on the wire rather than read off the constant: a client
  pinning `2024-11-05` gets `2024-11-05` echoed back (not overridden by our preference), and an
  `initialize` response carries no session header at all. (Aside, not a defect: the amendment's
  "`grep -rn "Mcp-Session-Id" src/` returns zero" now returns **1** — the sole hit is
  `inbound/mcp_http.py`'s own comment saying no session id is issued. The regression test strips
  comments, so it is the plan's phrasing that is loose, not the posture.)
  `Authorization: Bearer` is confirmed as the correct form **for `/mcp`** — it is in
  `token_auth.py`'s `_BYPASS_EXACT`, so the dashboard's `?token=`/`pc_token_<port>` cookie shape
  does not apply here. The inverse was measured too, because it is the likelier reader error: a
  Bearer header against an `/api/…` path (and a client mistakenly pointed at `/api/mcp` instead of
  `/mcp`) both return **`403 {"error": "Token required"}`**, so the guide uses that string as the
  "wrong path" tell rather than an auth-shape mistake. No real token appears in the guide
  (placeholder only; scanned for the three session tokens and for any 40+ char token-shaped run).

  **NOT exercised, with reasons:** a real IDE's own MCP config file — no editor was driven, so the
  guide publishes the three values PersonalClaw requires (streamable-HTTP transport, URL,
  `Authorization` header) as verified and explicitly hands the client-specific JSON key spelling
  back to the reader's client docs; and a stdio→HTTP bridge, flagged in the guide as unexercised
  rather than implied to work.

  **Code fix this guide wants but did not make (fencing: docs only):** `personalclaw inbound token
  create mcp` closes with "The surface is loopback-only until you set `inbound.public_url` +
  `allow_remote`" — which is Defect 4's promise the gateway cannot keep, now printed at the exact
  moment a reader is deciding what to do next. The guide contradicts it inline with a blockquote,
  but the CLI string should either drop that line or say the two fields do not work for an MCP
  client. Owner call, same class as Defect 4 (E4), and it belongs with whoever resolves the knob.

  **Gates:** `tests/test_docs_lint_baseline.py` + `tests/test_agent_reference.py` green (docs-lint
  ratchet did not rise) · `make lint` clean. Gateway on 10521 stopped; `~/.personalclaw` untouched.

### 2026-08-18 — MRI-5 re-validation on current `main` — **DONE** (the 2026-08-17 PARTIAL is closed)

The 2026-08-17 MRI-5 entry above was PARTIAL for two reasons: it had to fix three defects
*mid-drive*, so no single clean run had ever exercised the surface end to end; and Defect 4.
All three fixes are on `main` (verified by content, not by changelog: `_enum_value` at
`inbound/tools.py:88`, the `initialize` counter-offer + `SUPPORTED_PROTOCOL_VERSIONS` at
`inbound/mcp_http.py:72`, `refused="invalid arguments"` at `inbound/mcp_http.py:317`). This is
the clean run: **no code was changed to make anything pass.** Isolated home
`/private/tmp/mri5-drive2/home`, loopback port 10357, never `~/.personalclaw`. Default auth mode
throughout — MRI-4's harness trap says `AUTH_MODE=none` swaps out the very middleware under test.

**Client:** the official SDK, `mcp 1.28.1`, `mcp.client.streamable_http.streamablehttp_client`
→ `ClientSession`. Recorded precisely because the `done_when` names an alternative: the venv's
`mcp` CLI **cannot** serve as the client — its commands are `version` / `dev` / `run` / `install`,
all server-side dev tooling, with no connect-to-a-remote-HTTP-server verb. The SDK client library
is the thing an IDE actually runs, and it is what ran here. No hand-rolled JSON-RPC in any happy
path; where raw HTTP appears below it is to read a status code the SDK hides, and it says so.

| Clause | Verdict |
|---|---|
| real MCP client connects over loopback | ✅ unpinned: client asked its default `2025-11-25`, session came up on the counter-offered `2025-06-18`, `serverInfo personalclaw 0.1.3`, `capabilities {'tools': {}}` |
| exercises all six tools | ✅ all six, against pre-seeded data, every one fenced |
| trips the rate cap | ✅ request #21 refused, verbatim below; recovery confirmed |
| flips the kill switch mid-session | ✅ **re-measured** — the first attempt was contaminated (below) |
| SEL + `inbound_audit.jsonl` trails match design | ✅ 19 refusals ↔ 19 SEL `denied` rows, reason strings identical; `verify_integrity` all-valid |
| remote refused without `allow_remote` + `public_url` | ✅ refused — Defect 4 unchanged: still the *wrong layer*, third independent reproduction |
| validation ledger written | ✅ this entry |

**All six tools, enumerated off the wire from `TOOLS`, not counted from the doc.** `tools/list`
returned exactly six: `knowledge_search`, `memory_recall`, `sessions_search`, `status`,
`task_get`, `tasks_list`. Every read path was seeded FIRST through its own non-MCP write path
(`VectorMemoryStore.write_episodic`, `create_typed_item`, `ConversationLog.append`,
`registry.create_task`), marker `zarafruit`, so an empty list could not be mistaken for a
working tool:

- `status {}` → `PersonalClaw 0.1.3 / tasks: available / memory: keyword search + entity graph / knowledge items: 1`
- `memory_recall {"query":"zarafruit","limit":5}` → `1 memory hit(s)` + the seeded episode
- `knowledge_search {"query":"zarafruit","limit":5}` → `1 knowledge item(s): zarafruit harvest notes`
- `sessions_search {"query":"zarafruit","limit":5}` → the transcript, and the planted
  `sk-ant-api03-…` came back `[REDACTED: credential]` — §C3 redaction live
- `tasks_list {"limit":10}` → `1 of 1 task(s): - [open] t-180fcf1b: Audit the zarafruit ledger`
- `task_get {"id":"t-180fcf1b"}` → full record, `status: open · priority: high`

The enum fix is confirmed by a **contrast**, not by reading the code: the seeding script printed
`TaskStatus.OPEN TaskPriority.HIGH` from the same objects the tool rendered as `open` / `high`.

**Rate cap, verbatim, from a full bucket** (`rps 1.0`, `burst 20`). Requests 1–20 → `200`; then

```
request #21: HTTP 429 Retry-After='1' Cache-Control='no-store' body={"error": "rate limited"}
```

Recovery works as designed: 2.5s idle at 1 token/s bought two more `200`s. The SDK symptom is
harsher than the HTTP one — the session died at request #19 with an `ExceptionGroup`, because
`raise_for_status` in the streamable-http transport escapes the `async with` blocks, so exceeding
the burst costs an IDE its whole connection rather than one call (already noted 2026-08-17;
re-confirmed, shape unchanged).

**Kill switch mid-session — and the trap that nearly produced a false pass.** The first run
flipped the switch in a phase that ran *after* the tool sweep, by which point phases 1–2 had
already drained the 20-token bucket. The next call returned `McpError: Session terminated` — which
is **exactly** what a `429` transport teardown also produces. That reading would have credited the
kill switch for the rate cap's work. Re-measured in isolation: waited out the bucket, stayed under
six requests, confirmed the session healthy immediately before the flip, then flipped
`inbound.mcp.enabled → false` through the real write path (`personalclaw config set`), and read the
**status code** off a raw POST on a separate connection, because 404 and 429 are indistinguishable
from the SDK error alone:

```
BEFORE flip, live SDK session ->  PersonalClaw 0.1.3
   CLI said: inbound.mcp.enabled = false
   raw POST /mcp [post-flip] -> HTTP 404 Retry-After=- body={"error": "not found"}
   SYMPTOM, same still-open SDK session: McpError: Session terminated
   CLI said: inbound.mcp.enabled = true
   raw POST /mcp [post-re-enable] -> HTTP 200
AFTER re-enable, fresh SDK session ->  PersonalClaw 0.1.3
```

`404` + the surface's own JSON body is the kill switch; the re-enable needs no restart, matching
MRI-4's asymmetry note (off-live works because enablement is re-checked per request).

**Both trails, and a refused call in each.** 91 audit rows, of which 19 are refusals spanning
every refusal class the surface has, each carrying `refused_reason`; the SEL holds 19
`caller_identity=inbound:mcp` `outcome=denied` rows with **byte-identical reason strings** — a
1:1 mirror, not a sampled one:

| `refused_reason` | status | audit | SEL `denied` |
|---|---|---|---|
| `rate limit` | 429 | 5 | 5 |
| `disabled: inbound.mcp.enabled is off` | 404 | 3 | 3 |
| `non-loopback peer and allow_remote is off` | 403 | 3 | 3 |
| `invalid arguments` | 200 | 2 | 2 |
| `GET not supported` | 405 | 2 | 2 |
| `unknown tool` | 200 | 1 | 1 |
| `allow_remote is on but inbound.public_url is unset` | 403 | 1 | 1 |
| `Host '127.0.0.1:10358' does not match inbound.public_url` | 403 | 1 | 1 |
| `bad or missing bearer token` | 401 | 1 | 1 |

Verbatim, the argument refusal that 2026-08-17 found reaching neither trail — now in both:

```
{"ts": "2026-08-19T02:33:34.466707+00:00", "surface": "mcp", "route": "POST /mcp", "status": 200,
 "bytes_in": 110, "bytes_out": 129, "duration_ms": 6, "tool": "tasks_list",
 "refused_reason": "invalid arguments"}
{"event_id": "bc5285b1f7924a0f", "event_type": "api_access", "caller_identity": "inbound:mcp",
 "source": "inbound", "operation": "POST /mcp", "outcome": "denied",
 "resources": "invalid arguments", "prev_hash": "bb84581b…", "entry_hash": "a360af88…"}
```

`sel().verify_integrity(max_entries=None)` → `checked=51 valid=51`. Also worth recording so it is
not re-filed: the SEL's hash chain **forks** at every `cli` → `inbound` process boundary (four
breaks in this drive, all where a `config set` row precedes a gateway row). That is **documented
design, not a defect** — `sel.py:349` tolerates `prev_hash` mismatches explicitly because
gateway/channel/mcp processes append without IPC, and the per-entry HMAC is the integrity
primitive. Flipping the kill switch via the CLI is the *documented* way to do it, so this fork is
reachable in ordinary use; the verifier is built for it.

**NEW finding — a CSRF-layer refusal reaches NEITHER trail.** Ten remote requests in this drive
were refused by `csrf_middleware`, and the inbound audit and the SEL both contain **zero** rows
for them (`grep -ci csrf` → 0 in each; the 19 audited refusals are all accounted for by the
`peer_allowed`-reachable probes). Because the middleware refuses before the route runs,
`handle_mcp` never executes and never audits. So for the caller Defect 4 actually affects — a
remote MCP client — the refusal is *invisible* to the surface's own security trail. The module's
"every rejection is audited" holds only for rejections that reach the module. This is Defect 4's
blast radius, not a separate defect, and it goes to the same owner.

**Remote refusal: proven, and Defect 4 reproduced a third time.** Exercised with a genuinely
non-loopback peer at **zero network exposure** by binding a second gateway to `fe80::1%lo0` — an
IPv6 link-local address present only on `lo0` yet absent from `auth._LOOPBACK`
(`{"127.0.0.1","::1","::ffff:127.0.0.1","localhost"}`), so `is_loopback()` is False for it.
`lsof` confirmed the listener as `[fe80:1::1]:10358` and nothing of this drive's on `0.0.0.0`.
All five states — including the one that should have been **allowed** — returned the identical

```
HTTP 403  body=CSRF check failed: request origin not allowed.
```

That failed vacuity floor *is* the finding, exactly as 2026-08-17 recorded it. The layer is then
pinned by changing **one header**: with `Origin: http://127.0.0.1:10358` (an allowed origin from
`build_allowed_origins`) on the same non-loopback peer with the same token, the body becomes
`{"error": "forbidden"}` — the inbound surface's own. `csrf_middleware` therefore runs before
`peer_allowed` and refuses every remote MCP client regardless of the knobs. With it out of the
way, `peer_allowed` is **correct and non-vacuous**:

| State | Result |
|---|---|
| `allow_remote` off (shipped default) | `403 {"error":"forbidden"}` |
| on, `public_url` unset | `403` |
| on, `Host` ≠ `public_url` | `403` |
| **both satisfied (vacuity floor)** | **`200`, six tools** |
| both satisfied, wrong token | `401 {"error":"unauthorized"}` |
| forged `Host`, `allow_remote` off | `403` — a forged Host buys nothing |

Config was restored to the shipped closed default (`allow_remote false`, `public_url ""`) and
verified by re-reading `config.json`. **Nothing was widened to make a probe pass**; the one
widening used (states 2–5) is the knob's own documented setting, on a `lo0`-only address, reverted
immediately. Defect 4 stays where 2026-08-17 put it: an owner scope call (E4) belonging to
EXTERNAL-ACCESS (24). Security outcome remains fail-closed — remote is refused harder than
designed — so this clause is met as written; what is broken is the *legibility* of the knob and,
per the finding above, the auditability of the refusal.

**NOT exercised, with reasons:** an IDE's own MCP config (no IDE is installed here; the SDK client
an IDE embeds is what ran, and the `mcp` CLI has no client verb — see above); `2025-03-26`, a real
published revision this surface still rejects, unchanged from 2026-08-17 and still a wire-contract
decision rather than a validation call; a genuinely off-host peer (deliberately avoided — the
`fe80::1%lo0` bind reaches the same branch without exposing a port).

**Gates:** `make lint` exit 0 (black 1790 files, isort, flake8, mypy 919 sources) ·
`tests/test_inbound_mcp.py` **101 passed** alone, and **256 passed** for the union of
`test_inbound_mcp.py`, `tests/security/`, `test_config_roundtrip.py`, `test_docs_lint_baseline.py`,
`test_agent_reference.py`, `test_inert_surface_baseline.py` — the three ratchets included because
this change edits a doc, and none of them rose. Both gateways stopped and both ports confirmed
free.
**Real-home probe, non-vacuous:** `find <dir> -newer /private/tmp/mri5-drive2/REF_MARKER` (a
marker touched before anything started; `find` here is `bfs 4.1.1`, so a *relative* timestamp
would have errored while exiting 0 — hence `-newer <file>`) → `~/.personalclaw` **0**,
`~/workplace` **0**, positive control under the drive dir **564**, so the probe does find files
when there are files to find.
