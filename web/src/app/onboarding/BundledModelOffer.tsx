import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, HardDriveDownload, X } from 'lucide-react'
import { api, type OnboardingState } from '../../lib/api'
import { Button } from '../../ui/Button'
import { SquareIconButton } from '../../ui/SquareIconButton'
import { WavyProgress } from '../../ui/WavyProgress'
import { TextLink } from '../../ui/TextLink'
import { useModelDownloads } from '../../pages/settings/useModelDownloads'

const MiB = (n: number) => `${Math.round(n / (1024 * 1024))} MiB`

function etaLabel(seconds: number): string {
  if (!seconds || seconds < 1) return ''
  if (seconds < 60) return `, about ${Math.round(seconds)}s left`
  return `, about ${Math.round(seconds / 60)} min left`
}

/** OU-14 — the no-account way past the model wall, inside the onboarding essentials lane.
 *
 *  The model lane is the step the activation audit named as the single biggest drop-off, and
 *  every card below it needs an account somewhere. This offers the alternative: one click, no
 *  key, a stated size. The size is part of the offer and not a detail — 138 MiB is minutes on a
 *  slow connection, and agreeing to a download whose cost you were not told is not agreeing.
 *
 *  It renders NOTHING unless `GET /api/onboarding` reports a downloadable chat model, so a home
 *  that already has one, or has a provider bound, sees the lane exactly as it was. Progress,
 *  cancellation and per-failure copy come from the same generic download job the Settings card
 *  uses (`useModelDownloads`), so there is one download mechanism and one place it can be wrong.
 */
export function BundledModelOffer() {
  const [state, setState] = useState<OnboardingState | null>(null)
  const [error, setError] = useState('')
  const [probeError, setProbeError] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(() => {
    // RECORDED then reset, never discarded. What is lost with an unreadable probe is this card —
    // the lane's only no-account option — so its absence has to be explained rather than just
    // happen. The lane below still works either way; this says why the cheap option vanished.
    api.onboarding()
      .then((s) => { setProbeError(''); setState(s) })
      .catch((e: Error) => { setProbeError(e.message || String(e)); setState(null) })
  }, [])
  useEffect(refresh, [refresh])

  const offer = state?.chat_download_offer ?? null
  const { jobs, start, cancel } = useModelDownloads(offer?.provider ?? '', refresh)
  const job = offer ? jobs[offer.model] : undefined

  if (probeError) {
    return (
      <p
        data-testid="onboarding-model-offer-error"
        data-type="caption"
        className="mb-s inline-flex items-start gap-1.5 text-on-surface-var"
      >
        <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden />
        <span>
          Couldn&rsquo;t check for a no-account option &mdash; {probeError}.{' '}
          <TextLink onClick={refresh}>Try again</TextLink>
          , or pick a provider below.
        </span>
      </p>
    )
  }
  if (!offer) return null
  const running = job?.state === 'running' || job?.state === 'queued'
  const failed = job?.state === 'error' ? job.error : ''

  return (
    <div
      data-testid="onboarding-model-offer"
      className="mb-s rounded-lg bg-surface-container px-m py-2.5"
      style={{ border: '1px solid var(--color-outline-variant)' }}
    >
      <div className="flex items-start gap-2.5">
        <HardDriveDownload size={16} className="mt-0.5 shrink-0 text-on-surface-var" aria-hidden />
        <div className="min-w-0 flex-1">
          {running ? (
            <>
              <p data-type="body-s" className="text-on-surface">
                Getting a small model ready — {MiB(job!.downloaded_bytes)} of{' '}
                {MiB(job!.total_bytes || offer.bytes)}{etaLabel(job!.eta_s)}
              </p>
              <div className="mt-1.5 flex items-center gap-s">
                {job!.total_bytes > 0
                  ? <WavyProgress width={200} value={job!.progress} label="Downloading the bundled model" />
                  : <WavyProgress width={200} />}
                <SquareIconButton
                  icon={X}
                  label="Cancel the model download"
                  onClick={() => { void cancel(offer.model).catch((e: Error) => setError(e.message)) }}
                />
              </div>
            </>
          ) : (
            <>
              <p data-type="body-s" className="text-on-surface">
                No account? Download a small model instead — {MiB(offer.bytes)}, once
              </p>
              <p data-type="caption" className="mt-0.5 text-on-surface-var">
                It runs on this machine with no key, and needs no network after this download.
                Tiny, so expect short answers and no tool use — enough to start, and you can
                add a real provider any time.
              </p>
              <div className="mt-s">
                <Button
                  size="sm"
                  variant="tonal"
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
