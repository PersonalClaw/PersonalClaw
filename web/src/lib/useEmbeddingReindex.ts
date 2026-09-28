import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type ReindexJob } from './api'

/** Follow the one embedding re-index (`/api/models/embedding/reindex`).
 *
 *  The gateway runs one at a time, and it starts one on its own: at its start, for whatever the
 *  bound model has not embedded (the first start after an update that re-embeds the library once,
 *  or the rest of one a stop interrupted), and after an Embedding binding. A surface that showed
 *  only a job it started itself showed nothing of those, so a user who opened Settings → Models
 *  or Knowledge mid-way saw no re-index at all. On mount this reads the running job, if any, and
 *  follows its progress. `start` starts the re-index, or joins the running one (the route answers
 *  it rather than starting a second), and follows that; `starting` is true until the route answers.
 *
 *  `onSettled` fires when a followed job ends, so its host can re-read what it re-embedded.
 *  `enabled: false` reads and follows nothing: a generic host (every use case's row in Settings →
 *  Models) shows the re-index on one of its instances only. */
export function useEmbeddingReindex({ enabled = true, onSettled }: { enabled?: boolean; onSettled?: () => void } = {}) {
  const [job, setJob] = useState<ReindexJob | null>(null)
  // Between a start and the route's answer, which probes the model first and so can take seconds.
  const [starting, setStarting] = useState(false)
  const stream = useRef<EventSource | null>(null)
  const settled = useRef(onSettled)
  settled.current = onSettled

  const follow = useCallback((next: ReindexJob) => {
    setJob(next)
    stream.current?.close()
    stream.current = null
    if (next.status !== 'running') return
    let es: EventSource
    try { es = new EventSource(api.embeddingReindexStreamUrl(next.id)) } catch { return }
    stream.current = es
    const onFrame = (e: Event) => {
      let frame: ReindexJob | null = null
      try { frame = JSON.parse((e as MessageEvent).data) as ReindexJob } catch { return }
      if (!frame) return
      setJob(frame)
      if (frame.status !== 'running') {
        es.close()
        stream.current = null
        settled.current?.()
      }
    }
    for (const ev of ['snapshot', 'progress', 'done', 'error']) es.addEventListener(ev, onFrame)
    // A stream failure is NOT a re-index failure: the job keeps running server-side, only the
    // progress feed is gone. Closing silently froze the surface on its last percentage forever, so
    // it could not tell "still working" from "we stopped hearing about it". Recorded on the job it
    // renders, in words that say what is actually known.
    es.onerror = () => {
      es.close()
      stream.current = null
      setJob((r) => (r && r.status === 'running'
        ? { ...r, status: 'error', error: 'Lost the progress feed — the re-index may still be running in the background. Reload to check.' }
        : r))
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    let alive = true
    api.embeddingReindexJobs()
      .then((jobs) => { if (alive && jobs.active) follow(jobs.active) })
      .catch(() => { /* nothing running to follow; `start` still reports its own failure */ })
    return () => {
      alive = false
      stream.current?.close()
      stream.current = null
    }
  }, [enabled, follow])

  // A refused start is captured on the job the host renders: 409 model_not_ready (or any failure)
  // means nothing started and no vector was cleared, and `id: ''` is what tells the host that the
  // job never existed ("Re-index not started: …").
  const refused = useCallback((err: unknown) => {
    let msg = err instanceof Error ? err.message : String(err)
    try { msg = JSON.parse(msg).error || msg } catch { /* not JSON: the message as it is */ }
    setJob({ id: '', model: '', status: 'error', phase: 'error', done: 0, total: 0, knowledge: 0, memory: 0, error: msg })
  }, [])

  const start = useCallback(() => {
    setStarting(true)
    api.startEmbeddingReindex().then(follow).catch(refused).finally(() => setStarting(false))
  }, [follow, refused])

  return { job, start, starting }
}
