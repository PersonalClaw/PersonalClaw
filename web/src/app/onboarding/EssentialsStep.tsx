import { useCallback, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Cpu, Search, Mic, MessagesSquare, Download, Check, Loader2, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../../ui/Button'
import { LoadError, LoadingStatus } from '../../ui/ListScaffold'
import { TextLink } from '../../ui/TextLink'
import { listItemEnter, stagger, spring } from '../../design/motion'
import { useCachedData } from '../../lib/useCachedData'
import { useGuardedInstall, guardedFromApp } from '../../lib/useGuardedInstall'
import { ConsentModal, PermissionList, CronConsentList } from '../../pages/apps/installConsent'
import { SchemaField } from '../../pages/settings/ModelBackends'
import { api, type AppCatalogEntry, type ChatModelOption, type ModelProviderType, type OnboardingState, type OnboardingStatePatch } from '../../lib/api'

/** ONBOARDING-UX S1 T1.2r (OU-2) — the essential-apps step: the flow's first act
 *  after the name, and the only place a fresh install can become a working agent
 *  without a detour through Settings.
 *
 *  **Four lanes, one required.** A model provider is the required rail (nothing works
 *  without one), so its lane carries the full sub-flow: install → key → Test → chat
 *  binding. Search, speech and channel are opt-in single-step installs; their
 *  configuration belongs in Settings, not in a first run.
 *
 *  **Nothing installs on its own.** Every install is a click on a card's own Install
 *  button, after that card has disclosed what the app will be granted. The step
 *  mounts, lists, and waits — `essentialsStep.test.tsx` asserts zero install requests
 *  fire without a click, which is the guarantee that makes a Store catalog safe to
 *  render in a flow the user is being walked through.
 *
 *  **The consent surface is the Store's, not a quieter copy.** `PermissionList`,
 *  `CronConsentList` and `ConsentModal` are imported from the Store itself, so the
 *  disclosure and the scanner-warning override are the same components with the same
 *  copy. A second consent path here would be a second thing to keep honest.
 *
 *  Every API call is one the Store/Settings already own — `POST /api/apps`,
 *  `POST /api/model-providers` (+ its Test), `PUT /api/models/active/{use_case}`. No
 *  endpoint was added for onboarding. */

/** Which lane an app belongs to, decided by the provider's DECLARED capabilities
 *  rather than `providerType` alone: faster-whisper (stt) and piper-tts (tts) are
 *  both `providerType: 'model'`, so a `providerType`-only filter would offer a
 *  speech model as a chat provider and dead-end at the binding step. */
type LaneId = 'model' | 'search' | 'speech' | 'channel'

const LANES: { id: LaneId; icon: LucideIcon; title: string; blurb: string; required: boolean }[] = [
  { id: 'model', icon: Cpu, title: 'Model provider', required: true,
    blurb: 'The model your agent thinks with. Required — nothing else works without one.' },
  { id: 'search', icon: Search, title: 'Web search', required: false,
    blurb: 'Lets the agent look things up. Add a key later in Settings.' },
  { id: 'speech', icon: Mic, title: 'Speech', required: false,
    blurb: 'Speak to the agent and hear it back (transcription / voice).' },
  { id: 'channel', icon: MessagesSquare, title: 'Messaging channel', required: false,
    blurb: 'Reach your agent from a chat app. Connect it later in Settings.' },
]

function capsOf(e: AppCatalogEntry): string[] { return e.providerCapabilities ?? [] }

/** A busy region that actually ANNOUNCES itself. Two shapes are wrong here and both
 *  ship silently: `aria-label` on a bare `<svg>` is a prohibited attribute the browser
 *  discards, and a `role="status" aria-busy` region with only an icon in it announces
 *  nothing at all. `LoadingStatus` is the tree's canonical announcement, so it carries
 *  the words while the spinner carries the look. */
function Spinner({ what, size = 16 }: { what: string; size?: number }) {
  return (
    <div role="status" aria-busy="true" className="flex items-center py-2">
      <LoadingStatus what={what} />
      <Loader2 size={size} className="animate-spin text-on-surface-low" aria-hidden="true" />
    </div>
  )
}

export function laneOf(e: AppCatalogEntry): LaneId | null {
  const caps = capsOf(e)
  if (e.providerType === 'search') return 'search'
  if (e.providerType === 'channel') return 'channel'
  if (e.providerType === 'model') {
    if (caps.includes('chat')) return 'model'
    if (caps.includes('stt') || caps.includes('tts')) return 'speech'
  }
  return null
}

/** Every card array the catalog surfaces, deduped by app name. A first-party source
 *  reaches the Store as `localApps` (the dev dir / `PERSONALCLAW_FIRST_PARTY_APPS_DIR`)
 *  or as `gitApps`/`remoteApps` (the shipped default git source), so a step that read
 *  only one of them would show an empty lane on half the installs. */
export function candidatesByLane(c: Awaited<ReturnType<typeof api.appCatalog>> | undefined): Record<LaneId, AppCatalogEntry[]> {
  const out: Record<LaneId, AppCatalogEntry[]> = { model: [], search: [], speech: [], channel: [] }
  const seen = new Set<string>()
  const pool = [...(c?.bundled ?? []), ...(c?.localApps ?? []), ...(c?.gitApps ?? []), ...(c?.remoteApps ?? [])]
  for (const e of pool) {
    if (!e?.name || seen.has(e.name)) continue
    seen.add(e.name)
    const lane = laneOf(e)
    if (lane) out[lane].push(e)
  }
  for (const lane of Object.keys(out) as LaneId[]) {
    out[lane].sort((a, b) => (a.displayName || a.name).localeCompare(b.displayName || b.name))
  }
  return out
}

/** How many cards a lane shows before "Show all" — a first run should offer a choice,
 *  not the full 20-app model catalog. */
const LANE_PREVIEW = 4

type ModelPhase = 'pick' | 'configure' | 'bind' | 'done'

export function EssentialsStep({ readiness, onDone, onSkip, onProgress }: {
  /** `GET /api/onboarding`, already fetched by the flow. `needs_model` is the
   *  backend's dry-run of real chat resolution — when it is false the model lane is
   *  ALREADY satisfied and the step must not ask for an install it doesn't need. */
  readiness: OnboardingState | null
  /** The model lane is resolved and the user is moving on. */
  onDone: (summary: string) => void
  /** "Set up later" — the flow must never trap a user on a step. */
  onSkip: () => void
  /** Persist a partial patch of first-run progress (OU-1's both-level merge). */
  onProgress: (patch: OnboardingStatePatch) => void
}) {
  // NOT persisted: a first run must read the live catalog, and a warm sessionStorage
  // cache would hide the load-failure branch below on every reload after the first.
  const { data: catalog, error: catalogError, refresh } = useCachedData(
    'onboarding:essentials-catalog', () => api.appCatalog())

  const lanes = useMemo(() => candidatesByLane(catalog), [catalog])
  const [installed, setInstalled] = useState<Record<string, true>>({})
  const [open, setOpen] = useState<string>('')       // app name whose disclosure is open
  const [expanded, setExpanded] = useState<Record<string, true>>({})  // lanes showing all cards
  const [modelApp, setModelApp] = useState<string>('')
  // The model lane starts already-satisfied when chat can resolve today (a re-entry, or
  // a home configured outside the flow), and skips straight to binding when a provider
  // exists but nothing is bound — the two states the old readiness step handled.
  const [phase, setPhase] = useState<ModelPhase>(() => {
    if (readiness && !readiness.needs_model) return 'done'
    if (readiness?.has_model_provider) return 'bind'
    return 'pick'
  })
  const [boundLabel, setBoundLabel] = useState('')

  // One guarded-install state machine for the whole step, exactly as the Store's card
  // grid does it: the pending source rides a ref so the consent re-attempt targets the
  // same app the user was shown findings for.
  const pendingRef = useRef<AppCatalogEntry | null>(null)
  const guarded = useGuardedInstall((confirm) =>
    api.installApp(pendingRef.current?.pointer || pendingRef.current?.source || '', confirm).then(guardedFromApp))

  const recordInstall = useCallback((entry: AppCatalogEntry, lane: LaneId) => {
    setInstalled((m) => ({ ...m, [entry.name]: true }))
    // Each lane records ONLY its own field — the backend merges at both levels, so no
    // lane has to read back and echo the whole document to avoid clobbering a sibling.
    if (lane === 'model') { setModelApp(entry.name); setPhase('configure'); onProgress({ essentials: { model: entry.name } }) }
    else if (lane === 'search') onProgress({ essentials: { search: true } })
    else if (lane === 'speech') onProgress({ essentials: { speech: true } })
    else onProgress({ essentials: { channel: entry.name } })
  }, [onProgress])

  // The ONLY install trigger in this component: a click on a disclosed card's own
  // Install button. Nothing here runs from an effect or a render.
  const install = useCallback(async (entry: AppCatalogEntry, lane: LaneId) => {
    pendingRef.current = entry
    guarded.reset()
    const r = await guarded.install()
    if (r?.ok) { setOpen(''); recordInstall(entry, lane) }
  }, [guarded, recordInstall])

  const confirmInstall = useCallback(async () => {
    const entry = pendingRef.current
    const lane = entry ? laneOf(entry) : null
    const r = await guarded.confirmInstall()
    if (r?.ok && entry && lane) { setOpen(''); recordInstall(entry, lane) }
  }, [guarded, recordInstall])

  const modelReady = phase === 'done'

  // A dead catalog fetch is NOT "no apps available" — say so, and offer the retry.
  // `data === undefined && error` is the one condition that distinguishes them.
  if (catalog === undefined && catalogError) {
    return (
      <div className="flex flex-col gap-m">
        <LoadError what="app catalog" error={catalogError} onRetry={refresh} />
        <p className="text-on-surface-low text-[0.8125rem]">
          Apps are listed from the first-party source — the workspace apps directory in a dev
          tree, otherwise the published apps repository. You can set this up later in the Store.
        </p>
        <TextLink onClick={onSkip}>Set up later</TextLink>
      </div>
    )
  }
  if (catalog === undefined) {
    return <Spinner what="apps" size={18} />
  }

  return (
    <div className="flex flex-col gap-l">
      {LANES.map((lane) => {
        const items = lanes[lane.id]
        const isModel = lane.id === 'model'
        const shown = expanded[lane.id] ? items : items.slice(0, LANE_PREVIEW)
        const laneDone = isModel ? modelReady : items.some((e) => installed[e.name])
        return (
          <section key={lane.id} role="group" className="flex flex-col gap-s" aria-label={lane.title}>
            <div className="flex items-baseline gap-2">
              <lane.icon size={15} className="shrink-0 translate-y-0.5 text-primary" aria-hidden="true" />
              <span className="text-on-surface text-[0.875rem]">{lane.title}</span>
              <span className="text-on-surface-low text-[0.75rem]">{lane.required ? 'Required' : 'Optional'}</span>
              {laneDone && (
                <span className="inline-flex items-center gap-1 text-[0.75rem]" style={{ color: 'var(--color-success)' }}>
                  <Check size={14} aria-hidden="true" /> Ready
                </span>
              )}
            </div>
            <p className="text-on-surface-low text-[0.8125rem]">{lane.blurb}</p>

            {/* The model lane's post-install sub-flow replaces its card list once an
                app is chosen — key entry, Test, then the binding choice. */}
            {isModel && phase !== 'pick' ? (
              <ModelSubFlow app={modelApp} phase={phase} boundLabel={boundLabel}
                onBound={(label) => { setBoundLabel(label); setPhase('done'); }}
                onConfigured={() => setPhase('bind')} />
            ) : items.length === 0 ? (
              <p className="text-on-surface-low text-[0.8125rem]">
                No {lane.title.toLowerCase()} app is available from the first-party source
                (the workspace apps directory in a dev tree, otherwise the published apps
                repository). Add a source in the Store later.
              </p>
            ) : (
              <motion.div className="flex flex-col gap-1.5" initial="initial" animate="animate"
                variants={{ animate: { transition: stagger(0.04) } }}>
                {shown.map((e) => (
                  <AppCard key={e.name} entry={e} open={open === e.name} installed={!!installed[e.name]}
                    busy={guarded.busy && pendingRef.current?.name === e.name}
                    error={pendingRef.current?.name === e.name ? guarded.error : null}
                    onToggle={() => { setOpen((cur) => (cur === e.name ? '' : e.name)); guarded.reset() }}
                    onInstall={() => install(e, lane.id)} />
                ))}
                {items.length > shown.length && (
                  <TextLink onClick={() => setExpanded((m) => ({ ...m, [lane.id]: true }))}>
                    Show all {items.length} {lane.title.toLowerCase()} apps
                  </TextLink>
                )}
              </motion.div>
            )}
          </section>
        )
      })}

      <div className="flex items-center gap-m">
        <Button variant="primary" size="md" disabled={!modelReady}
          disabledReason="Set up a model provider first — the agent can't think without one"
          onClick={() => onDone(boundLabel || 'Ready to chat')}>
          Continue
        </Button>
        {/* Guidance never gates: the required lane is required to CONSIDER, not a wall.
            OU-4's full-skip path lands in a working dashboard through here. */}
        <TextLink onClick={onSkip}>Set up later</TextLink>
      </div>

      {/* A scanner WARNING routes through the Store's own consent modal — same
          findings, same explicit "Install anyway". */}
      {guarded.blocked && pendingRef.current && (
        <ConsentModal label={pendingRef.current.displayName || pendingRef.current.name}
          result={guarded.blocked} busy={guarded.busy}
          onConfirm={confirmInstall} onClose={() => guarded.reset()} />
      )}
    </div>
  )
}

/** One catalog card. Collapsed it is a name + a Review button; expanded it discloses
 *  what the app will be granted and what it will run on a schedule, and only THEN
 *  offers Install. The disclosure is the Store's components verbatim. */
function AppCard({ entry, open, installed, busy, error, onToggle, onInstall }: {
  entry: AppCatalogEntry; open: boolean; installed: boolean; busy: boolean
  error: string | null; onToggle: () => void; onInstall: () => void
}) {
  const label = entry.displayName || entry.name
  return (
    <motion.div variants={listItemEnter} layout transition={spring.spatialFast}
      className="rounded-lg bg-surface-high p-3">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-on-surface text-[0.8125rem]">{label}</div>
          <div className="truncate text-on-surface-low text-[0.75rem]">{entry.description || entry.name}</div>
        </div>
        {installed ? (
          <span className="inline-flex shrink-0 items-center gap-1 text-[0.75rem]" style={{ color: 'var(--color-success)' }}>
            <Check size={13} aria-hidden="true" /> Installed
          </span>
        ) : (
          <Button variant="ghost" size="sm" ariaExpanded={open} onClick={onToggle}>
            {open ? 'Close' : 'Review'}
          </Button>
        )}
      </div>

      {open && !installed && (
        <div className="mt-3 flex flex-col gap-m border-t border-outline-variant pt-3">
          {entry.permissions && Object.keys(entry.permissions).length > 0 && (
            <PermissionList perms={entry.permissions} />
          )}
          {(entry.crons ?? []).length > 0 && <CronConsentList crons={entry.crons!} />}
          <div className="flex items-start gap-2 text-on-surface-low" data-type="body-s">
            <ShieldCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>Installing fetches this app behind the security scanner — a dangerous verdict is always refused.</span>
          </div>
          {error && <div className="text-danger text-[0.8125rem]" role="alert">{error}</div>}
          <div>
            <Button variant="primary" size="sm" loading={busy} onClick={onInstall}>
              <Download size={15} aria-hidden="true" /> Install {label}
            </Button>
          </div>
        </div>
      )}
    </motion.div>
  )
}

/** The model lane's required rail, after its app is installed: enter the provider's
 *  own schema-declared fields (the key), Test the connection for real, then bind a
 *  chat model. Three existing endpoints, no new one. */
function ModelSubFlow({ app, phase, boundLabel, onConfigured, onBound }: {
  app: string; phase: ModelPhase; boundLabel: string
  onConfigured: () => void; onBound: (label: string) => void
}) {
  if (phase === 'done') {
    return (
      <p className="inline-flex items-center gap-1.5 text-[0.8125rem]" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {boundLabel ? `Chat model: ${boundLabel}` : 'A chat model is configured — you\'re ready.'}
      </p>
    )
  }
  if (phase === 'bind') return <BindModel onBound={onBound} />
  return <ConfigureProvider app={app} onConfigured={onConfigured} />
}

/** Key entry + Test. The instance is named after its provider type — a first run
 *  should not have to invent an instance name — and a re-entry updates the existing
 *  instance rather than dead-ending on the create endpoint's 409. */
function ConfigureProvider({ app, onConfigured }: { app: string; onConfigured: () => void }) {
  const { data: types, error: typesError, refresh } = useCachedData(
    'onboarding:provider-types', () => api.modelProviderTypes())
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [seeded, setSeeded] = useState('')

  const t: ModelProviderType | undefined = types?.find((x) => x.app === app) ?? undefined
  const props = t?.settingsSchema?.properties || {}
  const required = t?.settingsSchema?.required || []
  // Seed the schema defaults once the type resolves, without an effect: the first
  // render that knows the type also knows its defaults.
  if (t && seeded !== t.type) {
    const seed: Record<string, string> = {}
    for (const [k, f] of Object.entries(t.settingsSchema?.properties || {})) seed[k] = String(f.default ?? '')
    setValues(seed); setSeeded(t.type)
  }

  if (types === undefined && typesError) {
    return <LoadError what="provider types" error={typesError} onRetry={refresh} />
  }
  if (types === undefined) {
    return <Spinner what="provider types" />
  }
  if (!t) {
    // The app installed but its provider type has not registered — the install result's
    // own restart notice already told the user; say what to do rather than showing an
    // empty form. Retry re-reads the live registry.
    return (
      <div className="flex flex-col gap-s">
        <p className="text-on-surface-var text-[0.8125rem]">
          {app} installed, but its provider type hasn't registered yet. That usually means the
          gateway needs a restart to load it.
        </p>
        <div><Button variant="secondary" size="sm" onClick={refresh}>Check again</Button></div>
      </div>
    )
  }

  const submit = async () => {
    for (const r of required) {
      if (!String(values[r] ?? props[r]?.default ?? '').trim()) {
        setError(`${props[r]?.['x-meta']?.label || r} is required`); return
      }
    }
    setBusy(true); setError('')
    const options: Record<string, string> = {}
    for (const [k, f] of Object.entries(props)) {
      const v = (values[k] ?? String(f.default ?? '')).trim()
      if (v) options[k] = v
    }
    // The key travels to the provider endpoints only — it is never put in component
    // state that renders, never logged, and never echoed into an error string.
    try {
      try {
        await api.createModelProvider({ name: t.type, type: t.type, model: '', options })
      } catch (e) {
        // Already exists (a re-entry through this step): apply the new options to the
        // existing instance instead of dead-ending, so a corrected key takes effect.
        if (!/already exists/i.test(thrownMessage(e))) throw e
        await api.updateModelProvider(t.type, { options })
      }
      const res = await api.testModelProvider(t.type)
      if (!res.ok) { setError(res.message || 'The provider test failed.'); setBusy(false); return }
      onConfigured()
    } catch (e) {
      setError(thrownMessage(e) || 'Could not save the provider.'); setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-m">
      {/* Says nothing about WHERE the settings are stored, deliberately. An earlier draft
          promised "the credential store, never a config file" — driving this step against a
          real gateway showed that `POST /api/model-providers` writes the whole `options`
          object, `sensitive` fields included, into `config.json`. That is a pre-existing
          property of the endpoint the Store and Settings already use, not something this
          step introduces or should quietly re-route; but onboarding must not make a
          storage promise the backend does not keep. */}
      <p className="text-on-surface-var text-[0.8125rem]">
        {t.label} is installed. Fill in its settings, then test the connection for real before moving on.
      </p>
      <div className="flex flex-col gap-2">
        {Object.entries(props).map(([k, f]) => (
          <SchemaField key={k} name={k} field={f} value={values[k] ?? ''}
            onChange={(v) => setValues((m) => ({ ...m, [k]: v }))} />
        ))}
      </div>
      {error && <div className="text-danger text-[0.8125rem]" role="alert">{error}</div>}
      <div>
        <Button variant="primary" size="sm" loading={busy} onClick={submit}>Save and test</Button>
      </div>
    </div>
  )
}

/** Bind a chat model — the last leg. `active_models.json` holds canonical
 *  `provider:model` refs (what the Models panel writes), NOT the display `name`,
 *  which the discovery fallback builds as `provider/model`. */
function BindModel({ onBound }: { onBound: (label: string) => void }) {
  const { data: models, error, refresh } = useCachedData('onboarding:chat-models', () => api.chatModels())
  const [binding, setBinding] = useState('')
  const [failed, setFailed] = useState('')

  if (models === undefined && error) return <LoadError what="chat models" error={error} onRetry={refresh} />
  if (models === undefined) {
    return <Spinner what="chat models" />
  }
  if (models.length === 0) {
    return (
      <div className="flex flex-col gap-s">
        <p className="text-on-surface-low text-[0.8125rem]">No chat-capable models were discovered for this provider.</p>
        <div><Button variant="secondary" size="sm" onClick={refresh}>Check again</Button></div>
      </div>
    )
  }

  const bind = async (m: ChatModelOption) => {
    setBinding(m.name); setFailed('')
    const ref = m.provider ? `${m.provider}:${m.model_id}` : m.model_id
    try { await api.setActiveModel('chat', [ref]); onBound(m.model_id) }
    catch (e) { setBinding(''); setFailed(thrownMessage(e) || 'Could not bind that model.') }
  }

  return (
    <div className="flex flex-col gap-m">
      <p className="text-on-surface-var text-[0.8125rem]">Pick the model the agent should chat with:</p>
      {failed && <div className="text-danger text-[0.8125rem]" role="alert">{failed}</div>}
      <motion.div className="flex flex-col gap-1.5" initial="initial" animate="animate"
        variants={{ animate: { transition: stagger(0.04) } }}>
        {models.map((m) => (
          <motion.div key={m.name} variants={listItemEnter}>
            <Button variant="ghost" size="md" shape="squircle" className="w-full justify-start"
              loading={binding === m.name} disabled={!!binding && binding !== m.name}
              disabledReason="Another model is being bound" onClick={() => bind(m)}>
              <Cpu size={15} aria-hidden="true" className="shrink-0 text-primary" />
              <span className="min-w-0 truncate">{m.model_id}</span>
              <span className="shrink-0 text-on-surface-low text-[0.75rem]">{m.provider}</span>
            </Button>
          </motion.div>
        ))}
      </motion.div>
    </div>
  )
}

/** A THROWN api-client error's text, unwrapping the JSON error body the client
 *  stringifies into `Error.message`. Distinct from `lib/errText`, which turns a failed
 *  `Response` into user-facing copy — this one reads an already-rejected promise.
 *  Never includes a submitted credential: only the server's own message. */
function thrownMessage(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e ?? '')
  try { const p = JSON.parse(raw); return String(p?.error ?? raw) } catch { return raw }
}
