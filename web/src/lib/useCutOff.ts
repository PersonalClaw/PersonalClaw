import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, type RunningList, type RunningRow } from './api'

// ── What a restart cut off, and whether the socket is back ───────────────────────────────────────
//
// The running list (`GET /api/running`) keeps the gateway's last endings in memory, so a restart
// takes them with it: work that was running when the gateway went is, once it is back, neither live
// nor ended. Every reader of the list says what such work was, the same way, from this hook:
//
//   · while the socket is down, that its rows may be out of date (`reconnecting`);
//   · back on a gateway whose start time moved (`GET /api/status`), every row that was live and is
//     now neither live nor ended, as cut off (`interrupted`). The rows compared are the last read
//     while the socket was up, so a read that lands while it is down cannot hide what the restart
//     cut off. Such a row goes once its work is live or ended again, or after as long as the
//     gateway keeps an ending, and at most as many are kept.
//
// The rows compared are only as current as the page was awake and reading: a phone in a pocket
// runs no script and hears no frame, so rows it read at night are hours old by the morning, and
// work that ended before a restart it slept through would read as cut off. So the hook keeps when
// its rows were last known current (a read while the socket was up, a tick of its own clock while
// the socket was up and its reads answered, a drop the page was awake and reading for), and tells a
// cut-off only when those rows can bound the restart (`rowsBoundTheStart`). With no start time read from
// before the drop, whether it restarted cannot be told, so nothing is said either. Wire
// `onReconnect` and `onStatus` to the reader's socket (`useChatSocket`).

/** Restart-cut rows kept at most, and for how long: as the gateway keeps its own endings
 *  (`running_now.TAIL_ROWS`, `TAIL_SECS`). */
export const INTERRUPTED_KEPT = 20
export const INTERRUPTED_MS = 10 * 60 * 1000

/** How often the hook's clock ticks, how long a gap between two ticks says the page was not running
 *  (a background tab's timers are slowed to one a minute, a suspended page's stop), and how long
 *  after its rows were last known current a gateway the page could not watch go may have started. */
const TICK_MS = 15_000
export const ASLEEP_MS = 3 * 60 * 1000
export const START_MS = 2 * 60 * 1000

/** Work a restart cut off, and when the page saw it cut off (epoch ms). */
export interface CutRow { row: RunningRow; at: number }

/** One start of the gateway: its own stamp, which tells one start from another, and when it
 *  started on this page's clock (now, less the uptime it says), which a gateway host whose clock
 *  runs ahead or behind cannot move. */
interface Start { stamp: number; at: number }

/** The gateway's start (`GET /api/status`), or `null` when it cannot be read. It feeds no list, so
 *  a failure here says nothing rather than failing the reader. */
async function startOf(): Promise<Start | null> {
  try {
    const s = await api.status()
    if (typeof s.start_time !== 'number') return null
    const at = typeof s.uptime_secs === 'number' ? Date.now() - s.uptime_secs * 1000 : s.start_time * 1000
    return { stamp: s.start_time, at }
  } catch {
    return null
  }
}

/** A page the gateway serves on this machine loses its socket only when the gateway goes (or the
 *  page sleeps): there is no network between them to drop. A forwarded port reads as this machine
 *  too, so a tunnel that drops while its gateway goes on reads as the gateway going. */
