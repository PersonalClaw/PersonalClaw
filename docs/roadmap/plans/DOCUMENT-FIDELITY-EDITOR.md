# DOCUMENT-FIDELITY-EDITOR

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/DFE.md`](../atomic/DFE.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Document Fidelity Editor — Editing a Real Document, With Layout Control

**Status:** DESIGNED — created 2026-07-30 (owner ask, 2026-07-29: a WYSIWYG editor as a
SEPARATE plan — "Sure. We can do it in separate plan but not skip it"; owner ruling on scope,
2026-07-29: **"Full fidelity editor with layout control"** — fonts, spacing, page layout, tables)
**Created:** 2026-07-30
**Wave:** 4 (S1: the styled model + a real docx reader; S2: the binary write path + the editing
surface; S3: layout control + page setup; S4: sheets and decks)
**Depends on:** DOCUMENT-HANDLING-TOOLS (DONE — owns the model, the four writers, and the
generate path this plan makes editable). Coordinates with DESIGN-SYSTEM-CONSISTENCY (the editor
is a flagship surface and must adopt primitives, not hand-roll), ARTIFACT-* surfaces (the editor
mounts in the artifact viewer and saves through the artifact version model), CONTEXT-ECONOMY
(document bytes must never enter a prompt — unchanged by this plan).

**Scope:** Make a generated document **editable in place, with visual fidelity and layout
control**, and make the edit round-trip without loss. Today a docx/xlsx/pptx artifact is
structurally read-only: it renders as extracted text plus a Download button, and there is no
route that accepts document bytes. This plan adds (a) style-carrying fields to the document
model, (b) the first real docx→model **parser** (every existing read path produces text or
markdown, never a model), (c) a binary artifact write endpoint, (d) an editing surface that is
not Monaco, and (e) explicit page/section control. It deliberately does NOT add collaborative
editing (see §7) or server-side page rasterization (see §6).

**Soul guardrails:**
1. **The model stays the source of truth, and stays vendor-neutral.** Style fields are neutral
   names (`bold`, `align`, `page_size`), never OOXML vocabulary. `documents/model.py`'s
   docstring rule — "No OOXML vocabulary lives here" — survives this plan. Vendor translation
   stays inside `documents/writers/`.
2. **Round-trip or refuse.** An edit path that silently drops what it cannot represent is worse
   than no edit path: the user sees their document "saved" and loses a table. Every session
   below ships its loss-report surface in the SAME change as its parser, and the editor tells
   the user what it could not preserve BEFORE they commit.
3. **Fidelity is honest, not implied.** Where the editor cannot render exactly what Word will,
   it says so. The existing `OfficeDocPreview` already sets this precedent ("text preview —
   download for full formatting") and the plan keeps that discipline as the editor's fidelity
   improves.
4. **No new heavy dependency without an owner ruling.** `lxml` (already present via python-docx)
   is the highest-fidelity tool on hand. LibreOffice/pandoc conversion was already REJECTED by
   the owner for the PDF writer on the grounds that "a documented capability that only works
   where LibreOffice is installed is worse than one that doesn't exist"
   (`documents/writers/pdf_writer.py:3-7`) — that ruling binds this plan too.
5. **Writers stay pure.** `Writer = Callable[[object], bytes]` with no I/O
   (`documents/registry.py:16-18`). A style-carrying model flows through the existing seam
   without changing its shape.

Class **B** (persisted artifact bytes + a new model shape read back from disk), so this
lands as a **plain clean break under the pre-1.0 banner** (tolerant
reads, no gate/migration; CHANGELOG entry; advise `personalclaw snapshot` in release notes).

---

## Context (code recon, 2026-07-30 — verified against code, every claim has a citation)

### What exists and is reusable

- **The model + four writers, complete and pure.** `documents/model.py` (76 lines):
  `Block`/`DocumentModel`/`SheetModel`/`Slide`/`DeckModel`; `BLOCK_KINDS` is exactly
  `("heading","paragraph","bullets","numbered","table","image","pagebreak","code")`
  (`model.py:14-23`), validated in `Block.__post_init__` (`:39-42`).
  `documents/registry.py` + `writers/{docx,xlsx,pptx,pdf}_writer.py`.
- **The artifact version model is already what an editor needs.** `list_versions`
  (`artifacts/native.py:752`), FIFO prune at `MAX_VERSIONS = 50`, a per-artifact event timeline
  (`created|edited|iterated|referenced|reverted`), and a kind-agnostic server-side `revert`
  that restores as a NEW version (`native.py:541-598`). `update_binary`
  (`native.py:503-539`) already bumps + snapshots. **Nothing new is needed for history.**
- **Version diffing exists, frontend-only.** `web/src/pages/artifacts/ArtifactCompare.tsx` —
  Monaco `DiffEditor` for text kinds, a side-by-side `<img>` pair for binary kinds
  (`:124-141`). It *replaces* the viewer body rather than sitting beside it — the precedent for
  how the editor should mount.
- **Two editor libraries are already bundled.** Monaco (`monaco-editor ^0.55.1`,
  `@monaco-editor/react ^4.7.0`) wired CSP-clean to the local bundle in
  `web/src/app/monacoSetup.ts`, and **CodeMirror 6** (`@codemirror/{state,view,language}`,
  `lang-markdown`), used for the live-markdown composer (`web/src/ui/composer/MarkdownInput.tsx`,
  `liveMarkdown.ts`). **No tiptap/prosemirror/slate/lexical/quill in any package.json** — the
  editing surface is a genuine build-or-adopt decision (§S2, owner task 2).
- **`ContentSurface` is the one render/edit surface** (`web/src/ui/content/ContentSurface.tsx`)
  with preview↔split↔edit, draft/dirty state, scroll-sync, and a host `onSave`. The artifact
  viewer delegates to it entirely (`ArtifactViewer.tsx:87`, `:217-228`).
- **`Document.iter_inner_content()` EXISTS in the installed python-docx 1.2.0** (measured, both
  on `Document` and `_Cell`). This matters: `knowledge/readers.py:243-247` documents that
  paragraph↔table ordering is unobtainable "without walking the underlying XML body". **That
  comment is stale relative to the installed version** — the parser in §S1 gets correct
  document order for free, and should fix that comment as it goes.
- **python-docx exposes everything the owner's ruling asks for** (measured on 1.2.0):
  run-level `font.{name,size,bold,italic,underline,color,highlight_color,strike,
  small_caps,subscript,superscript,...}`; `ParagraphFormat.{alignment,first_line_indent,
  left_indent,right_indent,line_spacing,space_before,space_after,keep_together,
  keep_with_next,page_break_before,tab_stops,widow_control}`; `Section.{page_width,page_height,
  orientation,left/right/top/bottom_margin,gutter,header,footer,...}`; `Table`/`_Cell`
  (`merge`, `grid_span`, `width`, `vertical_alignment`, nested `add_table`).

### The obstacles (each verified; each addressed by a numbered contract)

1. **The model carries ZERO style.** Exhaustively: the only style-adjacent field in the entire
   model is `Block.level` — a heading level, which is semantic, not visual. `from_markup.py`
   actively **deletes** inline formatting: `_strip_inline` (`from_markup.py:31-42`) removes
   `**bold**`, `*italic*`, `` `code` `` and link targets, with a docstring saying so
   deliberately. There is no run/span concept — `Block.text` is a flat `str`, and table cells
   are `list[list[str]]`, so a cell cannot hold a paragraph, a list, or a typed number.
   Addressed by **§C1**.
2. **There is no docx→model parser anywhere.** Both read paths produce text/markdown:
   `knowledge/readers.py::_read_docx` (`:217-260`) emits markdown and drops all run formatting,
   paragraph formatting, sections, images, hyperlink targets, list markers, and merge spans;
   `doc_parser.py::_extract_docx` (`:124`) is a stdlib zip+XML text extractor with no tables at
   all. Editing requires a real parser. Addressed by **§C2**.
3. **No HTTP route accepts binary artifact bytes.** `PATCH /api/artifacts/{slug}` reads
   `body.get("content")` — a JSON string (`artifacts/handlers.py:229-241`), with the comment
   "the FE only holds a raw-URL ref" (`:213`). `update_binary` exists in-process only. A save
   from the editor has nowhere to go. Addressed by **§C3**.
4. **`ContentSurface.renderEditor` hardcodes Monaco** (`ContentSurface.tsx:182-191`), and
   `EditCapability` (`contentTypes.ts:30-37`) carries only `{language, split?}` — no renderer
   slot. Office types register `binary: true` with **no `edit` capability at all**
   (`registerBuiltins.ts:192-217`), and `editable` is gated on `isEditable(type)`
   (`ContentSurface.tsx:105`), so an office artifact is structurally read-only. Addressed by
   **§C4**.
5. **Office artifacts are mislabelled and mis-rendered in the library TODAY.** `ARTIFACT_KINDS`
   (`web/src/pages/files/fileMeta.ts:164-175`) and the `ArtifactKind` union
   (`web/src/lib/api.ts:1387`) both **omit docx/xlsx/pptx/pdf/csv/video**, so
   `artifactKindMeta('docx')` falls through to `ARTIFACT_KINDS[0]` = **"Widget"**
   (`fileMeta.ts:178`) — a generated Word document shows a Widget icon, is labelled "Widget" in
   the viewer header, and **cannot be filtered for**. Worse, `ArtifactCard` treats every
   `binary` kind as an image (`ArtifactCard.tsx:126`, `:166`), so an office artifact renders a
   **broken `<img>`** in the grid. And `PdfFilePreview` resolves its URL from `path` only
   (`renderers.tsx:139`), so a **pdf artifact never previews at all**. These are pre-existing
   defects from DOCUMENT-HANDLING-TOOLS, not this plan's doing — but they are on this plan's
   surface and it fixes them first (**§S0**), because an editor reached through a broken card
   labelled "Widget" is not a finished feature.

### Honest limits (state these in the PR; do not oversell)

- **"Full fidelity" means round-trip fidelity for the constructs the model represents, not
  pixel-identical rendering.** Without a rasterizer there is no way to show exactly what Word
  will draw. §S3's page-geometry preview is a faithful *approximation* driven by real section
  metrics, and it must be labelled as one.
- **No LibreOffice, no rasterization, no CRDT** — verified absent (§6, §7). Server-side page
  images and concurrent editing are both greenfield and both out of scope.
- **A document not generated by PersonalClaw will round-trip imperfectly**, because the model
  cannot represent every OOXML construct. §C2's loss report is what makes that honest rather
  than silent; §C5 makes it non-destructive.

---

## Design

- **S0 — fix the surface the editor lands on.** Register the six missing artifact kinds so an
  office document is labelled and filterable; stop `ArtifactCard` rendering non-image binaries
  as `<img>`; make a pdf artifact preview from its raw URL. Small, independent, and a
  prerequisite for the rest being reachable.
- **S1 — the styled model + the first real docx parser.** `Block` gains inline **runs** and
  paragraph-level style; `documents/parsers/docx_parser.py` reads a .docx into a
  `DocumentModel` using `iter_inner_content()` for true order, and returns a structured
  **loss report** of everything it could not represent. The docx writer learns to emit the new
  fields. Round-trip is proven by parse→write→parse equality, not asserted.
- **S2 — the binary write path + the editing surface.** `PUT /api/artifacts/{slug}/raw`
  accepts document bytes (multipart), version-checked; `EditCapability` gains a renderer slot
  so a non-Monaco editor can mount through the registry; the editor edits the MODEL (a
  structured document tree), and saving re-renders through the existing pure writer. Nothing
  edits OOXML in the browser.
- **S3 — layout control.** Page size, orientation, margins, headers/footers, and paragraph
  spacing/indentation/alignment become first-class model fields with editor controls, plus a
  page-geometry preview that shows real margins and page breaks.
- **S4 — sheets and decks.** The same three moves (styled model → parser → editor) for xlsx
  (per-cell formats, column widths, merges, formulas-as-formulas) and pptx (layouts,
  per-shape geometry). Deliberately last: it is the same pattern twice more, and the docx path
  proves the pattern first.
- **What this is NOT:** not collaborative/concurrent editing (§7); not server-side page
  rasterization (§6); not a template or theme system (DOCUMENT-HANDLING-TOOLS deferred those
  and this plan does not pick them up); not tracked changes, comments, footnotes, or a TOC
  (each is a plausible follow-up, none is in the owner's stated scope); not an OOXML editor —
  the model remains the thing being edited.

---

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Style-carrying model (`documents/model.py`, additive)

```python
@dataclass
class Run:
    """One styled span of text. The model's first inline construct.

    Neutral vocabulary only — `bold`, not `w:b`. A writer translates; the model never
    knows what OOXML calls this.
    """
    text: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False            # monospace span
    font: str = ""                # "" = inherit the document default
    size_pt: float = 0.0          # 0 = inherit
    color: str = ""               # "" = inherit; else #RRGGBB
    link: str = ""                # "" = not a link; else the href

@dataclass
class ParagraphStyle:
    align: str = ""               # "" | left | center | right | justify
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    line_spacing: float = 0.0     # 0 = inherit; else a multiple (1.5 = 150%)
    indent_left_pt: float = 0.0
    indent_right_pt: float = 0.0
    first_line_indent_pt: float = 0.0
    keep_with_next: bool = False

@dataclass
class PageSetup:
    size: str = "letter"          # letter | a4 | legal | tabloid
    orientation: str = "portrait" # portrait | landscape
    margin_pt: dict[str, float] = field(default_factory=dict)   # top/bottom/left/right
    header_text: str = ""
    footer_text: str = ""
    page_numbers: bool = False
```

Additive changes to the EXISTING dataclasses, every one defaulted so today's callers are
unchanged:

```python
# Block gains:
runs: list[Run] = field(default_factory=list)   # when non-empty, SUPERSEDES `text`
style: ParagraphStyle | None = None
cells: list[list["Cell"]] = field(default_factory=list)  # table: supersedes `rows`
image_width_pt: float = 0.0
image_alt: str = ""

