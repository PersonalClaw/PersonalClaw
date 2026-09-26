import { useCallback, useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api, isLiveDownload, type BundledModelOffer, type DownloadJob, type OnboardingState } from '../../lib/api'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { WavyProgress } from '../../ui/WavyProgress'
import { fvs } from '../../design/fontWeight'
import { useModelDownloads } from '../settings/useModelDownloads'

/** Bytes as a whole number of MiB — the unit the download offer and the progress row share. */
export const mib = (n: number) => `${Math.round(n / (1024 * 1024))} MiB`

/** `eta_s` as something a person reads. `0` means "not known yet", never "instant". */
export function etaText(seconds: number): string {
  if (!seconds || seconds < 1) return ''
  if (seconds < 60) return `about ${Math.round(seconds)}s left`
  return `about ${Math.round(seconds / 60)} min left`
}

/** Where the one-time download stands for THIS session's surface. `done` only for a download
 *  this surface started or watched run — a finished job the server still remembers from an
 *  earlier session is not news here, and treating it as one is how a stale record would read as
 *  "downloaded" beside a model that has since been deleted. And `done` only once the model has
 *  been offered the chat binding (see `useBundledModelDownload`), so a surface that reacts to it
 *  reads a binding that is already written. */
export type BundledDownloadPhase = 'idle' | 'running' | 'failed' | 'done'

/** Make the downloaded model the chat model — unless something else already is.
 *
 *  The user asked for this model, and a download that left chat on the implicit fallback left it
 *  off every model list and unnamed wherever the chat model is named. Read live, not off a
 *  readiness read taken before the download: an existing binding is the user's, and it is never
 *  overwritten. Resolves to `''`, or to the refusal in the server's words. */
async function bindIfNothingIs(offer: BundledModelOffer): Promise<string> {
  try {
    const now = await api.onboarding()
    if ((now.chat_model_refs ?? []).length === 0) {
      await api.setActiveModel('chat', [`${offer.provider}:${offer.model}`])
    }
    return ''
  } catch (e) {
    const raw = e instanceof Error ? e.message : String(e ?? '')
    try { return String(JSON.parse(raw)?.error ?? raw) || 'the binding was refused' } catch { return raw || 'the binding was refused' }
  }
}

/** OU-14 — the ONE state machine behind every surface that offers the small model's download:
 *  the chat screen's notice and onboarding's model step.
 *
 *  They used to be two copies of the same fetch-and-track logic, and the copies drifted in the
 *  way that mattered: the chat screen's was mounted wherever chat was, so a reload re-attached
 *  to a running job and kept showing progress; onboarding's lived inside a sub-form reached
 *  only through a "Configure" click, so the same reload dropped the bar and left no way back to
 *  it. One hook, read by both, is the only arrangement in which "a reload resumes the bar" is
 *  true of one surface exactly when it is true of the other.
 *
 *  What it owns: the `GET /api/onboarding` read (and a failure of it, reported as a failure, not
 *  as an absence), the job through the shared `useModelDownloads` runner — so progress, cancel
 *  and per-failure copy come from the one download mechanism the Settings card uses — and the
 *  offer as last seen, KEPT after the download fulfils it, because the server stops offering a
 *  model the moment it is on disk and a surface that forgot the offer then had nothing to say
 *  the download had finished with.
 *
 *  It also owns what a finished download MEANS: the model becomes the chat model when nothing
 *  else is (`bindIfNothingIs`). That used to be onboarding's alone, so the same download started
 *  from the chat screen left chat on the implicit fallback — answering, but bound to nothing,
 *  listed nowhere and unnamed. One rule, in the one machine both surfaces read. */