function servedHere(): boolean {
  return ['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname)
}

/** Whether rows last known current at `knownUntil` can say what a start at `startedAt` (both epoch
 *  ms, the page's clock) cut off. Current at the drop (`atDrop`: the page awake for it and its reads
 *  answering) on the gateway's own machine, they were current when the gateway went, however long
 *  it took to come back. Otherwise the page slept, its reads failed, or a network stood between
 *  them, and the drop may not have been the gateway going: work could have ended in the gap, so
 *  only a start within a start's length of them is told. */
export function rowsBoundTheStart(o: { startedAt: number; knownUntil: number; atDrop: boolean; local: boolean }): boolean {
  if (o.local && o.atDrop) return true
  return o.startedAt - o.knownUntil <= START_MS
}

/** Whether the page slept since `slept` was last cleared: a gap since its clock's last tick says
 *  it did, and is kept until its next good read. */
function sleptSince(now: number, ticked: { current: number }, slept: { current: boolean }): boolean {
  if (now - ticked.current > ASLEEP_MS) slept.current = true
  return slept.current
}

/** Whether the rows were current at `now`: the page awake (its clock ticked) and its reads
 *  answering. A failure that began within a tick of `now` is the gateway going, not the reads. */
function currentAt(now: number, ticked: { current: number }, slept: { current: boolean }, failedSince: { current: number | null }): boolean {
  if (sleptSince(now, ticked, slept)) return false
  return failedSince.current === null || now - failedSince.current <= TICK_MS
}

/** Live before the socket dropped, and neither live nor ended once it was back. */
export function cutOff(before: RunningRow[], after: RunningList): RunningRow[] {
  const known = new Set([...after.running, ...after.finished].map((r) => r.work))
  return before.filter((r) => !known.has(r.work))
}

/** `failed`: the reader's last read of the list failed, so the rows it holds are not known current
 *  however long the socket stays up. */
export function useCutOff(list: RunningList | undefined, refresh: () => void, failed = false): {
  interrupted: CutRow[]
  reconnecting: boolean
  onReconnect: () => void
  onStatus: (up: boolean) => void
} {
  const [reconnecting, setReconnecting] = useState(false)
  const down = useRef(false)
  const [interrupted, setInterrupted] = useState<CutRow[]>([])
  const boot = useRef<number | null>(null)
  const live = useRef<RunningRow[]>([])
  // When `live` was last known current (epoch ms), the last tick of the hook's clock, whether a gap
  // in its ticks said the page slept since `live` was read, since when its reads have failed, and
  // the drop since the last reconnect, with whether the rows were current as it came.
  const current = useRef(Date.now())
  const ticked = useRef(Date.now())
  const slept = useRef(false)
  const failedSince = useRef<number | null>(failed ? Date.now() : null)
  const drop = useRef<{ atDrop: boolean } | null>(null)
  useLayoutEffect(() => {
    if (!failed) failedSince.current = null
    else if (failedSince.current === null) failedSince.current = Date.now()
  }, [failed])
  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = Date.now()
      if (!sleptSince(now, ticked, slept) && !down.current && failedSince.current === null) current.current = now
      ticked.current = now
    }, TICK_MS)
    return () => window.clearInterval(timer)
  }, [])
  useEffect(() => {
    if (!list) return
    if (!down.current) {
      live.current = list.running
      current.current = Date.now()
      slept.current = false
    }
    const known = new Set([...list.running, ...list.finished].map((r) => r.work))
    setInterrupted((was) => {
      const kept = was.filter((i) => !known.has(i.row.work))
      return kept.length === was.length ? was : kept
    })
  }, [list])
  useEffect(() => {
    if (interrupted.length === 0) return undefined
    const timer = window.setInterval(() => {
      const now = Date.now()
      setInterrupted((was) => {
        const kept = was.filter((i) => now - i.at < INTERRUPTED_MS)
        return kept.length === was.length ? was : kept
      })
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [interrupted.length])
  useEffect(() => {
    void startOf().then((s) => { if (boot.current === null) boot.current = s?.stamp ?? null })
  }, [])
  const onReconnect = useCallback(() => {
    const before = live.current
    const knownUntil = current.current
    // The drop it heard, or, with none heard, this return as the drop.
    const atDrop = drop.current ? drop.current.atDrop : currentAt(Date.now(), ticked, slept, failedSince)
    drop.current = null
    refresh()
    void (async () => {
      const started = await startOf()
      if (started === null) return
      const was = boot.current
      boot.current = started.stamp
      if (was === null || started.stamp === was) return
      if (!rowsBoundTheStart({ startedAt: started.at, knownUntil, atDrop, local: servedHere() })) return
      let after: RunningList
      try {
        after = await api.running()
      } catch {
        return  // the reader's own read says the list cannot be read
      }
      const now = Date.now()
      // A row said cut off already is not said again: a restart that came while the rows were not
      // read since the last one (a read that failed, or one that landed while the socket was down)
      // finds the same work in `before`.
      setInterrupted((kept) => {
        const said = new Set(kept.map((i) => i.row.work))
        const cut = cutOff(before, after).filter((r) => !said.has(r.work)).map((row) => ({ row, at: now }))
        return cut.length === 0 ? kept : [...cut, ...kept].slice(0, INTERRUPTED_KEPT)
      })
    })()
  }, [refresh])
  const onStatus = useCallback((up: boolean) => {
    // A drop the page was awake and reading for leaves its rows current to the drop. One it hears
    // on waking does not (its clock's last tick is from before it slept), nor one after its reads
    // stopped answering.
    if (!up && !down.current) {
      const atDrop = currentAt(Date.now(), ticked, slept, failedSince)
      if (atDrop) current.current = Date.now()
      drop.current = { atDrop }
    }
    down.current = !up
    setReconnecting(!up)
  }, [])
  return { interrupted, reconnecting, onReconnect, onStatus }
}
