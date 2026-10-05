# Bundled offline model

The small Apache-2.0 chat model PersonalClaw can run itself, so a fresh install can hold a
conversation before any provider is bound — no account, no API key, no Ollama.

🔴 **The weight is not in the wheel. The FIRST chat on a fresh install needs network.** The app
ships; the 138 MiB weight is fetched once, on an explicit click, into
`$PERSONALCLAW_HOME/models/bundled-chat/`. Everything after that download is offline. This is a
deliberate trade — a ~147 MiB wheel for every `pip install` is a
real cost, and it is over PyPI's 100 MiB per-file limit. If you need a genuinely offline first
run, pre-seed a home once with `PERSONALCLAW_HOME=/path make bundled-model` and copy it.

This is the **floor**, not a recommendation. Bind a real model in Settings → Models and the
floor stops being used automatically.

## What it runs

| | |
|---|---|
| Model | `unsloth/SmolLM2-135M-Instruct-GGUF` (a GGUF build of `HuggingFaceTB/SmolLM2-135M-Instruct`) |
| Licence | **Apache-2.0**, both the base model and the GGUF build — see [`bundled-model-signoff.txt`](bundled-model-signoff.txt) (the one copy of the record, kept beside the code that reads it; the docs link here) for the recorded sign-off, and `weights/MODEL_LICENSE` for the licence text, which the fetcher copies next to the downloaded weight so Apache-2.0 §4 is satisfied on the machine holding the bytes |
| Quantization | Q8_0 — 144,811,072 bytes (138.1 MiB) |
| Runtime | `numpy`, already a core dependency. No compiler, no vendor runtime, no platform-specific wheel |
| Memory | ~540 MB resident once loaded (the weight is dequantized to float32), loaded **lazily** on the first turn |
| Speed | roughly 5–50 tokens/second on a CPU, depending on memory bandwidth |

## The download

The weight is in **no** distribution artifact — not the wheel, not the container image, not the
desktop bundle. It is fetched once per machine, on an explicit click, and the requirements on
that fetch are all in `provider.py::download_weight` rather than in any caller:

| | |
|---|---|
| **Asked for** | never automatic. A button labelled with the model, its licence and its size — onboarding's model step, the chat screen, or Settings → Providers → Bundled offline model. Declining costs nothing |
| **Visible** | bytes, a percentage and an ETA — core's generic download job (`POST /api/models/downloads` → SSE) measures the download directory as it grows. A reload re-attaches to a running job on every one of those surfaces |
| **Bounded** | https only, a 30 s per-read socket timeout **and** a 45-minute total deadline. Both, because a socket timeout does not catch a connection that delivers a byte at a time forever. The 150 MiB ceiling is enforced **while** the bytes arrive (`admit_transfer`): a source announcing more is refused before the first write, and a transfer that passes it is stopped and its partial removed |
| **Cancellable** | for real. The transfer is a loop of `await asyncio.to_thread(read, 4 MiB)`, so a cancel lands between chunks and the partial file is removed |
| **Verified** | against the sha256 in the sign-off record, before an atomic replace. A fetched weight is untrusted input in a way a wheel-bundled one was not |
| **Once** | a cross-process, cross-thread `single_flight` lock on the home. Two gateways on one home produce one transfer; the loser waits for the winner's file |
| **Never half-installed** | bytes go to a `.partial` sibling and are verified before the replace, so nothing at the real path is ever unverified and a dead transfer cannot be mistaken for a finished model |

Failures carry one of core's `DOWNLOAD_*` outcome codes, because the four need four different
things from a user: `unreachable` → retry, once the thing the sentence names is fixed;
`bad-status` → the pinned source is broken, stop retrying; `truncated` / `digest-mismatch` →
the bytes were refused. Every one leaves the install usable and the offer retryable.

