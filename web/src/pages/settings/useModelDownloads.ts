import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type DownloadJob } from '../../lib/api'

/** Track async local-model download jobs for one provider.
 *
 *  On mount it lists live jobs (so a page reload re-attaches to an in-flight
 *  download) and opens a per-job SSE stream for each running job. `start` kicks
 *  off a download and begins streaming its progress; `cancel` detaches it.
 *  Jobs are keyed by model name so the manager can render progress per row.
 *  Terminal jobs (done/error/cancelled) trigger `onSettled` so the caller can
 *  refresh the model list. */
export function useModelDownloads(provider: string, onSettled: () => void) {
  const [jobs, setJobs] = useState<Record<string, DownloadJob>>({})
  const streams = useRef<Map<string, EventSource>>(new Map())
  const settled = useRef(onSettled)
  settled.current = onSettled

  const closeStream = useCallback((id: string) => {
    streams.current.get(id)?.close()
    streams.current.delete(id)
  }, [])

  const attach = useCallback((job: DownloadJob) => {
    setJobs((prev) => ({ ...prev, [job.model]: job }))
    if (job.status !== 'running' || streams.current.has(job.id)) return
    let es: EventSource
    try { es = new EventSource(api.downloadStreamUrl(job.id)) } catch { return }
    streams.current.set(job.id, es)
    const onFrame = (e: Event) => {
      let data: DownloadJob | null = null
      try { data = JSON.parse((e as MessageEvent).data) as DownloadJob } catch { return }
      if (!data) return
      setJobs((prev) => ({ ...prev, [data!.model]: data! }))
      if (data.status !== 'running') { closeStream(data.id); settled.current() }
    }
    for (const ev of ['snapshot', 'progress', 'done', 'error', 'cancelled']) es.addEventListener(ev, onFrame)
    es.onerror = () => { /* transient — EventSource retries */ }
  }, [closeStream])

  // Re-attach to any in-flight jobs of this provider on mount.
  useEffect(() => {
    let alive = true
    api.modelDownloads().then((all) => {
      if (!alive) return
      all.filter((j) => j.provider === provider).forEach(attach)
    }).catch(() => { /* none */ })
    const map = streams.current
    return () => { alive = false; map.forEach((es) => es.close()); map.clear() }
  }, [provider, attach])

  const start = useCallback(async (model: string) => {
    const job = await api.startModelDownload(provider, model)
    attach(job)
    if (job.status !== 'running') settled.current()  // already-downloaded short-circuit
  }, [provider, attach])

  const cancel = useCallback(async (model: string) => {
    const job = jobs[model]
    if (!job) return
    closeStream(job.id)
    await api.cancelModelDownload(job.id).catch(() => { /* gone */ })
    setJobs((prev) => { const n = { ...prev }; delete n[model]; return n })
    settled.current()
  }, [jobs, closeStream])

  return { jobs, start, cancel }
}
