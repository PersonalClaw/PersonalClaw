import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Download } from 'lucide-react'
import { isLiveDownload, type AvailableModel, type BundledModelOffer, type DownloadJob } from '../../lib/api'
import { Button } from '../../ui/Button'
import { BundledDownloadProgress, mib } from '../chat/bundledModelDownload'
import { useModelDownloads } from './useModelDownloads'

/** What a person calls the model: its `display_name` when the id is a file or binding id
 *  (`SmolLM2-135M-Instruct` for `SmolLM2-135M-Instruct-Q8_0`), else the id itself. */
export const modelLabel = (m: Pick<AvailableModel, 'name' | 'display_name'>) => m.display_name || m.name

/** Whether a Models-page row is a model this machine could DOWNLOAD: a local model (it carries a
 *  `downloaded` flag), not on disk, from a provider the local-model registry lists (`local` on its
 *  `/api/models/available` card). That registry is the provider interface a download goes through
 *  — `POST /api/models/downloads` calls the provider's own `download_model` (a HuggingFace fetch,
 *  an Ollama pull) — so nothing is offered that the provider cannot actually fetch. */
export function isDownloadable(m: AvailableModel, localProviders: ReadonlySet<string>): boolean {
  return m.downloaded === false && localProviders.has(m.provider)
}

/** One Models-page row's download of its model: a chosen model's Download (`InlineModelDownload`)
 *  and a truncated model's Repair (`ModelsPanel`'s row) both run through it, so the two draw the
 *  same progress row and say the same things.
 *
 *  It is the one download machine, not a second: `useModelDownloads` owns the job, its progress
 *  stream and cancel. This adds what a row needs on top: the refusal in the server's words, and the
 *  download reported ONCE when it lands.
 *
 *  🔑 `listed` IS HOW A ROW RE-ATTACHES AFTER A RELOAD. The page reads the download list once
 *  (`ModelsPanel`) and hands each row the latest job for its model, so a download or a Repair still
 *  running shows its progress again, and one that ended shows how. A list read per row would be a
 *  request per model on every open, so the row reads none of its own (`reattach: false`). */
export function useRowDownload(model: AvailableModel, onDownloaded: () => void, listed?: DownloadJob) {
  const { jobs, start, cancel } = useModelDownloads(model.provider, () => {}, { reattach: false, adopt: listed })
  const job = jobs[model.id]
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  const label = modelLabel(model)
  const bytes = Math.round((model.size_mb ?? 0) * 1024 * 1024)
  // The shape the shared progress row draws. `bytes` is the catalog's size — the one the Download
  // button quoted — and the row prefers the job's own total once the runner reports one.
  const offer: BundledModelOffer = {
    provider: model.provider, model: model.id, label, bytes,
    licence: model.license ?? '', description: model.description ?? '',
  }

  // A download this row asked for or watched run, reported ONCE when it lands. A model already on
  // disk answers the start with an immediately-`done` job that is never seen running, and it is
  // still the download this user just asked for — hence `asked` beside `watched`.
  const reportRef = useRef(onDownloaded)
  reportRef.current = onDownloaded
  const asked = useRef(false)
  const watched = useRef('')
  useEffect(() => {
    if (!job) return
    if (isLiveDownload(job)) { watched.current = job.id; return }
    if (job.state === 'done' && (asked.current || watched.current === job.id)) {
      asked.current = false; watched.current = ''
      reportRef.current()
    }
  }, [job])

  // The route's sentence (`ApiError.message`), never a JSON body to unpack.
  const refusal = (e: unknown, fallback: string) => (e instanceof Error ? e.message : String(e ?? '')) || fallback
  const begin = async () => {
    setStarting(true); setError('')
    asked.current = true
    try { await start(model.id) } catch (e) { asked.current = false; setError(refusal(e, 'The download could not start.')) }
    finally { setStarting(false) }
  }
  const stop = async () => {
    setError('')
    try { await cancel(model.id) } catch (e) { setError(`Couldn't cancel this download: ${refusal(e, 'the request failed')}`) }
  }

  /** The job while it is live, for the progress row. */
  const running: DownloadJob | null = job && isLiveDownload(job) ? job : null
  const failed = error || (job?.state === 'error' ? job.error || 'The download failed.' : '')
  return { offer, bytes, running, starting, failed, begin, stop }
}

/** Why a row's download did not start or finish, under it: the server's sentence. */
export function DownloadFailure({ text }: { text: string }) {
  return (
    <p role="alert" data-type="caption" className="inline-flex items-start gap-xs" style={{ color: 'var(--color-danger)' }}>
      <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" /> <span>{text}</span>
    </p>
  )
}

/** 🔑 A CHOSEN MODEL THAT IS NOT ON THIS MACHINE IS DOWNLOADED WHERE IT WAS CHOSEN. The owner:
 *  "if user selects a model from settings/models page it shows 'not downloaded' if the model isn't
 *  yet downloaded. But it should just give the user download option right there". It used to be a
 *  chip reading "not downloaded" whose tooltip sent the user to another page to find the download.
 *
 *  The row it sits under is already BOUND — choosing a model writes the binding, which is what
 *  makes the choice survive a reload — so a finished download is the binding taking effect, with
 *  nothing to choose again. `onDownloaded` is the page re-reading that.
 *
 *  The job, its progress stream, cancel and re-attaching to a running download after a reload are
 *  `useRowDownload`'s, and the progress is drawn by the same `BundledDownloadProgress` row the
 *  onboarding offer and the chat notice draw. Nothing downloads on its own: the size and licence
 *  are stated, and the click is the consent. */
export function InlineModelDownload({ model, listed, onDownloaded }: {
  model: AvailableModel
  /** The latest job the page's download list holds for this model (see `useRowDownload`). */
  listed?: DownloadJob
  /** The download finished — started here, or re-attached to after a reload. Once per job. */
  onDownloaded: () => void
}) {
  const download = useRowDownload(model, onDownloaded, listed)
  const { offer, bytes, running, failed } = download

  const needsToken = model.gated === true && model.token_ready === false
  const facts = [bytes ? mib(bytes) : '', model.license ?? ''].filter(Boolean).join(' · ')

  return (
    <div data-testid="inline-model-download" className="flex flex-col gap-xs px-m pb-s">
      {running ? (
        <BundledDownloadProgress offer={offer} job={running} onCancel={download.stop} />
      ) : (
        <div className="flex flex-wrap items-center gap-s">
          <span data-type="caption" className="text-on-surface-var">
            {offer.label} is not on this machine yet{facts ? ` — ${facts}` : ''}.
          </span>
          <Button variant="tonal" size="xs" loading={download.starting} loadingLabel="Starting the download"
            disabled={needsToken}
            disabledReason="This gated model needs a HuggingFace token — add one under “HuggingFace token” below"
            onClick={download.begin}>
            <Download size={12} aria-hidden="true" /> {failed ? 'Try again' : `Download${bytes ? ` ${mib(bytes)}` : ''}`}
          </Button>
        </div>
      )}
      {failed && <DownloadFailure text={failed} />}
    </div>
  )
}
