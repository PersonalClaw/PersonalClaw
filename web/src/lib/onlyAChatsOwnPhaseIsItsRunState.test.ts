// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'
import { isAChatsPhase } from './workPhase'

// ── Only a chat's own phase is its run state ─────────────────────────────────────────────────────
//
// One `work_phase` frame type says what every kind of work is doing: a chat's turn, and a helper, a
// loop and a workflow run (`src/personalclaw/dashboard/work_phases.py`). A helper's frame names the
// chat it works for in `session`, as its other frames do, and a loop's or a run's names none. A page
// that took every `work_phase` frame naming its chat, or naming no chat, as the chat's run state
// would show a helper's step, or a run's ending, as the chat's turn. So every reader that takes a
// frame's payload as a chat's run state asks `isAChatsPhase` first; the others take the frame only as
// a signal to read their lists again.

const SRC = join(process.cwd(), 'src')

/** The readers that take a `work_phase` frame as a signal only, never its payload. Agent World's
 *  feed and Mission Control read the running list, rung by `running_changed`, and no phase frame. */
const SIGNALS = [
  'lib/useThreads.ts',
]

const READS_THE_FRAME = /'work_phase'/

/** The files among `paths` that read `work_phase` frames, and those of them that take a payload
 *  without asking whose phase it is. */
function readersOf(paths: string[], text: (path: string) => string) {
  const readers = paths.filter((path) => READS_THE_FRAME.test(text(path)))
  const unasked = readers.filter((path) => !SIGNALS.includes(path) && !text(path).includes('isAChatsPhase('))
  return { readers, unasked }
}

const nonTestSources = () =>
  filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)).map((p) => p.slice(SRC.length + 1))

describe('only a chat’s own phase is taken as its run state', () => {
  it('a chat’s frame is one, and a helper’s, a loop’s and a run’s are not', () => {
    expect(isAChatsPhase({ kind: 'chat', session: 'c1' })).toBe(true)
    for (const kind of ['subagent', 'loop', 'workflow']) expect(isAChatsPhase({ kind, session: 'c1' })).toBe(false)
    expect(isAChatsPhase(undefined)).toBe(false)
  })

  it('every reader that takes a frame’s payload asks whose phase it is', () => {
    const { readers, unasked } = readersOf(nonTestSources(), (path) => readSource(join(SRC, path)))
    expect(readers.length, 'found too few readers of work_phase frames — parser drift?').toBeGreaterThan(6)
    for (const signal of SIGNALS) expect(readers, `${signal} no longer reads work_phase: drop it from SIGNALS`).toContain(signal)
    expect(unasked).toEqual([])
  })

  it('a reader that takes the payload without asking is found (the positive control)', () => {
    const planted: Record<string, string> = {
      'pages/planted.tsx': "if (m.type === 'work_phase') takeRunState(m.data)",
      'pages/asked.tsx': "if (m.type === 'work_phase' && isAChatsPhase(m.data)) takeRunState(m.data)",
      'lib/useThreads.ts': "return type === 'work_phase'",
    }
    expect(readersOf(Object.keys(planted), (path) => planted[path]).unasked).toEqual(['pages/planted.tsx'])
  })
})
