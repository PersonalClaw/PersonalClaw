import { useEffect, useRef } from 'react'
import { AlertTriangle, Check, HardDriveDownload } from 'lucide-react'
import type { BundledModelOffer as Offer } from '../../lib/api'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { fvs } from '../../design/fontWeight'
import { BundledDownloadProgress, mib, useBundledModelDownload } from '../../pages/chat/bundledModelDownload'

/** OU-14 — the no-account way past the model wall: a first-class action in onboarding's model
 *  lane, not a detail inside one provider's settings form.
 *
 *  🔴 IT USED TO BE BEHIND A "CONFIGURE" CLICK. Step 3 opened with Ollama discovery and a list
 *  of providers that all need an account; this offer appeared only after the user picked
 *  "Configure" on "Bundled offline model" under *Already installed* — while the server was
 *  offering the download the whole time. So the one option that needs no account was the one a
 *  new user was least likely to find, and a reload mid-download dropped the progress bar with
 *  no way back to it. It now renders at the top of the lane whenever there is something to
 *  download, and a reload re-attaches to a running download (`useBundledModelDownload`, the
 *  same machine the chat screen's notice runs).
 *
 *  What it says is the whole offer, because agreeing to a download you were not told about is
 *  not agreeing: the model's name, its licence, the size and that it is one download and then
 *  offline, and what it is — a small floor for getting started, with no tools.
 *
 *  It renders NOTHING unless `GET /api/onboarding` reports a downloadable chat model, so a home
 *  that already has the model on disk sees the lane exactly as it was. The server reports one
 *  whenever it is not on disk, whatever else is configured: a provider that reads as set up can
 *  still be one that does not answer, and the small model is always a valid choice. When the
 *  download finishes it says so and hands the offer to `onReady`, once — by then the shared
 *  download machine has made it the chat model if nothing else was, and the lane checks it,
 *  rather than waiting for a reload to notice.
 */
export function BundledModelOffer({ onReady }: {
  /** The download finished in THIS session and was offered the chat binding (`bindError` is the
   *  refusal, or `''`): the lane takes it from here and verifies. */
  onReady: (offer: Offer, bindError: string) => void
}) {
  const { probeError, offer, job, phase, error, bindError, starting, start, cancel, refresh } = useBundledModelDownload()

  // Once per finished download. The handler is the parent's inline arrow, so it rides a ref:
  // listing it as a dependency would re-fire on every repaint.
  const readyRef = useRef(onReady)
  readyRef.current = onReady
  const announced = useRef('')
  useEffect(() => {
    if (phase !== 'done' || !offer || !job || announced.current === job.id) return
    announced.current = job.id
    readyRef.current(offer, bindError)
  }, [phase, offer, job, bindError])

  if (probeError) {
    return (
      <p
        data-testid="onboarding-model-offer-error"
        data-type="caption"
        className="inline-flex items-start gap-1.5 text-on-surface-var"
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
  const failed = phase === 'failed' ? job?.error ?? '' : ''

  return (
    <div
      data-testid="onboarding-model-offer"
      role="group"
      aria-label={`Download ${offer.label}`}
      className="rounded-lg bg-surface-container px-m py-m"
      style={{ border: '1px solid var(--color-outline-variant)' }}
    >
      <div className="flex items-start gap-2.5">
        <HardDriveDownload size={16} className="mt-0.5 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0 flex-1">
          {phase === 'running' && job ? (
            <BundledDownloadProgress offer={offer} job={job} onCancel={cancel} />
          ) : phase === 'done' ? (
            // The download is on disk; the lane is binding and checking it. Said, not implied —
            // a card that simply vanished at 100% read as the download having gone wrong.
            <p role="status" data-type="body-s" className="inline-flex items-center gap-1.5 text-on-surface"
              style={fvs(600)}>
              <Check size={15} aria-hidden style={{ color: 'var(--color-success)' }} />
              Downloaded {offer.label} — setting it up as your chat model…
            </p>
          ) : (
            <>
              <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
                No account? Start with a small offline model
              </p>
              <p data-type="caption" className="mt-0.5 text-on-surface-var">
                <span className="text-on-surface">{offer.label}</span> · {offer.licence} · {mib(offer.bytes)},
                downloaded once — then it runs offline on this machine, with no key.
              </p>
              <p data-type="caption" className="mt-0.5 text-on-surface-var">
                A small floor for getting started: expect short answers and no tools. You can add a
                real provider any time.
              </p>
              <div className="mt-s">
                <Button size="sm" variant="primary" loading={starting} loadingLabel="Starting the download"
                  onClick={start}>
                  {/* On a phone the model's name — stated just above — is spoken but not drawn: at
                      full length the button ran past the edge of its card. The spaces sit outside
                      the hidden span, where the accessible name keeps them. */}
                  <span>Download <span className="sr-only sm:not-sr-only">{offer.label}</span> ({mib(offer.bytes)})</span>
                </Button>
              </div>
            </>
          )}
          {(error || failed) && (
            <p
              role="alert"
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