# DocumentModel gains:
page: PageSetup | None = None
default_font: str = ""
default_size_pt: float = 0.0
```

Three clauses an executor must honor:
- **`text` and `runs` must not disagree.** `runs` non-empty wins; `Block.__post_init__` derives
  `text` from `runs` (concatenated) whenever `runs` is set, so **every existing reader of
  `.text` keeps working unchanged**. That derivation is the compatibility contract — do not
  make callers check which field is populated. Same rule for `rows` ← `cells`.
- **`BLOCK_KINDS` does not grow in S1.** Styling an existing kind is not a new kind. (S4 may
  add `chart`; that is its decision, not this contract's.)
- **A `Cell` holds blocks, not a string:** `Cell(blocks: list[Block], colspan: int = 1,
  rowspan: int = 1, align: str = "", shading: str = "")`. This is what lets a table cell carry
  a paragraph or a list, which `list[list[str]]` structurally cannot — and it is why `cells`
  is a new field rather than a widening of `rows`.

### C2 — The docx parser + loss report (`documents/parsers/docx_parser.py`, new)

```python
@dataclass
class LossReport:
    """What the parser could not represent, for the user's eyes BEFORE they commit.

    `items` is a list of human-readable strings ("2 footnotes", "tracked changes",
    "1 embedded chart"), not a machine taxonomy: its consumer is a person deciding
    whether to edit this document here or in Word.
    """
    items: list[str] = field(default_factory=list)
    lossless: bool = True

def parse_docx(data: bytes) -> tuple[DocumentModel, LossReport]:
    """A .docx → (model, what-was-lost). Never raises on a valid docx.

    Uses `Document.iter_inner_content()` for TRUE paragraph↔table order — the installed
    python-docx 1.2.0 has it, which is what makes an ordered parse possible at all
    (knowledge/readers.py:243-247's "not without walking the XML" comment predates it).
    """
```

Rules:
- **Detect-and-report, never drop-and-shrug.** Footnotes, comments, tracked changes, embedded
  charts/objects, headers/footers beyond plain text, text boxes, and section-level features the
  model has no field for each add a `LossReport` item. A construct that is silently dropped
  without a report is a bug, and the test suite asserts the report for each.
- **Images become `artifact_slug` references, not inline bytes.** The parser extracts each
  embedded image, stores it via the artifact provider, and references it — matching how the
  model already treats images and keeping document bytes out of the model.
- Never trust document input: this is untrusted binary. Reuse the existing zip-bomb defenses'
  posture (`doc_parser.py:25-26`, `_safe_decompress` at `:90`) rather than inventing new caps,
  and cap parsed block count.

### C3 — The binary artifact write path (`artifacts/handlers.py`)

```python
# NEW: PUT /api/artifacts/{slug}/raw   (multipart/form-data: file=<bytes>)
#   → 200 {slug, version, mime}
#   Always bumps a version and snapshots — update_binary has no non-snapshotting mode,
#   because a binary body has no held-back draft state.
#   Guards, all required:
#     * kind must be in BINARY_KINDS (else 409 — this is not a text-artifact path)
#     * MAX_BINARY_CONTENT_BYTES (16 MiB) enforced BEFORE the body is buffered
#     * mime must match the artifact's declared kind (a .docx slug cannot be handed a pdf)
#     * `If-Match: <version>` REQUIRED → 409 on mismatch (see below)
#     * SEL audit row on every accepted write, with the byte count
```

**The version check is not optional, and it is the whole concurrency story.** There is no
CRDT, no OT, and no ETag anywhere in the artifact store today — the provider holds one coarse
`threading.RLock` (`native.py:64`) and last-write-wins. An editor that can save a whole
document therefore MUST be able to detect that the artifact moved underneath it, or two tabs
silently destroy each other's work. `If-Match` on the version the editor loaded is the cheap,
honest answer: it turns a silent loss into a "this document changed — reload" refusal. The
existing `live_dirty` badge (`native.py:306-310`) is the UI precedent for surfacing exactly
that state.

### C4 — Mounting a non-Monaco editor (`web/src/ui/content/`)

```ts
// contentTypes.ts — EditCapability gains a renderer slot (additive, optional):
export interface EditCapability {
  language: string
  split?: boolean
  /** A custom editor. When present, ContentSurface renders THIS instead of Monaco.
   *  Absent = today's Monaco path, unchanged for every existing type. */
  render?: React.ComponentType<DocumentEditorProps>
}

