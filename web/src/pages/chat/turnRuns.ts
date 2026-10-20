import { fmtElapsed } from '../../lib/duration'
import type { ChatTurn } from './chatTypes'

// ── How long a turn worked ───────────────────────────────────────────────────────────────────────
//
// The folded work of a finished turn says how long the turn took ("Worked for 12s · 6 steps"). An
// answer can be shared by more than one of the gateway's turns (a helper's report, a loop's nudge
// and an automation each start one whose answer joins the one before it), so an answer keeps a run
// per turn and says their sum, never the wait between them.
//
// Each run's time comes from one of three places:
//  * the gateway's own start and end, on a page that heard the turn end: when its run state started
//    the turn (`run_state.turn.started_at`) and when its last `work_phase` frame said it ended
//    (`since`);
//  * else, as after a reload, what the gateway measured for the turn and stored with its last
//    answer row (`turn_telemetry.duration_ms`), so a reload says what the live page said, and a
//    regenerated answer or a failed turn tried again is timed exactly;
//  * else, for a turn stored without one (an older row, a turn that reported nothing), its rows:
//    from the first row it wrote to its last. Its first row, not the row that started it: a
//    regenerated answer, or a failed turn tried again, keeps the question's row and its old time,
//    and from there it would count the hours between.
//
// A run is named by the row that started its turn, the name the gateway gives the running turn
// (`run_state.turn.message_id`), so the gateway's times replace the stored ones for the same run.

/** One of the gateway's turns an answer shows: its name, when it started and ended (instants, absent
 *  where its rows carry no time), and how long the gateway measured it (`ms`, what it stored with the
 *  turn's answer), which, where it is known, is the run's time. */
export interface TurnRun { id: string; start?: string; end?: string; ms?: number }

/** How long an answer's runs took together, in milliseconds; `undefined` when none was timed. */
export function workedMs(runs: readonly TurnRun[] | undefined): number | undefined {
  if (!runs?.length) return undefined
  return runs.reduce((ms, r) => ms + (r.ms ?? spanMs(r)), 0)
}

const spanMs = (r: TurnRun) => (r.start && r.end ? Math.max(0, Date.parse(r.end) - Date.parse(r.start) || 0) : 0)

/** The time the gateway measured for a turn, from the record it stored with the turn's last answer
 *  row (`turn_telemetry.duration_ms`), or `undefined` where it stored none: an older row, a turn
 *  that reported nothing, or a figure that is no measurement (not a number, or none above zero). */
export function storedMs(telemetry: { duration_ms?: unknown } | null | undefined): number | undefined {
  const ms = telemetry?.duration_ms
  return typeof ms === 'number' && Number.isFinite(ms) && ms > 0 ? ms : undefined
}

/** The folded line's time, "12s", "1m 19s", "1h 3m" (`fmtElapsed`, in whole seconds), or `''` for
 *  an answer that was not timed or took under half a second. */
export function workedFor(ms: number | undefined): string {
  return ms ? fmtElapsed(Math.round(ms / 1000)) : ''
}

const timed = (ts: string | undefined): ts is string => !!ts && !Number.isNaN(Date.parse(ts))

/** The runs `hydrateTurns` reads off the rows, as it reads them: `started` for each row that starts
 *  one of the gateway's turns, `row` for each row the turn wrote, with the answer it went into and,
 *  on the turn's last answer row, the time the gateway stored for it (`storedMs`). A run is on the
 *  answer its last row went into, where the gateway's last frame puts it (`withRunEnded`): a turn
 *  she steered has its answer in two, and the run is the later one's. */
export function rowRuns() {
  let name: string | undefined
  let run: TurnRun | undefined
  let on: ChatTurn | undefined
  return {
    /** A row that starts a turn: the rows after it are its run's, named by its time. */
    started(ts: string | undefined) { name = timed(ts) ? ts : undefined; run = undefined; on = undefined },
    /** A row the running turn wrote, which went into the answer `turn`; `ms` is the time the
     *  gateway stored for the turn, on the row it stored it with. */
    row(turn: ChatTurn, ts: string | undefined, ms?: number) {
      const at = timed(ts) ? ts : undefined
      if (!at && ms === undefined) return
      if (!run) run = at ? { id: name ?? at, start: at, end: at } : { id: name ?? '' }
      else if (at && (!run.end || Date.parse(at) > Date.parse(run.end))) { run.start ??= at; run.end = at }
      if (ms !== undefined) run.ms = ms
      if (on === turn) return
      if (on) {
        const rest = (on.runs ?? []).filter((r) => r !== run)
        if (rest.length) on.runs = rest
        else delete on.runs
      }
      turn.runs = [...(turn.runs ?? []), run]
      on = turn
    },
  }
}

/** `turns` with the run the gateway said has ended, timed by it, on the answer at the transcript's
 *  end, in place of the same run timed from what was stored. A turn that ended before it wrote a
 *  word has no answer there, and nothing to time. */
export function withRunEnded(turns: ChatTurn[], run: TurnRun): ChatTurn[] {
  const last = turns.length - 1
  if (last < 0 || turns[last].role !== 'assistant') return turns
  return turns.map((t, i) => {
    const rest = t.runs?.filter((r) => r.id !== run.id)
    if (i === last) return { ...t, runs: [...(rest ?? []), run] }
    if (!t.runs || rest!.length === t.runs.length) return t
    return { ...t, runs: rest!.length ? rest : undefined }
  })
}

/** `turns` with the runs a read of the chat timed from what it stored (`read`, as `hydrateTurns`
 *  built it), for the answer at the transcript's end, when the page missed the frame that would
 *  have timed it: a run it has keeps its times. */
export function withRowRuns(turns: ChatTurn[], read: ChatTurn[]): ChatTurn[] {
  const last = turns.length - 1
  const theirs = read[read.length - 1]
  if (last < 0 || turns[last].role !== 'assistant' || theirs?.role !== 'assistant' || !theirs.runs) return turns
  const have = new Set((turns[last].runs ?? []).map((r) => r.id))
  const missed = theirs.runs.filter((r) => !have.has(r.id))
  if (!missed.length) return turns
  return turns.map((t, i) => (i === last ? { ...t, runs: [...(t.runs ?? []), ...missed] } : t))
}
