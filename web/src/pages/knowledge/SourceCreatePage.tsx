import { useState } from 'react'
import { ArrowLeft, Check, Eye, Info, MonitorPlay, ShieldOff, Sparkles } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { IconButton } from '../../ui/IconButton'
import { PageTitle } from '../../ui/PageTitle'
import { Button } from '../../ui/Button'
import { Toggle } from '../../ui/Toggle'
import { Segmented } from '../../ui/Segmented'
import { TileButton } from '../../ui/TileButton'
import { Checkbox, Field, FieldError, Select, TextArea, TextInput } from '../../ui/forms'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { api, type SourceKind, type SourcePreviewResult } from '../../lib/api'
import { useCachedData, invalidateCache } from '../../lib/useCachedData'
import { notify } from '../../app/appSdk'
import { fvs } from '../../design/fontWeight'
import { accentChip } from '../../design/accent'
import { HEALTH_NEEDS_RENDER, INTERVAL_CHOICES, fmtInterval, formIcon } from './sourceMeta'

/** §2.4's create flow: pick a kind → describe it → (web only) preview and tune → save.
 *
 *  The preview is deliberately NOT uniform across kinds. WS-3 kept `preview` off the
 *  provider ABC because a feed's or a folder's preview IS its poll, so `SourceKind.previewable`
 *  is a measured fact per provider and this page says plainly where no dry run exists rather
 *  than faking one by half-polling a feed. */