export interface DocumentEditorProps {
  slug: string
  version: number
  model: DocumentModelJson        // the parsed model, NOT bytes
  loss: LossReport
  onDirty: (dirty: boolean) => void
  onSave: (model: DocumentModelJson) => Promise<void>
}
```

`ContentSurface.renderEditor` (`ContentSurface.tsx:182-191`) branches on `type.edit?.render`
and falls through to Monaco otherwise, so **no existing content type changes behavior**. The
office types then declare an `edit` capability with that renderer
(`registerBuiltins.ts:192-217`), which is what flips `isEditable` and gives them a Save button
for the first time.

**The editor edits the MODEL and never the bytes.** Saving posts the model to a re-render
endpoint (which reuses the existing pure writer) and the resulting bytes go to §C3. The browser
never constructs OOXML — the same provider-boundary reasoning that keeps vendor format strings
inside `documents/writers/` applies to the frontend.

### C5 — The lossy-edit contract (the guardrail made mechanical)

A document whose `LossReport` is non-empty is **still editable**, but:
1. The editor shows the report before the first edit, naming what will not survive.
2. Saving requires an explicit confirm that repeats it — the existing `confirm`/`confirmDelete`
   dialog primitives (`web/src/ui/dialog/`) are the mechanism; do not invent a new one.
3. **The pre-edit version is never overwritten.** Because every save bumps a version and
   snapshots (§C3), the original is always one `revert` away. That is what makes a lossy edit
   recoverable rather than destructive, and it is why this plan needs no new backup concept.

### C6 — Config (§2.1 five-point wiring)

```python
# config/loader.py, documents section (new, or the nearest existing sibling):
document_editing: bool = field(
    default=False,
    metadata=_meta("Edit documents in place",
                   "Open generated Word documents in an editor instead of download-only. "
                   "Editing re-renders the file, so constructs PersonalClaw can't represent "
                   "are lost — it tells you which before you save, and the previous version "
                   "is always one revert away."),
)
```
Wire through all five points: dataclass + `_meta`; `load()`'s explicit mapping (a plain
`bool(...)` read defaulting False — **not** `_guard_flag`, which fails ON and would enable a
lossy edit path for every existing user on upgrade); `to_dict()`; the `_EDITABLE_CONFIG` PATCH
allowlist; and a Settings control. Off by default while fidelity is proving itself.

### Integration points

- **Calls:** `documents.get_writer` (unchanged, pure), the artifact provider's `update_binary`
  / `list_versions` / `revert`, `identity.current_username()` for the event actor,
  `security.redact_credentials` on any extracted text that reaches a prompt or a log.
- **Called by:** `ArtifactViewer` (via `ContentSurface`), the artifact library card/grid.
- **Storage owned:** none new. Edits are artifact versions in the existing on-disk layout
  (`<root>/<slug>/versions/vN.<ext>`).
- **Deliberately NOT touched:** `knowledge/readers.py` and `doc_parser.py` (both stay
  text/markdown extractors for ingestion and preview — the new parser is a third, structured
  reader with a different job, and conflating them would break knowledge ingestion);
  `exporters.ts` (client-side exports stay as-is, per DOCUMENT-HANDLING-TOOLS' own scope note).

---

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 0 — Make the surface reachable (independent, small, ships alone)

| ID | Task | Files | Done when |
|---|---|---|---|
| T0.1 | Register the six missing artifact kinds (`docx`,`xlsx`,`pptx`,`pdf`,`csv`,`video`) in `ARTIFACT_KINDS` + the `ArtifactKind` union, each with its own icon + label | `web/src/pages/files/fileMeta.ts`, `web/src/lib/api.ts` | a docx artifact reads "Word document", not "Widget"; it is filterable in the library toolbar |
| T0.2 | Stop treating every `binary` kind as an image: `ArtifactCard` renders a kind icon for non-image binaries instead of a broken `<img>` | `web/src/pages/artifacts/ArtifactCard.tsx` | an office artifact's card shows an icon; an image artifact still shows its thumbnail (test) |
| T0.3 | Make a pdf ARTIFACT preview: resolve the raw URL from the artifact, not from a file `path` | `web/src/ui/content/renderers.tsx` | a generated pdf artifact previews inline; a pdf FILE still previews (both asserted) |
| V0 | Validation as a user: generate one of each of the four formats, open the library, confirm labels/icons/filters/previews. Full local gate | — | holds |

### Session 1 — The styled model + the docx parser

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | §C1 model additions (`Run`, `ParagraphStyle`, `PageSetup`, `Cell`, the new `Block`/`DocumentModel` fields) with the `text`←`runs` and `rows`←`cells` derivation in `__post_init__` | `documents/model.py`, tests | every existing model test passes untouched; a runs-only Block still answers `.text`; a cells-only table still answers `.rows` |
| T1.2 | Teach `from_markup` to PRODUCE runs instead of stripping them: `**bold**`, `*italic*`, `` `code` ``, and `[text](url)` become `Run` fields | `documents/from_markup.py`, tests | markdown with inline formatting round-trips through the model; the old `_strip_inline` behavior is gone (not left beside it) |
| T1.3 | Docx writer emits the new fields: runs, paragraph style, cell spans, image width/alt | `documents/writers/docx_writer.py`, tests | a bold run is bold in the output (asserted via python-docx read-back, not by trusting the call) |
| T1.4 | §C2 `documents/parsers/docx_parser.py`: `parse_docx` via `iter_inner_content()`, with the `LossReport`. Fix the stale ordering comment at `knowledge/readers.py:243-247` in the same change | `documents/parsers/docx_parser.py`, `knowledge/readers.py`, tests | paragraph↔table ORDER is preserved (test with an interleaved fixture); each unrepresentable construct produces a report item |
| T1.5 | **The round-trip proof:** parse→write→parse yields an equal model, for a fixture covering every `BLOCK_KIND` plus runs, styles, spans, and page setup | tests | model equality holds; a deliberate regression (drop a field in the writer) fails the test |
| V1 | Validation as a user: generate a document with the tools, parse it back, diff the models, and confirm a Word-authored fixture reports its losses honestly. Full local gate | — | holds |

### Session 2 — The binary write path + the editing surface

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | §C3 `PUT /api/artifacts/{slug}/raw` with every guard incl. **required `If-Match`**; SEL audit; regenerate the offline route reference | `artifacts/handlers.py`, `dashboard/server.py`, `src/personalclaw/reference/`, tests | a stale `If-Match` is refused 409; an oversized body is refused before buffering; a mime/kind mismatch is refused; the accepted write bumps exactly one version |
| T2.2 | A model read/render pair for the editor: `GET /api/artifacts/{slug}/model` (parsed model + loss report) and a render endpoint that re-renders a posted model through the existing writer | `artifacts/handlers.py`, tests | the round trip is server-side; the browser never sees OOXML |
| T2.3 | §C4 `EditCapability.render` + the `ContentSurface.renderEditor` branch; office types declare the capability | `web/src/ui/content/contentTypes.ts`, `ContentSurface.tsx`, `registerBuiltins.ts`, tests | every EXISTING content type still renders Monaco (asserted); an office type mounts the new editor |
| T2.4 | The editor itself: a structured document editor over the model — block list, inline run formatting (bold/italic/underline/code/link), table cell editing. Adopt `ui/` primitives (the primitive-adoption ratchet will reject raw `<button>`/`<input>`) | `web/src/pages/artifacts/` (or `ui/content/`), `.doc.ts` for any exported `ui/` component, tests | a user can bold a word and save; the file opens in Word with that word bold (validated by download + read-back) |
| T2.5 | §C5 the lossy-edit contract: report shown before editing, repeated in a save confirm, and the pre-edit version recoverable via the existing `revert` | editor + `web/src/ui/dialog/` call sites, tests | a lossy document warns before and at save; revert restores the pre-edit bytes exactly (byte-compare) |
| T2.6 | §C6 config `document_editing` through all five points + the Settings control | `config/loader.py`, `dashboard/handlers/core.py`, `web/src/pages/settings/`, tests | `test_config_roundtrip` green; off ⇒ office artifacts are exactly today's read-only preview |
| V2 | Validation as a user: generate → edit → save → download → open in a real Word/Pages/LibreOffice reader and confirm the edit survived; two tabs racing produces a 409, not a silent loss; toggling the config off restores download-only. Full local gate | — | holds |

### Session 3 — Layout control

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Page setup end-to-end: size, orientation, margins through model → docx writer → parser → editor controls | model, `docx_writer.py`, `docx_parser.py`, editor, tests | A4 landscape with 2cm margins round-trips and is correct when read back by python-docx |
| T3.2 | Paragraph layout controls: alignment, space before/after, line spacing, indents, keep-with-next | writer, parser, editor, tests | each round-trips; the editor's controls reflect the loaded document's real values, not defaults |
| T3.3 | Headers/footers + page numbers (plain text scope, per §C1) | writer, parser, editor, tests | a header round-trips; a header the model can't represent is reported, not dropped |
| T3.4 | Page-geometry preview: real margins and page-break positions, **labelled an approximation** (no rasterizer exists — see §6) | editor, tests | the preview reflects the configured page size/margins; the label states it is an approximation |
| V3 | Validation as a user: set up a document's page layout in the editor, download, and confirm the geometry in a real reader. Full local gate | — | holds |

### Session 4 — Sheets and decks (the same pattern, twice)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | `SheetModel` gains per-cell format (font/fill/number-format/alignment), column widths, merges, and **formulas as formulas**; xlsx writer + a new xlsx parser | model, `xlsx_writer.py`, `parsers/xlsx_parser.py`, tests | a formula stays a formula through the round trip (today `"=SUM(A1)"` is written as a string) |
| T4.2 | The sheet editor (grid) over the model | editor, tests | a cell edit + a number format survive download/read-back |
| T4.3 | `DeckModel`/`Slide` gain layout selection, per-shape geometry, and bullet levels; pptx writer + parser | model, `pptx_writer.py`, `parsers/pptx_parser.py`, tests | bullet depth round-trips (the writer hardcodes `level = 0` today) |
| T4.4 | The deck editor (slide list + per-slide fields) | editor, tests | a slide edit survives download/read-back |
| V4 | Validation as a user: full lifecycle on a spreadsheet and a deck. Full local gate | — | holds |

## Owner tasks (real world)
1. **Confirm the round-trip-fidelity reading of "full fidelity"** (§Honest limits). Without a
   rasterizer, "full fidelity" can mean *no construct is lost through the edit*, which this plan
   delivers, or *pixel-identical to Word*, which needs a renderer this plan explicitly rejects
   as a dependency. The plan assumes the former; it is the one scope question that changes S3.
2. **Decide the editing-surface library** (T2.4). None of tiptap/prosemirror/slate/lexical is
   present, so this is a real dependency decision: (a) adopt a rich-text framework — fastest to
   a good editor, a significant new frontend dep; (b) build on the bundled **CodeMirror 6** —
   no new dep, and there is in-house precedent (`liveMarkdown.ts`), but a document editor is
   well beyond what that extension does; (c) hand-rolled controlled components over the block
   model — no new dep, most work, most control. **The plan does not choose**: it is a
   dependency-weight call, which is an owner call (E5).
3. **Confirm off-by-default** (§C6) while fidelity proves itself, and confirm the lossy-edit
   posture in §C5 (warn + confirm + always-revertable) is the right trade versus refusing to
   edit a lossy document at all.
4. **Sequencing:** this plan is Wave 4 and depends on nothing unbuilt, so it can start whenever
   — but S0 alone is a cheap product win (office artifacts stop reading "Widget") and could
   ship well before the rest.

## Risks & open questions
- **The round-trip is the whole plan, and it is the thing most likely to be quietly wrong.** A
  parser that loses a construct without reporting it turns "edit your document" into "lose part
  of your document". Mitigation is structural: T1.5's parse→write→parse equality test, a report
  item asserted for every unrepresentable construct, and §C5's always-revertable save. If the
  round trip cannot be made honest for a construct, the answer is a report item — never a
  silent drop.
- **Scope realism.** This is four sessions and the largest single frontend surface in the
  roadmap. S0/S1 are self-contained and valuable alone (S1 gives the model runs and the first
  real parser, which the *generate* path also benefits from). If the editor stalls at S2, S0+S1
  still ship something coherent.
- **The version race.** Whole-document saves make silent clobbering possible in a way
  line-based text edits did not. §C3's required `If-Match` is the mitigation; without it two
  tabs destroy each other. Do not make it optional "for convenience".
- **Editing a document PersonalClaw did not generate** is where fidelity claims break down
  first. The loss report is the honest surface; V1 requires testing a Word-authored fixture, not
  only a self-generated one.
- **Open:** whether the editor should also become the surface for `markdown` artifacts (it would
  be a better editor than Monaco-with-preview for prose). Deferred — it widens the blast radius
  from "office kinds, off by default" to "the default markdown experience".
- **Open:** server-side page rasterization for a true preview. Needs a renderer (LibreOffice
  headless, or Playwright print-to-PDF — Playwright is already a `js-render` extra at
  `pyproject.toml:122`). The owner's standing rejection of "works only if LibreOffice is
  installed" applies; Playwright may be a different answer. Not in scope.
- **Open:** concurrent editing. Verified absent: no CRDT/OT/yjs/presence anywhere, and no
  optimistic-concurrency token on the write path. `If-Match` gives conflict *detection*, which
  is the honest floor. Real collaboration is a separate plan.

## Execution log

**2026-08-16 — `DFE-1` DONE** (Session 0, T0.1–T0.3 + Design bullet S0).

**DISCOVERY: T0.1 had already landed, and the plan's premise for it is stale.** §Reality
check item 5 says `ARTIFACT_KINDS` and the `ArtifactKind` union "both omit
docx/xlsx/pptx/pdf/csv/video". They have not since **#127** (2026-07-31,
`fix(artifacts): label generated documents by their real kind, not "Widget"`), which
registered all six, added `artifactKinds.test.ts` (both directions against the backend's
`ALLOWED_KINDS`, so the closed set cannot drift silently again), and thereby made the
kinds filterable — `ArtifactsSection` maps `ARTIFACT_KINDS` straight into the kind
`Segmented`, so presence in the table *is* filterability. T0.2 and T0.3 were both still
open exactly as described. No kind was added here; a `segmentedOverflow` ratchet pins
`ARTIFACT_KINDS` at 16 entries, and adding one would have reded it.

**DEVIATION (label wording).** done_when asks for the docx label to read
`'Word document'`; it reads **`Word`**. Left as-is deliberately: the kind strip already
renders 17 tabs at a measured 1152px (`segmentedOverflow.test.ts`), the neighbouring
labels are all short (`PDF`, `CSV`, `Spreadsheet`, `Slides`, `Video`), and lengthening one
buys nothing a user needs. The substantive clause — it names the real format and is never
`Widget` — is asserted (`artifactSurface.test.tsx`) and was confirmed in the browser.

**T0.2 — cards.** `ArtifactCard` had ONE flag (`isImage = !!ctype.binary`) doing two
jobs: "content is a URL ref, skip the body fetch" and "draw it as a thumbnail". Every
binary kind therefore got an image element pointed at its `/raw` bytes, and a browser
cannot decode OOXML, a pdf or a video as an image — so docx/xlsx/pptx/pdf/video all
rendered the failed-decode glyph. Split three ways (`isThumbnail` = the `image` type
alone · `isKindTile` = every other binary · `isExcerpt` = text), and a new `KindTile`
renders the kind's own icon and tone. **The narrowing is the trap**: `isThumbnail` alone
would have sent office artifacts down the *excerpt* path, printing the literal string
`/api/artifacts/<slug>/raw` as if it were the document's text — so the kind-tile branch
also skips the fetch, and a rail asserts the fetch count.

**T0.3 — pdf.** `PdfFilePreview` resolved from `path` alone and `PdfPreview` always ran
that path through `api.fileRawUrl`, so a generated pdf — which has no path — asked the
FILES endpoint for the empty path. Measured live: the old URL shape
(`/api/file-raw?path=&resolve=1`) returns **400**. `PdfPreview` now takes `{path?, src?}`,
the same contract `ImagePreview` already had for exactly this reason, and
`PdfFilePreview` resolves artifact-ref-or-path. Both halves are asserted; the FILE half is
the regression rail, since it was the only half that ever worked.

**Closed-set decision (the "Widget" class, not just its instance).** `artifactKindMeta`
fell back to `ARTIFACT_KINDS[0]` = `widget`, so an unmapped kind *impersonated a real
one* — which is why the original defect was silent for four releases. It now returns a
separate `UNKNOWN_ARTIFACT_KIND` (`key: ''`, label `Unknown kind`) that is deliberately
not a table entry, so it can never appear as a filter option and can never be mistaken
for a registered kind. The closed set stays enforced at test time; this only changes what
the runtime *says* when it is wrong. Confirmed by mutation: deleting the `docx` row now
fails with `expected 'Unknown kind' to match /word/i` instead of quietly reading
"Widget".

**V0 gate.** `make lint` clean (mypy 886 files) · full web suite **315 files / 3259
tests** green (includes the `segmentedOverflow` and `consistencyAudit` ratchets) ·
`typecheck` + `build` green · 9/9 `test_roadmap_dag_derived.py`. Three falsifications, each
confirmed applied before running: dropping the `docx` row → 4 reds; restoring
`isThumbnail = !!ctype.binary` → 5 reds on the icon rail while the image-thumbnail and
excerpt rails stayed green (so the rail discriminates); dropping `path` from
`PdfFilePreview` → only the pdf-FILE rail reds.

**As-a-user (port 10177, isolated home).** Generated a real docx/xlsx/pptx/pdf plus a png
and a markdown artifact. Seen with my own eyes: the docx card and viewer read
**"Word · v1"** (never "Widget"); the toolbar carries Word/Spreadsheet/Slides/PDF/Video
tabs and clicking **Word** filters to `?kind=docx` with one result; the office/pdf cards
show tinted kind icons with **zero** image elements while the png still thumbnails from
`/raw`; the markdown card still shows its excerpt. **Not seen as pixels:** the pdf's
inline page render — this automation profile will not embed a pdf in an `<object>`, and a
pdf FILE shows the identical browser fallback in the same session, so the limit is
environmental. What *was* verified in-browser for both sources: the object's `data` is the
correct URL and returns `200 application/pdf`.

- **2026-08-23 — `DFE-2` COMPLETE (all four clauses). Atom stays `todo` only because this code is
  unmerged**; flip it when the PR lands.
  A document model that cannot express bold makes every writer a lossy one. `_strip_inline` threw
  markdown formatting away at **seven** call sites (the brief said eight — the eighth occurrence was the
  definition itself), so `**bold**` reached the docx writer as the word "bold" and no file could carry it.
  **Clause 1 — every existing model test passes untouched.** `tests/test_documents.py` (48 tests) was
  never edited by any of the three agents; it passes on the integrated tree. That is the compatibility
  proof, not a claim.
  **Clause 2 — derivation.** `Block` gains `runs`/`cells`/`style` and `DocumentModel` gains `page`, all
  additive; `__post_init__` derives `text` from runs and `rows` from cells. Probed live:
  `Block(kind="paragraph", runs=[Run("a"), Run("b", bold=True)]).text == "ab"` and
  `Block(kind="table", cells=[[Cell(runs=[Run("H1")]), Cell(text="H2")]]).rows == [["H1", "H2"]]`.
  **When both are supplied the explicit value wins** — recomputing an author's `text` would make a
  deliberate override impossible to express. An unknown `align`/`orientation` **raises** rather than
  normalising to `""`: a typo like `"centre"` silently becoming "writer default" yields a file that looks
  plausible while discarding the layout the author asked for.
  **Clause 3 — one parser, and `_strip_inline` is GONE, measured.** The atom's own wording is "not left
  beside it", so the name appears nowhere in the package and a rail asserts that — with a vacuity guard
  pointed at a directory where the name is genuinely absent, which is exactly the false green a typo'd
  path produces. **The compatibility set was captured by RUNNING the old function on `origin/main` before
  deleting it:** 46 inputs, **40 byte-identical**, 6 divergences pinned in a table with both columns.
  **Five of those six ADD BACK characters the old function silently ate** — `2 * 3 * 4` → `2  3  4`,
  `snake_case_name` → `snakecasename`, `a_b_c_d` → `abc_d`. So the brief's premise that "`_strip_inline`
  never dropped any characters" was **false**, and the general rail is a subsequence property in both
  directions (old ⊆ new ⊆ raw) rather than a table lookup: nothing is dropped and nothing is invented.
  Two CommonMark rules keep prose from reading as markup (an opener is never followed by whitespace; `_`
  never opens inside a word). The sixth divergence is the atom's requirement — a code span is literal.
  **Clause 4 — the round-trip asserts the DOCUMENT, not the model.** A bold run is rendered to bytes,
  reopened with python-docx, and read back `bold is True`. Falsifying it (`bold = None`) reds 2 of 25;
  a test that stayed green there would have been asserting the model it was handed.
  **Hyperlinks are real** `w:hyperlink` relationships with `is_external=True` and an asserted `target_ref`,
  carrying explicit colour and underline rather than a `"Hyperlink"` character style a template need not
  define (a dangling style reference renders as prose). `0.0` on the spacing and margin fields means
  "writer default", never an explicit zero — inverting that would silently reformat every existing
  document, and a falsification pins it.
  **Backwards compatibility proved by differential.** A runs-less model covering all eight block kinds
  renders **byte-identical `word/document.xml`** through a transcription of the pre-DFE-2 dispatch and the
  new one, with identical zip part lists; the runs-less output contains no `w:rPr` or `w:pPr` at all.
  **DEPENDENCY FLOOR RAISED, and it is a content-loss fix rather than housekeeping.** `python-docx` moves
  from `>=0.8.6,<2` to `>=1.1,<2`. `Paragraph.text` only includes hyperlink display text from 1.1 onward
  (verified against the installed 1.2.0, whose own docstring states it) and `knowledge/readers.py:229`
  reads exactly that — so shipping clickable links while the declared floor allowed 0.8.x would leave
  **our own reader** silently dropping a link's words. `uv lock` churn is exactly one line, the specifier
  mirror.
  **Left for the next slice, recorded rather than half-built:** markdown table cells still become plain
  strings through the same parser. `Cell` now exists and `rows` derives from `cells`, so the remaining
  work is one line in `from_markup` plus its test — but the atom's clause is satisfied by the model's
  derivation, and widening `Block.rows`' own type would touch every writer, serializer and test that
  reads it.
  **DISCOVERY (pre-existing, outside this atom): `mcp_artifacts._document_create` swallows a writer
  exception into its outcome callback** rather than raising, so a writer failure presents to the caller as
  "no artifact appeared" instead of an error. It surfaced here as two `IndexError`s in
  `test_documents.py` that were one hop downstream of the real cause.

- **2026-08-24 — `DFE-2` VERIFIED MERGED, and the clean break finished where it had stopped short.**
  The entry above ended "flip it when the PR lands". It landed: `1dc5f1b7` is an ancestor of `origin/main`
  (`827751b9` at the start of this session, `9e0f727b` by the end — the train advanced under it, touching
  neither the documents package nor any file here), so `dag.json`'s `DFE-2: todo` is stale bookkeeping
  rather than open work. **Flipping it to `done` is left to the owner** — this session does not edit
  `dag.json`.
  Re-verified rather than taken on trust — 231 tests green across `test_documents.py`,
  `test_document_model_runs.py`, `test_markdown_inline_runs.py`, `test_docx_run_fidelity.py` — and each
  clause falsified by mutating the LIVE line: gating the runs→text derivation off reds 3, gating
  cells→rows off reds 2, `element.bold = None` in the docx writer reds 2 (including the python-docx
  read-back), and re-inserting the name into the package reds the clean-break rail. Clause 1 needs no
  argument: `1dc5f1b7` never touched `tests/test_documents.py`, which the commit's own file list shows.
  **The clean break was one file short of complete, and the rail's scope is why.** A repo-wide sweep for
  the deleted name found `web/src/pages/knowledge/readingOutline.ts:93` still citing
  ``documents/from_markup.py``'s `_strip_inline` — and citing it for behaviour that DFE-2 inverted, telling
  the next reader the backend still strips inline formatting when it now parses it into `Run`s. The
  existing rail scanned `src/personalclaw/documents/` only, so it could never see a frontend comment about
  a python function. **A dangling citation of a deleted function is how the old mental model comes back**,
  which is the thing "not left beside it" is actually protecting. Fixed the comment to say what the two
  sides now do differently, and REPLACED the narrow rail with one spanning both shipped trees
  (`src/personalclaw/**/*.py` + `web/src/**/*.{ts,tsx}`) rather than adding a second rail beside it — the
  wider python leg strictly subsumes the old one, so keeping both would have been the dual path the tenet
  forbids. `docs/` and the test itself stay out of scope on purpose: the compatibility table must keep
  naming `_strip_inline` for the deletion to remain auditable. Both legs falsified — restoring the
  original web citation reds it with "still cited under .../web/src", and re-pointing the web leg at a
  real, 963-file directory that lacks its needle trips the per-tree vacuity floor (breadth floor passes
  there, so only the vacuity guard can catch that shape).
  **Runtime import sweep, because `ignore_missing_imports` means mypy cannot catch a stranded first-party
  import:** 961 `personalclaw.*` modules imported and inspected, **0** still exposing a `_strip_inline`
  attribute; `from personalclaw.documents.from_markup import _strip_inline` raises `ImportError`; the sweep
  carries its own vacuity floor (the same inspection finds `parse_inline`). Grep agrees — 0 occurrences in
  `src/`, and after this change 0 in `web/src/`.
  **Clause 4 was proved against a DOUBLE, not the shipped model — a second gap, and the larger one.**
  `test_docx_run_fidelity.py` declares its own `_Run`/`_Style`/`_Cell`/`_Page` dataclasses and feeds those
  to the writer; its docstring defends this ("the writer only ever READS attributes off them"), and it is
  a reasonable way to pin the writer alone. But **no test in the repo imported `model.Run` at all**
  (`git grep 'from personalclaw.documents.model import' -- tests/` returns `BLOCK_KINDS, Block,
  DocumentModel` and nothing more), so nothing bound the doubles to the shape we ship, and nothing joined
  markup → model → bytes → read-back: the fidelity suite starts at a hand-built double, and the markup
  suite stops at the model. The seam BETWEEN the parser's `Run` and the writer that consumes it — the only
  place the atom's promise actually lives — had no coverage from either side.
  Closed with two additions, both measured against the pre-existing suites rather than asserted:
  · a **field-for-field parity rail** (names → default VALUES, so a rename, a drop and a changed default
    are all caught) binding each double to its dataclass, with a vacuity floor that constructs all three
    drift shapes and confirms the check condemns them while calling the real double current. Falsified by
    adding `strike: bool = False` to `model.Run`: the parity rail reds, and **the other 29 fidelity tests
    plus all 27 of `test_document_model_runs.py` stay GREEN** — the drift is invisible to every
    pre-existing test, which is the whole argument for the rail.
  · the **full-chain round trip**: `document_from_markdown` → `render_docx` → reopen → `bold is True`,
    asserting `type(...) is Run` so it cannot silently drift back onto a double. Falsified with a
    realistic precedence bug — letting `block.text` win over `block.runs` in the paragraph dispatch, which
    looks harmless until you notice `__post_init__` ALWAYS derives `text` from `runs`, so every genuinely
    parsed document loses all formatting. **It reds ONLY the new chain test (30 others green)**, because
    the doubles set `.runs` after construction and so never carry a derived `text`, and the markup suite
    does not import the writer at all. That is the defect class the doubles were structurally unable to
    see.
  The chain test also pins why the dependency floor moved: the link's words live in `w:hyperlink`, so
  `para.runs` cannot reach them and `para.text` is the only surface that proves nothing was dropped —
  and `.text` includes them only from python-docx 1.1 onward.
  **Still correctly out of scope** (unchanged from the entry above): `from_markup` emits plain strings for
  markdown table cells. The clause is about the model's `rows`←`cells` derivation, which holds; teaching
  the parser to emit `Cell`s is DFE-3 territory.
  **Brief correction:** the driving brief said the atom's `done_when` was truncated in the atom index. It
  is not — `dag.json`'s `DFE-2.done_when` is the complete four-clause sentence, and the brief quoted it
  verbatim. The clause list is exactly T1.1 + T1.2 + T1.3's "Done when" columns, and T1.4/T1.5 belong to
  `DFE-3`.

---

## Execution log — DFE-3 (docx->model parser + LossReport + round-trip) — **PARTIAL, atom stays `todo`**

- [2026-08-24][DFE-3] **Four clauses met; the V1 gate's Word-authored fixture is NOT, so the atom stays
  `todo`.** Shipped in PR #1984: `documents/docx_parser.py` (1073) with
  `parse_docx(bytes) -> (DocumentModel, LossReport)`, a 30-kind loss vocabulary, and three suites
  (`test_docx_parser.py` 958, `test_docx_roundtrip.py` 352, `test_docx_writer_coverage.py` 851/58 tests).
  Every writer convention is inverted explicitly: `Courier New` -> `Run(code=True)`, and the
  `0563C1` + underline pair -> `Run(link=...)` **only inside a `w:hyperlink`** (the same two values in
  prose are reported as a loss, otherwise every link this repo writes would read as lossy).
- [2026-08-24][DFE-3] 🔴 **UNMET — "a Word-authored fixture reporting its losses honestly".** No Word
  exists in this environment and no `.docx` is committed anywhere in the repo
  (`git ls-files | grep -i docx` returns only the writer and the DFE-2 fidelity suite). The fixture
  injects the constructs Word emits — footnote ref, comment ref, tracked insertion, bookmark, field, VML
  text box, nested table — as **raw XML, not a file Word saved**, and its docstring says so and lists
  what it therefore does not prove (`w:rsid*`, `mc:AlternateContent`, `w:proofErr`, theme fonts, Word's
  own `settings.xml`). **What would clear it: one owner-supplied `.docx` committed as a fixture.**
- [2026-08-24][DFE-3] **MEASURED — the writer drops nothing unconditionally, which changes what a
  LossReport is.** A census of all 27 model fields against emitted documents: **17 emitted, 10 partial,
  0 dropped**. Every loss is conditional — on a sibling field (`Block.text` beside `runs`, `Block.rows`
  beside `cells`, `Cell.text` beside `Cell.runs`), on the block kind (`Block.style` honoured on
  heading/paragraph/bullets/numbered/code, dropped on table/image/pagebreak), or on direction (bold and
  code can be switched on, never off). So the report is computed **per block**, not read off a static
  field list. The census also surfaced a contradiction already in the code: `Block.__post_init__`
  blesses an explicit `text` beside `runs` as *"an author's deliberate override... recomputing it would
  silently discard"*, and `docx_writer` then discards exactly that (`"" if block.runs else block.text`).
- [2026-08-24][DFE-3] **The sharpest finding: parse->write->parse idempotence is BLIND to a uniform
  writer regression.** If the writer stopped emitting bold, the round trip would remain perfectly
  stable. So the seven regression tests assert an `AssertionError` out of the *authored-model*
  comparison, not the stability one, and each asserts green **before** patching. Confirmed empirically
  and re-confirmed by the driver before push: breaking the document-order walk reds the recovery test
  and **leaves `test_parse_write_parse_is_stable` green**.
- [2026-08-24][DFE-3] **REAL PRE-EXISTING BUG, fixed here.** `w:numPr` is a child of `w:pPr`, never of
  `w:p`. Reading it off the paragraph element found nothing, so every **Word-authored** list parsed as
  plain paragraphs — masked because the writer's own `List Bullet`/`List Number` style path works.
  Numbering now resolves numId -> abstractNum -> `numFmt`; unresolvable falls back to `numbered`, not
  `bullets`, because stripping the numbers off a numbered list is the more visible wrong answer.
- [2026-08-24][DFE-3] **Two honest limits of the model, reported rather than hidden.**
  python-docx's default template has NON-UNIFORM margins (1.00in top/bottom, 1.25in left/right) which
  `PageSetup.margin_in` cannot hold, so *every* bare-template document legitimately reports one
  `page_property` item (`test_default_template_margins_are_reported` owns it; other tests filter it via a
  documented helper). Same family flags a non-Letter page size, since `PageSetup` carries orientation but
  no size — an A5 document re-renders as Letter.
- [2026-08-24][DFE-3] **`code` vs `paragraph` is genuinely ambiguous in this writer's output** — a `code`
  block and a paragraph whose every run is `code=True` render to identical OOXML. All-monospace is read
  as `code`; both readings are stable and lossless, and the choice is documented in the module docstring.
- [2026-08-24][DFE-3] **DISCOVERY — `documents/__init__.py` now overclaims.** Its docstring says no
  vendor file-format vocabulary appears outside `writers/`; this parser sits at
  `documents/docx_parser.py`. Left unedited (outside the atom's fence) and noted in the parser's own
  docstring instead; a `readers/` subpackage is the clean home once there is a second reader. Unrelated:
  a pre-existing `personalclaw/doc_parser.py` (plain-text extraction for knowledge ingestion) is close in
  name, different job, no collision.
- [2026-08-24][DFE-3] **Gate:** `make lint` clean (mypy, 996 source files) and **243 passed** across the
  six document suites, re-run by the driver on the combined tip rather than taken on report; rails green
  unmodified (structural baseline, docs lint, inert-surface, provider-boundary, apps-import-boundary,
  supply-chain — 96 + 21). No docs row is owed: the inert-surface census keys on config keys / Enum
  members / trigger kinds / `_EDITABLE_CONFIG` / SDK exports, and `_ENUM_BASES` is Enum-subclass only, so
  a plain `LOSS_KINDS` tuple is invisible to it. Falsifications: order walk broken -> 9 red; one loss kind
  suppressed -> 3 red; census positive row -> 3 red; census absence row -> 8 red, writer byte-identical
  after restore. Every mutation restored from a file copy, never `git checkout`.

- [2026-08-24][DFE-3] **VERIFICATION of the four clauses nobody had re-measured, each falsified against
  the LIVE source rather than argued.** Baseline on `origin/main` `8c8e5fe3`: 243 passed across the six
  document suites.
  · **Interleaved order — MET.** `test_docx_parser.py::test_interleaved_paragraph_and_table_order_is_preserved`
    already alternates **P,T,P,T,P** (`_interleaved_bytes`), not one-of-each, and
    `test_the_two_sequence_reading_would_reorder_that_fixture` is its vacuity floor: it computes the naive
    `doc.paragraphs + doc.tables` reading on the same bytes and asserts `naive != walked`. Falsified by
    sorting `_walk`'s children so tables come last → **9 red**, and `test_parse_write_parse_is_stable`
    stayed GREEN, re-confirming that idempotence cannot see this class of defect.
  · **LossReport in BOTH directions — MET, and each direction is the other's floor.** Items:
    `test_every_loss_kind_has_a_test` maps all **30** `LOSS_KINDS` to a named test *and* asserts each name
    exists in `globals()`; suppressing the append inside `LossReport.add` → **42 red**. Empty:
    `test_docx_roundtrip.py::test_a_document_this_repo_wrote_parses_with_no_losses` asserts `report.lossless`
    unfiltered on the rich fixture; adding `"jc"` (alignment — a field the model HAS) to `_PARA_PROPS` →
    **2 red**. So "reports too little" and "reports too much" both red.
  · **Round trip across every `BLOCK_KIND` — MET, through real bytes.** `render_docx` returns a
    **37,299-byte, 17-part ZIP** and `parse_docx` reopens it via `Document(io.BytesIO(...))`; the parse
    recovers all eight kinds AS THEMSELVES (`heading, heading, paragraph, paragraph, bullets, numbered,
    table, table, code, pagebreak, image, paragraph`) with `_canonical` equal and `lossless` true.
    `test_the_fixture_covers_every_block_kind` is the rail that keeps the fixture from shrinking. Note
    `test_docx_writer_coverage.py::_KIND_RECOVERY` records `code` and `image` as NOT recoverable — that is
    a claim about the raw OOXML, and the parser's two documented conventions (all-monospace → `code`,
    `[image: slug]` → `image`) are what close the gap; the two suites do not disagree.
  · **Deliberate writer regression — MET.** Seven parametrized monkeypatch regressions each assert green
    BEFORE patching. Independently falsified on the shipped file: `docx_writer._add_run`'s
    `if run.bold or bold:` → an always-false condition → **11 red** including
    `test_the_round_trip_recovers_the_authored_model`.
- [2026-08-24][DFE-3] **The one real gap, closed: nothing pinned the parser's output CLASSES.** `_canonical`
  projects to primitives, and `test_parse_write_parse_is_stable` compares two models the parser itself
  produced — so a uniformly wrong class agrees with itself. Only `test_page_setup_survives_the_round_trip`
  pinned one (dataclass `__eq__` is class-scoped) and `PageSetup` is the one field carrying no user content.
  This is **DFE-2's hazard on DFE-3's seam**. Added
  `test_the_parse_returns_the_SHIPPED_model_classes` (`type(...) is` over DocumentModel/LossReport/PageSetup/
  Block/Run/Cell/ParagraphStyle; the set comparisons are self-flooring because `set() == {Run}` is false)
  plus `test_the_shipped_class_check_discriminates_by_CLASS_not_by_SHAPE`, whose double is asserted
  field-for-field identical to `Run` and still rejected. Falsified by making the parser emit a module-level
  `class _ShadowRun(Run)`: **exactly 1 red — the new test — and all 15 others GREEN**, which is the measure
  of how much the gap was worth.
- [2026-08-24][DFE-3] **V1 gate's "generate a document with the tools, parse it back, diff the models" was a
  MANUAL step no test joined.** Every round trip called `render_docx` directly, so the tool the agent
  actually invokes was outside the circuit. `TestToolGeneratedDocumentParsesBack` in `test_documents.py` now
  drives `mcp_artifacts._document_create`, reads `prov.raw_bytes(slug)` and parses those bytes: kinds, title
  and visible text match `document_from_markdown` of the same input, and the report is pinned at exactly
  `["page_property"]` — exact equality, so it reds whether the parser goes silent or grows an item the model
  can hold. Isolated under `tmp_path` + `PERSONALCLAW_HOME`.
- [2026-08-24][DFE-3] **The scope line's `knowledge/readers.py:243-247` stale ordering comment is FIXED.**
  It claimed python-docx "exposes no ordering between paragraphs and tables without walking the underlying
  XML body". `docx_parser._walk` does exactly that walk now, so appending is a **choice** this path makes —
  it flattens to plain text for search/embedding where no consumer reads position, and interleaving would
  change the stored text of every already-ingested document. Comment corrected; behaviour deliberately
  unchanged, and the re-ingest is named as belonging to whichever change owns it.
- [2026-08-24][DFE-3] **CORRECTION to the 2026-08-24 entry above.** It states idempotence is blind to a
  uniform writer regression. True in principle and confirmed for the order-walk break, but the bold
  regression DOES trip `test_parse_write_parse_is_stable` — via an asymmetry, not via insight:
  `_fill_cell` bolds a header through `_add_run(bold=...)` when the cell has `runs` but through a direct
  `run.bold = True` loop when it has only `text`. Lap 1 (authored `Cell(text=...)`) keeps the bold, lap 2
  (parser output `Cell(runs=[...])`) loses it. The claim's *conclusion* stands — claim 3 must compare
  against the AUTHORED model — but the reason it fires here is the writer's two cell paths.
- [2026-08-24][DFE-3] **Gate on the verification branch:** `make lint` clean (mypy 1001 source files) ·
  272 passed across the seven document suites · **`make test` 26114 passed / 30 skipped / 12 xfailed, 0
  failed** (845s) · `scripts/gate_report.py` all 6 gates PASS · probe sweep 16, diff-scoped introduced 0 ·
  no personal strings in the diff. Atom NOT flipped here: the Word-authored-fixture clause lives on
  `feature-dfe3-docx-parser-lossreport` (#1996) and the flip is the owner's at integration.
- [2026-08-25][DFE-3] **DONE** (#2002 via batch, originally #2001, integrated on `main` `03729754` which carries #1996). All clauses
  met with a named test and a demonstrated floor. Closed in this pass: the V1 gate's FIRST half —
  "generate a document with the tools, parse it back, diff the models" was a manual step no test joined,
  because every round trip called `render_docx` directly and bypassed `mcp_artifacts._document_create`
  (`test_documents.py::TestToolGeneratedDocumentParsesBack`); and the class-identity gap — `_canonical`
  projects to primitives so it cannot see a class, and `test_parse_write_parse_is_stable` compares two
  parser-produced models so a uniformly wrong class agrees with itself. Measured: a module-level
  `class _ShadowRun(Run)` reds **exactly 1** test (the new one) with 15 green — that count is the gap.
  Verified at integration by re-running that falsification independently: 1 failed / 15 passed, restored
  from a file copy, 16 passed. Gate on the rebased tip: `make lint` clean (mypy 1001 sources) · 133 passed
  across the three docx suites · `gate_report.py` 6/6 PASS · probe sweep 16, diff-scoped introduced 0.
  Flipping DFE-3 does NOT complete this plan — DFE-4..DFE-8 remain `todo`, so the plan status is unchanged.


## Execution log — DFE-4 (binary write path + model read/render endpoints) — **DONE**

- [2026-08-24][S2 · atom `DFE-4`] **DONE.** Three routes on one shared guard chain in
  `artifacts/handlers.py`: `PUT /api/artifacts/{slug}/raw` (bytes in), `GET
  /api/artifacts/{slug}/model` (parsed model + loss report out), and `PUT
  /api/artifacts/{slug}/model` (model in, rendered by the SHIPPED writer server-side, then
  through the *same* guarded write path as `…/raw` so there is one write, not two).
  Supporting: `kind_for_mime()` + `ArtifactVersionConflict` in `artifacts/models.py`,
  `update_binary(expect_version=…)` on the provider and `native.py`, a new
  `documents/model_json.py` (strict JSON⇄`DocumentModel`), `LossReport.to_dict()`, and 12
  codes in `http_errors.py`.

  All five behavioural clauses plus the reference clause hold, each falsified by mutating the
  live line, grepping the mutation back, observing the red, and restoring from a file copy
  (SHA-verified, never `git checkout`). The load-bearing one is clause 2: **the cap is decided
  from `Content-Length` before the artifact lookup and before any `await request.read()`**, and
  its test asserts the *instrument* ("the body was buffered before the size cap was checked")
  rather than a status code — moving the check after the read reds it while the router-level
  413 test still passes, which is exactly why the status-code test alone would have been
  vacuous. Re-verified independently after the agent reported: 29/29 in the new suite, 6/6
  `gate_report`, and that same mutation reproduced and restored byte-identical.

- **DEVIATION from §C3's parenthetical — raw body, not `multipart/form-data`.** §C3 says
  `(multipart/form-data: file=<bytes>)`, which is incompatible with the very next line of the
  same block ("cap enforced BEFORE the body is buffered"): a multipart frame's
  `Content-Length` is the frame's, not the part's, and extracting the part requires reading it.
  The route therefore takes the bytes as the body with `Content-Type` as the MIME. Nothing in
  the product needs multipart — the editor's save path posts a *model*, not bytes.

- **`If-Match` had no precedent in this repo — measured, not assumed.** `git grep` for
  `If-Match`/`if_match` across `src/` and `tests/` returned **zero** hits; the only related
  thing is *outbound* `If-None-Match` in `self_update.py`. So the convention is defined here:
  `If-Match: <version>` carrying the artifact's own monotonic integer, quoted/weak forms
  tolerated because clients add them unasked. No opaque ETag is minted — a second identifier
  for a thing that already has a version is a second thing to keep in sync.

- **Added a status the plan does not name: 428.** A *missing* `If-Match` is
  `if_match_required`/428, kept distinct from *stale*/409, because the remedies differ (add the
  header vs. reload and re-apply) and conflating them tells a client to reload when its version
  was fine.

- **The precondition is compared inside the provider's lock, not only in the handler.** A
  handler-only comparison leaves the check-then-write race the precondition exists to close.
  This also matters because `_write_bytes` **truncates** to the cap rather than refusing, so a
  late check yields a corrupt document instead of an error.

- **FINDING — the shipped writer's own output is not lossless on first parse.**
  `render_docx(model)` → `parse_docx` reports one `page_property` item: python-docx's default
  template ships asymmetric margins (1.00in top/bottom, 1.25in left/right) while `PageSetup`
  holds a single `margin_in`. The second lap is lossless. The report is doing its job, but it
  means "a freshly generated document is lossless" is **false**, and §C5's editor warning will
  fire on every generated `.docx` until `PageSetup` grows per-edge margins — `T3.1`'s
  territory, not this atom's. Pinned by `test_the_loss_report_names_what_did_not_fit`.

- **Scope note on "no route hands raw OOXML to a client."** `GET …/raw` deliberately does — it
  is the download affordance and predates this atom. What the clause guarantees is that the
  *editor circuit* (`GET`/`PUT …/model`) never does, asserted structurally by
  `test_only_the_download_route_serves_document_bytes` with a vacuity check that the download
  handler *can* return a raw body.

- **`PUT …/model` refuses xlsx/pptx/pdf even though writers exist.** `_MODEL_KINDS = ("docx",)`
  gates both halves on the *parser*, deliberately: without a read half, a save could only
  overwrite with content the editor never loaded.

- **Plan defect — §C2 cites `documents/parsers/docx_parser.py`.** The shipped file is
  `documents/docx_parser.py`; there is no `parsers/` package. Cosmetic, but the plan's own file
  reference is wrong.

- Gates: `make lint` clean (black 2033 files, isort, flake8, **mypy 1002 source files**) ·
  new suite **29/29** · 24 touched suites **620 passed** · `gate_report` **6/6 PASS** ·
  wire-error census + agent-reference + append-only rails **30/30** · full `make test`
  **26209 passed / 0 failed** · `~/.personalclaw` unchanged by the run · `web/` untouched.
  Offline reference regenerated (`routes.md` +3 rows, `index.md` 769→772) plus the two rows
  added to the hand-curated `docs/reference/api-overview.md`.

---

## Execution log — DFE-5 (editing surface: renderer slot, model editor, lossy-edit contract, config) — **DONE**

- [2026-08-25][S2 · atom `DFE-5`] **DONE.** Every `done_when` clause holds. The atom was
  filed as GATED on owner task 2 (the editing-library decision, E5); that decision is taken
  below rather than escalated, because it is a dependency-weight call the owner is present for.

- **OWNER DECISION (task 2, E5): option (c) — hand-rolled controlled components over the block
  model. No new frontend dependency.** Reasons, in order of weight: (a) a rich-text framework
  (tiptap/prosemirror/slate/lexical) is a significant dep whose whole value is editing a
  *document DOM*, and this editor edits a `DocumentModel` — we would adopt a second document
  representation and then map between them, which is exactly the second fidelity story §C2
  refuses; (b) CodeMirror 6 is already bundled but is a TEXT editor, and the model's unit of
  formatting is a `Run`, not a character offset in a string; (c) the atom's own scope line
  requires the editor to adopt DESIGN-SYSTEM primitives, which a framework's own widgets would
  fight. The cost is stated: this editor is structural (per-block fields + select-and-mark), not
  WYSIWYG, and §6's "the preview is a labelled approximation" already set that expectation.

- **The lossy-edit contract is a MECHANISM, not a notice.** §C5's three clauses are implemented
  as a *gate in front of* the controls rather than a banner beside them: while the loss report
  is non-empty and unacknowledged, every field is `disabled` and every mark button is off with
  a reason. A warning a user can type straight past has already failed, and this repo's
  standing lesson is that a control which appears to work and then quietly reverts is worse
  than one that refuses. The same `<LossList>` component renders in the pre-edit gate AND in
  the save confirmation's body, so the two copies cannot drift. `loss.lossless` and
  `loss.summary` are read from the server's verdict, never re-derived client-side — the exact
  thing `LossReport.to_dict()`'s own docstring warns about.

- **`document_editing` is enforced server-side, not only in the UI.** `PUT …/model` refuses
  `403 document_editing_off` while the flag is off, re-read per request, with a SEL denial row.
  So "off restores today's read-only preview" holds for a client that never loaded our bundle.
  The UI layer is the *second* rail: the flag is applied to the CONTENT-TYPE REGISTRY
  (`ui/content/documentEditing.ts` re-registers the office types with/without `edit`), so off
  is byte-for-byte the pre-DFE-5 registration — `isEditable` false, no view toggle, no editor.
  A disabled editor would have been the wrong shape for "exactly today's preview".

- **DEVIATION from §C4's `DocumentEditorProps`.** The plan's shape is
  `{slug, version, model, loss, onDirty, onSave}`. Shipped: `{slug, title, mode, readOnly,
  onDirty}` — the editor does its OWN read, holds its own version, and owns its own save.
  Reason: the alternative makes `<ContentSurface>` — the one type-agnostic dispatcher — know
  the document API, the model shape and `If-Match`. The seam this atom actually needs is
  "this type brings its own editor", and every document-specific fact stays inside the editor.

- **Second deviation, and it is a safety one: a custom editor SUPPRESSES the surface's
  Save/Revert.** `ContentSurface`'s Save posts the string `draft`, and for a binary artifact
  `content` is only a raw-URL ref — so leaving the host Save wired while a custom editor is
  mounted is a save that *destroys the body*. `draftEditable = editable && !custom` gates the
  draft affordances (Monaco's `readOnly`, wrap/copy, Revert/Save) while `editable` still gates
  the view toggle; `customDirty` carries the editor's own unsaved state into the toolbar dot
  and `onDirtyChange`. Both directions asserted in `documentEditorSlot.test.tsx`.

- **Settings home: a new `documents` subpage** (Workspace group, beside Inbox/Apps — the flag
  governs the ARTIFACT surface, not a session, so `ChatPanel` was the wrong home) plus its
  bento card, which states which way the flag is set rather than only describing the feature.
  The card shares `useDashCfg`'s cache key rather than opening a third namespace over one
  collection (`splitCollectionBusts.test.ts`).

- **Config round-trip, all five points, and the two `test_config_roundtrip.py` does not cover
  checked by hand:** dataclass + `_meta` ✔ · `load()` explicit `bool(...)` — deliberately not
  `_guard_flag`, which fails ON and would hand every existing install a lossy path on upgrade
  ✔ · `to_dict()` via `asdict` ✔ · **write paths: BOTH** `_EDITABLE_CONFIG`
  (`dashboard.document_editing`) and `api_dashboard_config`'s own `_allowed` set + bool
  validation + GET payload — the panel path has a separate allowlist, and a field missing there
  is a 400 "Unknown fields", i.e. a toggle that cannot be turned on at all ✔ · Settings control
  ✔. Pinned by `tests/test_document_editing_gate.py`.

- **DISCOVERY — `config/loader.py` is 100 lines from a ceiling marked FORBIDDEN TO RAISE.**
  Adding this field red `test_structural_baseline.py`: the file was 5887 lines on `origin/main`
  against a 6000-line ceiling whose rail demands ≥100 lines of headroom *for exactly this
  case* ("a routine config-field addition would red the gate"). A 20-line addition left 95. The
  atom's field was compressed to 13 lines to land at 5900 (headroom 100), which is the floor,
  not slack: **the next config field added to `DashboardConfig` reds that gate with no room to
  compress.** The ceiling comment forbids raising it, so the real remedy is a section split of
  `loader.py`, which is not this atom's scope. Recorded for the owner, not worked around.

- **The read-back proof is server-side, deliberately.** "A user bolds a word, saves, and the
  downloaded file opens bold in Word" is asserted by driving `PUT …/model` and reading the
  STORED bytes back through python-docx (`run.bold`, not our model) —
  `test_a_bolded_word_is_bold_in_the_stored_document`. The browser half asserts the PAYLOAD
  (three runs, only the middle one bold, `If-Match` = the loaded version), so the two halves
  meet at a shape both have checked rather than at a screenshot.

- **Revert is byte-compared, not eyeballed.** `test_revert_restores_the_pre_edit_bytes_exactly`
  asserts `restored == original` on the raw bytes, with a vacuity assertion that the edit
  changed them first. "Close enough" is precisely what a lossy re-render produces.

- **Falsification.** Three live-line mutations, each grepped back to confirm it applied, red
  observed, then restored from a file copy at the literal path (never `git checkout`):
  (1) `if not AppConfig.load().dashboard.document_editing` → `if False and not …` ⇒
  `test_a_model_write_is_refused_while_document_editing_is_off` fails `assert 200 == 403`;
  (2) `setAcknowledged(r.loss.lossless)` → `setAcknowledged(true)` ⇒ 4 reds across the
  pre-edit-gate and save-confirm groups; (3) `draftEditable = editable && !custom` →
  `= editable` ⇒ "suppresses the string-draft Save/Revert" fails. Tree clean after each.

- **Six existing ratchets caught this change and were fixed, not exempted:** the axe manifest
  (`web/e2e/routes.ts` — a settings panel nobody scans is a route the gate never visits), the
  h3 census (the gate became a `role="alert"` region, which is the right announcement for it
  anyway), `cappedListDisclosed` (the loss list uses `MoreRow`, not a hand-spelled "…and N
  more"), `blastRadiusIsVerified` (the danger confirm now names the document in its title),
  and both disabled-reason rails (`disabledReason` on all four buttons + a `title` on the
  disabled field — so a keyboard user who lands on a dead Bold learns why). Plus the
  `http_errors` append-only rail (a `document_editing_off` row) and the two generated baselines
  (`config-baseline.json`, `docs/design/consistency-audit.json`).

- **Gates.** `make lint` clean (black 2059 files, isort, flake8, **mypy 1012 source files**) ·
  new backend suite **8/8** · touched backend suites **54 passed** · full `make test`
  **26588 passed / 30 skipped / 12 xfailed / 0 failed** · `gate_report.py` **6/6 PASS** ·
  `npm run typecheck:web` clean · full `npm run test:web` **5170 passed / 486 files / 0 failed**
  · `npm run build` OK · probe sweep 16 (0 diff-introduced) · `~/.personalclaw` unchanged
  (the suite's own real-home rail reported it clean).

- **NOT in this atom, by scope:** `xlsx`/`pptx` mount the same editor and it shows their blocks
  honestly, but the grid / slide editors are `DFE-7`/`DFE-8`; page setup and paragraph layout
  controls are `DFE-6`; and `PUT …/model` still refuses non-docx kinds (`_MODEL_KINDS`), which
  is DFE-4's deliberate parser-gated design.

- [2026-08-26][DFE-5] **OWNER RATIFICATION — option (c) is ratified and `DFE-5` flips to `done` with
  no code change.** The atom was held on owner task 2, the editing-library decision
  (`PLATFORM-PRIMITIVES.md:1305`, recorded E5), and the implementation had already taken it: option
  **(c)**, hand-rolled controlled components over the block model, **no new frontend dependency**.
  RULED: **ratified.** One `DocumentModel` is the whole fidelity story. A rich-text framework edits a
  document DOM, so adopting one means a second document representation plus a lossy mapping between
  them — which is exactly the second fidelity story §C2 refuses — and it adds a frontend dependency to
  a local-first tool that ships its own SPA. The stated cost is accepted and recorded so nobody
  re-litigates it: **the editor is STRUCTURAL** (per-block fields plus select-and-mark), not WYSIWYG.
  Every `done_when` clause was already met and gated green. Flipped in `dag.json` and `DFE.md`.
  Consequence: `DFE-6`, `DFE-7` and `DFE-8` all depended only on `DFE-5` and are now startable.

---

## Execution log — DFE-5 owner ratification + DFE-6 (layout control) — **DONE**
- [2026-08-26][DFE-5] **Ratification RE-VERIFIED at implementation time, not taken on trust.**
  (The ruling itself is the entry above; this records what was checked before accepting it.)
  `4ec8209c` is an ancestor of `origin/main`; **54 backend tests** green across
  `test_document_editing_gate.py` (8), `test_config_roundtrip.py` and
  `test_artifact_binary_write_api.py`; **42 frontend tests** green across
  `documentEditorSlot`/`documentEditorContract`/`documentModelEdit`; `make lint` clean and
  `gate_report.py` 6/6. Each of the six clause-groups was located as a real assertion, not prose —
  including the python-docx read-back of STORED bytes (`test_a_bolded_word_is_bold_in_the_stored_
  document`) and the byte-compare revert (`restored == original`, with `edited != original` as its
  vacuity leg). Two falsifications: `if not …document_editing` → `if False and not …` reds
  `test_a_model_write_is_refused_while_document_editing_is_off` with `assert 200 == 403` while the
  other 7 stay green; `e.status === 409` → `499` reds exactly "a 409 says what happened and leaves
  the draft saveable" (1 of 13). Both restored from a file copy at the literal path.

- [2026-08-26][S3 · atom `DFE-6`] **DONE.** All four `done_when` clauses met (T3.1-T3.4).

- **This atom closes a defect DFE-4 recorded and could not fix.** DFE-4's log states: "a freshly
  generated document is lossless is **false**, and §C5's editor warning will fire on every
  `.docx` until `PageSetup` grows per-edge margins — `T3.1`'s territory". That was the real cost of
  a single `margin_in`: python-docx's default template ships 1.00in top/bottom and 1.25in
  left/right, so **every document this project generated itself parsed as lossy** and the editor
  warned the user that editing their own new file would lose formatting. Four per-edge fields make
  that geometry representable, and the report on a generated document is now empty
  (`test_the_tool_generated_document_reports_exactly_its_one_honest_loss` inverted to assert
  `report.items == []`). Teaching people to click through a warning that sometimes matters was the
  larger bug.

- **`PageSetup` grew a named SIZE, and swapping the template's dimensions was never equivalent.**
  The writer's old landscape path swapped `section.page_width`/`page_height`, which can only ever
  yield a landscape version of *the template's* paper — so a model asking for A4 landscape got
  landscape **Letter** and the size was dropped silently. `size` is a closed set
  (`letter`/`a4`/`legal`/`tabloid`, `""` = template decides) validated in `__post_init__` like
  `align`/`orientation`, and `size_in()` applies orientation so orientation stays ONE fact rather
  than doubling the table. `w:orient` is now declared from the RESULTING geometry rather than the
  model's string, because a reader that trusts the attribute over the dimensions must agree with
  the paper it prints on.

- **DEVIATIONS from §C1, both deliberate.** (1) §C1 spells margins `margin_pt: dict[str, float]`;
  shipped as four named floats (`margin_top_pt`…`margin_right_pt`). The model has no other
  dict-typed field, `model_json._object()` validates payloads by dataclass FIELD NAME (a dict would
  need its own key validation), and the frontend would need string-keyed lookups — four floats get
  all of that for free and read the same as the neighbouring `space_before_pt`. (2) §C1 defaults
  `size="letter"` / `orientation="portrait"`; shipped `""` for both, matching the shipped model's
  own stated doctrine that `""` means "the writer's template decides, and a template with a house
  page size keeps it". Concrete defaults would have made every existing caller start writing Letter
  into files that never asked for one.

- **`first_line_indent_pt` is the one field where NEGATIVE is meaningful** — a hanging indent pulls
  the first line left of the body. So its unset test is `!= 0`, not the `> 0` every sibling numeric
  uses, and both the writer census row and the round-trip row measure it with **-18pt**: a
  positive-only measurement would have reported "emitted" for a field that silently dropped every
  hanging indent. Falsified (`!= 0` → `> 0`): 3 reds, all of them the `first_line_indent_pt` rows,
  with the other seven layout fields green — so the parametrized rail discriminates per field.

- **Headers/footers are plain text, and what does not fit is REPORTED rather than flattened.**
  `header_text`/`footer_text` hold one line; a part carrying a table, several non-empty paragraphs,
  an image, or a field other than `PAGE` raises a `header_footer` item and is left out of the model
  entirely. Squeezing a two-paragraph header with a logo into one string would claim a fidelity the
  re-render does not have, and keeping only the first paragraph would lose the second without
  saying so. `page_numbers` is its own flag writing a real `w:fldSimple PAGE` field, because a page
  number is computed per page and no static string can express it — both spellings are read back
  (`fldSimple`, and the `instrText` sequence Word writes), since checking only ours would read a
  Word-authored footer as plain text and then drop its numbering.

- **A LINKED header declares nothing of its own.** python-docx reports the inherited definition for
  a section whose header is linked to the previous one, so the first draft raised a `header_footer`
  loss for content already captured. Skipped — a report that cries wolf is one nobody reads, which
  is the same reasoning `_RUN_PROPS`' docstring already gives for being a denylist.

- **`_PARA_PROPS` lost two entries, and the rail that pinned them was rewritten rather than
  deleted.** `ind` and `keepNext` are modelled now, so they moved out of the "cannot hold" map;
  `test_an_unmodelled_paragraph_property_is_reported` used `w:ind` as its example and would have
  become a rail that could no longer fail, so it now uses `w:pBdr` and gained a companion
  (`test_indentation_is_READ_not_reported`) asserting the other direction.

- **`_kinds()` was DELETED, not left in place.** That helper filtered `page_property` out of every
  assertion in `test_docx_parser.py` because every fixture carried one. With the margin loss gone it
  filtered nothing — and a filter that no longer filters would have masked the next real
  `page_property` regression across 36 call sites. All 36 now call `report.kinds()` directly.

- **Two loss kinds needed new owner tests.** `_COVERED_BY` maps every `LOSS_KINDS` member to a test,
  and `test_every_loss_kind_has_a_test` reds when the named test does not exist. `header_footer`'s
  owner asserted a header IS reported, which this atom inverts, so it moved to
  `test_a_header_the_model_cannot_hold_is_reported` (a table in the header); `page_property`'s moved
  to `test_a_page_size_the_model_cannot_hold_is_reported` (A5 is genuinely unnameable).

- **The writer census grew from 27 rows to 38 and stayed measured.** `test_docx_writer_coverage.py`
  re-derives every verdict from freshly rendered bytes and `test_the_census_covers_every_model_field`
  makes a new model field unaddable without a measurement — so all twelve new fields (four
  `ParagraphStyle`, eight `PageSetup`) carry a live measurement, and `CENSUS_SHAPE` moved to
  `{emitted: 28, partial: 10, dropped: 0}`.

- **MEASURED FINDING — an exact-EMU page comparison can never pass.** `Inches(210/25.4)` is exactly
  7560000 EMU (914400/25.4 = 36000 exactly, so the metric sizes are kept as unrounded divisions —
  a rounded `8.2677` is 11 EMU short). But `w:pgSz`/`w:pgMar` are stored in TWIPS, so A4 saves as
  11906 twips and reads back **10692130** against `Mm(297)`'s 10692000. That is a property of the
  file format, not of this writer, so every page-dimension assertion compares within one twip
  (635 EMU ≈ five-thousandths of a millimetre) and says why. Margins are exact (1pt = 20 twips).

- **The frontend mirrors the size table, and a cross-language rail pins it.** The preview needs page
  dimensions in the browser and cannot import Python, so `ui/content/documentPage.ts` duplicates
  `PAGE_SIZE_IN`; `test_the_frontends_page_size_table_matches_the_models` reads that file and
  asserts every model size name appears, with a vacuity floor (`a5:` must be ABSENT) so the rail
  cannot pass against a file listing every plausible paper. A drift is either a preview drawing the
  wrong paper or a size the editor offers and the server refuses with a 400.

- **"Controls reflect the loaded document, not defaults" is asserted with a foil for every control.**
  Each positive case (A4/landscape/2cm/real header/page-numbers ON) is paired with a VACUITY leg
  mounting a Letter-portrait-1in document and reading every one of those controls differently —
  1in shows as `2.54`, which cannot be the `2` the other document showed. A third case pins the
  real trap: a document with `page: null` shows the UNSET option, not Letter, because `PageSetup`'s
  zeros mean "the template decides" and rendering them as Letter would write Letter into the file
  on the first unrelated save. Falsified (`pageOf` → always `EMPTY_PAGE`): 7 reds, while the
  unset-state test correctly stayed GREEN — so the positive tests are the discriminating ones.

- **The preview is a labelled approximation, and the label is prose beside the shape** rather than a
  tooltip: it has to be readable at the moment the shape is, or the shape reads as a rendering. Its
  insets are per-axis percentages (2cm of a 210mm height is 9.5%; of a 297mm width, 6.7% — the test
  asserts they DIFFER, or the preview would be drawing one margin share on both axes) and it renders
  nothing at all when no size is named. `aspect` is mirrored to `data-aspect` because jsdom does not
  model the `aspect-ratio` CSS property, so a test can only read the drawn proportion from there.
  Falsified (the share calculation → a constant): exactly the 2 geometry tests red, 15 green.

- **THREE existing design ratchets caught this change and were fixed, not exempted**, and one was a
  real a11y defect. (1) `toggleLabelInName` — WCAG 2.5.3: the visible label "Keep with next" was not
  contained in the switch's accessible name "Keep this paragraph with the next", so voice control
  could not act on what the user reads; renamed to "Keep with next paragraph". (2)
  `disabledReasonCensus` + (3) `toggleDisabledReason` — three `Select`s and two `Toggle`s were
  conditionally disabled with no reachable reason (`Select` carries no `disabledReason` prop). Fixed
  by making the controls **ABSENT rather than dead** on a read-only host, which is the call DFE-5
  already recorded for the editor itself ("a disabled editor would have been the wrong shape") —
  a sentence saying why beats a row of dead dropdowns, and the geometry PREVIEW still renders,
  because reading a historical version's layout is not editing it. Header/footer moved from raw
  `<input>` to the shared `TextInput` in the same pass.

- **`config/loader.py` is UNTOUCHED at 5900 lines.** This atom needs no config field —
  `dashboard.document_editing` already gates the whole editing surface — so the 100-line headroom
  the structural ceiling demands is unchanged. `test_structural_baseline.py` green.

- **Gates.** `make lint` clean (black 2125 files, isort, flake8, **mypy 1043 source files**) ·
  targeted backend **399 passed / 0 failed** across 12 suites (new `test_document_layout.py` 42) ·
  `gate_report.py` **6/6 PASS** · `npm run typecheck:web` clean · full `npm run test:web`
  **5420 passed / 506 files / 0 failed** · `npm run build` OK · probe sweep 0 diff-introduced ·
  `docs/design/consistency-audit.json` reverted after the build regenerated it (its drift is
  pre-existing on `main`).

- **NOT in this atom, by scope:** the grid and slide editors are `DFE-7`/`DFE-8`; `PUT …/model`
  still refuses non-docx kinds (`_MODEL_KINDS`), which is DFE-4's deliberate parser-gated design.
  `_settled_docx_bytes` was KEPT (19 call sites over two files) but its docstring corrected — one
  render is lossless now, so it no longer exists to dodge a margin loss, only to make a page setup
  an explicit fact of the model a test holds.

---

## Execution log — DFE-7 (sheets: styled SheetModel + xlsx parser + grid editor) — **DONE**

- [2026-08-26][S4 · atom `DFE-7`] **DONE.** All three `done_when` clauses hold, each proved by
  re-parsing the WRITTEN BYTES rather than round-tripping through our own writer. `SheetModel`
  gained `SheetCell`/`Sheet` (per-cell format, column widths, merges, frozen header, formulas as
  formulas), `documents/xlsx_parser.py` is the new read half, `documents/sheet_json.py` the strict
  wire boundary, and `web/src/ui/content/SheetGrid.tsx` the grid editor. `_MODEL_KINDS = ("docx",)`
  is deleted; `documents/model_codec.py` now owns the kind→(parse, to_dict, from_dict) table and
  `MODEL_KINDS` is `("docx", "xlsx")`.

- **THE DEFECT IS WORSE THAN THE ATOM SAYS, AND IT IS TWO-DIRECTIONAL — measured, not assumed.**
  The atom says `"=SUM(A1)"` "is written as a string". Probed against openpyxl 3.1.5: it is not.
  openpyxl SNIFFS any string starting with `=` into `data_type == "f"`, so the old writer's formula
  survived *by accident*. The genuine defects were (a) **no parser existed at all**, so a formula
  could never come back as a formula — `GET …/model` answered 415 for every .xlsx; and (b) the
  mirror image, which corrupts user data: a LABEL somebody typed as `"=TBD"` was silently promoted
  to a formula that Excel opens as `#NAME?`, and the model had no way to express it. Both directions
  are now pinned, and they are each other's vacuity leg — `data_type == "s"` is forced for a literal
  and `"f"` asserted for a declared formula, so neither guard can pass by coercing everything one way.

