# Plan: Document Handling Tools — Produce the Formats a Person Actually Sends

**Status:** DONE — Session 1 (writer seam + docx/xlsx + tools + kinds) and Session 2 (pptx + pdf + deck_create + round-trip) both shipped 2026-07-29. All four formats generate. Created 2026-07-29 (owner ask: competitive gap analysis, Genspark + Manus; owner direction: "we will have document handling tools where we have tools for these usecases as well as other formats that are supported by artifacts and knowledge except media formats")
**Created:** 2026-07-29
**Wave:** 2 (S1: the writer seam + docx/xlsx; S2: pptx/pdf + the artifact/knowledge round-trip)
**Depends on:** nothing hard. All four libraries are **already core dependencies** (`pyproject.toml:44-53`: `python-docx`, `pdfplumber`, `python-pptx`, `openpyxl`) — used today for *reading only*. Builds on the shipped artifact store (`artifacts/`), the content-type registry (`web/src/ui/content/contentTypes.ts`), and the knowledge readers (`knowledge/readers.py`). Coordinates with ARTIFACTS-EVOLUTION (61 — new artifact kinds land in its library grid + viewer; **read its `create_binary` lesson in §Context before adding a kind**), KNOWLEDGE-LIBRARY (49 — knowledge already *reads* these formats; this plan closes the write half so a knowledge item can round-trip out), WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS (its synthesis nodes gain real deliverable outputs), CONTEXT-ECONOMY (DONE — generated files must never be inlined into context; §C4 states the rule).
**Scope:** PersonalClaw can *read* `.docx`/`.pdf`/`.pptx`/`.xlsx` (`knowledge/readers.py`, `doc_parser.py`) and can *generate* HTML, markdown, SVG, JSON, text, React, infographics, and images — but it **cannot produce a single office or PDF document**. Verified: `python-docx`, `openpyxl`, `python-pptx` and `pdfplumber` are core deps used **exclusively** for extraction; grep finds zero `Workbook()`, zero `Presentation()` writes, zero `write_pdf`/`to_pdf`/`generate_pdf`. Both competitors make decks/sheets/docs their headline deliverable. This plan adds **agent tools that generate documents**, plus the artifact kinds, previews and exports that make them first-class — covering every non-media format artifacts and knowledge already understand. **Soul guardrails:** (1) **one writer seam, not four bespoke tools** — a single `documents/` package with a per-format writer registered in one registry; adding a format is one writer + one registration, never a sweep (mirrors the content-type registry's own stated design rule: "Adding a type = one `register()` call … never a sweep across the UI"); (2) **generated documents are artifacts, not loose files** — every output lands in the existing artifact store with a slug, version history and revert, so the shipped library/viewer/timeline machinery applies unchanged; no new store, no parallel "documents" directory; (3) **structured input, not vendor markup** — the agent supplies a declarative document model (or existing markdown/HTML the platform already produces), and the writer renders it. The agent never emits OOXML, and no vendor file-format string appears outside `documents/writers/`; (4) **media stays out** — audio/video/image *generation* is explicitly not this plan (image gen already exists; video is issue #94's separate fix). Class **B** (new artifact kinds + persisted binary bodies) — pre-LIFECYCLE-DOCTRINE, so it lands as a **plain clean break under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry + snapshot advice in release notes).

---

## Context (code recon, 2026-07-29 — verified; cite these rather than re-deriving)

