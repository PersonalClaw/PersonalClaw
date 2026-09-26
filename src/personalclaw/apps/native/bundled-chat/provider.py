"""The bundled zero-config chat floor — a GGUF weight executed in-process on numpy.

**What this app is for (OU-14).** A fresh PersonalClaw install has no provider bound, no
API key and, for most people, no Ollama running. Until now that meant the first chat turn
raised ``ERR_MODEL_UNRESOLVED`` and the dashboard showed the calm ``NoModelSetupState``
(OU-12) — legible, honest, and still a wall. This app removes the wall: a very small,
Apache-2.0 instruct model ships with the wheel and answers that first turn offline, with no
credential and no network.

**Why the runtime is numpy and not llama.cpp.** The feasibility study
(``research/bundle-default-model-feasibility.md``) recommended ``llama-cpp-python`` and left
"which wheel strategy" as an engineering sub-call. Measured on PyPI: ``llama-cpp-python``
0.3.35 publishes an **sdist only** — ``pip install`` compiles llama.cpp with cmake and a C++
toolchain. Making that a dependency of every install would replace "you have no model" with
"you have no compiler", which is a worse first run than the one this app exists to fix.
``gpt4all`` (the named fallback) ships no Linux-aarch64 wheel; ``onnxruntime-genai`` ships
wheels everywhere but needs an ORT-GenAI model directory that no permissive sub-1B model
publishes. So the executor here is **numpy**, which is already a core dependency (declared
in ``pyproject.toml`` as the substrate for the in-wheel ``native-vector-memory`` app), needs
no compiler, and works on every platform CPython does. The bundle therefore adds **zero**
new dependencies.

**Why this is not a provider-boundary exception.** ``docs/architecture/provider-boundary.md``
keeps vendor integrations out of core, and every line of this file is in an app bundle that
imports core only through ``personalclaw.sdk.*``. There is also no vendor here to integrate:
a GGUF reader is a *file-format* reader and a Llama-architecture forward pass is arithmetic —
no endpoint, no auth, no catalog, no wire quirk. Core gains no new exception, no new
dependency and no vendor string from this app.

**How it binds with nothing configured.** The app registers its provider TYPE at import
(like every model app) and, when a weight is actually present, ALSO registers an in-memory
``ProviderEntry`` flagged ``floor=True``. Nothing is written to ``config.json``: the entry
lives and dies with the app, so there is no orphan provider row to strand if the bundle is
removed, and no credential is involved anywhere. ``floor=True`` is what makes the resolver
sort this entry LAST, so the moment a user binds any real provider the floor stops being
chosen — the model axis' version of the search registry's ``keyless`` floor. The entry is
also BINDABLE (``bundled-chat:<model>``), which is how onboarding makes it the chat model when
a user downloads it there.

**Whether it can serve is the type's own answer, asked live.** The type registers a readiness
probe (:func:`_readiness`) beside its factory, and core consults it before choosing or
building any entry of this type: no weight on disk means "not downloaded yet", and — for the
implicit nothing-is-bound path only — ``offer_as_fallback: false`` means "not offered". A build
alone could not say either: this provider constructs perfectly well with no weight and fails on
its first turn, which is how a home with no model used to be told it was ready. Because the
probe reads the disk and the app's settings on every call, the switch takes effect the moment
it is saved rather than at the next restart.

**Honesty is part of the contract.** A 135M model is not a good assistant; it is a working
first turn. Shipping it silently would let a user conclude the *product* is poor. So the
provider declares :data:`FLOOR_NOTICE`, ``GET /api/onboarding`` reports
``chat_is_bundled_floor``, and the chat surface says plainly what is answering and how to
replace it. If the weight is absent (a source checkout that never ran the fetch step), the
app registers no entry and :func:`availability` says why — the honest degrade is back to
OU-12's calm setup state, never a broken turn.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import shutil
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from personalclaw.sdk.local_model import (
    DOWNLOAD_BAD_STATUS,
    DOWNLOAD_TRUNCATED,
    DOWNLOAD_UNREACHABLE,
    BundleDeclaration,
    BundleDeclarationError,
    LocalModel,
    LocalModelProvider,
    admit_transfer,
    licence_decision,
    parse_declaration,
    verify_download,
)
from personalclaw.sdk.model import (
    EVENT_COMPLETE,
    EVENT_TEXT_CHUNK,
    Capability,
    LLMEvent,
    ModelProvider,
    PromptCache,
    PromptExceedsWindow,
    ProviderCapability,
    ProviderEntry,
    ProviderResolutionError,
    StructuredOutput,
    get_default_registry,
    per_call_temperature,
)
from personalclaw.sdk.prompt import USER_REQUEST_MARKER
from personalclaw.sdk.settings import ProviderSettings
from personalclaw.sdk.util import config_dir, single_flight

logger = logging.getLogger(__name__)

#: This app's name — the key its settings are stored under, and the provider ENTRY name the
#: resolver reports. Kept as one constant so the manifest, the settings read and the
#: user-visible provider label cannot drift apart.
APP_NAME = "bundled-chat"

#: The provider TYPE this app registers with the LLM registry. Deliberately not a vendor
#: name: what is being described is "the weight that ships in this wheel", and which weight
#: that is may change without every binding in the world changing with it.
PROVIDER_TYPE = "bundled-chat"

#: The one sentence every surface that mentions this provider must be able to say. It exists
#: as a constant because the honest label is a product requirement, not a UI decoration: a
#: user who meets a 135M model with no warning concludes PersonalClaw is bad at chat.
FLOOR_NOTICE = (
    "This is the small offline model bundled with PersonalClaw. It runs on your machine "
    "with no account and no API key, which is why it can answer at all before you have set "
    "anything up — but it is tiny (135M parameters), so expect short, shaky answers and no "
    "tool use. Bind a real model in Settings → Models and this stops being used."
)

#: Sampling + budget defaults. Small on purpose: a CPU-only 135M model generates at roughly
#: 5–50 tokens/second depending on memory bandwidth, so an unbounded reply is a minutes-long
#: wait. ``temperature=0`` (greedy) is the most coherent setting at this size; the repetition
#: penalty is what stops greedy decoding from falling into the "a soothing melody, a soothing
#: melody" loop small models are prone to.
DEFAULT_MAX_OUTPUT_TOKENS = 320
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TOP_P = 0.95
DEFAULT_REPEAT_PENALTY = 1.12
DEFAULT_REPEAT_WINDOW = 96
#: The window this model is run with — the "Prompt budget" setting. The prompt AND the reply
#: must fit in it together, so the prompt's own room is this minus the reply reserve: 3,776
#: tokens at the defaults. The weight takes 8,192, but prefill time grows with the prompt, so the
#: default is half that; the setting's schema bounds it by the weight's own limit.
#:
#: 🔴 4096 AND NOT 2048. At 2048 the reply reserve left 1,728 tokens of room; while the turn was
#: still assembled a full context for this model (~1,640 tokens on a fresh home), a FIRST message
#: past ~370 characters was refused outright. The model is now handed the request alone
#: (``request_only``), so nearly all of the room is the message's — and 2048 would still halve
#: what a user can send.
#:
#: 🔴 AND IT IS A HARD BOUND, enforced by :func:`_chatml`. Before it was one, the newest message
#: was kept whole whatever its size: a 40,000-character paste became a ~9,000-token prefill and
#: OOM-killed a 6 GB-capped gateway 7.1 s after send, with nothing in its log.
DEFAULT_CONTEXT_TOKENS = 4096

#: How much system prompt this model can be given before it starts CONTINUING the instructions
#: instead of following them. See :func:`_chatml` for the measured failure this cap exists for.
DEFAULT_SYSTEM_BUDGET_TOKENS = 96

#: What replaces an over-budget system prompt. Short, and it says the two things this model can
#: actually act on: answer, and be brief. It does not pretend to tools it does not have.
FLOOR_SYSTEM_PROMPT = (
    "You are a helpful assistant. Answer the user's question directly and briefly."
)

#: ggml tensor type ids this executor reads. Q8_0 is the shipped quantization (32 values per
#: block, one fp16 scale, exact to dequantize); F32/F16 cover the norm tensors and an
#: unquantized weight. A k-quant (Q4_K/Q6_K) superblock format is deliberately NOT supported:
#: each is ~60 lines of bit-twiddling whose failure mode is silently wrong arithmetic, and the
#: size saving does not change which side of PyPI's per-file limit the artifact lands on.
_GGML_F32 = 0
_GGML_F16 = 1
_GGML_Q8_0 = 8
_Q8_0_BLOCK = 32
_Q8_0_BLOCK_BYTES = 34  # fp16 scale + 32 int8 quants

# GGUF metadata value types (spec v3).
(
    _T_U8,
    _T_I8,
    _T_U16,
    _T_I16,
    _T_U32,
    _T_I32,
    _T_F32,
    _T_BOOL,
    _T_STR,
    _T_ARRAY,
    _T_U64,
    _T_I64,
    _T_F64,
) = range(13)

_SCALAR_FORMATS: dict[int, tuple[str, int, Any]] = {
    _T_U8: ("<B", 1, np.uint8),
    _T_I8: ("<b", 1, np.int8),
    _T_U16: ("<H", 2, np.uint16),
    _T_I16: ("<h", 2, np.int16),
    _T_U32: ("<I", 4, np.uint32),
    _T_I32: ("<i", 4, np.int32),
    _T_F32: ("<f", 4, np.float32),
    _T_BOOL: ("<?", 1, np.bool_),
    _T_U64: ("<Q", 8, np.uint64),
    _T_I64: ("<q", 8, np.int64),
    _T_F64: ("<d", 8, np.float64),
}

#: GGUF marks added/control tokens with token_type 3. They are excluded from the ENCODER's
#: vocabulary so user text containing the literal ``<|im_start|>`` cannot become the real
#: control token and forge a chat-template role. The merge table cannot produce them either
#: (special tokens are added, never learned), so this is belt-and-braces — but a prompt
#: boundary that holds only by accident is not a boundary.
_TOKEN_TYPE_CONTROL = 3


class BundleUnavailable(RuntimeError):
    """No usable bundled weight is installed.

    Its own type because "the bundle is absent" must stay distinguishable from "the bundle is
    present and inference failed": the first is a normal state of a source checkout and is
    reported through :func:`availability`, the second is a defect.
    """


# ── GGUF reading ──────────────────────────────────────────────────────────────────────────


class _Cursor:
    """A byte cursor over a GGUF header. Stdlib ``struct`` only — no parsing library."""

    def __init__(self, blob: bytes) -> None:
        self._blob = blob
        self.offset = 0

    def take(self, count: int) -> bytes:
        end = self.offset + count
        if end > len(self._blob):
            raise ValueError("GGUF header is truncated")
        chunk = self._blob[self.offset : end]
        self.offset = end
        return chunk

    def scalar(self, type_id: int) -> Any:
        try:
            fmt, width, _ = _SCALAR_FORMATS[type_id]
        except KeyError:
            raise ValueError(f"unknown GGUF scalar type {type_id}") from None
        value = struct.unpack_from(fmt, self._blob, self.offset)[0]
        self.offset += width
        return value

    def string(self) -> str:
        length = int(self.scalar(_T_U64))
        return self.take(length).decode("utf-8", "replace")

    def value(self, type_id: int) -> Any:
        if type_id == _T_STR:
            return self.string()
        if type_id == _T_ARRAY:
            element = int(self.scalar(_T_U32))
            count = int(self.scalar(_T_U64))
            if element == _T_STR:
                return [self.string() for _ in range(count)]
            if element in _SCALAR_FORMATS:
                _fmt, width, dtype = _SCALAR_FORMATS[element]
                return np.frombuffer(self.take(count * width), dtype=dtype)
            return [self.value(element) for _ in range(count)]
        return self.scalar(type_id)


def dequantize(raw: bytes, ggml_type: int, count: int) -> np.ndarray:
    """Return *count* float32 values decoded from *raw* in ggml type *ggml_type*.

    Q8_0's layout is a block of 32 int8 quants preceded by one fp16 scale, so the whole
    tensor decodes as two vectorized reads and one broadcast multiply — no Python loop over
    blocks, which is what keeps a 138 MiB weight's load under a second.
    """
    if ggml_type == _GGML_F32:
        return np.frombuffer(raw, dtype=np.float32, count=count).astype(np.float32, copy=True)
    if ggml_type == _GGML_F16:
        return np.frombuffer(raw, dtype=np.float16, count=count).astype(np.float32)
    if ggml_type == _GGML_Q8_0:
        if count % _Q8_0_BLOCK:
            raise ValueError(f"Q8_0 tensor length {count} is not a multiple of {_Q8_0_BLOCK}")
        blocks = count // _Q8_0_BLOCK
        flat = np.frombuffer(raw, dtype=np.uint8, count=blocks * _Q8_0_BLOCK_BYTES)
        # One row per block, so the scale column and the quant columns come out as slices.
        # ``.copy()`` before ``.view()`` because both slices are strided and a view on a
        # strided buffer is not reinterpretable.
        rows = flat.reshape(blocks, _Q8_0_BLOCK_BYTES)
        scales = rows[:, :2].copy().view(np.float16).astype(np.float32)
        quants = rows[:, 2:].copy().view(np.int8).astype(np.float32)
        return (quants * scales).reshape(-1)
    raise ValueError(
        f"ggml tensor type {ggml_type} is not supported by the bundled executor "
        f"(supported: F32={_GGML_F32}, F16={_GGML_F16}, Q8_0={_GGML_Q8_0})"
    )


def _tensor_bytes(ggml_type: int, count: int) -> int:
    if ggml_type == _GGML_F32:
        return count * 4
    if ggml_type == _GGML_F16:
        return count * 2
    if ggml_type == _GGML_Q8_0:
        return count // _Q8_0_BLOCK * _Q8_0_BLOCK_BYTES
    raise ValueError(f"ggml tensor type {ggml_type} is not supported")


class GgufModel:
    """A parsed GGUF file: its metadata, its tensor index, and a reader for each tensor.

    The whole file is read into memory once and released as soon as the tensors have been
    dequantized (:meth:`release_source`). Holding both the packed bytes and the float32
    weights would cost 138 MiB for nothing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._blob: bytes | None = path.read_bytes()
        cursor = _Cursor(self._blob)
        if cursor.take(4) != b"GGUF":
            raise ValueError(f"{path} is not a GGUF file (bad magic)")
        self.version = int(cursor.scalar(_T_U32))
        tensor_count = int(cursor.scalar(_T_U64))
        kv_count = int(cursor.scalar(_T_U64))
        self.meta: dict[str, Any] = {}
        for _ in range(kv_count):
            key = cursor.string()
            self.meta[key] = cursor.value(int(cursor.scalar(_T_U32)))
        self.index: dict[str, tuple[list[int], int, int]] = {}
        for _ in range(tensor_count):
            name = cursor.string()
            dims = int(cursor.scalar(_T_U32))
            shape = [int(cursor.scalar(_T_U64)) for _ in range(dims)]
            self.index[name] = (shape, int(cursor.scalar(_T_U32)), int(cursor.scalar(_T_U64)))
        alignment = int(self.meta.get("general.alignment", 32)) or 32
        self.data_offset = (cursor.offset + alignment - 1) // alignment * alignment

    def tensor(self, name: str) -> np.ndarray:
        """Dequantize one tensor to float32, shaped the way numpy wants it.

        GGUF stores dimensions innermost-first (``ne``), so a linear layer indexed
        ``[in, out]`` in the file is ``(out, in)`` in numpy — which is exactly the layout
        ``x @ W.T`` wants.
        """
        if self._blob is None:
            raise RuntimeError("the GGUF source bytes were already released")
        shape, ggml_type, offset = self.index[name]
        count = math.prod(shape) if shape else 0
        start = self.data_offset + offset
        raw = self._blob[start : start + _tensor_bytes(ggml_type, count)]
        return dequantize(raw, ggml_type, count).reshape(tuple(reversed(shape)))

    def release_source(self) -> None:
        """Drop the packed file bytes. Called once every tensor has been dequantized."""
        self._blob = None