- **`value` and `formula` are separate fields, and that separation IS the fix.** No leading-`=`
  inspection happens anywhere on the server: the writer writes what the model declared, and the
  parser reads the FILE's own `data_type`. `SheetCell.__post_init__` refuses a `formula` that does
  not start with `=`, so the field cannot become a second hiding place for a plain label.

- **DEVIATION — no shared loss-report abstraction, by owner ruling mid-session.** I had started
  extracting `LossList` + the pre-edit gate out of `DocumentEditor.tsx` into a shared component, on
  the "one lossy-edit contract, not two" reading of the tenets. Ruled against: `DFE-6` is editing
  that exact file on a branch that is **not on main**, so a "shared" shape would be invented against
  code nobody can read, and both branches being unmerged means nobody can reconcile it at review
  time either. `SheetGrid` therefore carries its own `SheetLossList` and its own gate, consuming
  main's `LossReport` unchanged. **Named so it is not lost: if a real divergence between the docx and
  xlsx loss stories survives once both branches land, that is a follow-up coherence pass.**
  `model_codec.py` was kept — it is the route's own per-kind dispatch replacing a hand-kept tuple,
  not a cross-format loss abstraction.

- **`MODEL_KINDS` is declared, not computed.** Deriving it by calling `get_codec` for every candidate
  would import both document libraries at module import and throw away the laziness the route wants.
  The drift it could carry (a kind advertised with no runtime behind it) is closed by
  `test_every_declared_model_kind_resolves_to_a_codec` plus a vacuity leg asserting `get_codec`
  can still say no — a capability claim asserted, rather than made true at a cost.

