# DOCUMENT-HANDLING-TOOLS — atomic plans

**Source plan:** [`DOCUMENT-HANDLING-TOOLS`](../plans/DOCUMENT-HANDLING-TOOLS.md)  
**Code:** `DHT`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `DHT-1` | ✅ | Writer seam: documents/ package (declarative model + writer registry) | — | documents/model.py holds DocumentModel/SheetModel/DeckModel (zero OOXML vocabulary); registry.py exposes register_writer/get_writer (None for unknown, never raises)/available_formats reflecting what is actually usable; test_documents_registry contracts pass |
| `DHT-2` | ✅ | docx writer + markdown/HTML->model authoring path (sanitize + redact) | `DHT-1` | docx_writer.py renders every Block.kind and its output re-reads through the existing knowledge/readers.py docx reader (round-trip); document_from_markdown/document_from_html route HTML through sanitize_html + redact_credentials; a planted AWS key does not survive into the generated file |
| `DHT-3` | ✅ | xlsx writer (named sheets, header row, preserved cell types) | `DHT-1` | xlsx_writer.py multi-sheet output re-reads through the existing xlsx reader with numbers still numeric (not stringified) and bool-vs-int preserved; illegal sheet-name sanitization and ragged-row normalization covered |
| `DHT-4` | ✅ | Artifact kinds + MIME map + create_binary hardening (#94 fix) | — | ALLOWED_KINDS/BINARY_KINDS gain docx/xlsx (csv as text kind); _MIME_TO_EXT gains the office/PDF mimes so /raw serves correct Content-Type; native.py create_binary now raises ValueError for a non-binary kind instead of coercing to image (regression test proves the old coercion gone; issue #94 fixed as a consequence) |
| `DHT-5` | ✅ | Agent tools: document_create + sheet_create + document_formats | `DHT-2`, `DHT-3`, `DHT-4` | tools in mcp_artifacts.py create real artifacts, project-scoped + SEL-audited on success/refused/error; re-running same name updates in place (no -2 slug, reusing artifact_save dedup); oversized output returns a typed refusal naming the real size before storing; reply carries slug+version+raw URL, never bytes/base64 |
| `DHT-6` | ✅ | Frontend S1: docx/xlsx content types + OfficeDocPreview + extract endpoint | `DHT-4`, `DHT-5` | docx/xlsx content types + .doc.ts registered following the image precedent; GET /api/artifacts/{slug}/extract backs OfficeDocPreview (honest text-preview + Download, reusing doc_parser); a generated docx/xlsx appears in the library with preview + working download; no primitive-adoption ratchet trip; ui-docs drift guard green |
| `DHT-7` | ✅ | pptx writer + deck_from_markdown + deck_create tool + pptx kind | `DHT-1`, `DHT-5` | pptx_writer.py + deck_from_markdown (#/## slide boundaries, <!-- notes: --> speaker notes) + deck_create tool + pptx artifact kind; a markdown outline becomes a deck re-readable by the existing pptx reader including notes; placeholder-identity regression (title-via-idx not object identity) covered |
| `DHT-8` | ✅ | reportlab core dependency + pdf writer (platypus flowables) | `DHT-1` | reportlab>=4,<5 added to core dependencies (with WHY comment) and NOT in personalclaw-backend.spec excludes; uv.lock re-locked same commit; pdf_writer.py renders DocumentModel and re-reads through the existing pdfplumber reader with headings/paragraphs/tables/lists intact; pdf unconditionally in available_formats(); bullets extract clean (not (cid:127)) |
| `DHT-9` | ✅ | Frontend S2: pptx content type + pdf/csv kinds on existing types | `DHT-6`, `DHT-7` | pptx content type registered reusing OfficeDocPreview; kinds:['pdf'] added to the EXISTING pdf type (PdfFile renderer) and kinds:['csv'] to the existing csv type — no duplicate pdf/csv type (grep/test gate); a generated deck previews+downloads and a generated PDF uses the existing renderer |
| `DHT-10` | ✅ | Round-trip export: knowledge item / text artifact -> writer format (+ csv writer) | `DHT-5`, `DHT-8` | a source argument on document_create resolves a knowledge-item id or a TEXT artifact slug to markdown and flows through the same writer path (no new endpoint); a binary artifact is refused; a knowledge item becomes a .docx artifact and a JSON/rows artifact becomes .csv/.xlsx; source name used as title only when body does not already open with an H1 |

## Atom scopes

### `DHT-1` — Writer seam: documents/ package (declarative model + writer registry)

**Status:** done

Design S1 (the writer seam); Contracts C1 (document model + writer registry); T1.1

**Done when:** documents/model.py holds DocumentModel/SheetModel/DeckModel (zero OOXML vocabulary); registry.py exposes register_writer/get_writer (None for unknown, never raises)/available_formats reflecting what is actually usable; test_documents_registry contracts pass

### `DHT-2` — docx writer + markdown/HTML->model authoring path (sanitize + redact)

**Status:** done

Design S1; Contracts C2 (markup->model, security clause); T1.2

**Done when:** docx_writer.py renders every Block.kind and its output re-reads through the existing knowledge/readers.py docx reader (round-trip); document_from_markdown/document_from_html route HTML through sanitize_html + redact_credentials; a planted AWS key does not survive into the generated file

### `DHT-3` — xlsx writer (named sheets, header row, preserved cell types)

**Status:** done

Design S1; Contracts C1 (SheetModel); T1.3

**Done when:** xlsx_writer.py multi-sheet output re-reads through the existing xlsx reader with numbers still numeric (not stringified) and bool-vs-int preserved; illegal sheet-name sanitization and ragged-row normalization covered

### `DHT-4` — Artifact kinds + MIME map + create_binary hardening (#94 fix)

**Status:** done

Contracts C4 (kinds, MIME, size, coercion fix); T1.4

**Done when:** ALLOWED_KINDS/BINARY_KINDS gain docx/xlsx (csv as text kind); _MIME_TO_EXT gains the office/PDF mimes so /raw serves correct Content-Type; native.py create_binary now raises ValueError for a non-binary kind instead of coercing to image (regression test proves the old coercion gone; issue #94 fixed as a consequence)

### `DHT-5` — Agent tools: document_create + sheet_create + document_formats

**Status:** done

Contracts C3 (agent tools); T1.5

**Done when:** tools in mcp_artifacts.py create real artifacts, project-scoped + SEL-audited on success/refused/error; re-running same name updates in place (no -2 slug, reusing artifact_save dedup); oversized output returns a typed refusal naming the real size before storing; reply carries slug+version+raw URL, never bytes/base64

### `DHT-6` — Frontend S1: docx/xlsx content types + OfficeDocPreview + extract endpoint

**Status:** done

Contracts C5 (one registration per kind); T1.6

**Done when:** docx/xlsx content types + .doc.ts registered following the image precedent; GET /api/artifacts/{slug}/extract backs OfficeDocPreview (honest text-preview + Download, reusing doc_parser); a generated docx/xlsx appears in the library with preview + working download; no primitive-adoption ratchet trip; ui-docs drift guard green

### `DHT-7` — pptx writer + deck_from_markdown + deck_create tool + pptx kind

**Status:** done

Design S2 (pptx); T2.1

**Done when:** pptx_writer.py + deck_from_markdown (#/## slide boundaries, <!-- notes: --> speaker notes) + deck_create tool + pptx artifact kind; a markdown outline becomes a deck re-readable by the existing pptx reader including notes; placeholder-identity regression (title-via-idx not object identity) covered

### `DHT-8` — reportlab core dependency + pdf writer (platypus flowables)

**Status:** done

Design S2 (pdf); Contracts C4b (dependency ruling); T2.2

**Done when:** reportlab>=4,<5 added to core dependencies (with WHY comment) and NOT in personalclaw-backend.spec excludes; uv.lock re-locked same commit; pdf_writer.py renders DocumentModel and re-reads through the existing pdfplumber reader with headings/paragraphs/tables/lists intact; pdf unconditionally in available_formats(); bullets extract clean (not (cid:127))

### `DHT-9` — Frontend S2: pptx content type + pdf/csv kinds on existing types

**Status:** done

Contracts C5; T2.3

**Done when:** pptx content type registered reusing OfficeDocPreview; kinds:['pdf'] added to the EXISTING pdf type (PdfFile renderer) and kinds:['csv'] to the existing csv type — no duplicate pdf/csv type (grep/test gate); a generated deck previews+downloads and a generated PDF uses the existing renderer

### `DHT-10` — Round-trip export: knowledge item / text artifact -> writer format (+ csv writer)

**Status:** done

Design S2 (the round-trip); T2.4

**Done when:** a source argument on document_create resolves a knowledge-item id or a TEXT artifact slug to markdown and flows through the same writer path (no new endpoint); a binary artifact is refused; a knowledge item becomes a .docx artifact and a JSON/rows artifact becomes .csv/.xlsx; source name used as title only when body does not already open with an H1