# ── tokenizer ─────────────────────────────────────────────────────────────────────────────


def byte_to_unicode() -> dict[int, str]:
    """The GPT-2 byte↔printable-codepoint map every byte-level BPE vocabulary is written in.

    Reproduced rather than imported: it is fifteen lines of arithmetic with no dependency,
    and the alternative is a tokenizer library in core's dependency set.
    """
    printable = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    mapped = list(printable)
    spare = 0
    for byte in range(256):
        if byte not in printable:
            printable.append(byte)
            mapped.append(256 + spare)
            spare += 1
    return {b: chr(c) for b, c in zip(printable, mapped)}


#: Pre-tokenizer. The GPT-2 pattern, spelled with Python's own ``re`` classes because
#: ``\p{L}``/``\p{N}`` need the third-party ``regex`` module and this app adds no
#: dependencies. ``[^\W\d_]`` is "a word character that is neither a digit nor an
#: underscore", i.e. a letter — unicode-aware, since ``re`` on ``str`` is unicode by default.
_PRETOKEN = re.compile(
    r"'(?:[sStTmMdD]|[rR][eE]|[vV][eE]|[lL][lL])"
    r"| ?[^\W\d_]+"
    r"| ?\d+"
    r"| ?[^\s\w]+"
    r"| ?_+"
    r"|\s+(?!\S)"
    r"|\s+"
)


