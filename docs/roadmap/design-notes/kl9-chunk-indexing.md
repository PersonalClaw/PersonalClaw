# KL-9 — chunk indexing design

**chunks table** `(id, item_id, chunk_index, text, embedding, section, line_start, line_end)`
created in `KnowledgeStore._init_schema()` via idempotent `CREATE TABLE IF NOT EXISTS`
(the house pattern), FK `item_id → items(id) ON DELETE CASCADE`, index on `item_id`.

**Boundary strategy (`knowledge/chunking.py`).** Split content into 1-based lines; a
markdown heading line (`^#{1,6}\s`) opens a section. Because readers already render
pptx as `## Slide N: …`, xlsx/csv as `## <sheet>`, and docx headings as `#…`, slide/
sheet/heading boundaries all reduce to one heading rule. PDF joins pages with `\n` (no
markers) → one section → **size fallback**. A section ≤ `max_chars` (1500) is one chunk;
larger sections and structureless docs size-split greedily on line boundaries with a
~`overlap` (200-char) trailing carry-over.

**section/line ranges.** `section` = the heading text (None for preamble/structureless);
`line_start`/`line_end` = the 1-based source line span of the emitted lines (exact,
including for overlapped sub-chunks).

**Embedder.** Reuse `UnifiedEmbedder.embed()` (chunk body text) — no new dependency.

**Ingest.** `_embed` keeps the whole-item vector (now title+summary only — the
`compose_item_text` 1000-char body top-up is deleted, clean break) and additively embeds
chunks via `store.replace_chunks()`.