export function useBundledModelDownload(): {
  state: OnboardingState | null
  probeError: string
  /** The live offer, or — once this session's download finished — the offer it fulfilled. */
  offer: BundledModelOffer | null
  job: DownloadJob | undefined
  phase: BundledDownloadPhase
  /** A refused start or cancel, in the server's words. */
  error: string
  /** The finished download could not be made the chat model — the server's words, or `''`. */
  bindError: string
  starting: boolean
  start: () => void
  cancel: () => void
  refresh: () => void
} {
  const [state, setState] = useState<OnboardingState | null>(null)
  const [probeError, setProbeError] = useState('')
  const [kept, setKept] = useState<BundledModelOffer | null>(null)
  /** Models whose download this surface started or watched run — see `BundledDownloadPhase`. */
  const [owned, setOwned] = useState<ReadonlySet<string>>(() => new Set())
  /** Finished jobs whose model has been offered the chat binding, and how that went. */
  const [bindings, setBindings] = useState<Readonly<Record<string, 'pending' | 'settled'>>>({})
  const [bindError, setBindError] = useState('')
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  const refresh = useCallback(() => {
    // RECORDED then reset, never discarded. A probe that cannot be read must not invent an
    // answer either way — but what is lost with it is the download offer, the one escape from
    // an unconfigured install, so the failure is reported as a failure by the caller.
    api.onboarding()
      .then((s) => {
        setProbeError(''); setState(s)
        if (s.chat_download_offer) setKept(s.chat_download_offer)
      })
      .catch((e: Error) => { setProbeError(e.message || String(e)); setState(null) })
  }, [])
  useEffect(refresh, [refresh])

  const live = state?.chat_download_offer ?? null
  const tracked = live ?? kept
  // Provider-scoped; '' is the inert value (it matches no job) until an offer has been seen.
  const { jobs, start: startJob, cancel: cancelJob } = useModelDownloads(tracked?.provider ?? '', refresh)
  const trackedJob = tracked ? jobs[tracked.model] : undefined

  // A job seen RUNNING is this session's download, whether it was started here or re-attached
  // to after a reload. Recorded in an effect, not during render.
  useEffect(() => {
    if (!tracked || !trackedJob || !isLiveDownload(trackedJob) || owned.has(tracked.model)) return
    setOwned((s) => new Set(s).add(tracked.model))
  }, [tracked, trackedJob, owned])

  const fulfilled = !!tracked && trackedJob?.state === 'done' && owned.has(tracked.model)
  const offer = live ?? (fulfilled ? kept : null)
  const job = offer ? jobs[offer.model] : undefined

  // A download this surface owns has just finished: offer its model the chat binding, ONCE per
  // job, then re-read readiness so every label reads the binding that is now written.
  useEffect(() => {
    if (!fulfilled || !tracked || !trackedJob || bindings[trackedJob.id]) return
    const id = trackedJob.id
    setBindings((b) => ({ ...b, [id]: 'pending' }))
    void bindIfNothingIs(tracked).then((refused) => {
      setBindError(refused)
      setBindings((b) => ({ ...b, [id]: 'settled' }))
      refresh()
    })
  }, [fulfilled, tracked, trackedJob, bindings, refresh])

  let phase: BundledDownloadPhase = 'idle'
  if (job && isLiveDownload(job)) phase = 'running'
  else if (job?.state === 'error') phase = 'failed'
  else if (job?.state === 'done' && offer && owned.has(offer.model)) {
    // Still `running` to its readers until the binding step has settled, so "done" is never
    // announced over a chat binding that is about to change under it.
    phase = bindings[job.id] === 'settled' ? 'done' : 'running'
  }

  const start = useCallback(() => {
    if (!offer) return
    const model = offer.model
    setStarting(true); setError('')
    // Owned from the click: a model already on disk answers with an immediately-`done` job
    // that is never seen running, and it is still the download this user just asked for.
    setOwned((s) => new Set(s).add(model))
    void startJob(model)
      .catch((e: Error) => setError(e.message || String(e)))
      .finally(() => setStarting(false))
  }, [offer, startJob])

  const cancel = useCallback(() => {
    if (!offer) return
    void cancelJob(offer.model).catch((e: Error) => setError(e.message || String(e)))
  }, [offer, cancelJob])

  return { state, probeError, offer, job, phase, error, bindError, starting, start, cancel, refresh }
}

/** The running download, drawn the same on every surface: which model, bytes of the total, an
 *  ETA once one is known, a DETERMINATE bar when the total is known, and a reachable Cancel. */
export function BundledDownloadProgress({ offer, job, onCancel }: {
  offer: BundledModelOffer; job: DownloadJob; onCancel: () => void
}) {
  const eta = etaText(job.eta_s)
  return (
    <>
      <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
        Downloading {offer.label} — {mib(job.downloaded_bytes)} of {mib(job.total_bytes || offer.bytes)}
        {eta ? `, ${eta}` : ''}
      </p>
      <div className="mt-1.5 flex items-center gap-s">
        {job.total_bytes > 0
          ? <WavyProgress width={200} value={job.progress} label={`Downloading ${offer.label}`} />
          : <WavyProgress width={200} />}
        <SquareIconButton icon={X} label="Cancel the model download" onClick={onCancel} />
      </div>
    </>
  )
}
