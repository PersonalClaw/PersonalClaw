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

## Models Ollama answers from its cloud

An Ollama server runs most of the models it lists on the machine it runs on, and an instance at
`localhost` therefore serves models that run on this machine: free, tried first by local-first
routing, and scanned on the way out only as prompts that never leave the machine. A model Ollama
answers from **Ollama's cloud** is not one of them. Its prompts go to a hosted service, so
PersonalClaw treats it as the remote model it is: unpriced until you give it a price under
**Settings → Usage → Model prices** (a daily dollar cap refuses its calls until then, and a
price of $0 is how you say it costs you nothing), not tried first as local, given the outbound
scan your settings ask for, and shown under Settings as running off this machine, with no fit
chip.

This app tells core which models those are, one at a time (the `passes_on` probe it registers
with its provider type):

1. **What the server said.** Ollama names the host it answers a cloud model from
   (`remote_host`) in its model list (`GET /api/tags`), in the model's record (`POST /api/show`)
   and on each line of an answer (`POST /api/chat`), and names none for a model of its own. The
   app keeps the last of these per server, so a copy of a cloud model under a name of its own is
   placed too, once the server has listed it or answered for it.
2. **What the model is called**, before the server has been asked: Ollama tags its cloud models
   `cloud` (`glm-4.6:cloud`), or with the size before it (`gpt-oss:120b-cloud`).

Either one makes the model remote. A model whose tag says nothing of the cloud and that the server
has named no host for runs where the server is.

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
| `default_model` | Default Model | The model this instance answers with when nothing in Settings → Models names one. Leave it empty to choose its models in Settings → Models. |
| `embedding_model` | Embedding Model | Ollama model to use for embedding operations. Leave empty to use sentence-transformers instead. |
| `timeout_secs` | Request Timeout | Seconds to wait for the model to start answering, and then between the parts of its answer (default 600). Nothing arrives until the model has read the whole prompt, which on a long conversation can take minutes; a request that times out before its first word says so and names this setting. An unreachable address still fails within 10 seconds. |
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