- **FINDING — a date cell was unsaveable, and it is now an honest loss instead.** openpyxl converts a
  date-formatted number into a real `datetime`, and `SheetCell.value` crosses the wire as JSON, which
  has no date type — so `sheet_from_dict` would have refused a workbook containing a single date. The
  parser reduces it to its ISO 8601 string and reports a new `date_value` loss naming the
  degradation (a re-render writes text, so a reader can no longer sort or subtract it). Writing an
  ISO-looking string back as a date was refused on principle: that is the same sniffing this atom
  abolished, with a different pattern to match.

- **`LossItem` gained ONE field, `location`.** A spreadsheet loss belongs at `Sales!C2`, and
  "block 4" would be a lie. `where` prefers it when set, so every existing .docx loss is
  byte-identical (empty default) and `to_dict()` is unchanged — zero frontend contract churn.
  Five sheet kinds were appended to `LOSS_KINDS` (`sheet_feature`, `cell_style`,
  `formula_cached_value`, `date_value`, `row_height`) rather than opening a second closed
  vocabulary, because the editing surface renders ONE report for whatever it loaded.

- **The grid follows DFE-5's ratified posture (option (c)) — no new frontend dependency.** A real
  `<table>` of controlled `<input>`s over the `SheetModel`, no embedded spreadsheet widget, and **no
  recalculation engine**: a cell showing `=SUM(B2:B9)` shows, edits and saves the expression. A grid
  that displayed a cached number would be lying about what it is about to save. In the grid a leading
  `=` DOES mean formula — that is the interface convention, not sniffing, and what makes it safe is
  that it is overridable both ways from the inspector ("Treat as text" / "Treat as formula"), so
  `=TBD` is reachable in one click rather than impossible.

