import { useRef, useState } from 'react'
import { ArrowLeft, Check, Loader2, Upload, X, Link2, FileText, Mic } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { Button } from '../../ui/Button'
import { ChipInput } from '../tasks/formControls'
import { api, type KnowledgeType } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { TYPES, typeMeta, createKind, ACCEPTED_MIMES, GIST_LANGUAGES, fmtBytes } from './knowledgeMeta'
import { GistEditor } from './GistEditor'
import { createKnowledge, updateKnowledge, uploadKnowledgeFile } from './knowledgeStore'
import { AudioRecorder } from './AudioRecorder'

/** Dedicated create PAGE (matches the create-page pattern used across the app):
 *  step 1 = a type-grid picker (all 12 knowledge formats); step 2 = a per-type
 *  authoring form. Files upload to the node-graph pipeline; bookmarks create a
 *  typed item whose URL the graph scrapes; text/gist/journal author a typed item
 *  directly (POST /items). Every kind lands as ONE logical-document item that the
 *  ingestion pipeline then enriches. */
export function KnowledgeCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const [type, setType] = useState<KnowledgeType | null>(null)

  if (!type) {
    return (
      <div className="flex h-full flex-col">
        <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back" size={40} onClick={onBack} /><span data-type="title-l" className="text-on-surface">Add knowledge</span></div>} />
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
            <p className="text-on-surface-low text-[0.9375rem] mb-l text-center">What kind of knowledge are you adding?</p>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-m">
              {TYPES.map((t) => (
                <button key={t.key} type="button" onClick={() => setType(t.key)}
                  className="group flex flex-col items-center gap-2 rounded-xl bg-surface-container p-l hover:bg-surface-high transition-colors">
                  <span className="inline-flex size-12 items-center justify-center rounded-xl" style={{ background: `color-mix(in srgb, ${t.tone} 16%, transparent)` }}><t.icon size={22} style={{ color: t.tone }} /></span>
                  <span className="text-on-surface text-[0.875rem]" style={{ fontVariationSettings: '"wght" 500' }}>{t.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    )
  }

  return <CreateForm type={type} onBack={() => setType(null)} onClose={onBack} onCreated={onCreated} />
}

function CreateForm({ type, onBack, onClose, onCreated }: { type: KnowledgeType; onBack: () => void; onClose: () => void; onCreated: () => void }) {
  const tm = typeMeta(type)
  const kind = createKind(type)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [url, setUrl] = useState('')
  const [language, setLanguage] = useState('typescript')
  const [tags, setTags] = useState<string[]>([])
  // Tag autocomplete catalog — cached + persisted so the picker is instant on reopen.
  const { data: knownTags } = useCachedData('knowledge:tags', () => api.knowledgeTags().catch(() => [] as string[]), { persist: true })
  const [file, setFile] = useState<File | null>(null)
  // Set when the selected file exceeds its per-type cap — disables Add + shows the reason.
  const [fileTooBig, setFileTooBig] = useState(false)
  const [preview, setPreview] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  // Audio supports two sources: upload an existing file, or record in-browser.
  const [audioSrc, setAudioSrc] = useState<'upload' | 'record'>('upload')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  // Upload progress for a large (chunked/resumable) knowledge file; -1 = not uploading.
  const [uploadPct, setUploadPct] = useState(-1)
  const fileRef = useRef<HTMLInputElement>(null)

  async function pickFile(f: File) {
    setFile(f); setErr(''); setFileTooBig(false)
    if (f.type.startsWith('image/')) setPreview(URL.createObjectURL(f))
    if (!title) setTitle(f.name)
    // Per-filetype pre-check ON SELECTION (parity with chat-attach + Files): surface the
    // oversize message immediately and keep the Add button disabled — don't wait for a
    // click to tell the user their 2.5GB video won't fit.
    const { precheck } = await import('../../lib/chunkedUpload')
    const pcErr = await precheck(f)
    if (pcErr) { setErr(pcErr); setFileTooBig(true) }
  }

  const canSave =
    kind === 'bookmark' ? /^https?:\/\//.test(url.trim())
      : kind === 'file' ? (!!file && !fileTooBig)
      : !!(title.trim() || content.trim())

  async function save() {
    if (!canSave || busy) return
    setBusy(true); setErr('')
    try {
      if (kind === 'file' && file) {
        // Client-side per-filetype pre-check — reject oversize before uploading a byte.
        const { precheck } = await import('../../lib/chunkedUpload')
        const pcErr = await precheck(file)
        if (pcErr) { setErr(pcErr); setBusy(false); return }
        // Every uploaded file → ONE logical-document item, run through its node-graph.
        // Large files stream via the resumable protocol with progress.
        const res = await uploadKnowledgeFile(file, (p) => setUploadPct(p.pct))
        setUploadPct(-1)
        // The ingest endpoint only takes bytes — apply the form's title/tags to the
        // created item. A user-typed title (≠ the seeded filename) also blocks the
        // pipeline's AI-title promotion (it only replaces filename-seeded titles);
        // same for tags vs AI topics. Skip on dedup: the hit is someone else's item.
        const custom: Record<string, unknown> = {}
        if (title.trim() && title.trim() !== file.name) custom.title = title.trim()
        if (tags.length) custom.tags = tags
        if (res.item_id && !(res as { deduped?: boolean }).deduped && Object.keys(custom).length) {
          await updateKnowledge(res.item_id, custom).catch(() => {})
        }
      } else if (kind === 'bookmark') {
        // Bookmark → typed item carrying its URL; the node-graph scrapes the page.
        await createKnowledge({ type: 'bookmark', title: title.trim(), url: url.trim(), tags })
      } else {
        // text / gist / journal → typed item (POST /api/knowledge/items)
        await createKnowledge({ type, title: title.trim(), content, tags, gist_language: kind === 'gist' ? language : undefined })
      }
      onCreated()
    } catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setBusy(false); setUploadPct(-1) }
  }

  // Fleeting notes are content-only (auto-titled on ingest); journals are date-titled —
  // neither shows an editable title (matches the detail page's edit shell).
  const titleEditable = type !== 'fleeting' && type !== 'journal'

  // Cmd/Ctrl+Enter saves from anywhere in the form (the create-flow shortcut from the
  // vision) — a quick-capture affordance so you needn't reach for the Save button.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); save() }
  }

  return (
    <div className="flex h-full flex-col" onKeyDown={onKeyDown}>
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back to types" size={40} onClick={onBack} /><span data-type="title-l" className="text-on-surface inline-flex items-center gap-s"><tm.icon size={18} style={{ color: tm.tone }} /> New {tm.label.toLowerCase()}</span></div>} />
      {/* Full-height authoring shell mirroring the detail page's edit layout: inline title
          at top, a per-type middle that fills the height (Monaco for gist, textarea for
          text, drop-zone for files, URL field for bookmarks), and inline tags.
          Cancel/Save live in the bottom bar. */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex h-full min-h-0 flex-col gap-l px-l pt-l pb-l" style={{ maxWidth: 'var(--content-width)' }}>

          {/* Bookmark → its URL is the PRIMARY field (required; the page is scraped on
              save), so it leads — above the optional title — and gets autofocus. */}
          {kind === 'bookmark' && (
            <div className="shrink-0 flex items-center gap-s rounded-md bg-surface-container px-m h-10">
              <Link2 size={15} className="text-on-surface-low shrink-0" />
              <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" autoFocus aria-label="Bookmark URL" className="flex-1 bg-transparent text-on-surface text-[0.9375rem] outline-none placeholder:text-on-surface-low" />
            </div>
          )}

          {/* Inline title (underline style) — same as the edit shell. Bookmark titles are
              optional (default to the scraped page title) and sit BELOW the URL; text/gist
              use it as the primary title at the top. */}
          {titleEditable && (
            <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus={kind !== 'bookmark'}
              placeholder={kind === 'bookmark' ? 'Title (optional — defaults to the page title)' : `${tm.label} title`}
              aria-label={`${tm.label} title`}
              className="shrink-0 w-full bg-transparent text-on-surface outline-none border-b border-outline-variant/40 pb-1.5 text-[1.0625rem] focus:border-primary placeholder:text-on-surface-low" data-type="title-l" />
          )}

          {/* Gist language selector — inline label + select, same as the edit shell. */}
          {kind === 'gist' && (
            <div className="shrink-0 flex items-center gap-2">
              <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Language</span>
              <select value={language} onChange={(e) => setLanguage(e.target.value)} aria-label="Gist language"
                className="h-8 appearance-none rounded-md bg-surface-container px-m text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50 [color-scheme:dark]">
                {GIST_LANGUAGES.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
          )}

          {/* Tags — inline ChipInput, same place as the edit shell. */}
          <div className="shrink-0"><ChipInput values={tags} onChange={setTags} placeholder="Add a tag, Enter" suggestions={knownTags ?? []} /></div>

          {/* Body — fills the available height like the edit shell. */}
          {kind === 'gist' && (
            <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container">
              <GistEditor value={content} onChange={setContent} language={language} />
            </div>
          )}
          {kind === 'text' && (
            <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-outline-variant/40 bg-surface-container">
              {/* Focus the body when there's no title field (fleeting/journal) — it's the
                  primary input, so the cursor should land here rather than nowhere. */}
              <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Markdown supported…" autoFocus={!titleEditable} aria-label="Note content"
                className="h-full w-full resize-none bg-transparent px-m py-2 text-on-surface text-[0.875rem] leading-relaxed outline-none" />
            </div>
          )}
          {kind === 'file' && (
            <div className="min-h-0 flex-1 flex flex-col">
              {/* Audio: upload OR record in-browser. */}
              {type === 'audio' && (
                <div className="mb-m flex gap-1 rounded-pill bg-surface-high p-0.5 w-max">
                  {(['upload', 'record'] as const).map((s) => (
                    <button key={s} type="button" onClick={() => { setAudioSrc(s); setFile(null); setPreview(null); setFileTooBig(false); setErr('') }}
                      className={`inline-flex items-center gap-1.5 rounded-pill px-3 h-8 text-[0.8125rem] transition-colors ${audioSrc === s ? 'bg-surface text-on-surface' : 'text-on-surface-low hover:text-on-surface'}`}>
                      {s === 'upload' ? <Upload size={14} /> : <Mic size={14} />} {s === 'upload' ? 'Upload' : 'Record'}
                    </button>
                  ))}
                </div>
              )}
              {type === 'audio' && audioSrc === 'record' ? (
                <AudioRecorder
                  onRecorded={(f) => { setFile(f); setErr(''); if (!title) setTitle(f.name) }}
                  onClear={() => setFile(null)}
                />
              ) : (
                <>
                  <input ref={fileRef} type="file" hidden accept={ACCEPTED_MIMES[type] || undefined} onChange={(e) => { const f = e.target.files?.[0]; if (f) pickFile(f); e.target.value = '' }} />
                  <div onClick={() => !file && fileRef.current?.click()} onDragOver={(e) => { e.preventDefault(); setDragOver(true) }} onDragLeave={() => setDragOver(false)} onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) pickFile(f) }}
                    className={`min-h-0 flex-1 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed cursor-pointer transition-colors ${dragOver ? 'border-primary bg-primary/5' : file ? 'border-primary/40' : 'border-outline-variant/60 hover:border-primary/50'}`}>
                    {file ? (
                      <div className="flex items-center gap-m px-m">
                        {preview ? <img src={preview} alt="" className="size-16 rounded-md object-cover" /> : <tm.icon size={28} style={{ color: tm.tone }} />}
                        <div className="min-w-0"><div className="truncate text-on-surface text-[0.875rem]">{file.name}</div><div className="text-on-surface-low text-[0.75rem]">{fmtBytes(file.size)}</div></div>
                        <button type="button" onClick={(e) => { e.stopPropagation(); setFile(null); setPreview(null); setFileTooBig(false); setErr('') }} className="text-on-surface-low hover:text-danger"><X size={16} /></button>
                      </div>
                    ) : (
                      <><Upload size={22} className="text-on-surface-low" /><span className="text-on-surface-low text-[0.875rem]">Drop a {tm.label.toLowerCase()} file or click to choose</span></>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {err && <p role="alert" className="shrink-0 text-danger text-[0.8125rem]">{err}</p>}
          {uploadPct >= 0 && (
            <div className="flex shrink-0 items-center gap-2 text-[0.75rem] text-on-surface-var">
              <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
              <span>Uploading… {uploadPct}%</span>
              <span className="h-1 w-32 overflow-hidden rounded-full bg-surface-high">
                <span className="block h-full rounded-full bg-primary transition-[width] duration-200" style={{ width: `${uploadPct}%` }} />
              </span>
            </div>
          )}
        </div>
      </div>
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex items-center justify-between gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.75rem]"><FileText size={12} /> Saved to your knowledge library, then enriched automatically.</span>
          <div className="flex gap-s">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={save} disabled={busy || !canSave}>{busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />} {busy ? 'Saving…' : `Add ${tm.label.toLowerCase()}`}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
