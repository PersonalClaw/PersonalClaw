import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, HardDriveDownload, X } from 'lucide-react'
import { api, type OnboardingState } from '../../lib/api'
import { Button } from '../../ui/Button'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { WavyProgress } from '../../ui/WavyProgress'
import { TextLink } from '../../ui/TextLink'
import { fvs } from '../../design/fontWeight'
import { useModelDownloads } from '../settings/useModelDownloads'
import { MODELS_ROUTE } from './NoModelSetupState'

/** Bytes as a whole number of MiB — the unit the download offer and the progress row share. */
const MiB = (n: number) => `${Math.round(n / (1024 * 1024))} MiB`

/** `eta_s` as something a person reads. `0` means "not known yet", never "instant". */
function eta(seconds: number): string {
  if (!seconds || seconds < 1) return ''
  if (seconds < 60) return `about ${Math.round(seconds)}s left`
  const mins = Math.round(seconds / 60)
  return `about ${mins} min left`
}

/** OU-14 — the chat surface's own account of what will answer, and what it costs.
 *
 *  PersonalClaw ships no model weight (the wheel would be ~147 MiB, over PyPI's per-file
 *  limit). Instead a small Apache-2.0 model is downloaded ONCE, and this component is where a
 *  user meets that fact. It has two jobs and renders nothing in every other state:
 *
 *  1. **There is a model to download.** Say so with the SIZE, start it on an explicit click,
 *     then show bytes, a percentage and an ETA with a Cancel button. The size is not optional:
 *     138 MiB on a slow connection is minutes, and a progress spinner with no number is the
 *     failure shape this repo has spent real time removing.
 *  2. **The download happened and nothing else is bound.** Say plainly that the answers are
 *     coming from a tiny local model and how to replace it — because a newcomer whose first
 *     reply comes from a 135M model with no explanation concludes *PersonalClaw* is bad at
 *     chat, and never learns that binding a real model is one click away.
 *
 *  **Why a click and not an automatic fetch.** The download is real outbound network use, and
 *  this project does not do that behind the operator's back — the credential-free seed helper
 *  refuses to touch the network unasked for the same reason, and a headless server would
 *  otherwise pull 138 MiB for a user who is never going to chat. One button, labelled with the
 *  cost, is both honest and cheap.
 *
 *  Both states key off `GET /api/onboarding`, which the backend derives from the resolver and
 *  the local-model registry — `chat_download_offer` when a chat model is downloadable and
 *  nothing resolves, `chat_is_bundled_floor` when what resolves is a declared floor. Binding
 *  any real provider makes both false on the next poll, so neither can linger.
 */
