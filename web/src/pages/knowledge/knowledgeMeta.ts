import { FileText, StickyNote, BookMarked, Bookmark, Code2, Image, Music, Video, FileType2, FileSpreadsheet, Presentation, File, Shapes, Gavel } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { KnowledgeItem, KnowledgeType } from '../../lib/api'
import { epochSeconds } from '../../lib/epoch'

// ── typed knowledge formats (mirrors the OpenForge vision enum) ──
export interface TypeMeta { key: KnowledgeType; label: string; icon: LucideIcon; tone: string; group: 'text' | 'link' | 'media' | 'document' }
export const TYPES: TypeMeta[] = [
  { key: 'note', label: 'Note', icon: StickyNote, tone: 'var(--color-primary)', group: 'text' },
  { key: 'fleeting', label: 'Fleeting note', icon: FileText, tone: 'var(--color-primary)', group: 'text' },
  { key: 'journal', label: 'Journal', icon: BookMarked, tone: 'var(--color-primary)', group: 'text' },
  { key: 'gist', label: 'Gist', icon: Code2, tone: 'var(--color-info)', group: 'text' },
  { key: 'bookmark', label: 'Bookmark', icon: Bookmark, tone: 'var(--color-info)', group: 'link' },
  { key: 'image', label: 'Image', icon: Image, tone: 'var(--color-ok)', group: 'media' },
  { key: 'audio', label: 'Audio', icon: Music, tone: 'var(--color-ok)', group: 'media' },
  { key: 'video', label: 'Video', icon: Video, tone: 'var(--color-ok)', group: 'media' },
  { key: 'pdf', label: 'PDF', icon: FileType2, tone: 'var(--color-warn)', group: 'document' },
  { key: 'document', label: 'Document', icon: File, tone: 'var(--color-warn)', group: 'document' },
  { key: 'sheet', label: 'Spreadsheet', icon: FileSpreadsheet, tone: 'var(--color-warn)', group: 'document' },
  { key: 'slides', label: 'Slides', icon: Presentation, tone: 'var(--color-warn)', group: 'document' },
]

/** The mirrored-artifact type (PEP-7). Deliberately NOT a member of `TYPES`: that array is
 *  the create picker's catalog, and an artifact is mirrored from the Artifacts library, never
 *  authored here. It still needs a label/icon/tone because a search result CAN be one — and
 *  without this entry `resolveType` fell through to `note`, so an artifact hit read "Note". */
export const ARTIFACT_TYPE: TypeMeta = {
  key: 'artifact', label: 'Artifact', icon: Shapes, tone: 'var(--color-secondary)', group: 'document',
}

/** Is this item a mirrored artifact rather than a knowledge item the user filed? */
export function isArtifactItem(it: Pick<KnowledgeItem, 'type' | 'item_type'>): boolean {
  return it.type === 'artifact' || (it.item_type || '').toLowerCase() === 'artifact'
}

/** PROACTIVE-ASSISTANT §2.2's decision. Out of `TYPES` for the same reason as `ARTIFACT_TYPE`
 *  and a DIFFERENT missing half: `log_decision` also mints the one-shot review trigger, so a
 *  decision authored from the create picker would be a decision that never comes back — which is
 *  why `handlers/knowledge.py` refuses to create one and points at the chat tool instead.
 *
 *  It still needs a label/icon/tone, because a library or search result CAN be one. Without this
 *  entry `resolveType` fell through to `note` and every decision in the library read "Note". */
export const DECISION_TYPE: TypeMeta = {
  key: 'decision', label: 'Decision', icon: Gavel, tone: 'var(--color-secondary)', group: 'text',
}

/** Is this item a logged decision? */
export function isDecisionItem(it: Pick<KnowledgeItem, 'type' | 'item_type'>): boolean {
  return it.type === 'decision' || (it.item_type || '').toLowerCase() === 'decision'
}

/** The model-backed terminal stages (`runner.MODEL_BACKED_TERMINAL_STAGES`), in pipeline order,
 *  with the words a person reads for each. */
const MODEL_STAGES: ReadonlyArray<readonly [string, string]> = [
  ['insights', 'Insights'],
  ['entities', 'Entity extraction'],
  ['intents', 'Intent matching'],
]

