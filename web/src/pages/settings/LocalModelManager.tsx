import { useEffect, useRef, useState } from 'react'
import { ResultAnnouncement } from '../../ui/ListControls'
import { Download, Trash2, Check, HardDrive, AlertTriangle, X, Lock } from 'lucide-react'
import { api, type AvailableModel } from '../../lib/api'
import { SearchField } from '../../ui/SearchField'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { confirmDelete } from '../../ui/dialog'
import { WavyProgress } from '../../ui/WavyProgress'
import { Toggle } from '../../ui/Toggle'
import { Button } from '../../ui/Button'
import { useModelDownloads } from './useModelDownloads'
import {
  FIT_LABEL, FIT_TONE, budgetKnown, filterByFit, fitDescription, hostFitOf, statedSizeMb, unrunnable,
} from './modelFit'

const MB = (bytes: number) => (bytes / 1024 / 1024).toFixed(0)

/** Visible text AND accessible name of the browse filter — one string, so the switch cannot
 *  announce something other than the words beside it (SC 2.5.3). */
const HIDE_LABEL = "Hide models this device can't run"

/** One row's "will it run here?" chip (LMMV-8), sitting in the same cluster as the downloaded
 *  check and the gated lock.
 *
 *  `role="img"` + `aria-label` is this repo's declared form for a chip whose label carries state
 *  — `FeedbackPanel`'s accurate/wrong counts and `ModelsPanel`'s breaker dot use it, and
 *  `design/ariaProhibitedAttr` rails against the `<span aria-label>` alternative because ARIA
 *  discards a name on a generic. The label leads with the verdict and then carries the backend's
 *  `fit_reason`, so a screen reader gets the WHY and not just a colour nobody can hear.
 *
 *  Renders nothing when the row has no `fit` at all: a hosted model is not a local one, and the
 *  question does not apply to it. That is NOT the same as the 'unknown' verdict, which is a local
 *  model we genuinely could not decide and does get a (neutral) chip. */
function FitChip({ model }: { model: AvailableModel }) {
  const verdict = model.fit
  if (!verdict) return null
  const tone = FIT_TONE[verdict]
  const described = fitDescription(model)
  return (
    <span role="img" aria-label={described} title={described}
      className="inline-flex shrink-0 items-center rounded-pill px-1.5 text-[0.75rem]"
      style={{ background: `color-mix(in srgb, ${tone} 16%, transparent)`, color: tone }}>
      {FIT_LABEL[verdict]}
    </span>
  )
}

