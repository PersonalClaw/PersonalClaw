import { useEffect, useRef, useState } from 'react'
import { Download, Trash2, Check, HardDrive, AlertTriangle, X, Search, Lock } from 'lucide-react'
import { api, type AvailableModel } from '../../lib/api'
import { confirmDelete } from '../../ui/dialog'
import { WavyProgress } from '../../ui/WavyProgress'
import { useModelDownloads } from './useModelDownloads'

const MB = (bytes: number) => (bytes / 1024 / 1024).toFixed(0)

/** Download manager for ANY local downloadable model provider — faster-whisper, piper,
 *  sentence-transformers, the diarization backends, ollama. One uniform surface: lists
 *  the provider's catalog (from /api/models/available) with download/delete; a
 *  `searchable` provider (ollama) also gets a search box that queries its remote library
 *  and lets you pull any result. Downloaded models become bindable in Models. Downloads
 *  are async jobs (minutes-long) streaming live progress over per-job SSE, surviving a
 *  reload via useModelDownloads. Fully provider-scoped — core hardcodes no provider. */
export function LocalModelManager({
  provider, models, searchable, onChanged,
}: { provider: string; models: AvailableModel[]; searchable?: boolean; onChanged: () => void }) {
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<AvailableModel[] | null>(null)
  const [searching, setSearching] = useState(false)
  const searchSeq = useRef(0)

  const { jobs, start, cancel } = useModelDownloads(provider, onChanged)

  const setErr = (name: string, msg: string | null) => setErrors((prev) => {
    const next = { ...prev }; if (msg) next[name] = msg; else delete next[name]; return next
  })
  const download = async (name: string) => {
    setErr(name, null)
    try { await start(name) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Download failed'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw text */ }
      setErr(name, msg)
    }
  }
  const remove = async (name: string) => {
    if (!(await confirmDelete('model', name))) return
    setErr(name, null)
    try { await api.deleteLocalModel(provider, name); onChanged() }
    catch (e) { setErr(name, e instanceof Error ? e.message : 'Delete failed') }
  }

  // Debounced remote-catalog search (searchable providers only, e.g. ollama).
  useEffect(() => {
    if (!searchable) return
    const q = query.trim()
    if (!q) { setSearchResults(null); setSearching(false); return }
    const seq = ++searchSeq.current
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const res = await api.searchLocalModels(provider, q)
        if (seq === searchSeq.current) setSearchResults(res as unknown as AvailableModel[])
      } catch { if (seq === searchSeq.current) setSearchResults([]) }
      finally { if (seq === searchSeq.current) setSearching(false) }
    }, 350)
    return () => clearTimeout(t)
  }, [query, searchable, provider])

  const downloaded = models.filter((m) => m.downloaded).length
  // When searching, show remote results; otherwise the installed/catalog list.
  const showSearch = searchable && query.trim().length > 0
  const rows: AvailableModel[] = showSearch ? (searchResults ?? []) : models

  const renderRow = (m: AvailableModel) => {
    const job = jobs[m.name]
    const downloading = job?.status === 'running'
    const err = errors[m.name] || (job?.status === 'error' ? job.error : '')
    const frac = job && job.size_bytes > 0 ? job.bytes / job.size_bytes : undefined
    const sizeMb = m.size_mb ?? (m.size ? Math.round(m.size / 1024 / 1024) : 0)
    const gatedUndownloaded = m.gated && !m.downloaded
    return (
      <div key={m.name} className="rounded-md px-2.5 py-1.5"
        style={m.downloaded
          ? { background: 'color-mix(in srgb, var(--color-primary) 8%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 18%, transparent)' }
          : { background: 'var(--color-surface-high)' }}>
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span className="truncate text-on-surface text-[0.75rem] font-mono">{m.name}</span>
              {m.downloaded && <Check size={11} style={{ color: 'var(--color-success)' }} />}
              {gatedUndownloaded && <Lock size={10} className="shrink-0 text-on-surface-low" aria-label="Requires a token / license" />}
            </div>
            <div className="truncate text-on-surface-low text-[0.66rem]">
              {downloading
                ? `${job.phase}${job.bytes ? ` · ${MB(job.bytes)}${sizeMb ? ` / ${sizeMb}` : ''} MB` : ''}`
                : <>{m.description || (m.capabilities?.length ? m.capabilities.join(', ') : '')}{sizeMb ? ` · ${sizeMb} MB` : ''}</>}
            </div>
            {downloading && <div className="mt-1"><WavyProgress width={200} value={frac} /></div>}
          </div>
          {downloading ? (
            <button type="button" onClick={() => cancel(m.name)}
              aria-label={`Cancel ${m.name}`} title="Cancel"
              className="grid size-7 shrink-0 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface">
              <X size={13} />
            </button>
          ) : (
            <button type="button" onClick={() => (m.downloaded ? remove(m.name) : download(m.name))}
              disabled={gatedUndownloaded}
              aria-label={m.downloaded ? `Delete ${m.name}` : `Download ${m.name}`}
              title={gatedUndownloaded ? 'Requires a token / license (see provider settings)' : m.downloaded ? 'Delete' : 'Download'}
              className="grid size-7 shrink-0 place-items-center rounded-md text-on-surface-low transition-colors hover:text-on-surface disabled:opacity-40 disabled:hover:text-on-surface-low">
              {m.downloaded ? <Trash2 size={13} /> : <Download size={13} />}
            </button>
          )}
        </div>
        {err && (
          <div className="mt-1 flex items-start gap-1 text-[0.66rem]" style={{ color: 'var(--color-danger)' }}>
            <AlertTriangle size={11} className="mt-0.5 shrink-0" /> <span className="min-w-0">{err}</span>
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1 text-on-surface-low text-[0.68rem] uppercase tracking-wide">
        <HardDrive size={11} /> Models ({downloaded}/{models.length} downloaded)
      </div>

      {searchable && (
        <div className="relative mb-1.5">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-on-surface-low" />
          <input
            value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the library to install a model…"
            className="h-8 w-full rounded-md bg-surface-high pl-8 pr-8 text-[0.75rem] text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50"
          />
          {query && (
            <button type="button" onClick={() => setQuery('')} aria-label="Clear search"
              className="absolute right-2 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded text-on-surface-low hover:text-on-surface">
              <X size={12} />
            </button>
          )}
        </div>
      )}

      {showSearch && searching && rows.length === 0 ? (
        <div className="py-1 text-on-surface-low text-[0.75rem] italic">Searching…</div>
      ) : rows.length === 0 ? (
        <div className="py-1 text-on-surface-low text-[0.75rem] italic">
          {showSearch ? `No models match “${query.trim()}”.` : 'No downloadable models listed.'}
        </div>
      ) : (
        <div className="grid gap-1.5 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
          {rows.map(renderRow)}
        </div>
      )}
    </div>
  )
}