/** Did this item's model-backed enrichment FAIL on its last run — and in which words to say so?
 *
 *  🔴 A NO-MODEL HOME SHOWED NOTHING HERE. The row badged `partial` ("Incomplete") and `failed`,
 *  but RET-2 files a no-provider ingest `unsearchable` (it has no embedding either), which no
 *  badge knew — so after "Regenerate intelligence" every job failed and every row looked exactly
 *  as healthy as before, while each item's own page showed Insights and Entities ✕.
 *
 *  Read off the runner's persisted phase map (`file_metadata.node_phases`), the ground truth the
 *  item page's pipeline strip draws, never parsed out of `processing_error` prose. A model-backed
 *  stage reports `failed` only when its model call raised, so the reason is the model's — and
 *  "unavailable" is the runner's own word for it. An item that is queued or processing is about
 *  to replace that record, so it reports nothing here (its "Enriching" badge speaks instead). */
export function failedEnrichment(
  it: Pick<KnowledgeItem, 'processing_status' | 'file_metadata'>,
): { stages: string[]; reason: string } | null {
  if (it.processing_status === 'queued' || it.processing_status === 'processing') return null
  const raw = it.file_metadata?.node_phases
  const phases = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  const stages = MODEL_STAGES.filter(([key]) => phases[key] === 'failed').map(([, label]) => label)
  if (!stages.length) return null
  // Sentence case: only the first stage keeps its capital ("Insights and entity extraction").
  const words = stages.map((s, i) => (i === 0 ? s : s.toLowerCase()))
  const named = words.length === 1 ? words[0] : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
  return { stages, reason: `${named} failed — the model was unavailable` }
}

/** What "Regenerate intelligence" says when the gateway ACCEPTED it — never silence.
 *
 *  🔴 THE PAGE USED TO SAY NOTHING EITHER WAY: an empty `catch` ("surfaced by reload") swallowed
 *  the refusal and the success path ignored `{queued}`, so a click read as "nothing happened". A
 *  refusal now carries the server's own sentence through `reportActionFailure`; this is the other
 *  half. "They update here as they finish" is literal: the page polls while any item is queued,
 *  and each row, the stat chips and the graph re-read as the jobs land. */
export function regenerateQueuedSentence(queued: number): string {
  if (queued <= 0) return 'Nothing to regenerate — no item is missing its insights or entities.'
  return `Regenerating intelligence for ${queued} item${queued === 1 ? '' : 's'} — they update here as they finish.`
}

/** Resolve an item's visual type. Prefer the vision `type`; else infer from the
 *  backend's free `item_type` string / mime_type / url, falling back to note. */
export function resolveType(it: Pick<KnowledgeItem, 'type' | 'item_type' | 'mime_type' | 'url'>): TypeMeta {
  // Checked before the `TYPES` lookups because they are deliberately absent from them.
  if (isArtifactItem(it)) return ARTIFACT_TYPE
  if (isDecisionItem(it)) return DECISION_TYPE
  const explicit = it.type && TYPES.find((t) => t.key === it.type)
  if (explicit) return explicit
  const raw = (it.item_type || '').toLowerCase()
  const byRaw = TYPES.find((t) => t.key === raw)
  if (byRaw) return byRaw
  const mime = (it.mime_type || '').toLowerCase()
  if (mime.startsWith('image/')) return typeMeta('image')
  if (mime.startsWith('audio/')) return typeMeta('audio')
  if (mime.startsWith('video/')) return typeMeta('video')
  if (mime.includes('pdf')) return typeMeta('pdf')
  if (mime.includes('spreadsheet') || mime.includes('excel') || mime.includes('csv')) return typeMeta('sheet')
  if (mime.includes('presentation') || mime.includes('powerpoint')) return typeMeta('slides')
  if (mime.includes('word') || mime.includes('document')) return typeMeta('document')
  if (it.url) return typeMeta('bookmark')
  return typeMeta('note')
}
export function typeMeta(k: KnowledgeType): TypeMeta { return TYPES.find((t) => t.key === k) ?? TYPES[0] }

// Proper display names for gist languages — acronyms/casing the naive capitalize gets
// wrong ("Sql" → "SQL", "cpp" → "C++"). Anything not listed title-cases its first letter.
const _LANG_DISPLAY: Record<string, string> = {
  typescript: 'TypeScript', javascript: 'JavaScript', python: 'Python', go: 'Go',
  rust: 'Rust', java: 'Java', c: 'C', cpp: 'C++', html: 'HTML', css: 'CSS',
  sql: 'SQL', bash: 'Bash', json: 'JSON', yaml: 'YAML', markdown: 'Markdown',
}
export function languageLabel(lang: string): string {
  const k = lang.trim().toLowerCase()
  return _LANG_DISPLAY[k] || (lang.charAt(0).toUpperCase() + lang.slice(1))
}

/** Human label for an item's type, with the gist language appended ("Gist · Python")
 *  so the language is visible wherever the type is shown. */