- **Two ratchets caught this and were satisfied, never exempted.** (1) The **disabled-reason census**
  flagged `SheetGrid.tsx:252` — the Format `<Select>` was conditionally disabled with no reason
  anyone could reach. This was a real defect of the same family a sibling atom found: `Select` was
  the odd primitive out, lacking the `disabledReason` carrier `Button` has since `unavailable.ts`, so
  a caller's only options were an unexplained dead control or a wrapper. Added `disabledReason` to
  `ui/forms.tsx`'s `Select` (applied as `title` only WHILE disabled). (2) The **ui-docs
  documentation-as-data guard** then failed on the undocumented prop, so `forms.doc.ts` gained its
  entry plus a best-practice line. Also consumed rather than invented: `EmptyState` (ListScaffold),
  `Segmented` for the sheet tabs, `MoreRow` for the truncated loss list, and a per-cell
  `aria-label` of the cell's own ref — a visual column header is not a programmatic name, so without
  it a screen reader announces an unlabelled text box with no way to say which cell it is on.

- **CLEAN BREAK — `SheetModel.sheets` changed type** from `dict[str, list[list[object]]]` to
  `list[Sheet]`, with `SheetModel.from_rows({name: rows})` as the plain constructor for callers that
  have data and no opinion about presentation. Three production call sites in `mcp_artifacts.py`
  moved; five test sites updated. `Sheet.rows` is a derived PROPERTY, not a stored mirror, so the
  wire carries cells only — a stored copy is a second representation that goes stale on the first
  edit. Row-0 bold + freeze moved from the writer into `from_rows`, because presentation the model
  cannot see is presentation the editor shows as plain and saves as plain.