class ByteBpeTokenizer:
    """Byte-level BPE over the vocabulary and merge table embedded in the GGUF file.

    Encoding excludes control tokens (see :data:`_TOKEN_TYPE_CONTROL`) so no user text can
    forge a chat-template role marker; decoding maps every codepoint back to its byte, so a
    reply that splits a UTF-8 sequence across tokens still reassembles.
    """

    def __init__(
        self,
        tokens: Sequence[str],
        merges: Sequence[str],
        token_types: Sequence[int] | None = None,
    ) -> None:
        self.tokens = list(tokens)
        self._ranks = {tuple(m.split(" ")): i for i, m in enumerate(merges)}
        types = list(token_types or [])
        self._encodable: dict[str, int] = {}
        for index, token in enumerate(self.tokens):
            if index < len(types) and int(types[index]) == _TOKEN_TYPE_CONTROL:
                continue
            self._encodable.setdefault(token, index)
        self._byte_to_unicode = byte_to_unicode()
        self._unicode_to_byte = {v: k for k, v in self._byte_to_unicode.items()}
        self._cache: dict[str, list[int]] = {}

    def _merge(self, word: str) -> list[str]:
        parts = list(word)
        while len(parts) > 1:
            best_rank: int | None = None
            best_at = -1
            for i in range(len(parts) - 1):
                rank = self._ranks.get((parts[i], parts[i + 1]))
                if rank is not None and (best_rank is None or rank < best_rank):
                    best_rank, best_at = rank, i
            if best_at < 0:
                break
            parts[best_at : best_at + 2] = [parts[best_at] + parts[best_at + 1]]
        return parts

    def encode(self, text: str) -> list[int]:
        out: list[int] = []
        for chunk in _PRETOKEN.findall(text):
            mapped = "".join(self._byte_to_unicode[b] for b in chunk.encode("utf-8"))
            cached = self._cache.get(mapped)
            if cached is None:
                cached = [
                    self._encodable[piece]
                    for piece in self._merge(mapped)
                    if piece in self._encodable
                ]
                self._cache[mapped] = cached
            out.extend(cached)
        return out

    def decode(self, ids: Iterable[int]) -> str:
        buffer = bytearray()
        for token_id in ids:
            if 0 <= token_id < len(self.tokens):
                for char in self.tokens[token_id]:
                    byte = self._unicode_to_byte.get(char)
                    if byte is not None:
                        buffer.append(byte)
        return buffer.decode("utf-8", "replace")


# ── the executor ──────────────────────────────────────────────────────────────────────────

#: The most memory one block of attention scores may occupy, in bytes (see
#: :meth:`LlamaCpuModel._attend`). At 64 MiB a 4,096-token prefill of the shipped weight (9
#: heads) runs ~455 query rows per block; the peak is set by this number, not by the prompt.
_ATTENTION_BLOCK_BYTES = 64 * 1024 * 1024

#: How a generation ended, in the words providers already use for it (Anthropic/Bedrock
#: ``stop_reason``). Core reads the cap spelling to tell the user a reply was cut mid-sentence.
_STOP_END_TURN = "end_turn"
_STOP_MAX_TOKENS = "max_tokens"


class GenerationReport:
    """What one :meth:`LlamaCpuModel.generate` call did — filled in as it runs.

    A per-call object rather than state on the model, because the model is ONE process-wide
    instance shared by every open chat, and two turns generating at once must not read each
    other's counts.
    """

    __slots__ = ("output_tokens", "stop_reason")

    def __init__(self) -> None:
        self.output_tokens = 0
        self.stop_reason = _STOP_MAX_TOKENS