export function typeLabel(it: Pick<KnowledgeItem, 'type' | 'item_type' | 'mime_type' | 'url' | 'gist_language'>): string {
  const tm = resolveType(it)
  if (tm.key === 'gist' && (it.gist_language || '').trim()) {
    return `${tm.label} · ${languageLabel(it.gist_language!)}`
  }
  return tm.label
}

/** Normalize an item's insights blob to displayable {label, value} rows.
 *  OpenForge stores insights as a category-keyed dict; render whatever's there. */
const _titleCase = (k: string) => k.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function insightRows(insights?: Record<string, unknown> | null): Array<{ label: string; value: string }> {
  if (!insights || typeof insights !== 'object') return []
  const out: Array<{ label: string; value: string }> = []
  for (const [k, v] of Object.entries(insights)) {
    if (v == null || v === '') continue
    const label = _titleCase(k)
    // A nested object (e.g. a Tier-3 intent result {ticker, thesis, confidence})
    // reads as "Field: value · Field: value", never a raw JSON blob.
    if (Array.isArray(v)) {
      if (v.length) out.push({ label, value: v.map((x) => (x && typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') })
    } else if (typeof v === 'object') {
      const parts = Object.entries(v as Record<string, unknown>)
        .filter(([, vv]) => vv != null && vv !== '')
        .map(([kk, vv]) => `${_titleCase(kk)}: ${Array.isArray(vv) ? vv.join(', ') : String(vv)}`)
      if (parts.length) out.push({ label, value: parts.join(' · ') })
    } else {
      const value = String(v)
      if (value.trim()) out.push({ label, value })
    }
  }
  return out
}

// ── per-type create input shape (drives the dedicated create page) ──
//   text:  title + body editor (markdown / plain)
//   gist:  title + code editor + language
//   bookmark: url + optional title  → real backend web_url source
//   file:  drag-drop upload (mime-restricted) → real backend /ingest
export type CreateKind = 'text' | 'gist' | 'bookmark' | 'file'
export function createKind(t: KnowledgeType): CreateKind {
  if (t === 'gist') return 'gist'
  if (t === 'bookmark') return 'bookmark'
  if (['image', 'audio', 'video', 'pdf', 'document', 'sheet', 'slides'].includes(t)) return 'file'
  return 'text'  // note, fleeting, journal
}

export const ACCEPTED_MIMES: Record<string, string> = {
  image: 'image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml',
  audio: 'audio/mpeg,audio/wav,audio/ogg,audio/flac,audio/mp4,audio/x-m4a,audio/webm',
  video: 'video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm,video/x-m4v,.m4v',
  pdf: 'application/pdf',
  document: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/plain,text/markdown,.markdown,.text',
  sheet: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv,text/tab-separated-values,.tsv',
  slides: 'application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.ms-powerpoint',
}

export const GIST_LANGUAGES = ['typescript', 'javascript', 'python', 'go', 'rust', 'java', 'c', 'cpp', 'html', 'css', 'sql', 'bash', 'json', 'yaml', 'markdown']

export function fmtBytes(n?: number): string {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

/** How long ago a library item was created or last touched.
 *
 *  🔴 THIS HAD NEITHER OF THE TWO GUARDS ITS SIBLINGS SHIP, and a knowledge library is the surface
 *  where both bite. Censused across the eleven time formatters in `web/src`, `taskMeta.relTime` is
 *  the reference — it has both — and this was the ONLY one with neither:
 *
 *   1. **No ceiling.** The day branch was unbounded, so an item created last year read
 *      **"412d ago"**. Nobody communicates a date that way, and a library is long-lived BY
 *      DEFINITION: `created_at` renders on `LibraryHome`, `KnowledgeListPage` rows and the detail
 *      header, so an aged item is the normal case here rather than an edge one.
 *   2. **No future guard.** A stamp ahead of now made `s` negative, which falls through `s < 60`
 *      and renders **"just now"** — a confident wrong answer, not a blank.
 *
 *  Both now match `taskMeta`: past a week, show the actual date; a future stamp shows its date too,
 *  because "in 3 days" is not what any of these four call sites mean. Parsing goes through
 *  `epochSeconds`, the canonical parser, rather than a fourth local `Date.parse`. */
export function relTime(iso?: string): string {
  const secs = epochSeconds(iso)
  if (secs === undefined) return ''
  const t = secs * 1000
  const s = Date.now() / 1000 - secs
  if (s < 0) return new Date(t).toLocaleDateString()
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`
  return new Date(t).toLocaleDateString()
}
