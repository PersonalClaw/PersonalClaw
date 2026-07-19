import { FileText, StickyNote, BookMarked, Bookmark, Code2, Image, Music, Video, FileType2, FileSpreadsheet, Presentation, File } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { KnowledgeItem, KnowledgeType } from '../../lib/api'

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

/** Resolve an item's visual type. Prefer the vision `type`; else infer from the
 *  backend's free `item_type` string / mime_type / url, falling back to note. */
export function resolveType(it: Pick<KnowledgeItem, 'type' | 'item_type' | 'mime_type' | 'url'>): TypeMeta {
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

export function relTime(iso?: string): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = (Date.now() - t) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
