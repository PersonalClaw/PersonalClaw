# Ollama

OpenAI-compatible LLM and embedding provider via Ollama. Connect to local or remote Ollama instances for chat and embedding.

**Ollama** is a **model provider + local-model manager** — it registers Ollama chat/embedding models under Settings → Models and manages pulls from a local or remote Ollama instance.

## What this is

A standalone PersonalClaw app bundle (part of the core/app workspace split). It ships
as a self-contained directory:

- `app.json` — the manifest (identity, provider/backend/UI declarations, permissions).
- `provider.py` — the implementation, exposed via `create_provider`.
- `tests/` — the app's own tests.

It imports only the PersonalClaw **SDK** (never core internals), so core can evolve
without breaking it:

- `personalclaw.sdk.local_model`
- `personalclaw.sdk.model`

## Structured output

Ollama enforces a JSON Schema **server-side** via a top-level `format` field on
`/api/chat`, so this app declares the top grade of the platform's graded
structured-output capability (`json_schema`) and shapes the request natively — the
sampler is constrained to emit a conforming document rather than being asked for JSON
in prose and repaired afterwards. Because the constraint is applied by the Ollama
runtime and not by the model's own instruction-following, it holds on a small local
model too.

A requested contract arrives as a build option and is normalized once:

| Requested | Sent as `format` |
|---|---|
| `dict` / `list` (the types `output_type=` passes) | `{"type": "object"}` / `{"type": "array"}` |
| a JSON Schema object | that schema, verbatim |
| `"json"` | `"json"` (unschema'd JSON mode) |
| anything else, or nothing | no `format` field at all |

The last row matters: an ordinary chat turn carries no `format`, so nothing forces JSON
onto normal conversation, and an unexpressible request is refused rather than forwarded
into the JSON encoder as an opaque error.

## Install

From the App Store, add the `apps/` directory as a **local source**, then install
**Ollama** — the install runs through the security scanner and lifecycle exactly like
any other app. (Or `POST /api/apps {"source": ".../apps/ollama-models"}`.)

## Settings

| Key | Label | Notes |
|---|---|---|
| `endpoint` | Ollama Endpoint | Base URL of the Ollama API server. |
| `default_model` | Default Model | Model to use when no specific model is requested. Leave empty to use the first available. |
| `embedding_model` | Embedding Model | Ollama model to use for embedding operations. Leave empty to use sentence-transformers instead. |
| `timeout_secs` | Request Timeout | Maximum seconds to wait for a response from Ollama. |
| `context_window` | Served Context Window | Tokens this endpoint actually serves (Ollama's `num_ctx`). Leave blank to detect it. |

### The context window

The window a local runtime serves is a **deployment** choice, not a property of the model,
and it is usually far below the model's architectural maximum — measured on Ollama 0.34.2,
one model reported a 262144-token architecture while serving 32768. PersonalClaw sizes the
context gauge and the compaction trigger against the served number, so getting it wrong is
not cosmetic: too large and the 70% compaction gate is never crossed, history grows, and
Ollama silently truncates the prompt (HTTP 200, no error to catch).

This provider resolves it in three steps, most authoritative first:

1. **`context_window`, if you set it.** Set this whenever you run Ollama with an explicit
   `num_ctx` (or `OLLAMA_CONTEXT_LENGTH`) — you know the number and no probe can beat it.
2. **`GET /api/ps`**, which reports the window the model was actually loaded with. This is
   the number neither the model table nor `/api/show` has — both return the architectural
   maximum. Probed after the first turn (the endpoint lists only *loaded* models) and
   memoized, since the served window cannot change without a reload.
3. **Nothing, when the runtime says nothing.** The composer then shows a plain dot instead
   of a ring and tells you the usage is unknown, because a percentage of a window nobody
   declared is a made-up measurement, not a cautious one. Compaction is not left without a
   trigger: PersonalClaw falls back to a character-based *estimate* against a deliberately
   small local window, which may compact early — cheap — where a too-large denominator
   truncates the prompt with no error at all. Set `context_window` to replace the estimate
   with a real reading.

### The gauge past the window

Once the prompt no longer fits, Ollama does not reject it: it keeps part of the prompt,
returns HTTP 200, and reports the *truncated* token count — measured at half the served
window, for every prompt size above the cliff. Taken at face value the ring would then
**fall** as the context grew, which is why a session could sail past the compaction
threshold and never see it again. So the gauge also remembers the largest prompt this
binding has had measured, and applies one rule: a provider handed a larger prompt cannot
honestly report fewer input tokens. When that happens the ring reads **full**. The rule is
deliberately ordinal — it makes no guess about how many characters a token is worth,
because measured on one host and one model that figure ranged from 4.5 to 8.0 depending
only on the text.

## License

MIT — see [LICENSE](./LICENSE) beside this file.
