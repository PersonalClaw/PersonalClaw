# DOCUMENT-FIDELITY-EDITOR — atomic plans

**Source plan:** [`DOCUMENT-FIDELITY-EDITOR`](../plans/DOCUMENT-FIDELITY-EDITOR.md)  
**Code:** `DFE`  
**Source status:** proposed

DESIGNED, not started — verified against code (no documents/parsers/, no Run/Cell/ParagraphStyle/PageSetup in model.py, only GET /raw exists, no document_editing config, no DFE commits; empty execution log is accurate). Decomposed into 8 atoms along the plan's S0–S4 session seams, splitting S1, S2, and S4 at natural feature boundaries. The only pause point is the editor atom (DFE-5), gated on owner task 2's editing-library decision (E5).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DFE-1` | ✅ | Fix the artifact surface: register missing kinds, kind-icon cards, pdf artifact preview | — | A docx artifact reads 'Word document' not 'Widget' and is filterable in the library toolbar; office cards show a kind icon instead of a broken <img> while image cards still thumbnail (test); a generated pdf artifact previews inline and a pdf file still previews (both asserted). V0 gate holds. |
| `DFE-2` | ✅ | Style-carrying document model + from_markup runs + docx writer emits new fields | `EXT:DOCUMENT-HANDLING-TOOLS:owns model.py + the four writers this extends (DONE)` | Every existing model test passes untouched; a runs-only Block answers .text and a cells-only table answers .rows via __post_init__ derivation; markdown inline formatting (**bold**/*italic*/`code`/[t](url)) round-trips into Runs and old _strip_inline behavior is gone (not left beside it); a bold run reads back bold via python-docx. |
| `DFE-3` | ✅ | First real docx→model parser with LossReport + parse→write→parse round-trip proof | `DFE-2` | paragraph↔table order preserved on an interleaved fixture; each unrepresentable construct adds a LossReport item; parse→write→parse yields an equal model across every BLOCK_KIND plus runs/styles/spans/page-setup, and a deliberate writer regression fails the test. V1 gate holds (incl. a Word-authored fixture reporting its losses honestly). |
| `DFE-4` | ✅ | Binary artifact write path + model read/render endpoints | `DFE-3` | a stale If-Match is refused 409; an oversized body is refused before buffering; a mime/kind mismatch is refused; an accepted write bumps exactly one version and logs one SEL row with byte count; GET /model returns parsed model + loss report and the render round trip is fully server-side (browser never sees OOXML); offline route reference regenerated. |
| `DFE-5` | ✅ | Editing surface: non-Monaco renderer slot, the model editor, lossy-edit contract, config | `DFE-4` | every existing content type still renders Monaco (asserted); an office type mounts the new editor; a user bolds a word, saves, and the downloaded file opens bold in Word (read-back); a lossy doc warns before edit and at save-confirm and revert restores pre-edit bytes exactly (byte-compare); test_config_roundtrip green and document_editing=off restores today's read-only preview. V2 gate holds (incl. two-tab race → 409, not silent loss). |
| `DFE-6` | ⬜ | Layout control: page setup, paragraph layout, headers/footers, page-geometry preview | `DFE-5` | A4 landscape with 2cm margins round-trips correctly on python-docx read-back; alignment/space-before-after/line-spacing/indents/keep-with-next each round-trip and the editor's controls reflect the loaded document's real values (not defaults); a header round-trips and one the model can't represent is reported not dropped; the geometry preview reflects configured size/margins and is labelled an approximation. V3 gate holds. |
| `DFE-7` | ⬜ | Sheets: styled SheetModel + xlsx parser + grid editor (formulas stay formulas) | `DFE-5` | a formula stays a formula through the round trip (today "=SUM(A1)" is written as a string); a cell edit + a number format survive download/read-back. V4 gate (sheet half) holds. |
| `DFE-8` | ⬜ | Decks: DeckModel/Slide layout+geometry+bullet levels + pptx parser + slide editor | `DFE-5` | bullet depth round-trips (the writer hardcodes level=0 today); a slide edit survives download/read-back. V4 gate (deck half) holds. |

## Atom scopes

### `DFE-1` — Fix the artifact surface: register missing kinds, kind-icon cards, pdf artifact preview

**Status:** done

Session 0 (T0.1–T0.3) + Design bullet S0

**Done when:** A docx artifact reads 'Word document' not 'Widget' and is filterable in the library toolbar; office cards show a kind icon instead of a broken <img> while image cards still thumbnail (test); a generated pdf artifact previews inline and a pdf file still previews (both asserted). V0 gate holds.

### `DFE-2` — Style-carrying document model + from_markup runs + docx writer emits new fields

**Status:** done

Session 1 T1.1–T1.3; §C1 (Run/ParagraphStyle/PageSetup/Cell + additive Block/DocumentModel fields); soul guardrails 1 (vendor-neutral) & 5 (writers stay pure)

**Done when:** Every existing model test passes untouched; a runs-only Block answers .text and a cells-only table answers .rows via __post_init__ derivation; markdown inline formatting (**bold**/*italic*/`code`/[t](url)) round-trips into Runs and old _strip_inline behavior is gone (not left beside it); a bold run reads back bold via python-docx.

### `DFE-3` — First real docx→model parser with LossReport + parse→write→parse round-trip proof

**Status:** todo

Session 1 T1.4–T1.5; §C2 (parse_docx via iter_inner_content, LossReport, images→artifact_slug, untrusted-input caps); fix stale knowledge/readers.py:243-247 ordering comment

**Done when:** paragraph↔table order preserved on an interleaved fixture; each unrepresentable construct adds a LossReport item; parse→write→parse yields an equal model across every BLOCK_KIND plus runs/styles/spans/page-setup, and a deliberate writer regression fails the test. V1 gate holds (incl. a Word-authored fixture reporting its losses honestly).

### `DFE-4` — Binary artifact write path + model read/render endpoints

**Status:** todo

Session 2 T2.1–T2.2; §C3 (PUT /api/artifacts/{slug}/raw: BINARY_KINDS guard, MAX_BINARY_CONTENT_BYTES before buffering, mime/kind match, required If-Match→409, SEL audit); GET /api/artifacts/{slug}/model + a server-side re-render endpoint

**Done when:** a stale If-Match is refused 409; an oversized body is refused before buffering; a mime/kind mismatch is refused; an accepted write bumps exactly one version and logs one SEL row with byte count; GET /model returns parsed model + loss report and the render round trip is fully server-side (browser never sees OOXML); offline route reference regenerated.

### `DFE-5` — Editing surface: non-Monaco renderer slot, the model editor, lossy-edit contract, config

**Status:** todo

Session 2 T2.3–T2.6; §C4 (EditCapability.render + ContentSurface.renderEditor branch, office types declare edit), §C5 (report shown pre-edit + repeated in save confirm + pre-edit version revertable), §C6 (document_editing 5-point wiring, off by default). GATED on owner task 2 (editing-library decision, E5); editor adopts DESIGN-SYSTEM primitives (adoption ratchet enforces).

**Done when:** every existing content type still renders Monaco (asserted); an office type mounts the new editor; a user bolds a word, saves, and the downloaded file opens bold in Word (read-back); a lossy doc warns before edit and at save-confirm and revert restores pre-edit bytes exactly (byte-compare); test_config_roundtrip green and document_editing=off restores today's read-only preview. V2 gate holds (incl. two-tab race → 409, not silent loss).

### `DFE-6` — Layout control: page setup, paragraph layout, headers/footers, page-geometry preview

**Status:** todo

Session 3 T3.1–T3.4; PageSetup/ParagraphStyle model→docx writer→parser→editor controls; §6 (preview is a labelled approximation — no rasterizer)

**Done when:** A4 landscape with 2cm margins round-trips correctly on python-docx read-back; alignment/space-before-after/line-spacing/indents/keep-with-next each round-trip and the editor's controls reflect the loaded document's real values (not defaults); a header round-trips and one the model can't represent is reported not dropped; the geometry preview reflects configured size/margins and is labelled an approximation. V3 gate holds.

### `DFE-7` — Sheets: styled SheetModel + xlsx parser + grid editor (formulas stay formulas)

**Status:** todo

Session 4 T4.1–T4.2; SheetModel per-cell format (font/fill/number-format/alignment), column widths, merges, formulas-as-formulas; parsers/xlsx_parser.py; the sheet grid editor over the model

**Done when:** a formula stays a formula through the round trip (today "=SUM(A1)" is written as a string); a cell edit + a number format survive download/read-back. V4 gate (sheet half) holds.

### `DFE-8` — Decks: DeckModel/Slide layout+geometry+bullet levels + pptx parser + slide editor

**Status:** todo

Session 4 T4.3–T4.4; DeckModel/Slide layout selection, per-shape geometry, bullet levels; parsers/pptx_parser.py; the deck editor (slide list + per-slide fields)

**Done when:** bullet depth round-trips (the writer hardcodes level=0 today); a slide edit survives download/read-back. V4 gate (deck half) holds.