**The read half is genuinely strong, and it defines the formats in scope:**
- `knowledge/readers.py` handles `.md`, `.txt`, source code (11 languages), `.html`, `.docx` (with headings), `.pdf` (pdfplumber), `.pptx` (incl. speaker notes), `.xlsx`/`.xls` (openpyxl, per-sheet), `.csv`/`.tsv` (as markdown tables), `.json`, `.yaml`, `.log`.
- `doc_parser.py` is a stdlib-only, zip-bomb-defended, never-raises extractor for `.docx`/`.pdf`/`.pptx` (`DOC_EXTENSIONS`, `doc_parser.py:40`).
- **So the honest scope of "other formats that artifacts and knowledge support, except media" is:** `docx`, `xlsx`, `pptx`, `pdf`, `csv`, plus round-tripping the text kinds the platform already generates (`markdown`, `html`, `document`, `json`, `svg`, `text`). Media (`image`, and video per #94) is out.

**The artifact store is ready for binary kinds — but read this trap first:**
- `ALLOWED_KINDS` (`artifacts/models.py:30-41`) currently holds 10 kinds; `BINARY_KINDS = {"image"}` (`models.py:50`), with the in-file note "Today only images; video/audio-gen would join here" (`models.py:49`).
- **`create_binary` silently coerces an unknown kind to `image`** — `native.py:471`: `kind=normalize_kind(kind) if is_binary_kind(kind) else "image"`. This exact defect is live: `mcp_artifacts.py:665` passes `kind="video"`, which is in neither set, so **every generated video is stored as an image** (filed as issue #94). **Any kind this plan adds MUST be registered in BOTH `ALLOWED_KINDS` and `BINARY_KINDS`, and T1.4 hardens the coercion into a raise** so this class of bug cannot recur.
- `_MIME_TO_EXT` (`models.py:53-58`) maps MIME→on-disk extension and must gain the office/PDF MIMEs, or the raw endpoint serves the wrong `Content-Type` (it derives type from the stored extension — `handlers.py:272`).
- Caps: 50 versions FIFO-pruned, **16MiB binary bodies** (`models.py`). A generated deck can approach this; §C4 sets the guard.

**The frontend has exactly one place to teach:**
- `web/src/ui/content/registerBuiltins.ts` registers every type declaratively; `image` (`registerBuiltins.ts:161-171`) is the precedent for a **binary** type: `preview: { render: ImageFile }`, `binary: true`, `commentable: false`, **no `edit`**. A `pdf` type already exists for *files* (`registerBuiltins.ts:173`, `exts: ['pdf']`) with a `PdfFile` renderer — so PDF preview is **already built** and only needs the artifact `kinds: ['pdf']` recognizer added.
- Export today is **client-side only** (`exporters.ts`), and the registry's `exports` slot is per-type. `exportDocumentHtml` (standalone HTML) and `exportInfographicSvg` are the two existing targets. There are **no server-side export endpoints** — this plan adds generation tools, and deliberately does *not* add a parallel export path (§What this is NOT).

**The agent-tool precedent to imitate exactly:**
- `mcp_artifacts.py` holds `artifact_save/get/update/list/versions/delete` + `image_generate`/`video_generate`, each dispatched against the native provider, project-scoped via `_current_project_id`, and **SEL-audited** via the module's `_audit` helper. New tools join this module and follow that shape — including the audit call on every outcome path (success, refusal, error).

## Design

- **S1 — the writer seam + the two highest-value formats.** A new core package `documents/` with: `model.py` (the declarative document model — headings, paragraphs, lists, tables, images-by-artifact-ref, page breaks; a *sheet* model of named sheets × rows × typed cells; a *deck* model of slides with title/body/notes/image), `writers/` (one module per format, each a pure `render(model) -> bytes`), and `registry.py` (format→writer lookup, mirroring `artifacts/registry.py`'s shape). Two writers ship in S1: **docx** (`python-docx`) and **xlsx** (`openpyxl`). Two agent tools land: `document_create` (docx from the document model **or** from existing markdown/HTML the platform already produced) and `sheet_create` (xlsx from the sheet model or from CSV/JSON rows). Output goes straight into the artifact store via `create_binary` under new kinds. **Markdown/HTML→docx conversion is deliberately in-scope and is the primary path** — the agent is already excellent at markdown, and Genspark's verified approach (Claude *generates the source*, code renders the artifact) is the cheaper, more reliable shape than teaching a model OOXML.
- **S2 — pptx + pdf + the round-trip.** `pptx` writer (`python-pptx`, from the deck model or from a markdown outline where `#`/`##` become slide boundaries) and `pdf` writer. **PDF dependency: RESOLVED by owner ruling 2026-07-29 — add `reportlab` as a CORE dependency** (see §Dependency ruling below). The writer renders the `DocumentModel` directly to PDF via reportlab's platypus flowables, so PDF becomes a first-class writer target on every install rather than an environment-dependent capability. The earlier "local converter if present" option is **rejected** — it made a documented product capability silently unavailable on most machines. S2 also closes the round-trip: a knowledge item or text artifact can be exported to any writer format, so the read half finally has a write counterpart, and `csv` joins as a text-kind writer.
- **What this is NOT:** not a WYSIWYG editor (generated documents are previewed and re-generated, not hand-edited in-app — the source model/markdown is the editable thing); not a server-side export *framework* for existing client-side exports (`exporters.ts` stays as-is); not templating-by-the-user (a template system is a follow-up if real use demands it); not media generation; not a second artifact store.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — The document model + writer registry (`documents/`, new)

```python
# documents/model.py — declarative, vendor-neutral. No OOXML vocabulary here.
@dataclass
class Block:                      # one flow element of a text document
    kind: str                     # heading | paragraph | bullets | numbered | table | image | pagebreak | code
    text: str = ""                # heading/paragraph/code body
    level: int = 1                # heading level 1-6
    items: list[str] = field(default_factory=list)          # bullets/numbered
    rows: list[list[str]] = field(default_factory=list)      # table (row 0 = header)
    artifact_slug: str = ""       # image blocks reference an EXISTING artifact — never inline bytes

@dataclass
class DocumentModel:
    title: str
    blocks: list[Block]

@dataclass
class SheetModel:
    sheets: dict[str, list[list[object]]]   # name → rows; row 0 = header. Cell types preserved
                                            # (str/int/float/bool/None) so numbers stay numbers.

@dataclass
class DeckModel:
    title: str
    slides: list[dict]            # {title, body: list[str], notes: str, artifact_slug: str}

# documents/registry.py
Writer = Callable[[object], bytes]           # (model) -> file bytes; pure, no I/O
def register_writer(fmt: str, fn: Writer) -> None: ...
def get_writer(fmt: str) -> Writer | None: ...   # None ⇒ unsupported; caller returns a typed refusal
def available_formats() -> list[str]: ...        # what's actually installed/usable RIGHT NOW
```

`available_formats()` is load-bearing: the PDF path may be unavailable (no local converter), and the tool must report that honestly rather than failing mid-generation. Writers are **pure** — they take a model and return bytes; artifact persistence is the caller's job, so writers stay unit-testable without a store.

### C2 — Markdown/HTML → model (the primary authoring path)

```python
# documents/from_markup.py
def document_from_markdown(md: str, *, title: str = "") -> DocumentModel: ...
def document_from_html(html: str, *, title: str = "") -> DocumentModel: ...
def deck_from_markdown(md: str, *, title: str = "") -> DeckModel:
    """`#`/`##` start slides; body lines become bullets; an HTML comment
    `<!-- notes: ... -->` becomes speaker notes."""
```

**Security clause (non-negotiable):** HTML input is agent- or web-authored and therefore untrusted. Route it through the existing sanitizer discipline before converting — reuse the platform's sanitize path rather than writing a second one, and never let raw HTML reach a writer. Credential-shaped content must be redacted on the way in via the existing `redact_credentials` (`security.py`), exactly as the artifact injection path does.

### C3 — Agent tools (`mcp_artifacts.py` — join the existing module, follow its `_audit` shape)

```
document_create(name, *, format="docx", markdown="" | html="" | blocks=[...],
                slug="", collection="", tags=[])
    → creates/updates an artifact of kind matching `format`; returns slug + version + a
      "download at /api/artifacts/<slug>/raw" line (NEVER the bytes, NEVER base64 in the reply)
sheet_create(name, *, sheets={...} | csv="" | rows=[...], format="xlsx", slug="", ...)
deck_create(name, *, markdown="" | slides=[...], format="pptx", slug="", ...)
document_formats()  → available_formats() + a one-line reason for any unavailable format
```

Every tool: project-scoped via `_current_project_id`; SEL-audited on **every** outcome (`success`, `refused`, `error`) via the module's `_audit`; honors the existing `artifact_save` dedup/`force` semantics so a re-generation updates in place and bumps a version instead of minting `-2` (that behavior is ARTIFACTS-EVOLUTION S1's C2 — reuse it, don't reimplement).

### C4 — Artifact kinds, MIME, size, and the coercion fix

```python
# artifacts/models.py
ALLOWED_KINDS |= {"docx", "xlsx", "pptx", "pdf", "csv"}
BINARY_KINDS  |= {"docx", "xlsx", "pptx", "pdf"}     # csv is a TEXT kind
_MIME_TO_EXT  |= {
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
  "application/pdf": "pdf",
}
```
**Hardening (T1.4, required):** `native.py:471`'s silent `else "image"` coercion becomes a `ValueError` — a non-binary kind reaching `create_binary` is a programming error and must fail loudly. Fixing this is what stops #94's bug class from recurring; do it in the same session that adds the kinds, and add the regression test.

**Size guard:** binary bodies cap at 16MiB (`models.py`). A writer must check its output length and return a typed refusal naming the actual size **before** calling the store, so the user sees "the deck came to 22MB (cap 16MB) — reduce images" rather than a store-level failure.

**Context rule (per CONTEXT-ECONOMY):** generated document bytes NEVER enter a prompt. The tool reply carries slug + version + raw URL only. When an agent needs the *content* back, it goes through the existing read path (`doc_parser`/`readers`), which already projects text.

### C4b — Dependency ruling (owner, 2026-07-29) — the ONE new dependency this plan adds

```toml
# pyproject.toml, in the CORE `dependencies` list, beside the existing doc-reader block.
# Follow the comment style of the neighbouring entries (lines 42-53) — say WHY it is core.
"reportlab>=4,<5",
```

**Add it to core `dependencies`, not to an extra.** The reasoning an executor should preserve in the comment:
- **Pure Python, no native build, no torch weight** — the exact test the neighbouring doc-reader block applies (`pyproject.toml:42-43`: "Pure-Python, no native/torch weight — safe as core deps (incl. desktop bundle)"). reportlab ships prebuilt wheels and needs no compiler, so it satisfies the same bar `python-docx`/`pdfplumber`/`python-pptx` already cleared.
- **Core, not an extra, for the same reason `codegraph` was made core** (owner decision 2026-07-28, `pyproject.toml:57-60`): "an accelerator that half the installs lack is one nobody can rely on." A document *format* that only exists on some installs is worse than one that exists nowhere, because the agent will offer it and then fail.
- **It must survive the desktop bundle.** Unlike `faiss`/`torch` (deliberately excluded — `pyproject.toml:37`, `116-118`), reportlab is small enough to bundle. Verify it is **not** added to `personalclaw-backend.spec`'s excludes; a PDF writer that vanishes in the desktop build would reintroduce exactly the "capability half the installs lack" problem.

**Scope discipline:** this is the only dependency `DOCUMENT-HANDLING-TOOLS` adds. `python-docx`, `openpyxl`, `python-pptx` and `pdfplumber` are already core (`pyproject.toml:44-53`) — do not re-add, re-pin, or move them. Per the protocol's no-new-dependencies rule, an executor may add **only** the package named here.

### C5 — Frontend: one registration per kind (no sweep)

```ts
// web/src/ui/content/registerBuiltins.ts — follow the `image` precedent (line 161) exactly
registerContentType({ id: 'docx', label: 'Word', icon: FileText, tone: tone('#2b579a'),
  kinds: ['docx'], exts: ['docx'], mimes: [OOXML_DOC_MIME],
  preview: { render: OfficeDocPreview }, commentable: false, binary: true })
// xlsx (Table icon), pptx (Presentation icon) likewise.
// pdf: the `pdf` type ALREADY EXISTS (registerBuiltins.ts:173) with a working PdfFile
//      renderer — only add kinds: ['pdf'] to its recognizer. Do NOT register a second pdf type.
// csv: the `csv` type ALREADY EXISTS (line 151) — add kinds: ['csv'].
```

`OfficeDocPreview` renders an **extracted-text preview** (via a small read endpoint over the existing `doc_parser`) plus a prominent Download action — not a fidelity renderer. State that honestly in the UI ("text preview — download for full formatting"); do not imply WYSIWYG. Every exported `ui/` component needs a `.doc.ts` (there is a drift guard). Mind the **primitive-adoption ratchet** (`web/src/design/primitiveAdoption.test.ts`): use `Button`/`MenuRow`/`ui/forms`, never raw `<button>`/`<input>` outside `web/src/ui/`.

### Integration points
- **Calls:** `python-docx`/`openpyxl`/`python-pptx` (existing core deps), the artifact provider's `create_binary`/`update_binary` (`artifacts/native.py`), `security.redact_credentials` + the sanitize path, `pricing`-free (no model calls in a writer), SEL.
- **Called by:** the new agent tools; KNOWLEDGE-LIBRARY's export path (S2); WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS later (its synthesis nodes gain deliverables — do not pre-build its nodes here).
- **Storage owned:** none new — outputs are artifacts in the existing store (that is guardrail 2).
- **Deliberately NOT touched:** `knowledge/readers.py` and `doc_parser.py` (read paths stay as-is), `exporters.ts` (client-side exports stay), image/video generation, the widget/react sandbox contracts.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — The writer seam + docx/xlsx

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `documents/model.py` + `documents/registry.py`: the three models (C1), `register_writer`/`get_writer`/`available_formats`; unit tests | `src/personalclaw/documents/model.py`, `registry.py`, `tests/test_documents_registry.py` | models round-trip; unknown format returns `None` (never raises); `available_formats()` reflects what is actually usable |
| T1.2 | `documents/writers/docx_writer.py` (python-docx) + `documents/from_markup.py` (`document_from_markdown`/`document_from_html`, sanitize + redact on the HTML path); tests over every `Block.kind` | `src/personalclaw/documents/writers/docx_writer.py`, `from_markup.py`, tests | each block kind renders; a generated .docx is re-readable by the EXISTING `knowledge/readers.py` docx reader (round-trip test — the strongest available proof of validity); untrusted HTML is sanitized and credential-shaped text redacted |
| T1.3 | `documents/writers/xlsx_writer.py` (openpyxl): named sheets, header row, preserved cell types; tests incl. a numeric-typing assertion | `src/personalclaw/documents/writers/xlsx_writer.py`, tests | multi-sheet output re-reads through the existing xlsx reader with numbers still numeric (not stringified) |
| T1.4 | Artifact kinds + MIME map for `docx`/`xlsx` per C4, **and harden `create_binary`**: a non-binary kind now raises `ValueError` instead of coercing to `image`; regression test for the #94 class | `src/personalclaw/artifacts/models.py`, `artifacts/native.py`, tests | new kinds persist and serve the correct `Content-Type` from `/raw`; passing a bogus kind raises (test proves the old coercion is gone) |
| T1.5 | Agent tools `document_create` + `sheet_create` + `document_formats` in `mcp_artifacts.py`, following the module's project-scoping + `_audit` shape; size guard returns a typed refusal naming the real size before storing | `src/personalclaw/mcp_artifacts.py`, tests | tools create real artifacts; re-running with the same name updates in place (no `-2` slug); an oversized output refuses with the actual size; SEL rows on success/refusal/error |
| T1.6 | Frontend: register `docx`/`xlsx` content types + `OfficeDocPreview` (extracted-text preview + Download, honestly labelled) + `.doc.ts` entries; a small read endpoint for extracted text over the existing `doc_parser` | `web/src/ui/content/registerBuiltins.ts`, `web/src/ui/content/*Preview.tsx` (+ `.doc.ts`), a `dashboard/handlers/` route, `web/src/lib/api.ts` | a generated docx/xlsx appears in the artifact library with a text preview + working download; no ratchet trips; ui-docs drift guard green |
| V1 | Validation as a user: from chat, ask the agent to produce a Word document from markdown and a spreadsheet from data; confirm both appear in the Artifacts library with correct icons; download both and **open them in a real application** (Word/Excel/LibreOffice) to confirm validity; re-generate one to confirm a version bump + revert works; `make lint` + targeted pytest + `make test` + web typecheck/test/build | — | holds |

### Session 2 — pptx + pdf + the round-trip

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `documents/writers/pptx_writer.py` (python-pptx) + `deck_from_markdown` (`#`/`##` slide boundaries, `<!-- notes: -->` → speaker notes); artifact kind `pptx`; tool `deck_create` | `src/personalclaw/documents/writers/pptx_writer.py`, `from_markup.py`, `artifacts/models.py`, `mcp_artifacts.py`, tests | a markdown outline becomes a deck re-readable by the existing pptx reader **including notes** |
| T2.2 | Add `reportlab>=4,<5` to core `dependencies` per C4b (with the WHY comment, matching the neighbouring block's style) and confirm it is NOT in `personalclaw-backend.spec`'s excludes; write `pdf_writer.py` rendering the `DocumentModel` via platypus flowables; `pdf` is always in `available_formats()` | `pyproject.toml`, `personalclaw-backend.spec` (verify only), `src/personalclaw/documents/writers/pdf_writer.py`, tests | a generated PDF re-reads through the EXISTING pdfplumber reader with headings/paragraphs/tables/lists intact; `pdf` is unconditionally available on a clean install; the desktop spec does not exclude reportlab |
| T2.3 | Frontend: `pptx` content type; add `kinds: ['pdf']` to the EXISTING pdf type and `kinds: ['csv']` to the existing csv type (do not create duplicates) | `web/src/ui/content/registerBuiltins.ts` (+ `.doc.ts` if a new component) | a generated deck previews + downloads; a generated PDF uses the existing PdfFile renderer; no second pdf/csv type exists (test or grep gate) |
| T2.4 | The round-trip: export an existing knowledge item or text artifact to any available writer format (a `csv` text-kind writer lands here too), reusing the tools from S1/S2 rather than a new endpoint | `mcp_artifacts.py` / the knowledge export path, tests | a knowledge item becomes a .docx artifact; a JSON/rows artifact becomes .csv/.xlsx |
| V2 | Validation as a user: full loop — agent researches a topic → produces a deck and a PDF → both open correctly in real applications → export a knowledge item to Word → confirm every artifact carries version history and revert; verify `document_formats()` honestly reports any unavailable format on this machine; full local gate | — | holds |

## Owner tasks (real world)
1. ~~Rule on the PDF path.~~ **RESOLVED 2026-07-29:** `reportlab` approved as a core dependency (§C4b). No executor decision remains — the package is named, so the no-new-dependencies rule is satisfied for exactly that one addition.
2. **Confirm the "no WYSIWYG" stance.** Generated documents are previewed as extracted text + download; the editable source is the markdown/model. A fidelity editor would be a much larger plan.
3. **Decide whether `csv` should be a distinct artifact kind or stay a file-only content type.** The plan adds it as a text kind for round-trip symmetry; say if you'd rather it stayed file-only.

## Risks & open questions
- **Validity of generated files is the whole game.** A subtly-invalid `.docx` that our reader accepts but Word rejects is the worst outcome. Mitigation is layered: unit round-trips through our own readers (necessary, not sufficient) **plus** the V-tasks explicitly requiring the file to be opened in a real application before the session may close. Do not mark a session complete on unit tests alone.
- **The 16MiB binary cap** is reachable with image-heavy decks. Handled by the pre-store size guard (§C4) returning an actionable refusal.
- **Feature-expectation creep.** Users will want templates, themes, charts, and precise layout. Explicitly out of scope for S1/S2; a real usage signal should drive any follow-up rather than speculative breadth.
- **Open:** whether image blocks should support inline uploads as well as `artifact_slug` references. The plan requires a reference (keeps bytes out of context and reuses the store); revisit if it proves awkward.
- **Open:** chart generation inside xlsx/pptx. Deferred — the platform's existing infographic/SVG path already covers "produce a chart," and native OOXML charts are a large surface for unclear gain.

## Execution log

- [2026-07-29][S1] **DONE (T1.1–T1.6).** The writer seam + docx/xlsx + the artifact kinds
  + the agent tools + the frontend registrations. PersonalClaw can now produce a real
  Word document and a real spreadsheet.

  **Confirmed the premise before building:** the four libraries were genuinely
  read-only — the single `Presentation(` in the tree is `knowledge/readers.py`'s pptx
  READER, and there was no `Workbook()`/`Document()` construction anywhere.

  **The seam.** `documents/` = `model.py` (three declarative models, zero OOXML
  vocabulary) + `writers/` (one pure `render(model) -> bytes` per format) +
  `registry.py`. Registration IS the availability check: a writer whose library is
  missing never registers, so `available_formats()` can't advertise a format that would
  fail on use, and `document_formats` reports honestly. Writers are pure, so they are
  unit-testable without a store and cannot half-write.

  **Markdown → model is the primary path, as planned.** `document_from_markdown` handles
  the shapes an agent actually produces (headings, paragraphs, both list kinds, fenced
  code, tables, rules) and turns anything unrecognized into a paragraph — no input is
  silently dropped. A leading H1 becomes the document title rather than a duplicate
  heading. Code fences are verbatim; inline emphasis is stripped elsewhere. HTML goes
  through the platform's EXISTING `sanitize_html` + `redact_credentials` (never a second
  implementation) and then reuses the markdown path — one parser, not two. A test asserts
  a planted AWS key does not survive into a generated file.

  **TWO REAL BUGS FOUND AND FIXED, both pre-existing:**
  1. **The docx READER dropped every table.** `_read_docx` walked only `doc.paragraphs`,
     and python-docx keeps table content out of that collection — so any table in an
     ingested Word document (often its densest information) was invisible to search,
     embedding and the agent. Found by round-tripping my own output and noticing the
     table didn't come back, then confirming the table WAS in the file. Now rendered as
     markdown tables, matching how the xlsx/csv readers already present tabular data.
     Appended after the prose rather than interleaved: python-docx exposes no ordering
     between paragraphs and tables without walking the XML body, and losing POSITION is a
     far smaller defect than losing the content.
  2. **`create_binary` silently coerced an unknown kind to `"image"`** — which is why
     every generated video was stored as an image (issue #94): `kind="video"` was in
     neither `ALLOWED_KINDS` nor `BINARY_KINDS`, so the else-branch swallowed it. Now a
     `ValueError`. **That raise turned #94 from silent corruption into a hard
     requirement**, so `video` is registered as a real kind here (plus a `VideoFilePreview`
     renderer) rather than left crashing. Issue #94 is fixed as a consequence.

  **Artifact kinds:** `docx`/`xlsx`/`pdf`/`video` in both sets, `csv` as a TEXT kind per
  the owner ruling. `_MIME_TO_EXT` gained the office/PDF/video mimes — without them the
  raw endpoint derives the wrong Content-Type and a download arrives unopenable (verified
  live: both files serve their true OOXML types).

  **New `GET /api/artifacts/{slug}/extract`** backs the honest "text preview — download
  for full formatting" surface, reusing the SAME reader that ingests uploaded documents
  rather than a second extraction path. Capped, and redacted like every other
  LLM-adjacent text surface.

  **Frontend:** `docx`/`xlsx`/`video` content types registered; the EXISTING `pdf` and
  `csv` types gained `kinds:` rather than duplicate registrations (the `PdfFile` renderer
  already worked). `OfficeDocPreview` is deliberately an extracted-text view plus a
  prominent Download, and says so in the UI — implying WYSIWYG when the editable thing is
  the source markdown would be misleading. Full-fidelity editing is the owner's separate
  plan.

  **Validated as a user, to the plan's actual bar — opened in a real application, not
  just our own readers.** Drove the tools on an isolated dev home (port 10737):
  - A live agent DID discover and call `document_formats`, proving the tools are
    registered and reachable; its turn then stalled before the second call, which is
    model behavior, so I exercised all five paths directly (both refusals included).
  - `file(1)` identifies the outputs as **"Microsoft OOXML"** and **"Microsoft Excel
    2007+"**, with complete package structure (`[Content_Types].xml`, `word/document.xml`,
    `xl/workbook.xml`).
  - **Apple's `textutil`** — an independent implementation — extracted the heading, prose,
    both bullets and the full table.
  - **macOS Quick Look** rendered the .docx as a properly formatted document (styled
    title, blue heading, real bullets, bold table header) and the .xlsx with a bold header
    row and cell grid.
  - The extract endpoint returned the text **including the table**, which only works
    because of fix #1.

  Tests: 30 in `tests/test_documents.py` — registry contracts, every markdown shape, the
  sanitize/redact assertion, round trips through the real readers, numeric-typing and
  bool-vs-int preservation, illegal sheet-name sanitization, ragged-row normalization,
  and the coercion hardening. Gate: `make lint` green · `make test` **8972 passed** · web
  typecheck + 283 vitest + build green.

  **NOT done (S2, unchanged scope):** pptx + pdf writers (`reportlab` is S2's T2.2 and is
  deliberately NOT added here), `deck_create`, and the knowledge round-trip export.
  `deck_from_markdown` IS implemented and tested — the parser landed with the markup
  module since it shares its machinery; only the pptx writer and the tool are outstanding.

- [2026-07-29][S2] **DONE (T2.1–T2.4).** pptx + pdf writers, `deck_create`, and the
  round-trip export. All four formats now generate: **docx, xlsx, pptx, pdf.**

  **T2.2 dependency, per the owner ruling:** `reportlab>=4,<5` added to core
  `dependencies` (not an extra), with the WHY comment matching the neighbouring
  reader block's style. Confirmed it is **not** in `personalclaw-backend.spec`'s excludes
  (unlike faiss/torch), so the desktop bundle keeps PDF. **`uv.lock` re-locked in the SAME
  commit** and `uv sync --locked --extra dev` verified green — a stale lock is the failure
  that reddened 7 PRs on a previous sprint. `pdf` is therefore unconditionally in
  `available_formats()`, which is the point of making it core: a format that exists on
  only some installs is one the agent offers and then fails to deliver.

  **THREE BUGS FOUND BY RUNNING IT, all mine, none reachable from unit tests alone:**
  1. **Every pptx slide title was overwritten by its first bullet.** `_body_placeholder`
     excluded the title via `shape is slide.shapes.title` — but **python-pptx returns a NEW
     proxy object on each `shapes.title` access**, so the identity check was False even for
     the title placeholder itself. Measured directly (`is_title=False` for the TITLE-typed
     placeholder), then fixed by comparing `placeholder_format.idx`. Caught because the
     round-trip showed "Slide 2: Revenue up 18%" where "Where we are" belonged.
  2. **Every PDF bullet extracted as the literal string `(cid:127)`.** reportlab's default
     bullet is ZapfDingbats char 127, whose CID has no unicode mapping — so any generated
     PDF later ingested or searched carried that garbage instead of a bullet. Measured four
     variants: `bulletFontName="Helvetica"` and an explicit `"•"` **both still produce
     (cid:127)**; only `start="-"` extracts as `-`. A hyphen list is a fair visual trade for
     text that isn't corrupt.
  3. **The round-trip printed the title twice.** Passing the source item's name as `title`
     while its body already opened with an `# H1` produced both, because the markdown parser
     promotes a leading H1 to the title itself. Now the name is only used when the body
     doesn't already start with one.

  **T2.4 reuses the writer path rather than adding an endpoint,** as the plan requires: a
  `source` argument on `document_create` resolves a knowledge-item id or a TEXT artifact
  slug to markdown, which then flows through the identical writer. So a document exported
  from the library is not a second-class citizen with its own bugs. Knowledge is tried
  first (uuid ids vs kebab-case slugs don't realistically collide), and a **binary**
  artifact is refused — its `content` is a raw URL, so exporting one would write the URL
  into the document body.

  **T2.3** was already satisfied by S1 for pdf/csv (`kinds:` added to the EXISTING types,
  no duplicates); this session adds the `pptx` content type, reusing `OfficeDocPreview`.

  **Validated as a user** on an isolated dev home, driving the tools directly and then
  opening every output in an independent application:
  - `document_formats` → **docx, pdf, pptx, xlsx**
  - `file(1)` → `Microsoft OOXML` (pptx, docx), `PDF document, version 1.4`
  - **Quick Look** rendered the deck as a real 4:3 title slide and the PDF with a centered
    bold title, body text and hyphen bullets
  - **Apple `textutil`** read the round-tripped docx correctly, and confirmed the
    duplicate-title fix (title appears once)
  - our own pptx reader recovered titles, bodies **and speaker notes**; the pdfplumber
    reader recovered headings, both list kinds, the table, code and the page break
  - the bad-source refusal names what to pass instead

  Tests: 43 total in `tests/test_documents.py` (13 new for S2) — pptx title/body/notes
  round trip, the placeholder-identity regression, empty-body slides, the deck image
  reference, pdf availability, the pdfplumber round trip, the `(cid:` assertion, markup
  escaping, the round-trip export, and the binary-export refusal. Gate: `make lint` green ·
  `make test` **8985 passed** · web typecheck + 283 vitest + build green · `uv sync
  --locked` green.

  **DOCUMENT-HANDLING-TOOLS is now COMPLETE** (S1 + S2). Deliberately out of scope and
  unchanged: templates, themes, native OOXML charts, and full-fidelity editing (the owner's
  separate WYSIWYG plan).