- **DISCOVERY — the plan's `parsers/xlsx_parser.py` path is wrong, as DFE-4 already recorded for
  its docx twin.** There is no `documents/parsers/` package; the shipped convention is
  `documents/<fmt>_parser.py`. Followed the code, not the plan.

- **Falsification.** Three live-line mutations, each grepped back to confirm it applied, red
  observed, then restored from a file copy at the literal path (SHA-verified, never `git checkout`):
  (1) `if cell.data_type == "f" and isinstance(...)` → `if False and …` ⇒ **5 reds**, including the
  headline `test_a_formula_stays_a_formula_through_the_round_trip` and the route-level read-back
  failing `assert 's' == 'f'` — the formula literally written back as a string, i.e. the atom's
  defect reproduced; (2) the writer's `target.data_type = "s"` pin → a no-op ⇒
  `test_a_label_that_looks_like_a_formula_stays_a_label` fails `assert 'f' == 's'`; (3)
  `if cell.number_format:` → `if cell.number_format and False:` ⇒ **7 reds**, a set disjoint from
  (1) except the shared route test, which fails on a DIFFERENT assertion
  (`'General' == '#,##0.00'`). Tree clean after each; 67/67 green on restore.

- **Gates.** `make lint` clean (black 2142 files, isort, flake8, **mypy 1057 source files**) · new
  `tests/test_sheets.py` **33/33** · `tests/test_artifact_binary_write_api.py` **34/34** (5 new
  xlsx route tests) · 10 targeted backend suites **263 passed / 0 failed** (incl.
  `test_config_roundtrip.py` and `test_structural_baseline.py`) · `gate_report.py` **6/6 PASS** ·
  `npm run typecheck:web` clean · full `npm run test:web` **5443 passed / 507 files / 0 failed**
  (40 new: 23 `sheetModelEdit` + 17 `sheetGridContract`) · `npm run build` OK ·
  `~/.personalclaw` unchanged by the run (the suite's own real-home rail reported it clean) ·
  `config/loader.py` untouched at **5900 lines** (no config field needed — `dashboard.document_editing`
  already gates this surface) · `docs/design/consistency-audit.json` regenerated by the build and
  reverted, not committed · no new route, so the offline route reference is unchanged.

- **For DFE-6's rebase.** No file `DFE-6` owns was restructured, but three shared files were touched
  and all three are additive: `documents/model.py` (sheet dataclasses appended near `SheetModel`,
  nothing above it changed), `documents/docx_parser.py` (5 kinds appended to the END of `LOSS_KINDS`,
  plus `LossItem.location` and one `add()` kwarg), and `web/src/ui/content/documentEditing.ts` (an
  `EDITORS` table; `pptx` still maps to `DocumentEditor`). `DocumentEditor.tsx`,
  `documentModelEdit.ts` and `model_json.py` are **untouched**. New names are all sheet-prefixed
  (`SheetCell`, `Sheet`, `sheet_json`, `xlsx_parser`, `model_codec`, `SheetGrid.tsx`,
  `sheetModelEdit.ts`) so nothing can collide with `documentPage.ts` / `DocumentLayout.tsx`.

- **NOT in this atom, by scope:** `pptx` still has no parser, so `PUT …/model` refuses it and the
  deck editor is `DFE-8`; the V4 gate's DECK half is therefore still open. Charts, pivot tables,
  conditional formatting, data validation, borders and row heights are reported, not carried — a
  richer sheet model is a later decision, not a silent drop. No new runtime dependency: `openpyxl`
  was already declared in `pyproject.toml` (`>=3.1,<4`) and used by both the xlsx writer and
  `knowledge/readers.py`.

- **A THIRD ratchet caught a real omission after the fact, and it was right.** CI red:
  `tests/test_docx_parser.py::test_every_loss_kind_has_a_test`. Appending five kinds to `LOSS_KINDS`
  broke `sorted(_COVERED_BY) == sorted(LOSS_KINDS)` — the rail whose whole point is that *a loss kind
  nobody has shown fires is a loss the parser can silently stop reporting*. Satisfied, not weakened.
  **Measured before fixing: four of the five kinds already had real tests** — `sheet_feature`,
  `cell_style`, `formula_cached_value` and `date_value` are each asserted in `tests/test_sheets.py`
  against genuine .xlsx bytes, with `test_a_plain_sheet_is_lossless` as their shared vacuity leg. The
  rail could not see them because it resolves a name in its **own module's `globals()`**, and
  `LOSS_KINDS` is one vocabulary spanning two parsers. **`row_height` was the only kind with zero
  tests anywhere** (`git grep row_height` hit exactly two lines, both in `src/`) — reachable and
  emitted at `xlsx_parser.py:113`, never exercised.
  - Written: `test_an_explicit_row_height_is_reported_per_row_and_located_at_that_row` — a workbook
    where row 1 sets `height = 42` and row 2 does not, asserting `[item.where …] == ["S!1"]`
    (**one** item, the resized row, located in the sheet's own terms) — so a parser reporting every
    row it walked fails the *count*, not just the location. Plus an explicit vacuity leg,
    `test_a_sheet_whose_rows_were_never_resized_reports_no_row_height`, on two rows of real content.
  - **DEVIATION — the rail's discovery was widened, deliberately, and it got stricter doing it.**
    `_COVERED_BY` now names tests in either suite (`{**vars(_sheet_suite), **globals()}`, importing
    `tests.test_sheets` the way `test_guardrails_query_class.py` already imports a sibling suite), and
    a name must now resolve to a **callable** whose name starts with `test_` — where before a
    same-named constant or stray import would have satisfied it. ONE registry asserted complete
    against the whole tuple, rather than a second registry in the sheet suite that could leave a
    future kind covered by neither. No kind is exempted and nothing is allowlisted.
  - **Falsification of the two kinds this entry claims.** (1) `xlsx_parser.py:113`'s
    `"row_height"` → `"sheet_feature"` (grepped back: zero `"row_height"` left in the file) ⇒
    **exactly one red**, `test_an_explicit_row_height_…` (`assert [] == ['S!1']`); its vacuity leg
    correctly stayed green, since it asserts absence. (2) `_json_scalar`'s date branch `"date_value"`
    → `"cell_style"` (grepped back: 2 occurrences → 1) ⇒ **exactly one red**,
    `test_a_date_format_survives_but_says_its_value_became_text` (`assert ['cell_style'] ==
    ['date_value']`). Each restored from a file copy at the literal path, SHA-verified, never
    `git checkout`. The two mutations red **disjoint single tests**, so the kinds discriminate.

## Execution log — DFE-8 (decks: DeckModel/Slide layout+geometry+bullet levels + pptx parser + slide editor) — **DONE**

- [2026-08-27][S4 · atom `DFE-8`] **DONE.** Both `done_when` clauses hold, each proved by re-parsing
  the WRITTEN BYTES rather than round-tripping through our own writer. `Slide.body: list[str]` is
  gone; `Bullet(text, level)`, `ShapeBox`, `Slide.layout`/`title_box`/`body_box` and
  `DeckModel.width_in`/`height_in` are the model, `documents/pptx_parser.py` is the new read half,
  `documents/deck_json.py` the strict wire boundary, `documents/pptx_shapes.py` the one answer to
  "which shape is the body", and `web/src/ui/content/SlideDeck.tsx` the slide editor. `MODEL_KINDS`
  is now `("docx", "xlsx", "pptx")` and `EDITORS.pptx` mounts `SlideDeck` instead of `DocumentEditor`.

- **THE DEFECT WAS EXACTLY AS NAMED, AND IT WAS ONE OF THREE.** `pptx_writer._add_slide` pinned
  `para.level = 0` on every appended paragraph. Measured: the depth was not lost in the file, it was
  **never written** — and there was nowhere for it to come FROM either, because `Slide.body` was
  `list[str]` and no .pptx parser existed at all (`GET …/model` answered 415 for every deck). So the
  fix is three things, not one: the model carries `level`, the writer writes it, the parser reads it
  back. A fourth was found on the way in: **the FIRST bullet takes a different code path** (the writer
  reuses the placeholder's existing paragraph rather than appending one), so a fix applied only to the
  appended bullets would have left every deck's opening line flat. Pinned separately by
  `test_the_first_bullet_keeps_its_depth_too`.

- **`from_markup` was silently flattening markdown too, and that is the user-visible half.**
  `_BULLET`'s `^\s*` consumed the indentation that markdown uses to ENCODE depth, so
  `deck_from_markdown` — the way the agent actually makes decks — could only ever produce a flat
  outline. `_bullet_level` now reads the leading whitespace off the raw line (two spaces per level,
  tab expanded to four per CommonMark) before the bullet regex eats it. This is reading the format's
  own encoding, not sniffing prose: the same distinction `xlsx_parser` draws between `data_type == "f"`
  and a leading `=`. `deck_create`'s `slides` input accepts `{"text", "level"}` beside a plain string
  for the same reason — an agent with a nested outline has to be able to say so.

- **Geometry is stored only when it OVERRIDES the layout, and that discrimination is the whole
  design.** python-pptx reports a placeholder's inherited position as if the shape declared it, so a
  parser that stored what it read would pin every shape of every deck at whatever the template said
  the day it was parsed — and the next template change would stop reaching the file. `_box` compares
  the shape's box against the LAYOUT placeholder's box (public API, matched by placeholder idx) and
  returns `ShapeBox()` when they agree; `ShapeBox.placed` is keyed on the SIZE, not left/top, so a
  shape flush against the corner still counts as placed. Both legs are asserted, and the inherited leg
  is the one that would have passed vacuously.

- **A deck title round-trips through its cover slide, which closes a defect the editor would have
  had.** The writer renders `DeckModel.title` AS a title slide; without the parser folding that shape
  back into the field, editing the title in the editor would have appended a SECOND cover on every
  save. `_is_cover` is deliberately narrow — layout `Title Slide`, a title, no bullets, no notes, an
  unmoved title box — because folding REMOVES a slide from the list, so it is only safe when nothing
  on it would have nowhere to go. `test_a_first_slide_that_carries_content_is_NOT_folded_into_the_title`
  is the discriminating leg.

- **FINDING — a title slide's strapline is not a loss, it is the slide's body.** Measured, and it
  changes what a cover means: the subtitle IS the first text-frame placeholder that is not the title,
  so `body_placeholder` returns it, the model carries it as the outline, and the writer puts it back
  there. Pinned by `test_a_title_slides_strapline_is_carried_as_its_body_not_lost`. My first
  `slide_placeholder` test asserted the opposite and was wrong; the kind is exercised on a **Two
  Content** layout instead, where a genuine second body has nowhere to go.

- **Five deck kinds appended to `LOSS_KINDS`** (`slide_layout`, `slide_placeholder`, `slide_shape`,
  `bullet_run_style`, `slide_feature`) rather than opening a third closed vocabulary, for the reason
  DFE-7 gave: the editing surface renders ONE report for whatever it loaded. Each is registered in
  `_COVERED_BY` with a real test in `tests/test_decks.py` and each has a vacuity leg
  (`test_a_plain_deck_is_lossless` shared, plus `test_an_unused_placeholder_is_not_reported` and
  `test_a_bullet_whose_formatting_is_inherited_is_not_reported` — python-pptx reads "inherited" as
  `None`, and a parser that read `None` as a style would put an item on every bullet of every deck).
  `_COVERED_BY`'s discovery widened to `{**vars(_sheet_suite), **vars(_deck_suite), **globals()}` —
  one registry asserted set-equal to the whole tuple, no exemptions.

- **Locations are the EDITOR's terms, not the file's.** `"slide 3 · bullet 2"` and `"cover slide"`,
  because a folded cover is a FIELD and calling it "slide 1" would send a user looking at the wrong
  surface. `test_a_loss_on_a_folded_cover_is_located_as_the_cover` pins it.

- **No shared loss contract, per the standing owner ruling.** `SlideDeck` carries its own
  `DeckLossList` and its own pre-edit gate, consuming `LossReport` unchanged — the third copy now.
  **Named so it is not lost: three surfaces render one report shape with three near-identical
  components. If the divergence is still cosmetic once DFE-6 and DFE-7 are both on main, that is a
  follow-up coherence pass, and it is now worth doing** (two copies were arguable, three are a
  pattern). `model_codec.py` was extended, not duplicated: one more branch and one more `MODEL_KINDS`
  member.

- **The editor follows DFE-5's ratified posture (option (c)) — no new frontend dependency.** Slide
  list + per-slide fields over the model; **bullet depth is a `Select`, not a typing behaviour** (no
  tab-to-indent, no dashes parsed out of prose), which is the atom's point made visible. Geometry is
  **preserved, explained and releasable, never authored**: a moved shape says where it is and offers
  "Use the layout's positions" — eight number fields would be a canvas, and this is not one. Consumed
  rather than invented: `Field`, `Select`, `TextInput`, `TextArea`, `Segmented`, `EmptyState`,
  `MoreRow`, `InlineError`, `Centered`, `confirm`.

- **A primitive gap closed, exactly as DFE-7 closed Select's.** `TextInput` and `TextArea` had no
  `disabled` at all, so an editor behind a consent gate could only be built from raw `<input>`s (which
  is what `SheetGrid` had to do). Both now take `disabled` + `disabledReason` — applied as `title`
  only WHILE disabled, same posture as `Button` and `Select` — and `forms.doc.ts` gained the four
  entries plus the best-practice line the documentation-as-data guard requires. Non-disabled call
  sites are byte-identical: the dim class is appended conditionally rather than added to `INPUT_BASE`.

- **The a11y suite caught a real WCAG defect and it was fixed by DELETING a control, not renaming
  one.** An empty deck rendered two buttons both named "Add slide" — the slide-list toolbar's and the
  empty state's — an ambiguity a screen-reader user cannot resolve. The slide-list row is now hidden
  while the deck has no slides (its Segmented has nothing to list and its Delete is dead anyway), so
  one action has one control; the toolbar's is named "Add slide after this one", which is what it
  actually does. Asserted with a count, not a presence check.

- **Falsification.** Two live-line mutations, each grepped back to confirm it applied, red observed,
  then restored from a file copy at the literal path (SHA-verified, never `git checkout`).
  (1) `pptx_writer`'s `para.level = bullet.level` → `para.level = 0`, i.e. the atom's defect
  reinstated verbatim ⇒ **exactly 3 reds**: the headline `test_bullet_depth_survives_the_round_trip`,
  `test_the_written_file_itself_carries_the_depth` (the leg that reads the bytes with python-pptx
  rather than our parser) and `test_markdown_indentation_becomes_bullet_depth`. Both vacuity legs
  correctly stayed green — `test_a_flat_outline_stays_flat` because it asserts zeros, and
  `test_the_first_bullet_keeps_its_depth_too` because the first bullet goes through the OTHER code
  path, which is the two-paths finding above proving itself. (2) `deck_json._slide`'s
  `title=_text(...)` → `title=""`, neutralising the persistence of a slide edit at the wire ⇒
  **exactly 2 reds, a set disjoint from (1)**: the route-level
  `test_a_slide_edit_survives_the_save_and_read_back` failing `assert '' == 'Pipeline — revised'` —
  the second `done_when` clause reproduced as a defect — and
  `test_a_deck_survives_the_json_boundary_unchanged`. The two mutations red disjoint sets, so the
  clauses discriminate. 453/453 green on restore.

- **Gates.** `make lint` clean (black **2153 files**, isort, flake8, **mypy 1065 source files**) ·
  one existence-checked run of 14 targeted suites, **453 collected / 453 passed**, incl. new
  `tests/test_decks.py` **36**, `tests/test_docx_parser.py` (the `_COVERED_BY` rail),
  `tests/test_sheets.py`, `tests/test_documents.py`, `tests/test_artifact_binary_write_api.py`
  (5 new pptx route tests), `tests/test_structural_baseline.py`, `tests/test_config_roundtrip.py`,
  `tests/test_agent_reference.py` and `tests/test_api_version_one_origin.py` (the generated
  `reference/tools.md` was regenerated with
  `python -m personalclaw.manifest_reference`, since `deck_create`'s schema changed) ·
  `scripts/gate_report.py` **6/6 PASS** · `~/.personalclaw` unchanged (the suite's own real-home rail
  reported it clean) · `npm run typecheck:web` clean · full `npm run test:web`
  **5466 passed / 509 files / 0 failed** (23 new: `deckModelEdit` + `slideDeckContract`) ·
  `npm run build` OK, and it did NOT regenerate `docs/design/consistency-audit.json` ·
  `config/loader.py` untouched at **5900 lines** before and after (no config field needed —
  `dashboard.document_editing` already gates this surface, as `DFE-6`/`DFE-7` both found) · every new
  module far inside the 2800-line watch band (`pptx_parser.py` 272, `deck_json.py` 106,
  `pptx_shapes.py` 43, `SlideDeck.tsx` 385).

- **For the driver, on flattening (this branch is stacked on `feature-dfe7-sheets` @ `49320b25`).**
  Four `DFE-7` lines had to change, all of them because `DFE-7` deliberately wrote them to say pptx
  had no parser: `model_codec.py`'s `MODEL_KINDS` tuple + the comment above it that explained pptx's
  absence, `tests/test_sheets.py::test_a_kind_with_no_codec_resolves_to_none` (its "kind with no
  codec" example moved from `pptx` to **`pdf`** — which is the better example anyway, since pdf ships
  a WRITER, so a table answering "editable" from the writer registry would wrongly say yes),
  `tests/test_docx_parser.py`'s `_COVERED_BY` + its `known = {**vars(_sheet_suite), …}` line, and
  `web/src/ui/content/documentEditing.ts`'s `EDITORS` table + its comment. One `DFE-4` line changed:
  `test_a_kind_without_a_shipped_parser_has_no_model`'s `model_kinds == ["docx", "xlsx"]`.
  **One main-owned line to watch:** `web/src/ui/forms.tsx`'s TextArea `className` template — I had to
  edit it to append the disabled tone, and on this base it still carries
  `focus:ring-primary/50`. `web/src/design/focusRingContrast.test.ts` does not exist on this base
  (56 files here still carry alpha rings), so main's sweep owns that class; when flattening, take
  **main's** ring class on that line, not mine.

- **NOT in this atom, by scope:** an image block still resolves to a notes marker rather than real
  slide bytes (`artifact_slug` is written into the notes, and the parser deliberately does NOT sniff
  it back out — recovering a field by matching a text prefix is the guessing this family abolished).
  Pictures, tables, charts, free shapes, per-character formatting, extra placeholders, slide
  backgrounds and non-standard layouts are reported, not carried. The editor does not author geometry.
  No new runtime dependency: `python-pptx` was already declared in `pyproject.toml`
  (`>=0.6.21,<2`) and used by the pptx writer and `knowledge/readers.py`.

- **On V4's deck half, stated precisely.** The full lifecycle is proved end to end — create → `GET
  …/model` → edit a title, a bullet's text and a bullet's DEPTH → `PUT` with `If-Match` → read the
  STORED BYTES back with python-pptx — at the route, plus the editor's own contract at the component
  (registration, the gate as a mechanism, the save payload, a 409, the empty deck, a moved shape). The
  full local gate holds (above). What was **not** run this session is a live browser drive of the deck
  editor against a dev gateway; the machine was under a load of 40+ on 18 CPUs for most of it. Same
  standing as `DFE-7`'s sheet half, which is why the row is `🟡` rather than `✅`.