/** Download manager for ANY local downloadable model provider — faster-whisper, piper,
 *  sentence-transformers, the diarization backends, ollama. One uniform surface: lists
 *  the provider's catalog (from /api/models/available) with download/delete; a
 *  `searchable` provider (ollama) also gets a search box that queries its remote library
 *  and lets you pull any result. Downloaded models become bindable in Models. Downloads
 *  are async jobs (minutes-long) streaming live progress over per-job SSE, surviving a
 *  reload via useModelDownloads. Fully provider-scoped — core hardcodes no provider.
 *
 *  Every row also answers "will it run HERE?" (LMMV-8): a fit chip beside the downloaded/gated
 *  cluster, and a browse filter that can hide the ones this device cannot run — but ONLY on a host
 *  whose memory budget was actually measured. See `modelFit.ts`. */
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
  // Cancel gets the same per-row error surface as `download` and `remove`. Without it the only write on
  // this panel that could fail silently was the one whose failure matters most — the request IS the stop.
  const stopDownload = async (name: string) => {
    setErr(name, null)
    try { await cancel(name) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Cancel failed'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch { /* raw text */ }
      setErr(name, `Couldn't cancel this download: ${msg}`)
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

  // ── The browse filter: hide what this device cannot run (LMMV-8) ─────────────────────────────
  //
  // `null` means "still following the backend's `hide_unrunnable` preference"; a click pins the
  // user's answer. Derived rather than seeded into state with an effect, because the host budget
  // arrives with the models — a `useState(hide_unrunnable)` initialiser would capture `undefined`
  // on the first render and never pick the preference up.
  const [showAll, setShowAll] = useState<boolean | null>(null)
  const hostFit = hostFitOf(models)
  // 🔑 An UNKNOWN or UNMEASURED budget hides NOTHING, and does not even offer the control. Every
  // one of these reads goes through `budgetKnown`, so there is a single place where "we could not
  // measure this machine" is prevented from being spent as "these models do not fit".
  const fitFilterable = budgetKnown(hostFit)
  const hiding = showAll === null ? fitFilterable && !!hostFit?.hide_unrunnable : !showAll
  const unrunnableCount = unrunnable(models, hostFit).length
  // When searching, show remote results; otherwise the installed/catalog list.
  const showSearch = searchable && query.trim().length > 0
  // The filter applies to the CATALOG only. Remote library results come from the search endpoint,
  // which carries no fit annotation at all — so filtering them could only ever be a no-op, while
  // leaving `ResultAnnouncement`'s count (which reports `searchResults.length`) honest by
  // construction rather than by coincidence.
  const rows: AvailableModel[] = showSearch
    ? (searchResults ?? [])
    : filterByFit(models, hostFit, hiding)
  const hiddenCount = showSearch ? 0 : models.length - rows.length

  const renderRow = (m: AvailableModel) => {
    const job = jobs[m.name]
    const downloading = job?.state === 'running'
    const err = errors[m.name] || (job?.state === 'error' ? job.error : '')
    // Determinate when the total is known (progress 0..1); else indeterminate.
    const frac = job && job.total_bytes > 0 ? job.progress : undefined
    const sizeMb = m.size_mb ?? (m.size ? Math.round(m.size / 1024 / 1024) : 0)
    // The number this row states is the one its VERDICT was judged on — its own size — with the
    // family median appended only when it is a different fact, and always labelled as the family's.
    // See `statedSizeMb`. The progress line keeps `sizeMb`: real bytes arriving, not a catalog fact.
    const stated = statedSizeMb(m)
    // A red row is not a dead end: the backend named the largest variant in the family that DOES
    // fit, so offer it. Deliberately an OFFER and not a swap — the server refuses to substitute
    // inside the POST because the job's name, stream key and byte progress would then all belong to
    // a model the user never asked for. The row's own Download stays enabled: the verdict is advice
    // about this machine, not a licence to block a download the user may want anyway.
    const stepDown = m.fit === 'red' ? m.fit_step_down : null
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
              <FitChip model={m} />
            </div>
            <div className="truncate text-on-surface-low text-[0.75rem]">
              {downloading
                ? `downloading${job.downloaded_bytes ? ` · ${MB(job.downloaded_bytes)}${sizeMb ? ` / ${sizeMb}` : ''} MB` : ''}`
                : <>{m.description || (m.capabilities?.length ? m.capabilities.join(', ') : '')}{stated.mb ? ` · ${stated.mb} MB` : ''}{stated.familyMedianMb ? ` · family median ~${stated.familyMedianMb} MB` : ''}</>}
            </div>
            {stepDown && !downloading && (
              <Button variant="ghost-accent" size="xs" className="-ml-m mt-0.5"
                onClick={() => download(stepDown)}>
                Download {stepDown} instead — it fits
              </Button>
            )}
            {/* Determinate when the byte total is known, and then it must say WHAT is downloading —
                the bar sits in a list of models, so "progressbar 42%" alone does not identify which.
                Indeterminate (total unknown) stays unnamed and `aria-hidden`: the line above already
                reads "downloading · 120 / 400 MB". */}
            {downloading && (
              <div className="mt-1">
                {frac == null
                  ? <WavyProgress width={200} />
                  : <WavyProgress width={200} value={frac} label={`Downloading ${m.name}`} />}
              </div>
            )}
          </div>
          {downloading ? (
            <SquareIconButton icon={X} iconSize={13} label={`Cancel ${m.name}`} title="Cancel"
              onClick={() => stopDownload(m.name)} className="shrink-0" />
          ) : (
            <SquareIconButton icon={m.downloaded ? Trash2 : Download} iconSize={13}
              label={m.downloaded ? `Delete ${m.name}` : `Download ${m.name}`}
              title={gatedUndownloaded ? 'Requires a token / license (see provider settings)' : m.downloaded ? 'Delete' : 'Download'}
              disabled={gatedUndownloaded}
              onClick={() => (m.downloaded ? remove(m.name) : download(m.name))} className="shrink-0" />
          )}
        </div>
        {err && (
          <div className="mt-1 flex items-start gap-1 text-[0.75rem]" style={{ color: 'var(--color-danger)' }}>
            <AlertTriangle size={11} className="mt-0.5 shrink-0" /> <span className="min-w-0">{err}</span>
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">
        <HardDrive size={11} /> Models ({downloaded}/{models.length} downloaded)
      </div>

      {/* Hiding has to be VISIBLE. A silently shortened catalog reads as "this provider has no
          models", which is why the switch states the count it removed and stays next to it: the
          switch IS the way back to the full list. Rendered only on a measured host — offering a
          filter that provably cannot hide anything would be a control that lies about its effect. */}
      {fitFilterable && (
        <div className="mb-1.5 flex items-center gap-1.5 text-[0.75rem]">
          <Toggle size="sm" on={hiding} onChange={(v) => setShowAll(!v)} label={HIDE_LABEL} />
          <span className="text-on-surface-low">{HIDE_LABEL}</span>
          {hiding && hiddenCount > 0 && (
            <span style={{ color: 'var(--color-warning)' }}>
              {hiddenCount} hidden
            </span>
          )}
          {!hiding && unrunnableCount > 0 && (
            <span className="text-on-surface-low">{`${unrunnableCount} won't fit`}</span>
          )}
        </div>
      )}

      {searchable && (
        <div className="mb-1.5">
          <SearchField value={query} onChange={setQuery} size="sm"
            placeholder="Search the library to install a model…"
            ariaLabel="Search the model library" />
          {/* The results come from a fetch, so `active` waits for it: announcing while `searching`
              would report the PREVIOUS query's count, and announcing on a null result would say
              "No matching models" about a search that has not run. */}
          <ResultAnnouncement count={searchResults?.length ?? 0} noun="models"
            active={!!query.trim() && !searching && searchResults !== null} />
        </div>
      )}

      {showSearch && searching && rows.length === 0 ? (
        <div className="py-1 text-on-surface-low text-[0.75rem] italic">Searching…</div>
      ) : rows.length === 0 ? (
        <div className="py-1 text-on-surface-low text-[0.75rem] italic">
          {/* The filter can empty the list completely, and "No downloadable models listed" would
              then be a flat lie about a provider whose catalog we just hid. The switch above is
              still on screen, so this states the cause and points at the way back. */}
          {showSearch ? `No models match “${query.trim()}”.`
            : hiddenCount === 1
              ? 'The only listed model is hidden — it will not run on this device.'
              : hiddenCount > 1
                ? `All ${hiddenCount} listed models are hidden — none of them will run on this device.`
                : 'No downloadable models listed.'}
        </div>
      ) : (
        <div className="grid gap-1.5 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
          {rows.map(renderRow)}
        </div>
      )}
    </div>
  )
}