export function SourceCreatePage({ onBack, onCreated }: { onBack: () => void; onCreated: () => void }) {
  const { data, loading, error, refresh } = useCachedData('knowledge:sources', () => api.knowledgeSources())
  const [chosen, setChosen] = useState<string>('')
  const kinds = data?.kinds
  const kind = kinds?.find((k) => k.provider === chosen)

  if (kind) {
    return <SourceForm kind={kind} onBack={() => setChosen('')} onClose={onBack} onCreated={onCreated} />
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back to sources" size={40} onClick={onBack} /><PageTitle>Watch something</PageTitle></div>} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {kinds === undefined && error ? (
            <LoadError what="source kinds" error={error} onRetry={refresh} />
          ) : kinds === undefined || loading ? (
            <ListSkeleton rows={3} what="source kinds" />
          ) : kinds.length === 0 ? (
            // Not a styling nicety: the catalog is the registered POLL-CAPABLE providers, so
            // an empty one means nothing would poll whatever you created.
            <EmptyState icon={Info} title="No source kinds are available"
              hint="No poll-capable knowledge provider is registered, so a source created now would never be polled." />
          ) : (
            <>
              <p className="mb-l text-center text-on-surface-low text-[0.9375rem]">What should PersonalClaw keep an eye on?</p>
              <div className="grid grid-cols-1 gap-m sm:grid-cols-3">
                {kinds.map((k) => {
                  const Icon = formIcon(k.form)
                  return (
                    // The name is the KIND itself. A wrapper phrase read badly against the
                    // provider's own display name — measured in the a11y tree, the tiles
                    // announced "Watch a watched page" / "Watch a watched directory".
                    <TileButton key={k.provider} ariaLabel={k.display_name} onClick={() => setChosen(k.provider)} className="p-l">
                      {/* The shared accent pair, not a hand-picked alpha tint: an accent ink on an
                          accent tint measures 3.64–4.20:1 in light, below AA. The icon inherits
                          the pair's ink through `currentColor`. */}
                      <span className="inline-flex size-10 items-center justify-center rounded-xl" style={accentChip}><Icon size={20} aria-hidden /></span>
                      <span className="mt-m text-on-surface text-[0.9375rem]" style={fvs(500)}>{k.display_name}</span>
                      <span className="mt-1 text-on-surface-low text-[0.8125rem]">{kindBlurb(k)}</span>
                    </TileButton>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/** What this kind is FOR, in the user's terms. Presentation only — every string that is a
 *  promise about backend behaviour (the remediation guidance, the health labels) comes from
 *  the API, not from here. */
function kindBlurb(k: SourceKind): string {
  if (k.form === 'web_page') return 'A changelog, blog index or newsroom — read without JavaScript, with no model involved.'
  if (k.form === 'feed') return 'RSS, Atom, JSON Feed or a CSV export, including Hacker News and GitHub presets.'
  if (k.form === 'dir') return 'A folder on this machine — new and edited files are indexed, deletions are archived.'
  return `Provided by ${k.display_name}.`
}

interface SpecState {
  url: string
  detectors: string[]
  allowRender: boolean
  preset: string
  format: string
  path: string
  include: string
  rawSpec: string
}

/** Build the provider's spec from the form. One function so the SAVE and the PREVIEW send
 *  byte-identical specs — a preview that validated a different object than the one saved
 *  would be a dry run of something else. */
function buildSpec(kind: SourceKind, s: SpecState): { spec: Record<string, unknown>; error: string } {
  if (kind.form === 'web_page') {
    const spec: Record<string, unknown> = { url: s.url.trim() }
    // A detectors list is a FILTER over the provider's order, never a re-ordering — so an
    // all-selected list is sent as "unset" rather than as the full list, keeping the source
    // on the provider's order even if a sixth detector is added later.
    if (s.detectors.length && s.detectors.length !== (kind.detectors?.length ?? 0)) spec.detectors = s.detectors
    return { spec, error: '' }
  }
  if (kind.form === 'feed') {
    const spec: Record<string, unknown> = {}
    if (s.preset) spec.preset = s.preset
    if (s.url.trim()) spec.url = s.url.trim()
    // A preset supplies its own parser family; only a preset-less feed needs one chosen.
    if (!s.preset && s.format) spec.kind = s.format
    return { spec, error: '' }
  }
  if (kind.form === 'dir') {
    const spec: Record<string, unknown> = { path: s.path.trim() }
    const globs = s.include.split(',').map((g) => g.trim()).filter(Boolean)
    if (globs.length) spec.include = globs
    return { spec, error: '' }
  }
  try {
    const parsed = JSON.parse(s.rawSpec || '{}')
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { spec: {}, error: 'The spec must be a JSON object.' }
    }
    return { spec: parsed as Record<string, unknown>, error: '' }
  } catch {
    return { spec: {}, error: 'That is not valid JSON.' }
  }
}

function SourceForm({ kind, onBack, onClose, onCreated }: {
  kind: SourceKind
  onBack: () => void
  onClose: () => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [enrichment, setEnrichment] = useState('full')
  const [pollSecs, setPollSecs] = useState(String(kind.poll_interval_secs))
  const [spec, setSpec] = useState<SpecState>({
    url: '', detectors: kind.detectors ?? [], allowRender: false,
    // `formats` is a sorted VOCABULARY, not a preference order, so `[0]` picked `csv` —
    // measured in the real form, a user pasting an RSS URL got the CSV parser preselected.
    // Which one is most likely is a presentation judgement, so it is made here.
    preset: '', format: kind.formats?.includes('rss') ? 'rss' : (kind.formats?.[0] ?? ''), path: '',
    include: (kind.default_include ?? []).join(', '), rawSpec: '{}',
  })
  const [preview, setPreview] = useState<SourcePreviewResult | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = <K extends keyof SpecState>(k: K, v: SpecState[K]) => setSpec((p) => ({ ...p, [k]: v }))

  const built = buildSpec(kind, spec)
  const budget = { allow_render: spec.allowRender }
  // The FORM's own floor only — never a copy of the provider's rules. The authoritative
  // verdict is `validate_spec` on save, whose message is what the user is shown.
  const filled = kind.form === 'web_page' ? /^https?:\/\//.test(spec.url.trim())
    : kind.form === 'feed' ? (!!spec.preset || /^https?:\/\//.test(spec.url.trim()))
      : kind.form === 'dir' ? !!spec.path.trim()
        : !built.error
  const canSave = !!name.trim() && filled

  async function runPreview() {
    if (built.error) { setErr(built.error); return }
    setPreviewing(true); setErr(''); setPreview(null)
    try {
      setPreview(await api.previewKnowledgeSource({ provider: kind.provider, spec: built.spec, budget }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'The preview failed')
    } finally { setPreviewing(false) }
  }

  async function save() {
    if (!canSave || saving) return
    if (built.error) { setErr(built.error); return }
    setSaving(true); setErr('')
    try {
      await api.createKnowledgeSource({
        name: name.trim(), provider: kind.provider, spec: built.spec,
        enrichment, poll_interval_secs: Number(pollSecs), budget,
      })
      // The list caches under this key; leaving it stale would show the new source only
      // after a reload, which reads as "the save didn't work".
      invalidateCache('knowledge:sources')
      notify(`Now watching ${name.trim()}`, 'success')
      onCreated()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  const Icon = formIcon(kind.form)

  return (
    <div className="flex h-full flex-col">
      <TopBar left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back to source kinds" size={40} onClick={onBack} /><PageTitle className="inline-flex items-center gap-s"><Icon size={18} aria-hidden /> New {kind.display_name.toLowerCase()}</PageTitle></div>} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex flex-col gap-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>

          <Field label="Name" hint="What you'll recognise this source by in your library.">
            <TextInput value={name} onChange={setName} placeholder={`${kind.display_name} · e.g. Product changelog`} autoFocus required />
          </Field>

          {kind.form === 'web_page' && (
            <>
              <Field label="Listing-page URL" hint="A changelog, blog index, category or archive page — the page that LISTS the entries you want.">
                <TextInput value={spec.url} onChange={(v) => { set('url', v); setPreview(null) }} placeholder="https://example.com/changelog" required />
              </Field>
              <Field label="Detectors" hint="All of them run in order, most reliable first. Unticking one narrows the stack; it never re-orders it.">
                <div className="flex flex-wrap gap-x-l gap-y-2 rounded-md bg-surface-container px-m py-2">
                  {(kind.detectors ?? []).map((d) => (
                    <label key={d} className="inline-flex items-center gap-2 text-on-surface text-[0.8125rem]">
                      <Checkbox ariaLabel={`Run the ${d.replace(/_/g, ' ')} detector`} checked={spec.detectors.includes(d)}
                        onChange={(on) => { set('detectors', on ? [...spec.detectors, d] : spec.detectors.filter((x) => x !== d)); setPreview(null) }} />
                      <span className="font-mono">{d}</span>
                    </label>
                  ))}
                </div>
              </Field>
              <Field label="Render tier" hint="Off by default. A page that builds its content with JavaScript needs it; a plain page never does, and it costs a headless browser per poll.">
                <div className="flex items-center gap-m">
                  <Toggle on={spec.allowRender} onChange={(v) => { set('allowRender', v); setPreview(null) }} label="Allow the render tier for this source" />
                  <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.8125rem]"><MonitorPlay size={13} aria-hidden /> {spec.allowRender ? 'Allowed' : 'Not allowed'}</span>
                </div>
              </Field>
            </>
          )}

          {kind.form === 'feed' && (
            <>
              <Field label="Recipe" hint="A preset is a starting spec you can override — pick “Custom” to describe your own.">
                <Select value={spec.preset} onChange={(v) => set('preset', v)}
                  options={[{ value: '', label: 'Custom' }, ...(kind.presets ?? []).map((p) => ({ value: p, label: p.replace(/_/g, ' ') }))]} />
              </Field>
              <Field label="Feed URL" hint={spec.preset ? 'Optional — the recipe supplies one. Set it to poll a different query.' : 'The endpoint to poll.'}>
                <TextInput value={spec.url} onChange={(v) => set('url', v)} placeholder="https://example.com/feed.xml" required={!spec.preset} />
              </Field>
              {!spec.preset && (
                <Field label="Format">
                  <Select value={spec.format} onChange={(v) => set('format', v)}
                    options={(kind.formats ?? []).map((f) => ({ value: f, label: f.toUpperCase() }))} />
                </Field>
              )}
            </>
          )}

          {kind.form === 'dir' && (
            <>
              <Field label="Folder" hint="Checked on save: it must exist, and it may not be a sensitive location.">
                <TextInput value={spec.path} onChange={(v) => set('path', v)} placeholder="/Users/you/notes" required mono />
              </Field>
              <Field label="File patterns" hint={`Comma-separated globs. Up to ${kind.max_files ?? 0} files are tracked per folder.`}>
                <TextInput value={spec.include} onChange={(v) => set('include', v)} placeholder="*.md, *.txt" mono />
              </Field>
            </>
          )}

          {kind.form !== 'web_page' && kind.form !== 'feed' && kind.form !== 'dir' && (
            <Field label="Spec" hint={`${kind.display_name} defines its own spec. It is validated on save by the provider itself.`}>
              <TextArea value={spec.rawSpec} onChange={(v) => set('rawSpec', v)} rows={6} mono ariaLabel="Provider spec as JSON" />
            </Field>
          )}

          <Field label="Enrichment">
            <Segmented ariaLabel="Enrichment for this source" value={enrichment} onChange={setEnrichment}
              options={[{ key: 'full', label: 'Enriched', icon: Sparkles }, { key: 'raw', label: 'Raw · no AI', icon: ShieldOff }]} />
            <p className="mt-1 text-on-surface-low text-[0.75rem]">
              {enrichment === 'raw'
                ? 'Items are indexed and embedded locally and never reach a model — the pipeline for raw sources has no model stages at all.'
                : 'Items get the full pipeline: summaries, insights and entity extraction.'}
            </p>
          </Field>

          <Field label="Check for new items">
            {/* The provider's own advertised cadence is folded in when it is not already one of
                the coarse choices — a `<select>` whose value matches no option DISPLAYS the
                first one while holding the other. Measured on the dir kind: it showed
                "Every 15 min" and would have saved the provider's 5-minute default. */}
            <Select value={pollSecs} onChange={setPollSecs}
              options={[...new Set([...INTERVAL_CHOICES, kind.poll_interval_secs])]
                .sort((a, b) => a - b)
                .map((s) => ({ value: String(s), label: `Every ${fmtInterval(s)}` }))} />
          </Field>

          {kind.previewable ? (
            <div className="flex flex-col gap-m rounded-lg bg-surface-container p-l">
              <div className="flex flex-wrap items-center justify-between gap-s">
                <span className="text-on-surface text-[0.8125rem]" style={fvs(500)}>Dry run</span>
                <Button size="sm" variant="tonal" loading={previewing} disabled={!filled}
                  disabledReason={filled ? undefined : 'Enter a URL starting with http:// or https://'}
                  onClick={runPreview}><Eye size={15} /> Preview items</Button>
              </div>
              <p className="text-on-surface-low text-[0.75rem]">Runs the detectors once and shows what would be saved. Nothing is stored — but it is a real request to that server.</p>
              {preview && <PreviewResult result={preview} allowRender={spec.allowRender} onAllowRender={() => { set('allowRender', true); setPreview(null) }} />}
            </div>
          ) : (
            <p className="inline-flex items-start gap-2 rounded-lg bg-surface-container p-m text-on-surface-low text-[0.8125rem]">
              <Info size={14} className="mt-0.5 shrink-0" aria-hidden />
              {kind.display_name} has no dry run — its first poll is its preview. Save it, then check its health on the sources list.
            </p>
          )}

          {(err || built.error) && <FieldError>{err || built.error}</FieldError>}
        </div>
      </div>
      {/* The page-bottom action bar, matching `KnowledgeCreatePage`'s shell in this same
          section. `ui/FormFooter` is the DETAIL-PANE bar (it bleeds `-mx-l` to a pane's
          edges), which is the wrong geometry for a full-height page. */}
      <div className="shrink-0 border-t border-outline-variant/40 bg-surface/95 px-l py-3">
        <div className="mx-auto flex items-center justify-end gap-s" style={{ maxWidth: 'var(--content-width)' }}>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={saving} disabled={saving || !canSave}
            disabledReason={saving ? undefined : !name.trim() ? 'Give this source a name' : 'Fill in what to watch'}>
            <Check size={16} /> Start watching
          </Button>
        </div>
      </div>
    </div>
  )
}

/** The dry run's outcome. Every branch renders text the PROVIDER produced: the winning
 *  detector name, its escalations, and — when nothing was found — its own guidance, which is
 *  either "point at a listing page" or "this needs the render tier". Those two are opposite
 *  remediations and are never merged into one message here. */
function PreviewResult({ result, allowRender, onAllowRender }: {
  result: SourcePreviewResult
  allowRender: boolean
  onAllowRender: () => void
}) {
  if (result.error) return <FieldError>{result.error}</FieldError>

  const needsRender = result.health_status === HEALTH_NEEDS_RENDER
  return (
    // A polite live region: the outcome of a dry run is the answer to something the user
    // just asked for, and a result that only appears visually leaves a screen-reader user
    // pressing Preview and hearing nothing.
    <div role="status" className="flex flex-col gap-m">
      {result.items.length > 0 ? (
        <>
          <p className="text-on-surface-var text-[0.8125rem]">
            Found <span style={fvs(600)}>{result.items.length}</span> item{result.items.length === 1 ? '' : 's'}
            {result.detector && <> via <span className="font-mono">{result.detector}</span></>}
            {' · '}{result.requests_used} request{result.requests_used === 1 ? '' : 's'}
          </p>
          <ul className="flex flex-col gap-2">
            {result.items.map((it) => (
              <li key={it.guid} className="rounded-md bg-surface-high px-m py-2">
                <p className="truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{it.title}</p>
                {/* Untrusted scraped text, clipped by the backend and rendered as TEXT. */}
                {it.snippet && <p className="mt-0.5 line-clamp-2 text-on-surface-low text-[0.75rem]">{it.snippet}</p>}
                {it.url && <p className="mt-0.5 truncate text-on-surface-low text-[0.75rem]">{it.url}</p>}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <div className="flex items-start gap-2">
          {needsRender ? <MonitorPlay size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />
            : <Info size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />}
          <div className="min-w-0">
            <p className="text-on-surface text-[0.8125rem] leading-relaxed">{result.guidance || 'No items were found.'}</p>
            {needsRender && !allowRender && (
              <Button size="xs" variant="tonal" className="mt-m" onClick={onAllowRender}>Allow the render tier and retry</Button>
            )}
          </div>
        </div>
      )}
      {result.escalations.length > 0 && (
        <p className="text-on-surface-low text-[0.75rem]">{result.escalations.join(' · ')}</p>
      )}
    </div>
  )
}
