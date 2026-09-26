import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { Cpu, Search, Mic, MessagesSquare, Download, Check, Loader2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Button } from '../../ui/Button'
import { LoadError, LoadingStatus } from '../../ui/ListScaffold'
import { TextLink } from '../../ui/TextLink'
import { listItemEnter, stagger, spring } from '../../design/motion'
import { invalidateKeys, useQuery } from '../../lib/data'
import { catalogApps } from '../../lib/appCatalog'
import { useAppInstall } from '../../pages/apps/installConsent'
import { SchemaField, schemaDefaults } from '../../pages/settings/ProviderConfigForm'
import { SchemaFields } from '../../pages/tools/schema'
import { BundledModelOffer } from './BundledModelOffer'
import { StepActions } from './StepActions'
import { chatModelSummary } from './chatModelSummary'
import { checkChatModel, thrownMessage, type ChatModelVerdict } from './checkChatModel'
import { api, type AppCatalogEntry, type AppSummary, type BundledModelOffer as Offer, type ChatModelOption, type LocalModelEndpoint, type ModelProviderType, type OnboardingState, type OnboardingStatePatch, type ProviderOptionValue } from '../../lib/api'

/** ONBOARDING-UX S1 T1.2r (OU-2) — the essential-apps step: the flow's first act
 *  after the name, and the only place a fresh install can become a working agent
 *  without a detour through Settings.
 *
 *  **Four lanes, one required.** A model provider is the required rail (nothing works
 *  without one), so its lane carries the full sub-flow: install → key → Test → chat
 *  binding. Search, speech and channel are opt-in single-step installs; their
 *  configuration belongs in Settings, not in a first run.
 *
 *  **Nothing installs on its own.** A card's Install opens the Store's own consent
 *  dialog (`useAppInstall`), which reviews the app on the server and installs only when
 *  the user confirms there. The step mounts, lists, and waits —
 *  `essentialsStep.test.tsx` asserts zero install requests fire without a click AND a
 *  confirmation, which is the guarantee that makes a Store catalog safe to render in a
 *  flow the user is being walked through.
 *
 *  **The consent surface is the Store's, not a quieter copy.** This step renders no
 *  disclosure of its own: the dialog is the same one every Store install opens, with
 *  the same grants, scheduled jobs, scan and override. A second consent path here would
 *  be a second thing to keep honest — and this card's inline copy was one.
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

/** The two DECLARED fields a lane is decided by. A catalog entry and an installed app both carry
 *  them (`GET /api/apps` sends `providerCapabilities` too), so one classifier sorts both. */
type LaneFields = Pick<AppCatalogEntry, 'providerType' | 'providerCapabilities'>

function capsOf(e: LaneFields): string[] { return e.providerCapabilities ?? [] }

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

