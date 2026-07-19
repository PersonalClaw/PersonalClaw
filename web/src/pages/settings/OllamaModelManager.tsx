import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, Trash2, Search, RefreshCw, Loader2, Check, AlertCircle, Info, X } from 'lucide-react'
import { api, type OllamaLocalModel, type OllamaSearchResult, type OllamaModelInfo } from '../../lib/api'
import { confirmDelete } from '../../ui/dialog'

/** First-class Ollama model manager (#48) — manage the models hosted on an Ollama
 *  provider's endpoint directly from its card: see what's installed (size +
 *  metadata), inspect any model's details, delete to reclaim disk, and search the
 *  Ollama library to pull new models with live progress. Users curate their local
 *  models here before binding them to use-cases in Settings → Models. */
export function OllamaModelManager({ provider }: { provider: string }) {
  const [tab, setTab] = useState<'installed' | 'browse'>('installed')
  return (
    <div className="mt-3 border-outline-variant/30 border-t pt-3">
      <div className="mb-3 flex gap-1">
        <TabBtn active={tab === 'installed'} onClick={() => setTab('installed')}>Installed</TabBtn>
        <TabBtn active={tab === 'browse'} onClick={() => setTab('browse')}>Browse library</TabBtn>
      </div>
      {tab === 'installed' ? <InstalledModels provider={provider} /> : <BrowseLibrary provider={provider} />}
    </div>
  )
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick}
      className={`rounded-md px-2.5 py-1 text-[0.74rem] ${active ? 'bg-surface-high text-on-surface' : 'text-on-surface-low hover:text-on-surface'}`}>
      {children}
    </button>
  )
}

// ── Installed models: list + metadata + inspect + delete ──
function InstalledModels({ provider }: { provider: string }) {
  const [models, setModels] = useState<OllamaLocalModel[] | null>(null)
  const [err, setErr] = useState('')

  const reload = useCallback(() => {
    setErr('')
    api.ollamaModels(provider)
      .then((d) => { setModels(d.models ?? []); if (d.error) setErr(d.error) })
      .catch((e) => { setModels([]); setErr(e instanceof Error ? e.message : 'failed') })
  }, [provider])
  useEffect(reload, [reload])

  if (models === null) return <div className="flex items-center gap-2 text-on-surface-low text-[0.78rem]"><Loader2 size={13} className="animate-spin" /> Loading…</div>

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-on-surface-low text-[0.7rem] uppercase tracking-wide">Installed ({models.length})</span>
        <button onClick={reload} className="text-on-surface-low hover:text-on-surface" title="Refresh"><RefreshCw size={12} /></button>
      </div>
      {err && <div className="mb-2 flex items-center gap-1.5 text-error text-[0.74rem]"><AlertCircle size={12} /> {err}</div>}
      {models.length === 0 && !err ? (
        <p className="text-on-surface-low text-[0.78rem] italic">No models installed. Use “Browse library” to pull one.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {models.map((m) => <InstalledRow key={m.name} provider={provider} model={m} onDeleted={reload} />)}
        </div>
      )}
    </div>
  )
}

function InstalledRow({ provider, model, onDeleted }: { provider: string; model: OllamaLocalModel; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false)
  const [info, setInfo] = useState<OllamaModelInfo | null>(null)
  const [showInfo, setShowInfo] = useState(false)

  async function del() {
    if (!(await confirmDelete('model', model.name, { body: "This frees disk on the Ollama host and can't be undone." }))) return
    setBusy(true)
    try { await api.ollamaDeleteModel(provider, model.name); onDeleted() } catch { setBusy(false) }
  }
  async function inspect() {
    setShowInfo((v) => !v)
    if (!info) { try { setInfo(await api.ollamaShow(provider, model.name)) } catch { /* best-effort */ } }
  }

  const meta = [model.parameter_size, model.quantization, model.size_human].filter(Boolean).join(' · ')
  return (
    <div className="rounded-lg bg-surface-high/40 px-3 py-2" style={{ opacity: busy ? 0.5 : 1 }}>
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.76rem]">{model.name}</span>
        {meta && <span className="shrink-0 text-on-surface-low text-[0.68rem] tabular-nums">{meta}</span>}
        <button onClick={inspect} className="shrink-0 text-on-surface-low hover:text-on-surface" title="Details"><Info size={13} /></button>
        <button onClick={del} disabled={busy} className="shrink-0 text-on-surface-low hover:text-error" title="Delete"><Trash2 size={13} /></button>
      </div>
      {showInfo && info && <ModelInfoBlock info={info} />}
    </div>
  )
}

function ModelInfoBlock({ info }: { info: OllamaModelInfo }) {
  if (info.error) return <div className="mt-1.5 text-on-surface-low text-[0.7rem]">No details: {info.error}</div>
  const rows: [string, string | number | undefined][] = [
    ['Family', info.family], ['Parameters', info.parameter_size], ['Quantization', info.quantization],
    ['Format', info.format], ['Context', info.context_length ? info.context_length.toLocaleString() : ''],
    ['Capabilities', info.capabilities?.join(', ')], ['License', info.license_short],
  ]
  return (
    <div className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 border-outline-variant/30 border-t pt-2 text-[0.7rem]">
      {rows.filter(([, v]) => v).map(([k, v]) => (
        <span key={k} className="contents"><span className="text-on-surface-low">{k}</span><span className="text-on-surface">{v}</span></span>
      ))}
    </div>
  )
}