export function BundledFloorNotice() {
  const [state, setState] = useState<OnboardingState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [probeError, setProbeError] = useState('')
  const [declined, setDeclined] = useState(false)

  const refresh = useCallback(() => {
    setProbeError('')
    api.onboarding()
      .then((s) => { setProbeError(''); setState(s) })
      // RECORDED then reset, never discarded. A probe that cannot be read must not invent a
      // warning — a banner shown on a fetch failure would accuse a perfectly-bound home of
      // running a toy model — but it must not silently swallow either, because the thing lost
      // with the answer is the DOWNLOAD OFFER: the one escape from an unconfigured install. So
      // the failure is reported as a failure (below), not as an absence.
      .catch((e: Error) => { setProbeError(e.message || String(e)); setState(null) })
  }, [])
  useEffect(refresh, [refresh])

  const offer = state?.chat_download_offer ?? null
  // The provider name is only known once the offer arrives; the hook is provider-scoped, so
  // until then it tracks nothing. Empty string is the inert value — `api.modelDownloads()`
  // filters by provider and matches none.
  const { jobs, start, cancel } = useModelDownloads(offer?.provider ?? '', refresh)
  const job = offer ? jobs[offer.model] : undefined

  if (probeError) {
    return (
      <div
        role="status"
        data-testid="bundled-model-probe-error"
        className="mb-s rounded-lg bg-surface-container px-m py-2.5"
        style={{ border: '1px solid var(--color-outline-variant)' }}
      >
        <p data-type="caption" className="inline-flex items-start gap-1.5 text-on-surface-var">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            Couldn&rsquo;t check whether a model is set up &mdash; {probeError}.{' '}
            <TextLink onClick={refresh}>Try again</TextLink>
          </span>
        </p>
      </div>
    )
  }
  if (!state) return null

  // "Not now" dismisses the OFFER for this session, and dismisses only the offer: once the
  // model is downloaded the honest label below is not a thing a user opted out of, it is what
  // is answering them. Session-scoped on purpose — a persisted "never ask again" would need a
  // settings row to ever get back, and the calm setup state plus Settings → Models is already
  // the way to a real provider.
  if (offer && !declined) {
    const running = job?.state === 'running' || job?.state === 'queued'
    const determinate = !!job && job.total_bytes > 0
    const failed = job?.state === 'error' ? job.error : ''
    return (
      <div
        role="status"
        data-testid="bundled-model-offer"
        className="mb-s rounded-lg bg-surface-container px-m py-2.5"
        style={{ border: '1px solid var(--color-outline-variant)' }}
      >
        <div className="flex items-start gap-2.5">
          <HardDriveDownload size={16} className="mt-0.5 shrink-0 text-on-surface-var" aria-hidden />
          <div className="min-w-0 flex-1">
            {running ? (
              <>
                <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
                  Getting a small model ready — {MiB(job!.downloaded_bytes)} of{' '}
                  {MiB(job!.total_bytes || offer.bytes)}
                  {eta(job!.eta_s) ? `, ${eta(job!.eta_s)}` : ''}
                </p>
                <div className="mt-1.5 flex items-center gap-s">
                  {determinate ? (
                    <WavyProgress width={200} value={job!.progress} label="Downloading the bundled model" />
                  ) : (
                    <WavyProgress width={200} />
                  )}
                  <SquareIconButton
                    icon={X}
                    label="Cancel the model download"
                    onClick={() => { void cancel(offer.model).catch((e: Error) => setError(e.message)) }}
                  />
                </div>
                <p data-type="caption" className="mt-xs text-on-surface-var">
                  You can cancel and set up any provider instead — nothing here is required.
                </p>
              </>
            ) : (
              <>
                <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
                  Chat right away with a small model — a one-time {MiB(offer.bytes)} download
                </p>
                <p data-type="caption" className="mt-0.5 text-on-surface-var">
                  No account and no API key: it runs on this machine, and after this download it
                  needs no network at all. It is tiny, so expect short, shaky answers and no tool
                  use — it is there so a fresh install is not a dead end.{' '}
                  <a href={MODELS_ROUTE} className="text-primary underline">
                    Or connect a provider you already have
                  </a>
                  .
                </p>
                <div className="mt-s flex items-center gap-s">
                  <Button
                    size="sm"
                    loading={busy}
                    loadingLabel="Starting the download"
                    onClick={() => {
                      setBusy(true); setError('')
                      void start(offer.model)
                        .catch((e: Error) => setError(e.message))
                        .finally(() => setBusy(false))
                    }}
                  >
                    {`Download ${MiB(offer.bytes)}`}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDeclined(true)}>
                    Not now
                  </Button>
                </div>
              </>
            )}
            {(error || failed) && (
              <p
                data-type="caption"
                className="mt-1.5 inline-flex items-start gap-1.5"
                style={{ color: 'var(--color-danger)' }}
              >
                <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden />
                <span>{error || failed}</span>
              </p>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (!state.chat_is_bundled_floor) return null
  return (
    <div
      role="status"
      data-testid="bundled-floor-notice"
      className="mb-s rounded-lg bg-surface-container px-m py-2.5"
      style={{ border: '1px solid var(--color-outline-variant)' }}
    >
      <div className="flex items-start gap-2.5">
        <HardDriveDownload size={16} className="mt-0.5 shrink-0 text-on-surface-var" aria-hidden />
        <div className="min-w-0 flex-1">
          <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
            You&rsquo;re talking to the small model PersonalClaw downloaded
          </p>
          <p data-type="caption" className="mt-0.5 text-on-surface-var">
            It runs on this machine with no account and no API key, which is why it can answer
            before you&rsquo;ve set anything up &mdash; it needed one download and works offline
            from here. But it&rsquo;s tiny, so expect short, shaky answers and no tool use.{' '}
            <a href={MODELS_ROUTE} className="text-primary underline">
              Connect a real model
            </a>{' '}
            and this stops being used.
          </p>
        </div>
      </div>
    </div>
  )
}