export function laneOf(e: LaneFields): LaneId | null {
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
 *  only one of them would show an empty lane on half the installs.
 *
 *  Flattened by the ONE merge (`lib/appCatalog`) — this used to concatenate the four lists
 *  in a THIRD order of its own, so with a name in two lists the onboarding step could offer
 *  a different copy of an app than the Store card did (#2528).
 *
 *  `installed` is the server's list of what is already installed, and nothing in it is offered.
 *  The catalog already leaves installed apps out ("Library exclusion"), but only as of the read
 *  that built it — and this step's read is a cache that outlives an install. Going Back and
 *  returning painted the cached catalog, so an app installed a minute earlier was offered again
 *  and "Install" answered `app 'faster-whisper' already installed (use update)`. */
export function candidatesByLane(
  c: Awaited<ReturnType<typeof api.appCatalog>> | undefined,
  installed: ReadonlySet<string> = new Set(),
): Record<LaneId, AppCatalogEntry[]> {
  const out: Record<LaneId, AppCatalogEntry[]> = { model: [], search: [], speech: [], channel: [] }
  for (const e of catalogApps(c)) {
    if (!e?.name || installed.has(e.name)) continue
    const lane = laneOf(e)
    if (lane) out[lane].push(e)
  }
  for (const lane of Object.keys(out) as LaneId[]) {
    out[lane].sort((a, b) => (a.displayName || a.name).localeCompare(b.displayName || b.name))
  }
  return out
}

/** An app that is already installed, as its lane shows it. `on` is whether it is enabled — an
 *  installed app that is turned off is not what makes a lane ready. */
export interface InstalledApp { name: string; label: string; description: string; on: boolean }

/** What is already installed, by lane — so a lane can show an installed app AS installed
 *  instead of losing it (the catalog no longer lists it) or offering it again.
 *
 *  Read from the server (`GET /api/apps`, the Store Library's own read), plus the apps this
 *  session installed that the next read has not caught up with yet. A NATIVE app ships with the
 *  product rather than being installed from here, so it is not listed: the small offline model is
 *  one, and its lane offers its download instead. */
export function installedByLane(
  apps: AppSummary[] | undefined,
  justInstalled: Record<string, AppCatalogEntry>,
): Record<LaneId, InstalledApp[]> {
  const out: Record<LaneId, InstalledApp[]> = { model: [], search: [], speech: [], channel: [] }
  const seen = new Set<string>()
  for (const a of apps ?? []) {
    if (!a?.name || a.native) continue
    seen.add(a.name)
    const lane = laneOf(a)
    if (lane) out[lane].push({ name: a.name, label: a.displayName || a.name, description: a.description, on: a.enabled })
  }
  for (const e of Object.values(justInstalled)) {
    if (seen.has(e.name)) continue
    const lane = laneOf(e)
    if (lane) out[lane].push({ name: e.name, label: e.displayName || e.name, description: e.description, on: true })
  }
  for (const lane of Object.keys(out) as LaneId[]) out[lane].sort((a, b) => a.label.localeCompare(b.label))
  return out
}

/** Whether the server lists *name* as installed — asked when its Install is clicked, since the list
 *  the card was drawn from can predate an install made since. A read that fails answers "no": the
 *  review then opens, as it would have before anything asked, and gives the server's own answer. */
async function isInstalledOnServer(name: string): Promise<boolean> {
  try { return (await api.apps()).some((a) => a.name === name) } catch { return false }
}

/** How many cards a lane shows before "Show all" — a first run should offer a choice,
 *  not the full 20-app model catalog. */
const LANE_PREVIEW = 4

/** The model lane's sub-flow, in order. `verify` sits between every way of arriving at a
 *  model and the `done` that reports one, and that position is the point: it is the only
 *  state in which the lane asks the backend to BUILD what chat would build.
 *
 *  🪤 `done` used to be reachable directly from `readiness.needs_model === false` and from
 *  a successful bind, and both are CLAIMS rather than evidence. `needs_model` is derived
 *  from `can_resolve_use_case`, documented as — and required to stay — a no-instantiate
 *  probe: its first branch answers "resolvable" the moment `active_models.json` holds a
 *  ref, without checking the ref still builds. Writing that ref is the last thing this
 *  step does, so the step was reading its own write back as proof. Measured on a home
 *  whose `config.json` carries a provider whose type no installed app registers, plus a
 *  chat binding to it: `needs_model` is `false` (green "you're ready") while chat's real
 *  resolution raises `ERR_MODEL_UNRESOLVED`. */
type ModelPhase = 'pick' | 'configure' | 'bind' | 'verify' | 'done'

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
  const { data: catalog, error: catalogError, refresh } = useQuery(
    'onboarding:essentials-catalog', () => api.appCatalog())
  // #3529 — fetched HERE, once, rather than only inside `ConfigureProvider` (reached later,
  // post-install): the on-ramp's own empty-state copy needs to know whether a manual route
  // truthfully exists "below" before it can point at it, and `InstalledProviderTypes` needs
  // the same registry read to decide whether to render at all. One fetch, one derived fact,
  // handed to both — never two independent guesses that could disagree.
  const { data: providerTypes, error: providerTypesError, refresh: refreshProviderTypes } = useQuery(
    'onboarding:provider-types', () => api.modelProviderTypes())
  // 🔴 WHAT IS INSTALLED IS THE SERVER'S ANSWER, NOT THIS COMPONENT'S MEMORY. It was a
  // `useState({})` filled only by an install made in THIS mount, so going Back to the step or
  // reloading forgot every install: the cached catalog offered faster-whisper again, and its
  // Install answered `app 'faster-whisper' already installed (use update)`. It is `GET /api/apps`
  // under the Store Library's own key, so this step and the Library cannot disagree about it.
  const { data: installedApps, error: installedError, refresh: refreshInstalled } = useQuery<AppSummary[]>(
    'apps', () => api.apps(), { persist: true })
  /** Installs this session made — or was told had already happened — that the next `apps` read
   *  has not caught up with yet. The catalog entry, so the app keeps its lane until then. */
  const [justInstalled, setJustInstalled] = useState<Record<string, AppCatalogEntry>>({})
  const installedNames = useMemo(
    () => new Set([...(installedApps ?? []).map((a) => a.name), ...Object.keys(justInstalled)]),
    [installedApps, justInstalled])

  const lanes = useMemo(() => candidatesByLane(catalog, installedNames), [catalog, installedNames])
  const installedLanes = useMemo(() => installedByLane(installedApps, justInstalled), [installedApps, justInstalled])
  // Which registered types have no catalog card to reach them from (see
  // `typesMissingFromCatalog`'s own doc) — computed against the MODEL lane's own catalog
  // list specifically, since that's the one list a "configure it manually" card could
  // duplicate — and which of those the provider form can actually configure (see
  // `configurableAsInstance`).
  const missingProviderTypes = useMemo(
    () => typesMissingFromCatalog(providerTypes, lanes.model).filter(configurableAsInstance),
    [providerTypes, lanes.model])
  const [expanded, setExpanded] = useState<Record<string, true>>({})  // lanes showing all cards
  const [modelApp, setModelApp] = useState<string>('')
  // The model lane starts by VERIFYING when the coarse readiness probe claims chat can
  // resolve today (a re-entry, or a home configured outside the flow), and skips straight
  // to binding when a provider exists but nothing is bound — the two states the old
  // readiness step handled. `needs_model === false` is a claim, so it buys a check, not a
  // green tick; see `ModelPhase`.
  const [phase, setPhase] = useState<ModelPhase>(() => {
    if (readiness && !readiness.needs_model) return 'verify'
    if (readiness?.has_model_provider) return 'bind'
    return 'pick'
  })
  /** The chat model the VERIFICATION found bound, from the verdict's own `bound` refs — or
   *  `''` on a home that resolves through the implicit fallback, where there is no model to
   *  name and doing so would imply a choice nobody made.
   *
   *  🔴 ONE STATE, READ FROM THE BINDING, NOT TWO COPIES OF A LABEL. This used to be a pair:
   *  a `boundLabel` captured from whichever control did the binding, and a `verified` summary
   *  that preferred it. Both were component state, so a reload lost them — and the recap then
   *  fell back to the persisted `essentials.model`, which is the **app** name (#3528). The
   *  verdict already carries the authoritative answer (`active_model_refs('chat')`, the
   *  contents of `active_models.json`), so the label is read from there on every pass; there
   *  is nothing left to lose across a reload and nothing to drift out of step with the file. */
  const [chatModel, setChatModel] = useState('')
  /** Whether what answers chat is the small zero-config model — the VERIFICATION's own `floor`,
   *  not the readiness prop, which the flow read before anything was downloaded. */
  const [floor, setFloor] = useState(false)
  /** The model this session downloaded (its display name), so the lane can SAY the download
   *  finished instead of the offer card simply vanishing at 100%. */
  const [downloaded, setDownloaded] = useState('')
  /** The download finished but binding it as the chat model was refused — said, not swallowed. */
  const [bindError, setBindError] = useState('')
  /** The verification ANSWERED, and the answer is that chat cannot use what is set up. Kept so
   *  Continue's reason says so instead of "still checking" beside a finished check. */
  const [verifyFailed, setVerifyFailed] = useState(false)
  /** Bumped whenever something new has to be checked, and the key the check mounts under. The
   *  lane can already BE in `verify` when that happens — a reload opens it there, onto a
   *  provider that does not answer, and the small model is then downloaded from the offer above
   *  — so moving it to `verify` changes nothing, and the verdict on screen would be the old one. */
  const [verifyRun, setVerifyRun] = useState(0)
  /** What `ConfigureProvider` learned, for the bind step. `provider` is the entry name it
   *  created — the provider KEY (`t.type`, e.g. `ollama`), never the app name
   *  (`ollama-models`), because that is the token a test and a `provider:model` ref speak.
   *  `unprobed` is the reason string when the provider type has NO connectivity probe, so
   *  the bind step can say that an empty model list proves nothing there. `null` when the
   *  lane arrived at `bind` from readiness rather than through the form, in which case
   *  which provider is configured is genuinely unknown here. */
  const [configured, setConfigured] = useState<{ provider: string; unprobed: string } | null>(null)

  // The card the consent dialog was opened from, so a confirmed install records the lane it
  // belongs to — the dialog itself only knows the source it installed.
  const pendingRef = useRef<AppCatalogEntry | null>(null)

  const recordInstall = useCallback((entry: AppCatalogEntry, lane: LaneId) => {
    setJustInstalled((m) => ({ ...m, [entry.name]: entry }))
    // Every cache answering "what is installed" or "what can be installed" is stale now: this
    // step's catalog, the Store's catalog, and the Library's list (`apps`, which the shell's nav
    // reads too). Invalidated rather than merely re-read here, so a Store opened after this step
    // does not paint a cached catalog that still offers what this step just installed.
    invalidateKeys('apps')
    invalidateKeys('app-catalog')
    invalidateKeys('onboarding:essentials-catalog')
    // Each lane records ONLY its own field — the backend merges at both levels, so no
    // lane has to read back and echo the whole document to avoid clobbering a sibling.
    if (lane === 'model') { setModelApp(entry.name); setPhase('configure'); onProgress({ essentials: { model: entry.name } }) }
    else if (lane === 'search') onProgress({ essentials: { search: true } })
    else if (lane === 'speech') onProgress({ essentials: { speech: true } })
    else onProgress({ essentials: { channel: entry.name } })
  }, [onProgress])

  // The ONE consent path every Store install takes. It records the lane only after the
  // user confirmed in the dialog and the install committed.
  const consent = useAppInstall({
    onInstalled: () => {
      const entry = pendingRef.current
      const lane = entry ? laneOf(entry) : null
      if (entry && lane) recordInstall(entry, lane)
    },
  })

  // The ONLY install trigger in this component: a click on a card's own Install button, which
  // opens the review. Nothing here runs from an effect or a render.
  //
  // An app the server lists as installed is never offered (`candidatesByLane`), but that list can
  // be older than an install made since — in the Store, or another tab — and the review would then
  // open on `already installed (use update)`. So the click asks the server first, and an app it
  // already has is shown as installed: to this step that install is done, not an error. Decided by
  // what the server lists, not by reading a refusal's prose, which is a sentence and not a contract.
  const install = useCallback(async (entry: AppCatalogEntry) => {
    pendingRef.current = entry
    const lane = laneOf(entry)
    if (lane && await isInstalledOnServer(entry.name)) { recordInstall(entry, lane); return }
    void consent.begin({ source: entry.pointer || entry.source, label: entry.displayName || entry.name })
  }, [consent, recordInstall])

  // OU-13 — a local/LAN Ollama bind needs no API key and no model pick (the endpoint's own
  // chat model is bound for you), so it skips the 'configure'/'bind' phases. It still goes
  // through 'verify': the bind writes a `providers[]` entry AND a chat ref, and "the write
  // returned ok" is not "chat resolves" — the same distinction the catalog path draws.
  const handleLocalBound = useCallback(() => {
    setModelApp('ollama-models')
    setPhase('verify')
    onProgress({ essentials: { model: 'ollama-models' } })
  }, [onProgress])

  // #3529 — the second way into the model lane's sub-flow, alongside `recordInstall`'s
  // catalog-card path: a provider type whose app is ALREADY installed never gets a catalog
  // card to click (`resolve_catalog_entries`'s "Library exclusion" drops an installed app
  // from `/api/apps/catalog`), so `InstalledProviderTypes` below routes here directly, by
  // app name. Same phase transition and the same progress field `recordInstall`'s model
  // branch writes; only the trigger differs.
  const configureInstalled = useCallback((app: string) => {
    setModelApp(app)
    setPhase('configure')
    onProgress({ essentials: { model: app } })
  }, [onProgress])

  // OU-14 — the small model finished downloading in THIS session, and the shared download machine
  // has made it the chat model if nothing else was (`useBundledModelDownload` — the same rule the
  // chat screen's download follows). Two things the lane used to leave to a reload, done here:
  //
  //  1. Move to `verify`, the lane's one proof, so Continue unlocks on what chat really resolves
  //     rather than staying disabled beside a server that already reports the model ready.
  //  2. Remember what was downloaded, and a refused binding, so both are SAID.
  const handleBundledReady = useCallback((offer: Offer, refused: string) => {
    setDownloaded(offer.label); setBindError(refused)
    setModelApp(offer.provider)
    onProgress({ essentials: { model: offer.provider } })
    setVerifyRun((n) => n + 1)
    setPhase('verify')
  }, [onProgress])

  const modelReady = phase === 'done'

  /** Sources the build could not read this round (`unavailableSources`, #408). A 200 that
   *  carries these is NOT the same fact as a 200 that carries none: the lanes below are
   *  empty because a read FAILED, not because the sources offer nothing.
   *
   *  🪤 Measured on a fresh `python:3.13-slim` container installed from the published wheel:
   *  `GET /api/apps/catalog` answered 200 with every app list empty and
   *  `unavailableSources: [{PersonalClawApps.git, no-git}, {registry.git, no-git}]`, and this
   *  step rendered *"No model provider app is available from the first-party source …"* — an
   *  assertion about the world, on a question that was never answered, on the one lane the
   *  step calls **Required**. `cli_doctor.py`'s own docstring names this: *"The caller must
   *  not turn an unanswered question into a verdict"* (#2907), and `AppsSection.tsx` already
   *  reads the same field for the Store's badge (#2629). First run was the consumer that
   *  didn't — so a first-time user was told no provider exists, given no retry, and left on
   *  a required step whose Continue is disabled. */
  const unreadable = catalog?.unavailableSources ?? []
  const noGit = unreadable.some((u) => u.reason === 'no-git')

  /* Gated on the VERIFIED lane, not on a written binding: Continue is the flow's claim that the
   * required rail is satisfied, so it may not turn on before the backend has built what chat
   * builds AND the provider that answers has answered. `verify` gets its own reasons — "still
   * checking", "checked, and it can't" and "nothing set up" are three different waits, and one
   * sentence for them would tell a user who just bound a model to go set one up, or one whose
   * provider is down that it is still being checked.
   *
   * "Set up later" sits beside it until the lane is ready: guidance never gates, the required lane
   * is required to CONSIDER, not a wall, and OU-4's full-skip path lands in a working dashboard
   * through here. Both render in the flow's navigation bar (`StepActions`), where they are on
   * screen however far down the lanes run — and they are ONE element in every branch below, at
   * the same place in the tree, so the catalog landing does not remount the button a keyboard user
   * may be standing on. */
  const actions = (
    <StepActions
      primary={{
        label: 'Continue', disabled: !modelReady,
        disabledReason: phase === 'verify' && verifyFailed
          ? "Chat can't use what is set up yet — see above"
          : phase === 'verify'
            ? 'Still checking that a chat model really resolves'
            : "Set up a model provider first — the agent can't think without one",
        // OU-14: "a configured provider" is the one sentence about the downloaded 135M floor that
        // is not true, bound or not. This is the summary the done-screen recap repeats, and it is
        // built by the SAME function the re-entered flow uses (#3528).
        onClick: () => onDone(chatModelSummary(chatModel, floor)),
      }}
      secondary={modelReady ? undefined : { label: 'Set up later', onClick: onSkip }} />
  )

  // A dead catalog fetch is NOT "no apps available" — say so, and offer the retry.
  // `data === undefined && error` is the one condition that distinguishes them.
  if (catalog === undefined && catalogError) {
    return (
      <>
        <div className="flex flex-col gap-m">
          <LoadError what="app catalog" error={catalogError} onRetry={refresh} />
          <p className="text-on-surface-low text-[0.8125rem]">
            Apps are listed from the first-party source — the workspace apps directory in a dev
            tree, otherwise the published apps repository. You can set this up later in the Store.
          </p>
        </div>
        {actions}
      </>
    )
  }
  if (catalog === undefined) {
    return (
      <>
        <Spinner what="apps" size={18} />
        {actions}
      </>
    )
  }

  return (
    <>
    <div className="flex flex-col gap-l">
      {/* Stated ONCE, above the lanes: one unreadable source empties all four, so repeating
          the cause per lane would print one machine-level fact four times. `role="status"`
          because it appears after the step has mounted and settled — a user who has already
          read the lanes must be told the listing failed without having to re-read them. */}
      {unreadable.length > 0 && (
        <div role="status" data-testid="onboarding-sources-unreadable"
          className="flex flex-col gap-s rounded-l bg-surface-high p-m">
          <p data-type="body-s" className="text-on-surface">
            {noGit
              ? 'PersonalClaw reads its app sources with git, and git is not installed on this machine — so it cannot tell you what is available. Install git and retry, or skip this step and add a model provider later in Settings.'
              : 'PersonalClaw could not read its app sources, so it cannot tell you what is available. Check this machine’s network and retry, or skip this step and add a model provider later in Settings.'}
          </p>
          <ul className="flex flex-col gap-xs">
            {unreadable.map((u) => (
              <li key={u.source} data-type="caption" className="text-on-surface-low break-all">
                {u.source}
                {u.reason === 'budget' && ' — skipped, the listing ran out of time'}
              </li>
            ))}
          </ul>
          <div><TextLink onClick={refresh}>Try reading the app sources again</TextLink></div>
        </div>
      )}
      {/* Without this read an installed app cannot be told from an installable one, so say that
          rather than let a lane look complete: the catalog still leaves out what was installed
          when IT was read, but not anything installed since. */}
      {installedApps === undefined && installedError != null && (
        <p role="status" data-testid="onboarding-installed-unreadable" data-type="body-s" className="text-on-surface-var">
          Couldn&rsquo;t check which apps are already installed ({thrownMessage(installedError) || 'the request failed'}),
          so one you already have may be offered again.{' '}
          <TextLink onClick={refreshInstalled}>Check again</TextLink>
        </p>
      )}
      {LANES.map((lane) => {
        const items = lanes[lane.id]
        const isModel = lane.id === 'model'
        // The model lane's installed providers are listed by `InstalledProviderTypes` below, which
        // is where their Configure is — so only the other three lanes list theirs here.
        const have = isModel ? [] : installedLanes[lane.id]
        const shown = expanded[lane.id] ? items : items.slice(0, LANE_PREVIEW)
        const laneDone = isModel ? modelReady : have.some((a) => a.on)
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

            {/* OU-14 — the no-account option, FIRST in the lane: every card below needs an account
                or a server, and this needs neither. It used to sit behind "Configure" on one of
                those cards. It is offered in EVERY phase while the model is not on disk —
                picking, filling in a provider's form, and after a check that found the provider
                is not answering — because downloading it is always a valid choice. It renders
                nothing when there is nothing to download, and it steps aside once this
                session's download has finished and the lane has taken it over. Kept in one slot
                across phases, so a running download keeps its bar when the phase changes. */}
            {isModel && !downloaded && <BundledModelOffer onReady={handleBundledReady} />}

            {/* OU-13 — the zero-key on-ramp sits ABOVE the catalog while the model lane
                is still picking. Localhost auto-detects (a loopback probe, not a scan);
                the LAN sweep is opt-in. When nothing is reachable it offers no bind
                card, so the catalog below is unchanged. #3529 (containerised installs
                especially — a container's loopback and LAN are never the host's, so this
                is close to guaranteed to miss): its OWN empty states say so and point at
                `InstalledProviderTypes` below, using the parent's own answer for whether
                that route truly exists rather than assuming it. */}
            {isModel && phase === 'pick' && (
              <LocalModelOnRamp onBound={handleLocalBound}
                hasManualRoute={missingProviderTypes.some((t) => t.app === 'ollama-models')} />
            )}

            {/* #3529 — a provider type can be registered with NO route into it: its app is
                already installed, so the catalog never gives it a card, and (for Ollama
                specifically) discovery can miss it too — a different subnet, a hostname, a
                VLAN, or simply a containerised gateway whose loopback and LAN are its own,
                never the host's. This is the fallback that is ALWAYS present in 'pick',
                independent of whether the on-ramp above found anything. */}
            {isModel && phase === 'pick' && (
              <InstalledProviderTypes missing={missingProviderTypes}
                error={providerTypes === undefined ? providerTypesError : null}
                onRetry={refreshProviderTypes} onConfigure={configureInstalled} />
            )}

            {/* The model lane's post-install sub-flow replaces its card list once an
                app is chosen — key entry, Test, then the binding choice. */}
            {isModel && phase !== 'pick' ? (
              <ModelSubFlow app={modelApp} phase={phase} chatModel={chatModel}
                configured={configured} verifyRun={verifyRun}
                floor={floor} downloaded={downloaded} bindError={bindError}
                onBound={() => setPhase('verify')}
                onVerified={(model, isFloor) => { setChatModel(model); setFloor(isFloor); setPhase('done') }}
                onVerifyFailed={setVerifyFailed}
                onReconfigure={() => setPhase('configure')}
                onPickAnother={() => setPhase('pick')}
                onConfigured={(c) => { setConfigured(c); setPhase('bind') }} />
            ) : items.length === 0 && have.length === 0 && unreadable.length > 0 ? (
              /* Empty because the listing FAILED. The cause and the retry are stated once
                 above, so this says only what is true of this lane and claims nothing
                 about what exists.

                 Sits alongside #3447's `verify` phase rather than merging with it: both are
                 "do not state as fact what was never established", but over two different
                 reads. #3447 guards a POSITIVE claim (a model is ready) made from a probe
                 that built nothing; this guards a NEGATIVE claim (no such app exists) made
                 from a listing that failed. Different payload fields, different phases, so
                 expressing them once would couple a readiness state machine to a catalog
                 error and lose one of the two distinctions. */
              <p data-type="body-s" className="text-on-surface-low">
                Nothing to list — the app sources above could not be read.
              </p>
            ) : items.length === 0 && have.length === 0 ? (
              <p className="text-on-surface-low text-[0.8125rem]">
                No {lane.title.toLowerCase()} app is available from the first-party source
                (the workspace apps directory in a dev tree, otherwise the published apps
                repository). Add a source in the Store later.
              </p>
            ) : (
              <motion.div className="flex flex-col gap-1.5" initial="initial" animate="animate"
                variants={{ animate: { transition: stagger(0.04) } }}>
                {/* What is already installed comes first and says so; the catalog below it never
                    lists it again. */}
                {have.map((a) => <InstalledAppCard key={a.name} app={a} />)}
                {shown.map((e) => (
                  <AppCard key={e.name} entry={e} onInstall={() => install(e)} />
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

      {/* The Store's own consent dialog — same disclosure, same scan, same explicit confirm. */}
      {consent.dialog}
    </div>
    {actions}
    </>
  )
}

/** An app that is already installed: its name, what it is, and that it is installed — never an
 *  Install button, which the server would refuse (`already installed (use update)`). Its settings
 *  are Settings' to change, as its lane's own line says; a model provider's are configured right
 *  here, from "Already installed" in the model lane. */
function InstalledAppCard({ app }: { app: InstalledApp }) {
  return (
    <motion.div variants={listItemEnter} layout transition={spring.spatialFast}
      data-testid="onboarding-installed-app" className="flex items-center gap-s rounded-lg bg-surface-high p-m">
      <div className="min-w-0 flex-1">
        <div data-type="body-s" className="truncate text-on-surface">{app.label}</div>
        <div data-type="caption" className="truncate text-on-surface-low">{app.description || app.name}</div>
      </div>
      {app.on ? (
        <span data-type="caption" className="inline-flex shrink-0 items-center gap-xs" style={{ color: 'var(--color-success)' }}>
          <Check size={13} aria-hidden="true" /> Installed
        </span>
      ) : (
        // Installed but disabled is not ready, so it does not wear the green check.
        <span data-type="caption" className="shrink-0 text-on-surface-low">Installed, turned off in the Store</span>
      )}
    </motion.div>
  )
}

/** One catalog card: the app's name and what it is for, and an Install that opens the
 *  Store's consent dialog — the review of everything it gets happens there, before anything
 *  is installed. An installed app never gets one of these — it is an `InstalledAppCard`. */
function AppCard({ entry, onInstall }: {
  entry: AppCatalogEntry; onInstall: () => void
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
        <Button variant="ghost" size="sm" ariaLabel={`Install ${label}`} onClick={onInstall}>
          <Download size={14} aria-hidden="true" /> Install
        </Button>
      </div>
    </motion.div>
  )
}

/** The model lane's required rail, after its app is installed: enter the provider's
 *  own schema-declared fields (the key), test the connection, bind a chat model, then
 *  VERIFY that chat resolves. Four existing endpoints plus the verification.
 *
 *  `floor` (OU-14) is orthogonal to all of that: it is the verification's own report that what
 *  answers is the small bundled model, and it only decides what the DONE copy says.
 *  `downloaded` is the name of the model this session downloaded, so the lane says the
 *  download finished; `bindError` is a refused binding of it, said rather than swallowed. */
function ModelSubFlow({ app, phase, chatModel, configured, verifyRun, floor, downloaded, bindError, onConfigured, onBound, onVerified, onVerifyFailed, onReconfigure, onPickAnother }: {
  app: string; phase: ModelPhase
  /** The verified bound model, or `''` when resolution has no explicit binding to name. */
  chatModel: string
  /** A fresh check per value: the check remounts under it, so a new reason to check runs one
   *  even when the lane is already verifying. */
  verifyRun: number
  floor: boolean
  downloaded: string
  bindError: string
  configured: { provider: string; unprobed: string } | null
  onConfigured: (c: { provider: string; unprobed: string }) => void
  onBound: () => void
  onVerified: (model: string, floor: boolean) => void
  /** The verification found chat cannot use what is set up (`true`), or a new check began. */
  onVerifyFailed: (failed: boolean) => void
  onReconfigure: () => void
  /** Back to the lane's provider list. */
  onPickAnother: () => void
}) {
  const refused = bindError && (
    <p data-type="caption" className="text-on-surface-var" role="alert">
      It could not be set as your chat model ({bindError}). It still answers while nothing else is
      chosen; you can choose it in Settings → Models.
    </p>
  )
  if (phase === 'done') {
    // OU-14: when what answers is the BUNDLED floor model — bound or not — say which, and that
    // it is tiny. A bare "you're ready" here would be the first thing a new user reads about
    // their model, and it would be the one place the tiny default is presented as a finished
    // setup. `floor` comes from the verification itself, so it is false the moment anything
    // real is what resolves.
    if (floor) {
      return (
        <div className="flex flex-col gap-xs" data-testid="onboarding-floor-ready">
          <p data-type="body-s" className="inline-flex flex-wrap items-center gap-1.5 text-on-surface">
            <Check size={15} aria-hidden="true" style={{ color: 'var(--color-success)' }} />
            <span>
              {downloaded
                ? `Downloaded ${downloaded}. It answers your chats now.`
                : chatModel ? `Ready — chatting with ${chatModel}.` : 'Ready.'}
            </span>
          </p>
          <p data-type="caption" className="text-on-surface-var">
            It&rsquo;s the small model PersonalClaw downloaded: it runs on this machine with no key
            and no network, but it&rsquo;s tiny &mdash; expect short answers and no tools. Add a real
            provider in Settings → Providers whenever you like.
          </p>
          {refused}
        </div>
      )
    }
    return (
      <p className="inline-flex items-center gap-1.5 text-[0.8125rem]" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {chatModel ? `Chat model: ${chatModel}` : 'A chat model is configured — you\'re ready.'}
      </p>
    )
  }
  if (phase === 'verify') {
    return (
      <div className="flex flex-col gap-xs">
        {downloaded && (
          <p data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface">
            <Check size={15} aria-hidden="true" style={{ color: 'var(--color-success)' }} />
            Downloaded {downloaded}.
          </p>
        )}
        {refused}
        <VerifyChatModel key={verifyRun} onVerified={onVerified} onFailedChange={onVerifyFailed}
          onReconfigure={configured ? onReconfigure : undefined} onPickAnother={onPickAnother} />
      </div>
    )
  }
  if (phase === 'bind') return <BindModel configured={configured} onBound={onBound} />
  return <ConfigureProvider app={app} onConfigured={onConfigured} />
}

/** The lane's proof. Builds what chat builds (`GET /api/onboarding/model-check`) and reports
 *  the verdict; only `ok` advances to `done`, so nothing downstream — the green tick, the
 *  Continue button, the done-screen recap — can be reached by a binding that does not work.
 *
 *  **A build is not a call, so `ok` buys one more question.** A provider pointed at an address
 *  nothing listens on builds fine. So before `done`, the entry the verdict names (`provider`) is
 *  asked whether it ANSWERS — `POST /api/model-providers/{name}/test`, the probe Settings uses —
 *  on every path here, including a reload that opens the step on `verify`. That reload is where
 *  it used to go wrong: an Ollama entry saved at the default address with nothing running there
 *  read "A chat model is configured — you're ready", and every background run then failed. The
 *  in-process floor model has no address to test, and a type with no connectivity probe
 *  (`no_probe`) has nothing more to learn here, so both pass on the build alone.
 *
 *  **Every failing cause is the BACKEND's own sentence.** The bridge derives a distinct
 *  `why`/`fix` per cause (a ref naming a provider `config.json` no longer has; an entry
 *  present but unregistered; a type with no factory because no app claims it, or its app is
 *  installed-but-disabled, or it failed to load; a capability that misses the use case; a
 *  credential with no secret; an unmappable use case; an unreadable config; a build that
 *  failed for none of those reasons). This component renders those two strings and adds
 *  nothing, which is the only arrangement in which the count of causes a user can see equals
 *  the count the backend can tell apart. Collapsing them into one house sentence here would
 *  re-create at the presentation layer exactly the defect the bridge was fixed to remove.
 *
 *  🪤 NOT `useQuery`. That hook paints a cached value first and revalidates behind it, so a
 *  verification would flash the PREVIOUS verdict — a stale "ready" over a broken bind is the
 *  fabrication this whole component exists to prevent. A verdict is only ever this call's. */
function VerifyChatModel({ onVerified, onFailedChange, onReconfigure, onPickAnother }: {
  /** Reports the bound model the verdict names (or `''` when it names none) and whether what
   *  answers is the small bundled floor model. */
  onVerified: (model: string, floor: boolean) => void
  /** `true` when this check found chat CANNOT use what is set up (the build refused, or the
   *  entry that would answer did not); `false` when a check starts. Not called for a check
   *  that could not run, which is "we do not know", not a verdict. */
  onFailedChange: (failed: boolean) => void
  /** Back to the provider form, offered only when this flow is what configured it — there
   *  is no form to return to for a home that arrived already configured. */
  onReconfigure?: () => void
  /** Back to the lane's list of providers, so a provider that does not answer is not the only
   *  thing a reloaded step can offer. */
  onPickAnother: () => void
}) {
  const [verdict, setVerdict] = useState<ChatModelVerdict | null>(null)
  const [attempt, setAttempt] = useState(0)

  // The verdict handlers, held in refs so the fetch effect depends on the ATTEMPT alone. They
  // are inline arrows in the parent, so a new identity every render: listing them as
  // dependencies would re-run this effect (and re-fire the requests) on every repaint — the
  // unstable-callback loop `lib/data/useQuery` documents having measured.
  const verifiedRef = useRef(onVerified)
  verifiedRef.current = onVerified
  const failedRef = useRef(onFailedChange)
  failedRef.current = onFailedChange

  useEffect(() => {
    let alive = true
    setVerdict(null)
    failedRef.current(false)
    void checkChatModel().then((v) => {
      if (!alive) return
      setVerdict(v)
      // `floor` (OU-14) says whether what answers is the small bundled model — bound or not —
      // read off this verdict rather than off the readiness read the flow made before anything
      // was downloaded.
      if (v.kind === 'ok') verifiedRef.current(v.model, v.floor)
      else if (v.kind !== 'unknown') failedRef.current(true)
    })
    return () => { alive = false }
  }, [attempt])

  const again = <Button variant="secondary" size="sm" onClick={() => setAttempt((n) => n + 1)}>Check again</Button>
  // A way out of a verdict that cannot be fixed from here, on every failing branch: the form
  // when this flow is what configured the provider, and always the list of other providers —
  // a reloaded step opens on `verify` with no form behind it, and must not be a dead end.
  const exits = (
    <>
      {onReconfigure && <Button variant="ghost" size="sm" onClick={onReconfigure}>Change its settings</Button>}
      <Button variant="ghost" size="sm" onClick={onPickAnother}>Pick a different provider</Button>
    </>
  )

  // `ok` has already told the parent, which moves the lane to `done` and unmounts this — so the
  // passing branch renders the same waiting state rather than a second "ready" claim in a frame
  // that is about to be replaced.
  if (verdict === null || verdict.kind === 'ok') return <Spinner what="whether a chat model resolves" />
  if (verdict.kind === 'unknown') {
    // Not a verdict: it claims neither that chat is broken nor that it is ready.
    return (
      <div className="flex flex-col gap-s">
        <p data-type="body-s" className="text-on-surface-var">
          Couldn&rsquo;t check whether a chat model resolves: {verdict.message}
        </p>
        <div>{again}</div>
      </div>
    )
  }
  if (verdict.kind === 'silent') {
    return (
      <div className="flex flex-col gap-s">
        <p data-type="body-s" className="text-danger" role="alert">
          Chat would use {verdict.provider}, but it isn&rsquo;t answering.
        </p>
        {/* The connection test's own words: it is the only party that knows whether this is a
            refused connection, a timeout or a rejected key. */}
        <p data-type="body-s" className="text-on-surface-var">{verdict.message}</p>
        <p data-type="body-s" className="text-on-surface">
          Start it or correct its settings, then check again &mdash; or pick a different provider.
        </p>
        <div className="flex flex-wrap items-center gap-2">{again}{exits}</div>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-s">
      <p data-type="body-s" className="text-danger" role="alert">
        A model is set, but chat can&rsquo;t use it yet.
      </p>
      {/* WHY then FIX, both the backend's own words. The `why` is what is wrong and the `fix`
          is the one act that resolves it; merging them loses the half a user acts on. */}
      <p data-type="body-s" className="text-on-surface-var">{verdict.check.why}</p>
      <p data-type="body-s" className="text-on-surface">{verdict.check.fix}</p>
      <div className="flex flex-wrap items-center gap-2">{again}{exits}</div>
    </div>
  )
}

/** OU-13 — the local + LAN Ollama zero-key on-ramp, above the model catalog while the
 *  lane is still picking. Localhost detection runs automatically on mount — a loopback
 *  probe, never a network scan — and the LAN sweep fires NO request until the user
 *  presses "Scan my local network". A discovered endpoint is shown only after the
 *  backend's live `/api/tags` probe, and binding it writes NO API key (it rides the
 *  same credential-free path as `--seed-local-model`). When nothing is reachable the
 *  block offers no bind card, so the catalog below stays exactly as it was.
 *
 *  #3529 (owner, on a real fresh install): "there's an ollama on my host local network
 *  which should be accessible by finch vm. At least allow me to configure it manually" —
 *  and it is CLOSE TO GUARANTEED to miss for exactly that reason. A containerised gateway's
 *  loopback is the container's own, never the host's, and its LAN sweep scans the
 *  container's subnet, not the host's — so for every containerised install, discovery
 *  finding nothing is the default outcome, not an edge case. Both empty states below say so
 *  and point at `InstalledProviderTypes`, using `hasManualRoute` (the parent's OWN answer,
 *  never re-derived here) so this can never point at a route that doesn't actually exist. */
function LocalModelOnRamp({ onBound, hasManualRoute }: {
  /** A chat ref was written. Carries no label: the verification reads the model back. */
  onBound: () => void
  /** Whether a registered-but-uncatalogued provider type is actually rendering below —
   *  computed once, by `EssentialsStep`, from the same `/api/model-provider-types` read
   *  `InstalledProviderTypes` renders from. */
  hasManualRoute: boolean
}) {
  const { data: detection } = useQuery('onboarding:local-model', () => api.detectLocalModel())
  const [scanState, setScanState] = useState<'idle' | 'scanning' | 'done'>('idle')
  const [discovered, setDiscovered] = useState<LocalModelEndpoint[]>([])
  const [scanError, setScanError] = useState('')
  const [binding, setBinding] = useState('')   // endpoint currently binding
  const [bindError, setBindError] = useState('')

  const localhost: LocalModelEndpoint | null =
    detection?.detected && detection.endpoint && detection.model
      ? { endpoint: detection.endpoint, model: detection.model }
      : null

  const scan = async () => {
    setScanState('scanning'); setScanError('')
    try {
      const r = await api.scanLocalModels()
      setDiscovered(r.endpoints); setScanState('done')
    } catch (e) {
      setScanError(thrownMessage(e) || 'The network scan could not run.'); setScanState('done')
    }
  }

  const bind = async (ep: LocalModelEndpoint) => {
    setBinding(ep.endpoint); setBindError('')
    try {
      const r = await api.bindLocalModel(ep.endpoint)
      // The bind wrote the chat ref; which model that is comes back from the verification's
      // read of `active_models.json`, never from this response — see `VerifyChatModel`.
      if (r.ok) { onBound(); return }
      setBinding(''); setBindError('That local model could not be bound.')
    } catch (e) {
      setBinding(''); setBindError(thrownMessage(e) || 'That local model could not be bound.')
    }
  }

  // The localhost endpoint can also turn up in a scan; show it once, at the top.
  const lan = discovered.filter((e) => e.endpoint !== localhost?.endpoint)

  // #3529 — discovery's outcome must never be silent, whichever of its two checks ran.
  // Before this, a missed LOOPBACK probe said nothing at all (the block looked simply
  // unfinished until "Scan my local network" was clicked, with no hint the automatic
  // check had already run and missed), and a missed LAN SCAN said only that nothing was
  // found, naming no next step — a spinner that ends in an unchanged card is the same dead
  // end #3529 already named, wearing a different hat. `pointer` is appended only when
  // `hasManualRoute` says the fallback below is real.
  const pointer = hasManualRoute ? ' Enter its address directly below.' : ''
  let notFound: string | null = null
  if (scanState === 'done' && !scanError && lan.length === 0 && !localhost) {
    notFound = `No local model found on your network.${pointer}`
  } else if (scanState === 'idle' && detection !== undefined && !localhost) {
    notFound = `Nothing found on this machine yet.${pointer}`
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-outline-variant bg-surface p-3">
      <div className="flex items-baseline gap-2">
        <Cpu size={14} className="shrink-0 translate-y-0.5 text-primary" aria-hidden="true" />
        <span className="text-on-surface" data-type="body-s">Run a local model — no API key</span>
      </div>
      <p className="text-on-surface-low" data-type="caption">
        If you run Ollama on this machine or your network, PersonalClaw can use it with no
        key and nothing leaving your machine.
      </p>
      {notFound && <p className="text-on-surface-low" data-type="caption">{notFound}</p>}
      {bindError && <div className="text-danger" data-type="body-s" role="alert">{bindError}</div>}
      {localhost && (
        <LocalModelCard ep={localhost} where="on this machine"
          busy={binding === localhost.endpoint} onUse={() => bind(localhost)} />
      )}
      {lan.map((e) => (
        <LocalModelCard key={e.endpoint} ep={e} where="on your network"
          busy={binding === e.endpoint} onUse={() => bind(e)} />
      ))}
      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" loading={scanState === 'scanning'} onClick={scan}>
          <Search size={14} aria-hidden="true" /> Scan my local network
        </Button>
      </div>
      {scanError && (
        <div className="text-danger" data-type="body-s" role="alert">{scanError}{pointer}</div>
      )}
    </div>
  )
}

/** One reachable local endpoint, with a single "Use it" action. The bind is
 *  credential-free, and the card says so — the whole point is that no key is asked for. */
function LocalModelCard({ ep, where, busy, onUse }: {
  ep: LocalModelEndpoint; where: string; busy: boolean; onUse: () => void
}) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-surface-high p-3">
      <Cpu size={15} aria-hidden="true" className="shrink-0 text-primary" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-on-surface" data-type="body-s">{ep.model}</div>
        <div className="truncate text-on-surface-low" data-type="caption">Ollama {where} · {ep.endpoint} · no API key</div>
      </div>
      <Button variant="primary" size="sm" loading={busy} onClick={onUse}>
        <Check size={14} aria-hidden="true" /> Use this model
      </Button>
    </div>
  )
}

/** #3529 — which registered provider TYPES have no catalog card to reach them from.
 *  `/api/model-provider-types` lists a type only once its app is installed
 *  (`api_provider_types` walks the loaded provider registry, not the catalog), and
 *  `/api/apps/catalog` drops an installed app from its own listing
 *  (`resolve_catalog_entries`'s "Library exclusion") — so by construction neither
 *  list can name the same app today. This still checks rather than assumes that:
 *  a fixture (or a future registry change) is free to let the two overlap, and the
 *  point of this filter is exactly to never offer a second, redundant "configure it
 *  manually" card beside a catalog "Install" card for the one app. */
export function typesMissingFromCatalog(
  types: ModelProviderType[] | undefined,
  catalogModelApps: AppCatalogEntry[],
): ModelProviderType[] {
  if (!types) return []
  const catalogued = new Set(catalogModelApps.map((e) => e.name))
  return types.filter((t) => !catalogued.has(t.app))
}

/** Whether `ConfigureProvider` can set this type up at all: it CREATES AN INSTANCE (a
 *  `config.json` provider row named after the type), which only means something for a type
 *  that takes instances — every account- or server-backed provider app declares
 *  `multiInstance: true`.
 *
 *  🔴 A single-instance type registers its own entry and keeps its settings in the app's own
 *  store, so "configuring" one here wrote a row the app never reads. That is what the bundled
 *  offline model's "Configure" card did: its "Save and test" created a `bundled-chat` row with no
 *  model on disk, then reported "Nothing came back… pick a different provider", and a reload
 *  read the row back as "a chat model is configured — you're ready". Its route in is the
 *  download offer at the top of the lane; its settings live in Settings → Providers. */
export function configurableAsInstance(t: ModelProviderType): boolean {
  return t.multiInstance
}

/** #3529 — the fallback the on-ramp and the catalog cannot cover between them: a
 *  provider type whose app is already installed has no "Install" card (the catalog
 *  excludes what's already installed), so without this the ONLY way into its
 *  settings form was already-working discovery (Ollama) or never needing one
 *  (nothing else ships pre-installed today). Each row goes straight to the SAME
 *  `ConfigureProvider` the catalog path uses, driven by the type's own
 *  `settingsSchema` — never a hand-picked field, so a future pre-installed
 *  provider with a different required field needs no change here.
 *
 *  Purely presentational: `EssentialsStep` owns the one `/api/model-provider-types`
 *  fetch and the one `typesMissingFromCatalog` call, because the on-ramp above needs
 *  that same answer to decide whether IT can truthfully point down here — two
 *  independent fetches could disagree about whether this route exists at all. */
function InstalledProviderTypes({ missing, error, onRetry, onConfigure }: {
  missing: ModelProviderType[]
  error: unknown
  onRetry: () => void
  onConfigure: (app: string) => void
}) {
  if (error) {
    return <LoadError what="installed model providers" error={error} onRetry={onRetry} />
  }
  if (missing.length === 0) return null

  return (
    <div className="flex flex-col gap-s rounded-lg border border-outline-variant bg-surface p-m">
      <div className="flex items-baseline gap-s">
        <Cpu size={14} className="shrink-0 translate-y-0.5 text-primary" aria-hidden="true" />
        <span className="text-on-surface" data-type="body-s">Already installed</span>
      </div>
      <p className="text-on-surface-low" data-type="caption">
        Already installed, so it won&rsquo;t show up below as something to install — configure
        its connection directly.
      </p>
      {missing.map((t) => (
        <div key={t.type} className="flex items-center gap-s rounded-lg bg-surface-high p-m">
          <Cpu size={15} aria-hidden="true" className="shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-on-surface" data-type="body-s">{t.label}</div>
            <div className="truncate text-on-surface-low" data-type="caption">Installed as {t.app}</div>
          </div>
          <Button variant="secondary" size="sm" onClick={() => onConfigure(t.app)}>
            Configure {t.label}
          </Button>
        </div>
      ))}
    </div>
  )
}

/** Key entry + Test. The instance is named after its provider type — a first run
 *  should not have to invent an instance name — and a re-entry updates the existing
 *  instance rather than dead-ending on the create endpoint's 409.
 *
 *  The instance NAME is the provider type (`ollama`), not the app name (`ollama-models`):
 *  a `provider:model` ref, the test route and every diagnosis speak the entry name, so an
 *  app name here would produce a binding that silently never resolves. */
function ConfigureProvider({ app, onConfigured }: {
  app: string
  onConfigured: (c: { provider: string; unprobed: string }) => void
}) {
  const { data: types, error: typesError, refresh } = useQuery(
    'onboarding:provider-types', () => api.modelProviderTypes())
  // TYPED, as the settingsSchema declares each field — a boolean is `true`, a number is `4096`.
  // This used to be `Record<string, string>` seeded with `String(default)`, so a switch rendered
  // as a text box holding "true" and every option saved as a string, which a provider reading
  // `isinstance(v, int)` then silently ignored.
  const [values, setValues] = useState<Record<string, unknown>>({})
  // Which fields the user has actually typed into, distinct from a field merely sitting
  // at its (usually empty) schema default. `submit()` needs the distinction: a SENSITIVE
  // field the user deliberately blanked must clear a stored credential (#3554), but a
  // sensitive field nobody touched must stay out of the PATCH entirely — otherwise a
  // re-entry that only corrects, say, the default model would silently wipe a working key
  // the form never re-displays (this component has no GET of the instance's current
  // options; every field always starts back at its schema default).
  const [touched, setTouched] = useState<Record<string, true>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [seeded, setSeeded] = useState('')

  const t: ModelProviderType | undefined = types?.find((x) => x.app === app) ?? undefined
  const props = t?.settingsSchema?.properties || {}
  const required = t?.settingsSchema?.required || []
  // Seed the schema defaults once the type resolves, without an effect: the first
  // render that knows the type also knows its defaults — in their own types.
  if (t && seeded !== t.type) {
    setValues(schemaDefaults(t.settingsSchema)); setTouched({}); setSeeded(t.type)
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
      if (isBlankOption(values[r] ?? props[r]?.default)) {
        setError(`${props[r]?.['x-meta']?.label || r} is required`); return
      }
    }
    setBusy(true); setError('')
    const options: Record<string, ProviderOptionValue> = {}
    for (const [k, f] of Object.entries(props)) {
      const raw = values[k] ?? f.default
      // A string is trimmed; every other type is sent as the form holds it — `false` and `0`
      // are settings, not blanks.
      const v = typeof raw === 'string' ? raw.trim() : raw
      if (!isBlankOption(v)) { options[k] = v as ProviderOptionValue; continue }
      // An emptied SENSITIVE field the user actually touched is a deliberate "clear the
      // stored credential" (#3554: the field's own help text promises "leave empty to
      // fall back to the environment variable" — true on the first save and false on
      // every later one, because omitting the key here left the old value untouched by
      // the PATCH-merge). `null` says "clear this" explicitly, distinct from omitting the
      // key (which still means "leave whatever is stored alone"). An untouched field —
      // including one whose default happens to be non-empty — is omitted exactly as
      // before: nobody asked to change it.
      if (touched[k] && (f['x-meta'] || {}).sensitive) options[k] = null
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
      // 🪤 `ok: true` is NOT "connected". `POST /api/model-providers/{name}/test` answers
      // `{ok: true, status: 'no_probe'}` when the entry's type registers no catalog — i.e.
      // when nothing was tested at all — so treating `ok` as a passed connection test told
      // the user their key works on the strength of a probe that never ran. Carry the
      // distinction forward instead of asserting either way; the lane's real proof is the
      // build check at the end, and the bind step below says what an empty list can mean.
      onConfigured({ provider: t.type, unprobed: res.status === 'no_probe' ? (res.message || 'No connectivity probe available for this provider type') : '' })
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
      {/* "test the connection for real" was the earlier promise, and it is not one this
          button can always keep: a provider type that registers no catalog reports
          `no_probe` — nothing was tested — and saying otherwise is the same fabrication as a
          green tick on an unverified binding. So the copy states what saving does do, and
          names the check that always runs. */}
      <p className="text-on-surface-var text-[0.8125rem]">
        {t.label} is installed. Fill in its settings — saving tests the connection where this
        provider offers a test, and the last step checks that chat can really use it.
      </p>
      <div className="flex flex-col gap-2">
        <SchemaFields
          fields={Object.entries(props)}
          required={required}
          values={values}
          advancedFieldClassName="flex flex-col gap-s"
          renderField={(k, field) => (
            <SchemaField fieldKey={k} prop={field} value={values[k]}
              onChange={(v) => { setValues((m) => ({ ...m, [k]: v })); setTouched((m) => ({ ...m, [k]: true })) }} />
          )}
        />
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
function BindModel({ configured, onBound }: {
  configured: { provider: string; unprobed: string } | null
  /** A chat ref was written. Carries no label: the verification reads the model back. */
  onBound: () => void
}) {
  const { data: models, error, refresh } = useQuery('onboarding:chat-models', () => api.chatModels())
  const [binding, setBinding] = useState('')
  const [failed, setFailed] = useState('')

  if (models === undefined && error) return <LoadError what="chat models" error={error} onRetry={refresh} />
  if (models === undefined) {
    return <Spinner what="chat models" />
  }
  if (models.length === 0) {
    return <NoModelsDiscovered configured={configured} onRetry={refresh} />
  }

  const bind = async (m: ChatModelOption) => {
    setBinding(m.name); setFailed('')
    const ref = m.provider ? `${m.provider}:${m.model_id}` : m.model_id
    try { await api.setActiveModel('chat', [ref]); onBound() }
    catch (e) { setBinding(''); setFailed(thrownMessage(e) || 'Could not bind that model.') }
  }

  return (
    <div className="flex flex-col gap-m">
      <p className="text-on-surface-var text-[0.8125rem]">Pick the model the agent should chat with:</p>
      {/* Where the previous step could not test the connection, this list IS the first real
          evidence the provider answers — say so, rather than letting the user infer that
          "Save and test" proved something it could not. */}
      {configured?.unprobed && (
        <p data-type="body-s" className="text-on-surface-low">
          {configured.provider} has no connectivity test, so these models are the first
          evidence it answers.
        </p>
      )}
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

/** Discovery returned nothing — and this is where that used to be reported as a fact about
 *  the provider ("No chat-capable models were discovered for this provider"), which it is
 *  not. `GET /api/models/chat` gathers each provider's catalog with
 *  `asyncio.gather(..., return_exceptions=True)` and drops a raising provider silently, so
 *  a wrong key, a wrong endpoint and a provider that genuinely offers no chat model all
 *  arrive here as the same empty array. The entry this flow creates carries no pinned
 *  `model`, so there is not even a fallback id to distinguish them.
 *
 *  So ask. `POST /api/model-providers/{name}/test` is the live reachability probe Settings
 *  already uses, and its three answers are exactly the three sentences owed here: reached
 *  and offering nothing, not reachable (with the server's reason), or un-probeable — in
 *  which case the honest report is that an empty list proves nothing.
 *
 *  With no `configured` provider (the lane arrived at `bind` straight from readiness) there
 *  is nothing to test by name, so it states both possibilities rather than picking one. */
function NoModelsDiscovered({ configured, onRetry }: {
  configured: { provider: string; unprobed: string } | null
  onRetry: () => void
}) {
  const provider = configured?.provider || ''
  const [probe, setProbe] = useState<{ ok: boolean; status?: string; message: string } | null>(null)
  const [probeFailed, setProbeFailed] = useState('')

  useEffect(() => {
    if (!provider) return
    let alive = true
    setProbe(null); setProbeFailed('')
    api.testModelProvider(provider)
      .then((r) => { if (alive) setProbe(r) })
      .catch((e) => { if (alive) setProbeFailed(thrownMessage(e) || 'The connection test could not run.') })
    return () => { alive = false }
  }, [provider])

  let verdict: string
  if (!provider) {
    verdict = 'Nothing came back. That means either the configured provider offers no chat-capable model, or it could not be reached — this list cannot tell those apart. Test the provider in Settings → Providers to find out which.'
  } else if (probeFailed) {
    verdict = `Nothing came back, and ${provider}'s connection test could not run either: ${probeFailed}`
  } else if (probe === null) {
    verdict = `Nothing came back. Checking whether ${provider} is reachable…`
  } else if (!probe.ok) {
    verdict = `${provider} could not be reached: ${probe.message} So this empty list is a connection problem, not a provider without models — correct its settings and try again.`
  } else if (probe.status === 'no_probe') {
    verdict = `Nothing came back, and ${provider} offers no connectivity test (${probe.message}), so an empty list here cannot be told apart from a provider that is not answering. Set a model id on it in Settings → Providers, or pick a different provider.`
  } else {
    verdict = `${provider} answered, and offered no chat-capable model. Set a model id on it in Settings → Providers, or pick a different provider.`
  }

  return (
    <div className="flex flex-col gap-s">
      <p data-type="body-s" className="text-on-surface-var" role="status">{verdict}</p>
      <div><Button variant="secondary" size="sm" onClick={onRetry}>Check again</Button></div>
    </div>
  )
}

/** An option that carries no value: absent, `null`, or an empty/whitespace string. `false` and
 *  `0` are NOT blank — a switch turned off and a zero are settings a user made. */
function isBlankOption(v: unknown): boolean {
  return v === undefined || v === null || (typeof v === 'string' && !v.trim())
}
