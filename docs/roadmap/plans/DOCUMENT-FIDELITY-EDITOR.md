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