class LlamaCpuModel:
    """A Llama-architecture forward pass on numpy, with a per-call KV cache.

    Not a general inference engine: it implements exactly the arithmetic the bundled weight
    needs — RMSNorm, adjacent-pair RoPE (llama.cpp's ``NORM`` rotation, which is the
    convention GGUF conversion pre-permutes Q/K for), grouped-query attention, SwiGLU, and a
    tied output projection. An architecture it does not recognise is refused at load rather
    than run with the wrong maths.
    """

    def __init__(self, gguf: GgufModel) -> None:
        meta = gguf.meta
        architecture = str(meta.get("general.architecture", ""))
        if architecture != "llama":
            raise ValueError(
                f"the bundled executor implements the 'llama' architecture; this weight "
                f"declares {architecture!r}. A weight with a different block shape must not "
                "be run through it — wrong arithmetic produces fluent nonsense, not an error."
            )
        self.model_name = str(meta.get("general.name", "") or gguf.path.stem)
        self.n_layer = int(meta["llama.block_count"])
        self.d_model = int(meta["llama.embedding_length"])
        self.n_head = int(meta["llama.attention.head_count"])
        self.n_head_kv = int(meta["llama.attention.head_count_kv"])
        self.head_dim = self.d_model // self.n_head
        self.max_context = int(meta.get("llama.context_length", 2048))
        self.rms_eps = float(meta["llama.attention.layer_norm_rms_epsilon"])
        rope_base = float(meta.get("llama.rope.freq_base", 10000.0))
        if self.n_head % self.n_head_kv:
            raise ValueError(
                f"{self.n_head} query heads do not group evenly into {self.n_head_kv} kv heads"
            )

        self.bos_id = int(meta.get("tokenizer.ggml.bos_token_id", 1))
        self.eos_id = int(meta.get("tokenizer.ggml.eos_token_id", 2))
        self.pad_id = int(meta.get("tokenizer.ggml.padding_token_id", 0))
        self.tokenizer = ByteBpeTokenizer(
            list(meta.get("tokenizer.ggml.tokens", [])),
            list(meta.get("tokenizer.ggml.merges", [])),
            list(meta.get("tokenizer.ggml.token_type", [])),
        )

        self.embedding = gguf.tensor("token_embd.weight")
        self.output_norm = gguf.tensor("output_norm.weight")
        # No ``output.weight`` means tied embeddings — the same matrix is the unembedding.
        self.output_weight = (
            gguf.tensor("output.weight") if "output.weight" in gguf.index else self.embedding
        )
        self.blocks: list[dict[str, np.ndarray]] = []
        for layer in range(self.n_layer):
            prefix = f"blk.{layer}."
            self.blocks.append(
                {
                    key: gguf.tensor(f"{prefix}{key}.weight")
                    for key in (
                        "attn_norm",
                        "attn_q",
                        "attn_k",
                        "attn_v",
                        "attn_output",
                        "ffn_norm",
                        "ffn_gate",
                        "ffn_up",
                        "ffn_down",
                    )
                }
            )
        gguf.release_source()
        half = self.head_dim // 2
        self.inv_freq = (rope_base ** (-np.arange(half, dtype=np.float64) / half)).astype(
            np.float32
        )

    # ── layers ──

    def _rms_norm(self, x: np.ndarray, weight: np.ndarray) -> np.ndarray:
        scale = 1.0 / np.sqrt((x * x).mean(-1, keepdims=True) + self.rms_eps)
        return x * scale * weight

    def _rope(self, x: np.ndarray, positions: np.ndarray) -> np.ndarray:
        """Rotate ``(T, heads, head_dim)`` in ADJACENT pairs — llama.cpp's ``NORM`` mode.

        Not the half-split rotation HuggingFace uses: ``convert_hf_to_gguf.py`` permutes Q
        and K at conversion time so that the interleaved rotation reproduces it. Using the
        half-split convention here would silently produce coherent-looking garbage.
        """
        theta = positions[:, None] * self.inv_freq[None, :]
        cos = np.cos(theta)[:, None, :]
        sin = np.sin(theta)[:, None, :]
        pairs = x.reshape(x.shape[0], x.shape[1], -1, 2)
        even, odd = pairs[..., 0], pairs[..., 1]
        rotated = np.stack([even * cos - odd * sin, even * sin + odd * cos], -1)
        return rotated.reshape(x.shape)

    def _attend(self, q: np.ndarray, k_t: np.ndarray, v: np.ndarray) -> np.ndarray:
        """Causal softmax attention over the whole cache, computed in QUERY BLOCKS.

        ``q`` is ``(heads, span, head_dim)``, ``k_t`` is ``(heads, head_dim, total)`` and ``v``
        is ``(heads, total, head_dim)``; the result is ``(heads, span, head_dim)``.

        🔴 Why blocks. The score matrix for a prefill is ``heads × span × total`` float32, and
        computing it in one expression materialised it three or four times over (the scores,
        the masked copy, the shifted copy, the exponentials). Measured on the shipped weight
        before this: a 4,096-token prefill peaked at 3,687 MB RSS against 904 MB loaded, and
        8,192 tokens at 10.73 GB — the whole "an ordinary paste OOM-kills an 8 GB laptop"
        failure. Each block here is at most :data:`_ATTENTION_BLOCK_BYTES` of scores, and the
        softmax runs IN PLACE on it, so the peak no longer depends on the prompt length at all.
        Measured after, same weight and host: numpy's allocation peak for a 4,096-token prefill
        fell from 2,641 MB to 427 MB (RSS 3,222 → 1,301 MB), and at the weight's 8,192 limit
        from 9,922 MB to 738 MB (RSS 9,968 → 1,789 MB).

        Each query row's softmax is independent of every other row's, so blocking changes no
        arithmetic: the operations per row are the ones the one-shot expression performed, in
        the same order (``tests/test_bundled_chat_provider.py`` pins it against an independent
        reference at one row per block, a few rows, and one block for everything).

        It is also LESS work, not more. A block's queries sit at positions ``offset + start ..
        offset + stop - 1``, so no key at or past ``offset + stop`` is visible to any of them:
        the upper triangle a causal prefill used to compute and then overwrite with ``-inf`` is
        never computed, and only the block's own square of keys needs a mask at all.
        """
        heads, span, _ = q.shape
        total = k_t.shape[2]
        offset = total - span  # the absolute position of query row 0
        rows = max(1, _ATTENTION_BLOCK_BYTES // max(1, heads * total * 4))
        scale = math.sqrt(self.head_dim)
        out = np.empty((heads, span, v.shape[2]), dtype=np.float32)
        for start in range(0, span, rows):
            stop = min(span, start + rows)
            visible = offset + stop  # keys past this are in every query's future
            scores = q[:, start:stop] @ k_t[:, :, :visible]
            scores /= scale
            if stop - start > 1:
                # Causal mask inside the block's own square of keys — the only keys that can be
                # in the future of some query here. A single decode step needs none: it attends
                # to everything in the cache by construction.
                first = offset + start
                future = np.triu(np.ones((stop - start, stop - start), dtype=bool), 1)
                np.copyto(scores[:, :, first:visible], -np.inf, where=future[None])
            scores -= scores.max(-1, keepdims=True)
            np.exp(scores, out=scores)
            scores /= scores.sum(-1, keepdims=True)
            out[:, start:stop] = scores @ v[:, :visible]
        return out

    def forward(self, ids: Sequence[int], cache: "_KvCache") -> np.ndarray:
        """Run *ids* through every block and return the logits for the LAST position."""
        x = self.embedding[list(ids)].astype(np.float32)
        span = len(ids)
        positions = np.arange(cache.length, cache.length + span, dtype=np.float32)
        repeat = self.n_head // self.n_head_kv
        for layer, block in enumerate(self.blocks):
            h = self._rms_norm(x, block["attn_norm"])
            q = (h @ block["attn_q"].T).reshape(span, self.n_head, self.head_dim)
            k = (h @ block["attn_k"].T).reshape(span, self.n_head_kv, self.head_dim)
            v = (h @ block["attn_v"].T).reshape(span, self.n_head_kv, self.head_dim)
            keys, values = cache.append(layer, self._rope(k, positions), v)
            k_all = np.ascontiguousarray(np.repeat(keys, repeat, axis=1).transpose(1, 2, 0))
            v_all = np.ascontiguousarray(np.repeat(values, repeat, axis=1).transpose(1, 0, 2))
            q_all = np.ascontiguousarray(self._rope(q, positions).transpose(1, 0, 2))
            attended = (
                self._attend(q_all, k_all, v_all).transpose(1, 0, 2).reshape(span, self.d_model)
            )
            x = x + attended @ block["attn_output"].T
            h = self._rms_norm(x, block["ffn_norm"])
            gate = h @ block["ffn_gate"].T
            up = h @ block["ffn_up"].T
            # SwiGLU, in place: ``gate / (1 + exp(-gate)) * up`` with one temporary instead of
            # four — at a 4,096-token prefill each of these is a 25 MB array, and the prefill
            # peak is what decides whether a laptop survives the turn.
            denominator = np.negative(gate)
            np.exp(denominator, out=denominator)
            denominator += 1.0
            gate /= denominator
            del denominator
            gate *= up
            x = x + gate @ block["ffn_down"].T
        cache.length += span
        logits: np.ndarray = self._rms_norm(x[-1], self.output_norm) @ self.output_weight.T
        return logits

    # ── generation ──

    def generate(
        self,
        prompt_ids: Sequence[int],
        *,
        max_new_tokens: int,
        temperature: float,
        top_p: float,
        repeat_penalty: float,
        repeat_window: int,
        stop_ids: Sequence[int],
        should_stop: "threading.Event | None" = None,
        report: GenerationReport | None = None,
    ) -> Iterable[str]:
        """Yield decoded text as it is produced. Synchronous — run it off the event loop.

        ``report`` is filled in as the reply is produced: how many tokens it took and why it
        stopped — on the model's own stop token (``end_turn``) or on ``max_new_tokens``
        (``max_tokens``), which is a reply cut mid-sentence that the user has to be told about.
        """
        report = report if report is not None else GenerationReport()
        cache = _KvCache(self.n_layer, self.n_head_kv, self.head_dim)
        logits = self.forward(prompt_ids, cache)
        produced: list[int] = []
        rng = np.random.default_rng()
        emitted = 0
        for _ in range(max_new_tokens):
            if should_stop is not None and should_stop.is_set():
                report.stop_reason = _STOP_END_TURN
                break
            history = list(prompt_ids)[-repeat_window:] + produced[-repeat_window:]
            token = _pick_token(
                logits,
                temperature=temperature,
                top_p=top_p,
                repeat_penalty=repeat_penalty,
                history=history,
                rng=rng,
            )
            if token in stop_ids:
                report.stop_reason = _STOP_END_TURN
                break
            produced.append(token)
            report.output_tokens = len(produced)
            # Decode the whole run each step and emit only the delta, so a multi-token UTF-8
            # character is never sliced into two replacement glyphs.
            whole = self.tokenizer.decode(produced)
            if len(whole) > emitted:
                yield whole[emitted:]
                emitted = len(whole)
            logits = self.forward([token], cache)


class _KvCache:
    """Per-generation key/value cache. One instance per turn — never shared across turns."""

    def __init__(self, n_layer: int, n_head_kv: int, head_dim: int) -> None:
        self.length = 0
        self._k = [np.zeros((0, n_head_kv, head_dim), np.float32) for _ in range(n_layer)]
        self._v = [np.zeros((0, n_head_kv, head_dim), np.float32) for _ in range(n_layer)]

    def append(
        self, layer: int, keys: np.ndarray, values: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        self._k[layer] = np.concatenate([self._k[layer], keys])
        self._v[layer] = np.concatenate([self._v[layer], values])
        return self._k[layer], self._v[layer]


def _pick_token(
    logits: np.ndarray,
    *,
    temperature: float,
    top_p: float,
    repeat_penalty: float,
    history: Sequence[int],
    rng: np.random.Generator,
) -> int:
    """Choose the next token id — greedy at ``temperature<=0``, else nucleus sampling.

    The repetition penalty is applied first and to both branches: greedy decoding on a 135M
    model loops without it, and a loop reads to a user as the product being broken rather
    than the model being small.
    """
    scores = logits.astype(np.float32, copy=True)
    if repeat_penalty > 1.0 and len(history):
        seen = np.unique(np.asarray(history, dtype=np.int64))
        seen = seen[(seen >= 0) & (seen < scores.shape[0])]
        penalized = scores[seen]
        scores[seen] = np.where(
            penalized > 0, penalized / repeat_penalty, penalized * repeat_penalty
        )
    if temperature <= 0.0:
        return int(np.argmax(scores))
    scaled = scores / max(temperature, 1e-5)
    scaled -= scaled.max()
    probs = np.exp(scaled)
    probs /= probs.sum()
    order = np.argsort(probs)[::-1]
    cumulative = np.cumsum(probs[order])
    keep = int(np.searchsorted(cumulative, top_p) + 1)
    head = order[:keep]
    head_probs = probs[head] / probs[head].sum()
    return int(rng.choice(head, p=head_probs))


# ── where the weight lives, and how it gets there ──────────────────────────────────────────


def weights_dir() -> Path:
    """Where the downloaded weight lives: ``$PERSONALCLAW_HOME/models/bundled-chat/``.

    Under the HOME, not inside the installed package, and that is the whole shape of this app
    (owner decision 2026-09-24 — the wheel ships no weight and the model is fetched on first
    run). Three things follow from the location:

    * it survives a ``pip install --upgrade``, so the 138 MiB is downloaded once per machine
      rather than once per version;
    * it is inside the home a user already backs up, snapshots and points ``PERSONALCLAW_HOME``
      at, so nothing about it is special-cased in any of those;
    * ``$PERSONALCLAW_HOME/models/`` is core's own convention for downloaded weights
      (``local_models/layouts.cache_root_for``), which is what lets the shared download-job
      runner measure progress here without being told anything about this app.
    """
    return config_dir() / "models" / APP_NAME


def weight_path() -> Path:
    """The absolute path of the signed-off weight, present or not.

    The FILENAME comes from the sign-off record's ``artifact``, so the record, the downloader,
    the progress measurement and the executor cannot disagree about which file they mean. The
    DIRECTORY is always this app's own :func:`weights_dir`, and that split is deliberate on two
    counts:

    * **It cannot escape the home.** Joining the record's whole ``artifact`` onto the home would
      let a ``..`` segment write outside ``$PERSONALCLAW_HOME`` — and stripping a leading ``/``
      does not stop that. ``Path(...).name`` cannot traverse at all, which matters because this
      path is where a 138 MiB download lands.
    * **It keeps the home path statically visible.** A path assembled from a runtime value is
      invisible to the durability census (``tests/test_durability_inventory_census.py``), whose
      blind-spot count is shrink-only; building from ``weights_dir()`` keeps the literal
      ``models/<app>`` in the scan's view instead of adding a fourteenth unresolved identifier.
      (That scan is a regex over source TEXT, so the phrasing above is deliberate too: spelling
      the rejected form out literally would have the docstring itself counted as a home path.)

    ``test_the_record_and_this_app_agree_on_the_directory`` pins that the record's own directory
    component is that same directory, so the two cannot silently diverge. Falling back to a
    conventional name keeps this answerable in a tree with no record at all, which the tests
    drive.
    """
    declaration = _declaration()
    if declaration is not None:
        return weights_dir() / Path(declaration.artifact.strip()).name
    return weights_dir() / "model.gguf"


def installed_weight() -> Path | None:
    """The weight, if it is on disk and complete. ``None`` otherwise — never a partial file.

    "Complete" is a size check against the record, not merely "the file exists": a cancelled or
    truncated transfer must never be loaded, and a GGUF reader handed a short file fails deep
    inside a tensor read rather than at the door.
    """
    path = weight_path()
    if not path.is_file():
        return None
    declaration = _declaration()
    if declaration is not None and path.stat().st_size < declaration.size_bytes:
        logger.warning(
            "bundled-chat: %s is %d bytes, short of the signed-off %d — treating it as absent",
            path,
            path.stat().st_size,
            declaration.size_bytes,
        )
        return None
    return path


def _declaration() -> BundleDeclaration | None:
    """The sign-off record that ships beside this app — :data:`DECLARATION_PATH`.

    Cached: it is parsed on every status poll.
    """
    global _DECLARATION
    if _DECLARATION is _UNSET:
        _DECLARATION = _read_declaration()
    return _DECLARATION  # type: ignore[return-value]


def _read_declaration() -> BundleDeclaration | None:
    if not DECLARATION_PATH.is_file():
        logger.warning(
            "bundled-chat: no sign-off record at %s, so this install can neither offer nor "
            "fetch the default chat model — the package was built without it",
            DECLARATION_PATH,
        )
        return None
    try:
        return parse_declaration(DECLARATION_PATH.read_text(encoding="utf-8"))
    except BundleDeclarationError:
        logger.exception("bundled-chat: %s is not a readable sign-off record", DECLARATION_PATH)
        return None


def reset_declaration_cache() -> None:
    """Forget the parsed record. For tests, which write records into throwaway trees."""
    global _DECLARATION
    _DECLARATION = _UNSET


_UNSET = object()
_DECLARATION: object = _UNSET

#: The sign-off record: a real file beside this module, shipped by the
#: ``apps/native/*/bundled-model-signoff.txt`` package-data glob, and the ONE place every
#: install — checkout, wheel, container image, desktop bundle — reads it from. It is
#: deliberately not a link into ``docs/``: an install carries only the package, so a record
#: that resolved through anything outside it would exist in a checkout and nowhere else.
DECLARATION_PATH = Path(__file__).resolve().parent / "bundled-model-signoff.txt"


def offer() -> dict[str, Any] | None:
    """What a surface needs to offer the download, or ``None`` when there is nothing to offer.

    ``None`` covers both "already downloaded" and "no model is signed off" — neither is
    something to show a user a download button for. The size is in the payload because a
    download offer without a number is the thing this app must never be: 138 MiB on a slow
    connection is minutes, and a user agreeing to it is entitled to know that first.
    """
    declaration = _declaration()
    if declaration is None or installed_weight() is not None:
        return None
    return {
        "app": APP_NAME,
        "model": model_name(),
        "model_id": declaration.model_id,
        "licence": declaration.licence,
        "bytes": declaration.size_bytes,
    }


def model_name() -> str:
    """The name this model is known by on the download surface and in a binding ref."""
    declaration = _declaration()
    if declaration is None:
        return "bundled-chat-model"
    return Path(declaration.artifact).stem


def display_model_name() -> str:
    """What a person calls this model: ``SmolLM2-135M-Instruct``, not the file it lands as.

    Read off the sign-off record's ``model_id`` (``unsloth/SmolLM2-135M-Instruct-GGUF``) — the
    repository name without its owner and without the ``-GGUF`` packaging suffix a GGUF
    conversion's repository conventionally carries — so the name a surface shows moves with the
    record and is never a second copy of which model is signed off. :func:`model_name` stays the
    id the download, the delete and a binding use (``SmolLM2-135M-Instruct-Q8_0``).
    """
    declaration = _declaration()
    if declaration is None:
        return model_name()
    repo = declaration.model_id.strip().rsplit("/", 1)[-1]
    if repo.lower().endswith("-gguf"):
        repo = repo[: -len("-gguf")]
    return repo or model_name()


# ── the download ──────────────────────────────────────────────────────────────────────────

#: Read size. Big enough that a 138 MiB transfer is ~35 reads of real work per second rather
#: than a million tiny ones, small enough that a cancel lands within a fraction of a second.
_CHUNK_BYTES = 4 * 1024 * 1024

#: Per-read socket timeout AND total wall-clock deadline. BOTH, because they catch different
#: hangs: the socket timeout catches a connection that stops delivering, the deadline catches
#: one that delivers a byte at a time forever. A download with neither is the failure shape
#: this repo has spent real time removing — an in-flight request with no deadline whose
#: "loading" state is structurally permanent.
_SOCKET_TIMEOUT_S = 30.0
_DEADLINE_S = 45 * 60.0

#: Only https. A record pointing at ``http://`` or ``file://`` would turn a model fetch into
#: an unauthenticated read of whatever answered.
_ALLOWED_SCHEMES = ("https://",)


class DownloadFailed(RuntimeError):
    """A download that did not produce the signed-off bytes, with the reason a user can act on.

    Carries the machine-readable ``outcome`` (one of ``bundled_model.DOWNLOAD_*``) beside the
    sentence, because the four failures need four different things from the user and a single
    "download failed" string would flatten them.
    """

    def __init__(self, outcome: str, detail: str) -> None:
        super().__init__(detail)
        self.outcome = outcome


async def download_weight(*, progress: Any = None) -> Path:
    """Fetch the signed-off weight into the home, once. Returns its path.

    Every requirement of a large first-run download is here rather than in a caller:

    * **Bounded** — an https-only URL, a per-read socket timeout, a total deadline, and the
      record's size ceiling enforced WHILE the bytes arrive (the announced length before the
      first write, the running count after every read), not after the whole file has landed.
    * **Cancellable for real** — the transfer is a loop of ``await asyncio.to_thread(read)``,
      so cancelling the task lands between chunks (within one 4 MiB read) and the ``finally``
      unlinks the partial file. A download run as one blocking call could not be interrupted at
      all, which is the caveat core's own job runner documents about HuggingFace fetches.
    * **Never leaves a usable-looking partial** — bytes go to a sibling ``.partial`` file and
      are verified BEFORE an atomic replace. Nothing at the real path is ever unverified, so a
      later start cannot mistake a dead transfer for a finished one. (``.partial`` is also
      core's own partial suffix, so the on-disk prober already excuses the shortfall.)
    * **Fetched once** — a cross-process, cross-thread ``single_flight`` lock on the home. Two
      gateways started against one home do not both pull 138 MiB; the loser waits for the
      winner's file rather than racing it.
    * **Digest-verified** — against the record, by the same rail the release gate uses.

    ``progress`` is an optional callable taking ``(downloaded, total)``; it is called per chunk
    so a caller that wants byte counts has them without this function knowing about any UI.
    """
    declaration = _declaration()
    if declaration is None:
        raise DownloadFailed(
            DOWNLOAD_BAD_STATUS,
            "no default chat model is signed off in this install, so there is nothing to "
            "download. This is a packaging defect, not something you can fix here.",
        )
    if not declaration.source_url.startswith(_ALLOWED_SCHEMES):
        raise DownloadFailed(
            DOWNLOAD_BAD_STATUS,
            f"the signed-off source {declaration.source_url!r} is not https, so it will not be "
            "fetched.",
        )
    licence = licence_decision(declaration.licence)
    if not licence.permitted:
        raise DownloadFailed(DOWNLOAD_BAD_STATUS, licence.reason)

    target = weight_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with single_flight(f"{APP_NAME}:download:{declaration.sha256[:12]}") as acquired:
        if not acquired:
            # Another process is already fetching the SAME signed-off digest into the SAME
            # file. Waiting for its result is strictly better than a second 138 MiB transfer
            # racing it to the same path.
            return await _await_other_downloader(declaration)
        existing = installed_weight()
        if existing is not None:
            verdict = verify_download(existing, declaration)
            if verdict.ok:
                return existing
            logger.warning("bundled-chat: discarding %s — %s", existing, verdict.detail)
            existing.unlink(missing_ok=True)
        await _transfer(declaration, target, progress)
    _install_licence_texts(target.parent)
    return target


async def _transfer(declaration: BundleDeclaration, target: Path, progress: Any) -> None:
    """The bytes half of :func:`download_weight`: read, verify, atomically replace."""
    handle, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".partial")
    partial = Path(temp_name)
    # Take ownership of the descriptor IMMEDIATELY rather than at the write site. Every failure
    # below — a refused connection, a 404, a cancel — happens before the first byte, and a
    # descriptor only closed inside `with open(handle)` would leak one per attempt. A user
    # retrying a failing download is precisely who would run out of them.
    sink = open(handle, "wb")
    total = declaration.size_bytes
    received = 0
    deadline = time.monotonic() + _DEADLINE_S
    try:
        try:
            response = await asyncio.to_thread(
                urllib.request.urlopen,  # noqa: S310 — scheme pinned above
                declaration.source_url,
                None,
                _SOCKET_TIMEOUT_S,
            )
        except urllib.error.HTTPError as exc:
            raise DownloadFailed(
                DOWNLOAD_BAD_STATUS,
                f"the model source answered HTTP {exc.code} ({exc.reason}). The URL pins an "
                "immutable upstream revision, so this is not a transient error — the pin needs "
                "fixing. You can still use PersonalClaw: bind any provider in "
                "Settings → Models.",
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DownloadFailed(
                DOWNLOAD_UNREACHABLE,
                f"could not reach the model source ({exc}). This download needs network access "
                "once; everything after it works offline. Retry when you are connected, or bind "
                "a provider you already have in Settings → Models.",
            ) from exc
        with response:
            declared_total = response.headers.get("Content-Length")
            if declared_total and declared_total.isdigit():
                total = int(declared_total)
                # The ceiling, BEFORE a byte lands: a source announcing more than the signed-off
                # file may be is refused on its word rather than after it has filled the disk.
                refused = admit_transfer(declaration, total, announced=True)
                if refused is not None:
                    raise DownloadFailed(refused.outcome, refused.detail)
            while True:
                if time.monotonic() > deadline:
                    raise DownloadFailed(
                        DOWNLOAD_TRUNCATED,
                        f"the download was still running after {int(_DEADLINE_S / 60)} "
                        f"minutes ({received} of {total} bytes) and was stopped. The partial "
                        "file was removed. Retry on a faster connection, or bind a provider "
                        "you already have.",
                    )
                chunk = await asyncio.to_thread(response.read, _CHUNK_BYTES)
                if not chunk:
                    break
                # …and after every read, because a Content-Length can be absent or lie. The
                # chunk that crosses the line is never written, so the partial on disk never
                # exceeds the ceiling, and the ``finally`` below removes it.
                refused = admit_transfer(declaration, received + len(chunk), announced=False)
                if refused is not None:
                    raise DownloadFailed(refused.outcome, refused.detail)
                sink.write(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received, total)
        sink.close()  # flush before the digest is taken, or a short read verifies the wrong bytes
        verdict = verify_download(partial, declaration)
        if not verdict.ok:
            raise DownloadFailed(verdict.outcome, verdict.detail)
        partial.replace(target)
        partial = None  # type: ignore[assignment]
    except asyncio.CancelledError:
        logger.info("bundled-chat: download cancelled at %d/%d bytes", received, total)
        raise
    finally:
        # Unconditional, and it covers cancellation too: the ONE thing that must never survive
        # a failed transfer is a file at a path a later run would treat as a finished model.
        sink.close()
        if partial is not None:
            partial.unlink(missing_ok=True)


async def _await_other_downloader(declaration: BundleDeclaration) -> Path:
    """Wait for the process holding the lock to finish, then use its file.

    Bounded like the transfer itself: a holder that dies without finishing must not leave this
    side waiting forever, so this gives up with the same actionable message a failed transfer
    gives.
    """
    deadline = time.monotonic() + _DEADLINE_S
    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        existing = installed_weight()
        if existing is not None and verify_download(existing, declaration).ok:
            return existing
        with single_flight(f"{APP_NAME}:download:{declaration.sha256[:12]}") as free:
            if free:
                break  # the other side stopped; fall through and do it ourselves
    return await download_weight()


def _install_licence_texts(destination: Path) -> None:
    """Copy ``MODEL_LICENSE`` + ``MODEL_NOTICE`` next to the downloaded weight.

    Apache-2.0 §4 requires the licence text to travel with a redistribution, and the machine
    that holds the bytes is where it has to be. They ship in the wheel precisely so this needs
    no second network hop — a licence a user can only read online is one they cannot read
    offline, which is the state this whole app exists to work in.
    """
    source = Path(__file__).resolve().parent / "weights"
    for name in ("MODEL_LICENSE", "MODEL_NOTICE"):
        origin = source / name
        if origin.is_file():
            try:
                shutil.copyfile(origin, destination / name)
            except OSError:
                logger.warning("bundled-chat: could not place %s beside the weight", name)


# ── a process-wide single load ─────────────────────────────────────────────────────────────

_MODEL_LOCK = threading.Lock()
_MODEL: LlamaCpuModel | None = None


def load_bundled_model(path: Path | None = None) -> LlamaCpuModel:
    """Load (once, process-wide) and return the bundled model.

    One instance per process on purpose: the dequantized float32 weights are roughly 540 MB,
    so a per-session instance would multiply that by the number of open chats.
    """
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        resolved = path or installed_weight()
        if resolved is None or not resolved.is_file():
            # `is_file()` too, not just `is None`: with an explicit *path* override this is the
            # only check, and a named failure has to be unconditional — a bare
            # FileNotFoundError from deep inside the GGUF reader is the shape a caller cannot
            # act on.
            raise BundleUnavailable(
                f"the default chat model is not downloaded yet ({resolved or weight_path()}). It "
                "is a one-time download; start it from the chat screen, onboarding's model step, "
                "or Settings → Providers → Bundled offline model."
            )
        logger.info("bundled-chat: loading %s", resolved.name)
        model = LlamaCpuModel(GgufModel(resolved))
        _MODEL = model
        return model


def reset_loaded_model() -> None:
    """Drop the cached model. For tests — a 540 MB singleton must not leak between them."""
    global _MODEL
    with _MODEL_LOCK:
        _MODEL = None


# ── the provider ──────────────────────────────────────────────────────────────────────────


def user_request(content: str) -> str:
    """The user's actual request, recovered from an assembled prompt.

    **Why a provider does this at all.** Core assembles every turn into ONE string — identity,
    memory, session context, the skills list, history, then the request — and hands it to the
    provider as the user message. Measured on the shipped weight over a real
    ``POST /api/chat``: that string was 11,000 characters, and the model answered *"You are
    currently running in AGENT mode -- full execution. Use whatever tools the user needs…"*. It
    CONTINUED the instructions rather than following them, which is the documented behaviour of
    a 135M model handed a long instruction block — and to a user it reads as the product being
    broken, not as the model being small. Every capable model takes the whole blob; this one
    cannot, so it reads the request back out.

    The boundary is core's own :data:`USER_REQUEST_MARKER`, imported rather than spelled out
    here: a hard-coded copy would silently stop matching the day core reworded it, and silently
    is the bad part. After the marker, the request runs until the next bracketed block (the
    dashboard's ``[WIDGETS]`` instructions), because those are addressed to a model that can
    render widgets and this one cannot.

    A message with no marker — a direct ``complete()`` call, a channel turn, a test — is
    returned untouched.
    """
    marker = content.find(USER_REQUEST_MARKER)
    if marker < 0:
        return content
    request = content[marker + len(USER_REQUEST_MARKER) :].lstrip("\n")
    cut = request.find("\n\n[")
    return (request[:cut] if cut >= 0 else request).strip()


def _chatml(
    model: LlamaCpuModel,
    messages: Sequence[dict],
    budget: int,
    system_budget: int = DEFAULT_SYSTEM_BUDGET_TOKENS,
) -> list[int]:
    """Render *messages* into the weight's ChatML token ids, newest-first within *budget*.

    Truncation drops the OLDEST turns and never the system message or the final user turn:
    a prompt trimmed from the wrong end reads as the model ignoring what was just asked. Each
    user turn is reduced to its request by :func:`user_request` first, so a multi-turn
    conversation still reaches the model as a conversation rather than as N copies of the
    assembled context.

    🔴 **The result never exceeds *budget* — a newest turn that cannot fit is REFUSED.** It
    used to be kept whole whatever its size, and that one rule is how an ordinary paste became
    an out-of-memory kill: a 40,000-character message became a ~9,000-token prefill, a 60,000
    one a (9, 27862, 27862) float32 score array. Nothing older can be dropped to make room for
    the message itself, so the honest outcome is :class:`PromptExceedsWindow` — raised here,
    before the model runs, carrying the numbers its sentence needs.

    **The system prompt is capped too, for the same measured reason.** The loop's prompt is
    written for a tool-using frontier model and this provider declares
    ``supports_tools = False``, so most of it does not even apply here. A system prompt over
    *system_budget* tokens is replaced by :data:`FLOOR_SYSTEM_PROMPT` — replaced rather than
    truncated, because a prompt cut mid-sentence is the same parroting hazard with a ragged
    edge. A short one (a user's own persona line, a test's) passes through untouched.
    """
    start, end = model.bos_id, model.eos_id
    newline = model.tokenizer.encode("\n")

    def turn(role: str, content: str) -> list[int]:
        return [start, *model.tokenizer.encode(f"{role}\n{content}"), end, *newline]

    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    declared = "\n".join(str(m.get("content", "")) for m in system).strip()
    if system_budget > 0 and len(model.tokenizer.encode(declared)) > system_budget:
        declared = FLOOR_SYSTEM_PROMPT
    head: list[int] = turn("system", declared) if declared else []
    tail = [start, *model.tokenizer.encode("assistant\n")]

    body: list[tuple[str, str, list[int]]] = []
    for message in rest:
        role = str(message.get("role") or "user")
        role = role if role in ("user", "assistant") else "user"
        text = str(message.get("content", ""))
        text = user_request(text) if role == "user" else text
        body.append((role, text, turn(role, text)))

    used = len(head) + len(tail)
    if body:
        role, text, newest = body[-1]
        if used + len(newest) > budget:
            framing = len(turn(role, ""))
            raise PromptExceedsWindow(
                model=model_name(),
                room_tokens=budget - used - framing,
                request_tokens=len(newest) - framing,
                request_chars=len(text),
            )
    kept: list[list[int]] = []
    for _role, _text, chunk in reversed(body):
        if used + len(chunk) > budget:
            break
        kept.insert(0, chunk)
        used += len(chunk)
    return [*head, *[t for chunk in kept for t in chunk], *tail]


class BundledChatProvider(ModelProvider, LocalModelProvider):
    """The default offline model: inference AND its own download/delete management.

    Two axes on ONE object, exactly as ``apps/native/ollama-models`` does it, and not by
    preference — ``providers/registry.py::ModelTypeHandler.register`` duck-types whatever
    ``create_provider()`` returns and only registers a local-model provider if THAT object
    carries the contract. A separate management class would simply never be registered.

    **The inference half.** Stateless: the caller owns history and passes the whole message
    list each turn, so there is no session, no subprocess and nothing to clean up. Tool calling
    is not advertised — a 135M model cannot be trusted to emit a tool schema, and the native
    loop degrades to a tool-less single shot when a provider says so, which is honest.

    **The management half (the reason the download needs no new route).** The fetch has to be
    visible, bounded, cancellable and legible when it fails, and all four already exist
    generically for any local-model provider: ``POST /api/models/downloads`` starts a job,
    ``GET /api/models/downloads/{id}/stream`` streams byte counts, speed and ETA,
    ``DELETE /api/models/downloads/{id}`` cancels, ``DELETE /api/models/local/{p}/{m}`` removes
    the file, and ``LocalModelManager.tsx`` renders the bar, the byte counts, the cancel button
    and the per-row failure for every provider that implements this. Implementing the contract
    buys all of it and adds NO route carrying this app's name — which is the only version core
    stays provider-agnostic under.
    """

    supports_tools = False
    prompt_cache = PromptCache.NONE
    #: :func:`user_request` discards everything before core's marker, so this model is handed
    #: the user's request alone. Declared so core assembles, budgets and records exactly that —
    #: before it was declared, a "used 1 skill" chip named a skill this model never saw.
    request_only = True

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        settings = dict(options or {})
        self._max_output_tokens = _positive_int(
            settings.get("max_output_tokens"), DEFAULT_MAX_OUTPUT_TOKENS
        )
        self._context_tokens = _positive_int(settings.get("context_tokens"), DEFAULT_CONTEXT_TOKENS)
        self._temperature = _non_negative_float(settings.get("temperature"), DEFAULT_TEMPERATURE)
        self._top_p = _non_negative_float(settings.get("top_p"), DEFAULT_TOP_P)
        self._repeat_penalty = _non_negative_float(
            settings.get("repeat_penalty"), DEFAULT_REPEAT_PENALTY
        )
        # An explicit weight path, for tests that drive a synthetic model. Production never
        # sets it: the path comes from the sign-off record, resolved against the home.
        self._weight_override: Path | None = (
            Path(str(settings["weight_path"])) if settings.get("weight_path") else None
        )
        self._context_pct: float | None = None
        self._cancelled = threading.Event()

    @property
    def sampling_temperature(self) -> float | None:
        """This provider IS the sampler, so it always samples at a temperature: this one."""
        return self._temperature

    # ── identity ──

    @property
    def name(self) -> str:
        return APP_NAME

    @property
    def display_name(self) -> str:
        return "Bundled offline model"

    @property
    def floor_notice(self) -> str:
        """The honest label. A surface that renders this provider must be able to say it."""
        return FLOOR_NOTICE

    # ── lifecycle ──

    async def start(self) -> None:
        """Nothing to spawn. The weight loads lazily on the first turn.

        Lazily and not here: ``start()`` runs when a provider is built, and building one
        costs nothing, while loading costs ~540 MB of resident memory. A user who never sends
        a message on an unbound home should not pay for the model they did not use.
        """
        return None

    async def shutdown(self) -> None:
        self._cancelled.set()

    async def approve_tool(self, request_id: str | int) -> None:
        """No tools are offered, so nothing can be pending approval."""
        return None

    async def reject_tool(self, request_id: str | int) -> None:
        return None

    def context_usage_pct(self) -> float | None:
        return self._context_pct

    async def cancel(self, *, wait_ack_timeout: float = 0.0) -> Any:
        self._cancelled.set()
        return "acked"

    # ── the window this provider serves ──

    def _served_window(self) -> int:
        """The window this provider runs its model with: the configured prompt budget, never
        past what the loaded weight declares it can take (``llama.context_length``).

        ONE derivation for every number that describes this window — the prompt budget, the
        reply reserve, the context gauge, the model card and :meth:`served_context_window` —
        because each of them used to answer separately: the prompt was built against the
        configured 4,096, the gauge divided by the weight's 8,192 and the card said a fixed
        4,096 whatever the setting. Until the weight is loaded the configured value stands
        alone; the settings form bounds it by the signed-off weight's own limit.
        """
        engine = _MODEL
        limit = engine.max_context if engine is not None and engine.max_context > 0 else 0
        return min(self._context_tokens, limit) if limit else self._context_tokens

    def _reply_reserve(self, window: int) -> int:
        """Room kept for the reply: the configured maximum, never more than half the window.

        Half, because that is core's rule for every catalog card
        (``local_models.budgets.MAX_OUTPUT_FRACTION``) — the budget check reads this app's card
        through it, so a reserve this provider sized differently would put the two halves of one
        turn on two different budgets.
        """
        return max(1, min(self._max_output_tokens, window // 2))

    async def served_context_window(self) -> int | None:
        """What core's window resolver asks before a turn: the window above, exactly."""
        return self._served_window()

    # ── inference ──

    async def stream(self, message: str) -> AsyncIterator[LLMEvent]:
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        reasoning_effort: str = "",
    ) -> AsyncIterator[LLMEvent]:
        """Stream one completion. ``tools`` and ``reasoning_effort`` are accepted and ignored.

        Accepted-and-ignored rather than rejected: the native loop passes both to every
        provider, and a floor model that refused a turn because a tool schema came along
        would turn "small model" into "broken chat". The capability descriptor already says
        it has no tools, which is where a caller is supposed to read that.
        """
        del tools, model, reasoning_effort
        self._cancelled.clear()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        failure: list[BaseException] = []
        report = GenerationReport()
        prompt_tokens = [0]

        def run() -> None:
            try:
                engine = load_bundled_model(self._weight_override)
                window = self._served_window()
                reserve = self._reply_reserve(window)
                # The prompt AND the reply must fit the window together: a prompt that fills it
                # exactly leaves the reply nowhere to go. A newest message that cannot fit raises
                # here, before the prefill — the prefill is the allocation.
                prompt = _chatml(engine, messages, window - reserve)
                prompt_tokens[0] = len(prompt)
                self._context_pct = min(100.0, len(prompt) / window * 100.0)
                for piece in engine.generate(
                    prompt,
                    max_new_tokens=reserve,
                    temperature=self._temperature,
                    top_p=self._top_p,
                    repeat_penalty=self._repeat_penalty,
                    repeat_window=DEFAULT_REPEAT_WINDOW,
                    stop_ids=(engine.eos_id, engine.pad_id),
                    should_stop=self._cancelled,
                    report=report,
                ):
                    loop.call_soon_threadsafe(queue.put_nowait, piece)
            except BaseException as exc:  # noqa: BLE001 — re-raised on the caller's task
                failure.append(exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        worker = threading.Thread(target=run, name="bundled-chat-generate", daemon=True)
        worker.start()
        try:
            while True:
                piece = await queue.get()
                if piece is None:
                    break
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=piece)
        finally:
            self._cancelled.set()
            await asyncio.to_thread(worker.join, 30.0)
        if failure:
            raise failure[0]
        yield LLMEvent(
            kind=EVENT_COMPLETE,
            context_usage_pct=self._context_pct,
            stop_reason=report.stop_reason,
            input_tokens=prompt_tokens[0],
            output_tokens=report.output_tokens,
        )

    # ── the local-model management contract ──

    #: No catalogue to search: this app offers exactly the one signed-off model, so a search box
    #: would have nothing to filter. ``LocalModelManager.tsx`` hides it for a provider saying so.
    searchable = False

    # `name` and `display_name` are NOT repeated here. Both contracts require them and the
    # identity half above satisfies both — a second pair of properties further down the class
    # would silently shadow the first, which is a dead definition and the exact shape the
    # clean-break tenet forbids.

    async def is_available(self) -> bool:
        """True whenever a model is signed off — i.e. whenever there is something to offer.

        Not "is it downloaded": a provider that reported unavailable until its model was
        present could never show the card that downloads it.
        """
        return _declaration() is not None

    async def list_models(self) -> list[LocalModel]:
        """The one model this app offers, with the exact byte size and its licence.

        ``size_mb`` is the EXACT signed-off size, not the ceiling, because it is what the
        progress bar divides by and what the user is shown before agreeing to the download. A
        bar driven off the 150 MiB ceiling would sit at 92% when the file finished.
        """
        declaration = _declaration()
        if declaration is None:
            return []
        present = installed_weight() is not None
        # The window THIS provider serves, not the defaults: the budget check reads the reply
        # reserve off this card, and a card that always said 4,096/320 disagreed with any user
        # who had changed either setting.
        window = self._served_window()
        return [
            LocalModel(
                name=model_name(),
                size_mb=declaration.size_bytes / (1024 * 1024),
                description=(
                    f"{declaration.model_id} — the small model PersonalClaw can run with no "
                    "account and no API key, so a fresh install can chat before anything is "
                    "set up. One-time download; it runs on the CPU afterwards with no network. "
                    "It is tiny, so expect short, shaky answers and no tool use."
                ),
                downloaded=present,
                capabilities=["chat"],
                gated=False,
                source=declaration.model_id,
                runtime="gguf-numpy",
                license=declaration.licence,
                non_commercial=False,
                context_tokens=window,
                output_tokens=self._reply_reserve(window),
                display_name=display_model_name(),
            )
        ]

    async def download_model(self, model_name_: str) -> bool:
        """Fetch the signed-off weight, then make it resolvable without a restart.

        Returns ``True`` only when the bytes verified. A failure RAISES with the actionable
        sentence rather than returning ``False``, because the shared job runner surfaces an
        exception's message to the user and turns a bare ``False`` into "download failed" with
        no reason — and the four ways this fails need four different things from a user.
        """
        del model_name_  # one model; the record names it
        try:
            await download_weight()
        except DownloadFailed as exc:
            # Re-raised, not swallowed: the job runner puts `str(exc)` on the job record, which
            # is what LocalModelManager renders per row.
            raise RuntimeError(str(exc)) from exc
        refresh_registration()
        return True

    async def delete_model(self, model_name_: str) -> bool:
        """Remove the downloaded weight and stop offering chat from it.

        Both halves, in that order: a deleted file with the floor entry still registered would
        resolve chat to a model that is gone, which is the stale-pin shape core's resolver has
        a whole error path for.
        """
        del model_name_
        path = weight_path()
        existed = path.is_file()
        path.unlink(missing_ok=True)
        for name in ("MODEL_LICENSE", "MODEL_NOTICE"):
            (path.parent / name).unlink(missing_ok=True)
        refresh_registration()
        return existed

    def cache_dir(self) -> str | None:
        """Where the weight lives — how the shared job runner measures progress.

        This is the app's ONLY contribution to progress reporting: core snapshots this
        directory's size when a job starts and reports the delta as it grows, so pointing at
        the real download directory is what turns an indeterminate spinner into a byte count,
        a percentage, a speed and an ETA.
        """
        return str(weights_dir())


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


# ── registration ──────────────────────────────────────────────────────────────────────────

BUNDLED_CHAT_CAPABILITY = ProviderCapability(
    type=PROVIDER_TYPE,
    capabilities=frozenset({Capability.CHAT, Capability.STREAMING}),
    supports_streaming=True,
    # No tools, no embeddings, no vision — a 135M text model does none of them, and
    # advertising one would make it a candidate for a role it cannot fill.
    supports_tools=False,
    supports_embeddings=False,
    supports_vision=False,
    max_context_tokens=DEFAULT_CONTEXT_TOKENS,
    structured_output=StructuredOutput.NONE,
    prompt_cache=PromptCache.NONE,
    notes=FLOOR_NOTICE,
)


def _factory(
    *,
    entry: ProviderEntry,
    session_key: str | None = None,
    **kwargs: object,
) -> ModelProvider:
    """Registry contract: build a provider from the app's settings AS SAVED NOW.

    The settings are read at BUILD time rather than captured when the entry was registered.
    The entry is registered once per process (and again when the weight lands), so options
    captured then made every change in the settings form — the reply length, the prompt
    budget, the sampling knobs — wait for a gateway restart. An entry's own options (a
    user-created ``config.json`` row of this type) still win over the app's settings.

    A per-call ``temperature`` build kwarg (best-of-N's ladder) wins over both — the caller
    asking for THIS temperature is more specific than the default. Every build kwarg used to
    be discarded here, so best-of-N on the bundled floor sampled N greedy copies of one answer.
    """
    del session_key  # stateless, credential-free
    options = {**ProviderSettings.load(APP_NAME), **dict(entry.options or {})}
    temperature = per_call_temperature(kwargs)
    if temperature is not None:
        options["temperature"] = temperature
    return BundledChatProvider(options)


def create_provider(config: dict | None = None) -> BundledChatProvider:
    """Manifest contract: build a provider from this app's settings dict."""
    return BundledChatProvider(dict(config or {}))


def availability() -> tuple[bool, str]:
    """Whether this app is usable on THIS machine. It always is, and that is the change.

    Core calls this for the Apps list so an app that would only ever fail is greyed out rather
    than offered. When the weight shipped in the wheel, "no weight in this install" WAS that
    permanent failure and this returned ``False``. It is no longer: a machine with no weight
    yet is one download away, and the download's own state — offered / running / failed, with
    bytes and a reason — is reported by the local-model surface, which is built for exactly
    that. Returning ``False`` here would grey out the app whose card is the way to fix it.
    """
    return True, ""


def floor_entry() -> ProviderEntry | None:
    """The in-memory zero-config floor entry, or ``None`` when there is nothing to run.

    ``None`` is the load-bearing half, and it is the state of every fresh install until the
    download finishes: an entry with no weight behind it is a provider that builds and then
    fails its first turn. (:func:`_readiness` answers the same question for any entry of this
    type, so a ``config.json`` row created by hand cannot smuggle that state back in.)

    The entry exists whenever the weight does — whatever ``offer_as_fallback`` says — because
    the switch decides whether the model answers when NOTHING is bound, not whether it can be
    bound at all. It carries no options: the settings are read at build (see :func:`_factory`).
    """
    path = installed_weight()
    if path is None:
        return None
    return ProviderEntry(
        name=APP_NAME,
        type=PROVIDER_TYPE,
        model=path.stem,
        options={},
        credential=None,
        declared_capabilities=frozenset({Capability.CHAT}),
        floor=True,
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _readiness(entry: ProviderEntry, *, implicit: bool) -> tuple[str, str] | None:
    """Core's readiness contract for this type: ``(why, fix)`` when an entry cannot serve.

    Two answers, both read LIVE — the disk and the saved settings, on every call — so neither
    waits for a restart:

    * **No weight on disk** — the model is not downloaded (or a partial transfer is all there
      is). A provider of this type BUILDS in that state and fails its first turn, which is why
      the answer has to come from here rather than from a build.
    * **Nothing is bound and ``offer_as_fallback`` is off** — the user switched off "Answer when
      nothing else is bound", so the implicit fallback must pass this model by. A binding to it
      (``implicit=False``) is still served: choosing the model is the user saying it should
      answer.

    The words are the ones a user sees on the model check and in chat's setup state, so they
    name the model, its size and where the download is.
    """
    if installed_weight() is None:
        declaration = _declaration()
        size = f"{round(declaration.size_bytes / (1024 * 1024))} MiB " if declaration else ""
        return (
            f"{display_model_name()}, the small offline model, is not downloaded yet — it is a "
            f"one-time {size}download",
            "download it from onboarding's model step, the chat screen, or Settings → Providers "
            "→ Bundled offline model — or bind chat to another model in Settings → Models",
        )
    if implicit and not _as_bool(ProviderSettings.load(APP_NAME).get("offer_as_fallback", True)):
        return (
            f"{display_model_name()} is downloaded, but “Answer when nothing else is bound” is "
            "switched off, so it only answers when chat is bound to it",
            "bind chat to it in Settings → Models, or switch that setting back on in Settings → "
            "Providers → Bundled offline model",
        )
    return None


def register() -> bool:
    """Register the type, and the floor entry when a weight is actually installed.

    Returns whether a floor entry was registered, which is what the tests assert on: the type
    registering is unconditional (so Settings can describe the provider and its download card
    can exist), the ENTRY is conditional (so resolution only succeeds when a turn can really be
    served). The type carries :func:`_readiness`, which is what every resolution asks first.
    """
    registry = get_default_registry()
    try:
        registry.register_type(BUNDLED_CHAT_CAPABILITY, _factory, readiness=_readiness)
    except ProviderResolutionError:
        logger.debug("bundled-chat: provider type already registered")
    entry = floor_entry()
    if entry is None:
        return False
    registry.register_entry(entry)
    return True


def refresh_registration() -> bool:
    """Re-evaluate the floor entry after the weight lands (or is deleted).

    The app module is imported ONCE per process, so a download that finishes an hour later
    would otherwise not be resolvable until a restart — and "you downloaded it, now restart"
    is not a first-run experience. Called by :meth:`BundledChatProvider.download_model` and
    :meth:`BundledChatProvider.delete_model`, which are the only two events that change the
    answer.

    The app's entry REPLACES whatever holds its name. ``register_entry`` is first-wins, so a
    ``config.json`` row named ``bundled-chat`` (the settings form used to create one) would
    otherwise keep the name after the download, and chat would resolve to that row — not a
    floor, so the chat screen would stop saying the small model is the one answering.
    """
    registry = get_default_registry()
    entry = floor_entry()
    registry.unregister_entry(APP_NAME)
    if entry is None:
        reset_loaded_model()
        return False
    registry.register_entry(entry)
    return True


register()