// ── Browse library: search + pull with progress ──
function BrowseLibrary({ provider }: { provider: string }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<OllamaSearchResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [err, setErr] = useState('')

  async function search() {
    const query = q.trim()
    if (!query) return
    setSearching(true); setErr(''); setResults(null)
    try {
      const d = await api.ollamaSearch(provider, query)
      setResults(d.results ?? [])
      if (d.error) setErr(d.error)
    } catch (e) {
      setResults([]); setErr(e instanceof Error ? e.message : 'search failed')
    }
    setSearching(false)
  }

  return (
    <div>
      <div className="mb-2 flex gap-1.5">
        <div className="flex flex-1 items-center gap-1.5 rounded-md bg-surface-high px-2 py-1">
          <Search size={13} className="shrink-0 text-on-surface-low" />
          <input value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') search() }}
            placeholder="Search ollama.com/library (e.g. llama, qwen, embed)" aria-label="Search Ollama model library"
            className="w-full bg-transparent text-on-surface text-[0.78rem] outline-none placeholder:text-on-surface-low" />
        </div>
        <button onClick={search} disabled={searching || !q.trim()}
          className="shrink-0 rounded-md bg-surface-container px-3 py-1 text-on-surface text-[0.74rem] hover:bg-surface-container-high disabled:opacity-50">
          {searching ? <Loader2 size={13} className="animate-spin" /> : 'Search'}
        </button>
      </div>
      {err && <div className="mb-2 flex items-center gap-1.5 text-error text-[0.74rem]"><AlertCircle size={12} /> {err}</div>}
      {results === null ? (
        !searching && <p className="text-on-surface-low text-[0.78rem] italic">Search the Ollama library to find models to pull.</p>
      ) : results.length === 0 && !err ? (
        <p className="text-on-surface-low text-[0.78rem] italic">No models found for “{q}”.</p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {results.map((r) => <SearchRow key={r.name} provider={provider} result={r} />)}
        </div>
      )}
    </div>
  )
}

function SearchRow({ provider, result }: { provider: string; result: OllamaSearchResult }) {
  const [state, setState] = useState<'idle' | 'pulling' | 'done' | 'error' | 'cancelled'>('idle')
  const [pct, setPct] = useState(0)
  const [msg, setMsg] = useState('')
  const abortRef = useRef<AbortController | null>(null)

  async function pull() {
    const ac = new AbortController()
    abortRef.current = ac
    setState('pulling'); setPct(0); setMsg('starting…')
    try {
      await api.pullOllamaModel(provider, result.name, (f) => {
        if (f.error) { setState('error'); setMsg(String(f.error)); return }
        const status = String(f.status ?? '')
        const total = Number(f.total ?? 0)
        const completed = Number(f.completed ?? 0)
        if (total > 0) setPct(Math.round((completed / total) * 100))
        if (status) setMsg(status)
      }, ac.signal)
      setState((s) => (s === 'error' ? s : 'done'))
    } catch (e) {
      // An aborted fetch surfaces as an AbortError — that's a user Stop, not a failure.
      if (ac.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) {
        setState('cancelled'); setMsg('stopped')
      } else {
        setState('error'); setMsg(e instanceof Error ? e.message : 'pull failed')
      }
    } finally {
      abortRef.current = null
    }
  }
  function stop() { abortRef.current?.abort() }

  return (
    <div className="flex items-center gap-2 rounded-lg bg-surface-high/40 px-3 py-2">
      <span className="min-w-0 flex-1 truncate font-mono text-on-surface text-[0.76rem]">{result.name}</span>
      {state === 'pulling' ? (
        <span className="shrink-0 text-on-surface-low text-[0.7rem] tabular-nums">{pct > 0 ? `${msg} · ${pct}%` : msg}</span>
      ) : state === 'error' ? (
        <span className="shrink-0 truncate text-error text-[0.7rem]" title={msg}>{msg}</span>
      ) : state === 'cancelled' ? (
        <span className="shrink-0 text-on-surface-low text-[0.7rem]">stopped</span>
      ) : null}
      {state === 'done' ? (
        <Check size={15} className="shrink-0 text-primary" />
      ) : state === 'pulling' ? (
        <button onClick={stop}
          className="inline-flex shrink-0 items-center gap-1 rounded-md bg-surface-container px-2 py-1 text-on-surface text-[0.72rem] hover:bg-surface-container-high"
          title="Stop download">
          <X size={12} /> Stop
        </button>
      ) : (state === 'error' || state === 'cancelled') ? (
        <button onClick={pull} className="shrink-0 text-on-surface-low hover:text-on-surface" title="Retry"><RefreshCw size={13} /></button>
      ) : (
        <button onClick={pull}
          className="inline-flex shrink-0 items-center gap-1 rounded-md bg-surface-container px-2 py-1 text-on-surface text-[0.72rem] hover:bg-surface-container-high">
          <Download size={12} /> Pull
        </button>
      )}
    </div>
  )
}
