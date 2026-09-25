import { useState } from 'react'
import { AlertTriangle, HardDriveDownload } from 'lucide-react'
import { Button } from '../../ui/Button'
import { TextLink } from '../../ui/TextLink'
import { fvs } from '../../design/fontWeight'
import { MODELS_ROUTE } from './NoModelSetupState'
import { BundledDownloadProgress, mib, useBundledModelDownload } from './bundledModelDownload'

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
 *  2. **The small model is what answers.** Say plainly that the answers are coming from a tiny
 *     local model and how to replace it — because a newcomer whose first reply comes from a
 *     135M model with no explanation concludes *PersonalClaw* is bad at chat, and never learns
 *     that binding a real model is one click away.
 *
 *  **Why a click and not an automatic fetch.** The download is real outbound network use, and
 *  this project does not do that behind the operator's back — the credential-free seed helper
 *  refuses to touch the network unasked for the same reason, and a headless server would
 *  otherwise pull 138 MiB for a user who is never going to chat. One button, labelled with the
 *  cost, is both honest and cheap.
 *
 *  The download's state is `useBundledModelDownload`'s — the same machine onboarding's model
 *  step reads — so a reload re-attaches to a running download here and there alike, and a
 *  download finished here makes the model the chat model when nothing else is, exactly as one
 *  finished in onboarding does (it used to leave chat on the implicit fallback, bound to
 *  nothing and named nowhere). Both
 *  states key off `GET /api/onboarding`: the floor label off `chat_is_bundled_floor`, which the
 *  backend derives from the resolver's readiness authority, and the offer off
 *  `chat_download_offer` AND `needs_model` — the server offers the download whenever the model
 *  is not on disk, and this screen only shows it while nothing answers chat.
 */
export function BundledFloorNotice() {
  const { state, probeError, offer, job, phase, error, bindError, starting, start, cancel, refresh } = useBundledModelDownload()
  const [declined, setDeclined] = useState(false)
  // A download that finished here is offered the chat binding (the hook's `bindIfNothingIs`); a
  // refusal is said beside the model it concerns rather than swallowed.
  const refused = bindError && (
    <p data-type="caption" role="alert" className="mt-0.5 text-on-surface-var">
      It could not be set as your chat model ({bindError}). It still answers while nothing else is
      chosen; you can choose it in Settings → Models.
    </p>
  )

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
  //
  // Offered here only while NOTHING answers chat (`needs_model`), or while a download is under
  // way, whose progress is news wherever it was started. The server offers the download
  // whenever the model is not on disk, because onboarding's model step always lets a user pick
  // it; a chat screen that echoed that would advertise a 135M model beside one that answers.
  if (offer && phase !== 'done' && !declined && (state.needs_model || phase === 'running')) {
    const failed = phase === 'failed' ? job?.error ?? '' : ''
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
            {phase === 'running' && job ? (
              <>
                <BundledDownloadProgress offer={offer} job={job} onCancel={cancel} />
                <p data-type="caption" className="mt-xs text-on-surface-var">
                  You can cancel and set up any provider instead — nothing here is required.
                </p>
              </>
            ) : (
              <>
                <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
                  Chat right away with a small model — a one-time {mib(offer.bytes)} download
                </p>
                <p data-type="caption" className="mt-0.5 text-on-surface-var">
                  {offer.label} ({offer.licence}). No account and no API key: it runs on this
                  machine, and after this download it needs no network at all. It is tiny, so expect
                  short, shaky answers and no tool use — it is there so a fresh install is not a dead
                  end.{' '}
                  <a href={MODELS_ROUTE} className="text-primary underline">
                    Or connect a provider you already have
                  </a>
                  .
                </p>
                <div className="mt-s flex items-center gap-s">
                  <Button size="sm" loading={starting} loadingLabel="Starting the download" onClick={start}>
                    {`Download ${mib(offer.bytes)}`}
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

  // The download just finished and the readiness read that follows it has not landed yet: say
  // it finished rather than leave a blank where the progress bar was.
  if (phase === 'done' && offer && !state.chat_is_bundled_floor) {
    return (
      <div role="status" data-testid="bundled-model-downloaded"
        className="mb-s rounded-lg bg-surface-container px-m py-2.5"
        style={{ border: '1px solid var(--color-outline-variant)' }}>
        <p data-type="body-s" className="text-on-surface" style={fvs(600)}>
          Downloaded {offer.label} — getting it ready to answer.
        </p>
        {refused}
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
          {refused}
        </div>
      </div>
    </div>
  )
}
