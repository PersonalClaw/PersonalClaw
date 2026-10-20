import type { KnowledgeItem, KnowledgeQueueStanding } from '../../lib/api'
import { fmtInterval } from './sourceMeta'

/** How a queued or enriching item's place in the ingest queue reads.
 *
 *  Items are read one at a time, what a person adds herself before background work, and a
 *  bare "Queued…" spinner said nothing about a wait that measured three hours behind a
 *  watched folder. So the page states what the queue knows: how many items are ahead, which
 *  line it is in, and — once enough items have finished to say — how long recent ones took,
 *  with the arithmetic that gives. That is an estimate from the recent pace and says so; the
 *  queue never promises a time. */

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** The short label a list row shows: "Queued · 3 ahead", "Queued · next", "Enriching". */
export function queueLabel(item: Pick<KnowledgeItem, 'processing_status' | 'queue'>): string {
  const standing = item.queue
  if (standing?.state === 'running') return 'Enriching'
  if (standing?.state === 'waiting') {
    return standing.ahead === 0 ? 'Queued · next' : `Queued · ${standing.ahead} ahead`
  }
  return item.processing_status === 'queued' ? 'Queued' : 'Enriching'
}

/** The sentence under an item that is waiting or being read; '' when the queue does not hold
 *  it (the item then says only that it is queued). `nowSecs` is a seam for tests. */
export function queueSentence(standing: KnowledgeQueueStanding | null | undefined, nowSecs = Date.now() / 1000): string {
  if (!standing) return ''
  const typical = standing.typical_secs
  if (standing.state === 'running') {
    const elapsed = Math.max(0, nowSecs - standing.since)
    const started = `Being read now, started ${elapsed < 60 ? 'under a minute' : fmtInterval(elapsed)} ago.`
    if (typical === null) return started
    const left = typical - elapsed
    return left > 0
      ? `${started} Recent items took about ${fmtInterval(typical)}, so roughly ${fmtInterval(left)} to go.`
      : `${started} Recent items took about ${fmtInterval(typical)}; this one is taking longer.`
  }
  const where = standing.ahead === 0
    ? (standing.running_since === null ? 'Next in line.' : 'Next in line, after the item being read now.')
    : `${plural(standing.ahead, 'item')} ahead of it.`
  const lane = standing.lane === 'background'
    ? ' It came in as background work, so anything you add yourself is read first.'
    : ''
  if (typical === null) return `${where}${lane} No timing yet: too few items have finished to say how long each takes.`
  const current = standing.running_since === null ? 0 : Math.max(0, typical - (nowSecs - standing.running_since))
  const done = current + (standing.ahead + 1) * typical
  return `${where}${lane} At the recent pace (about ${fmtInterval(typical)} an item), done in roughly ${fmtInterval(done)}.`
}
