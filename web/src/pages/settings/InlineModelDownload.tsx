import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Download } from 'lucide-react'
import { isLiveDownload, type AvailableModel, type BundledModelOffer } from '../../lib/api'
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

/** 🔑 A CHOSEN MODEL THAT IS NOT ON THIS MACHINE IS DOWNLOADED WHERE IT WAS CHOSEN. The owner:
 *  "if user selects a model from settings/models page it shows 'not downloaded' if the model isn't
 *  yet downloaded. But it should just give the user download option right there". It used to be a
 *  chip reading "not downloaded" whose tooltip sent the user to another page to find the download.
 *
 *  The row it sits under is already BOUND — choosing a model writes the binding, which is what
 *  makes the choice survive a reload — so a finished download is the binding taking effect, with
 *  nothing to choose again. `onDownloaded` is the page re-reading that.
 *
 *  It is the one download machine, not a second: `useModelDownloads` owns the job, its progress
 *  stream, cancel and re-attaching to a running download after a reload, and the progress is drawn
 *  by the same `BundledDownloadProgress` row the onboarding offer and the chat notice draw. Nothing
 *  downloads on its own: the size and licence are stated, and the click is the consent. */
export function InlineModelDownload({ model, onDownloaded }: {
  model: AvailableModel
  /** The download finished — started here, or re-attached to after a reload. Once per job. */
  onDownloaded: () => void
}) {
  const { jobs, start, cancel } = useModelDownloads(model.provider, () => {})
  const job = jobs[model.id]
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)

  const label = modelLabel(model)
  const bytes = Math.round((model.size_mb ?? 0) * 1024 * 1024)
  // The shape the shared progress row draws. `bytes` is the catalog's size — the one the Download
  // button quoted — and the row prefers the job's own total once the runner reports one.
  const shown: BundledModelOffer = {
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

  const refusal = (e: unknown, fallback: string) => {
    let msg = e instanceof Error ? e.message : String(e ?? '')
    try { msg = JSON.parse(msg).error || msg } catch { /* raw text */ }
    return msg || fallback
  }
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

  const needsToken = model.gated === true && model.token_ready === false
  const failed = error || (job?.state === 'error' ? job.error || 'The download failed.' : '')
  const facts = [bytes ? mib(bytes) : '', model.license ?? ''].filter(Boolean).join(' · ')

  return (
    <div data-testid="inline-model-download" className="flex flex-col gap-xs px-m pb-s">
      {job && isLiveDownload(job) ? (
        <BundledDownloadProgress offer={shown} job={job} onCancel={stop} />
      ) : (
        <div className="flex flex-wrap items-center gap-s">
          <span data-type="caption" className="text-on-surface-var">
            {label} is not on this machine yet{facts ? ` — ${facts}` : ''}.
          </span>
          <Button variant="tonal" size="xs" loading={starting} loadingLabel="Starting the download"
            disabled={needsToken}
            disabledReason="This gated model needs a HuggingFace token — add one under “HuggingFace token” below"
            onClick={begin}>
            <Download size={12} aria-hidden="true" /> {failed ? 'Try again' : `Download${bytes ? ` ${mib(bytes)}` : ''}`}
          </Button>
        </div>
      )}
      {failed && (
        <p role="alert" data-type="caption" className="inline-flex items-start gap-xs" style={{ color: 'var(--color-danger)' }}>
          <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" /> <span>{failed}</span>
        </p>
      )}
    </div>
  )
}