`unreachable` covers every failure a retry can fix, and the sentence says which one it was: no
network (retry when connected); a proxy asking for credentials (HTTP 407: ask whoever runs the
network to let the source through, or retry on another network) or refusing to connect (retry,
or ask whoever runs the network to allow the source); a source that cannot answer right now
(HTTP 408, 429 or 5xx, retry in a few minutes,
or when its `Retry-After` says); a certificate that could not be verified (fix the clock, or
trust the certificate authority a proxy on the network uses; the check is never switched off);
and a secure connection cut off before any certificate was checked (retry, or ask whoever runs
the network). Through an HTTPS proxy a 407 is not an HTTP status: `http.client` raises it as an
`OSError` from the refused CONNECT, and the sentence is read from that. Any other HTTP status is
`bad-status`.

`scripts/fetch_bundled_model.py` is the same call without a browser — for an offline image, a
fleet, or a test rig. It refuses to run without `PERSONALCLAW_HOME`.

## How it binds with nothing configured

`provider.py` registers its provider type at import, like every model app. Once the weight is
actually on disk it *also* registers an in-memory `ProviderEntry` flagged `floor=True`:

- nothing is written to `config.json`, so there is no provider row to strand if the app is
  removed and no credential anywhere;
- `floor=True` makes the resolver sort this entry **last**, so any provider the user binds
  wins;
- with no weight downloaded, no entry is registered at all — `chat` stays unresolved and the
  dashboard shows the calm "set up a model" state rather than failing a turn;
- the type also registers a **readiness probe** (`_readiness`), which core asks before it
  chooses or builds any entry of this type. It is the one answer onboarding's `needs_model`,
  its model check, the degraded chip and the chat model list all read, so a `config.json` row
  of this type with no weight behind it reads "not downloaded yet" everywhere instead of
  "you're ready";
- the entry is bindable as `bundled-chat:<model>`, and appears in `GET /api/models/chat`.
  Onboarding binds it as the chat model when you download it there with nothing else bound;
- `refresh_registration()` re-evaluates that after a download or a delete, so neither needs a
  restart. A download that finished an hour after startup binds immediately.

The app also implements core's `LocalModelProvider` contract on the same object, which is what
buys the progress bar, the cancel button and the per-row failure from the generic surface —
**and adds no route carrying this app's name.** That is the only version of this feature core
stays provider-agnostic under.

## Why the runtime is numpy

Measured on PyPI: `llama-cpp-python` publishes an **sdist only**, so `pip install` compiles
llama.cpp with cmake and a C++ toolchain; `gpt4all` ships no Linux-aarch64 wheel;
`onnxruntime-genai` needs an ORT-GenAI model directory no permissive sub-1B model publishes.
A zero-config promise that requires a compiler is not one. `numpy` is already a core
dependency (declared for the in-wheel `native-vector-memory` app), so this app adds **no**
new dependency, and `provider.py` implements exactly the arithmetic the model needs:
a GGUF reader, Q8_0 dequantization, byte-level BPE, RMSNorm, adjacent-pair RoPE,
grouped-query attention, SwiGLU and a tied output projection.

## Settings

Editable in Settings → Providers → Bundled offline model (a native app is locked on, but its
settings are editable). Every one takes effect when saved — the readiness probe and the
provider factory read the saved settings on each use, not once at startup:

- **Answer when nothing else is bound** (`offer_as_fallback`, default on) — the off switch.
  Off, the implicit fallback passes this model by, so with nothing bound chat goes back to the
  calm setup state. A chat binding to it still answers: choosing the model is the user saying
  it should.
- **Maximum reply length**, **Prompt budget**, **Temperature**, **Top-p**, **Repetition
  penalty**.

## Honesty

A 135M model is not a good assistant. It greets, answers simple factual questions, and gets
shaky fast; it has no tools. The provider declares `FLOOR_NOTICE`, `GET /api/onboarding`
reports `chat_is_bundled_floor`, and the chat surface says so at the point of use — because a
user who meets a tiny model with no warning concludes the product is bad rather than that the
model is small.

The same rule applies to the download. Onboarding's model lane says *"using the small model
PersonalClaw downloaded"*, not *"you're ready"*, and no surface claims an offline-capable FIRST
chat, because that claim stopped being true when the weight left the wheel. What is claimed is
the thing that is true: one download, then offline forever.
