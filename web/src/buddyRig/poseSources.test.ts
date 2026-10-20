/** The buddy's pose sources and contact sheets (#3830) are what the pose set draws.
 *
 *  From `web/`, `PC_WRITE_BUDDY_POSES=1 npx vitest run src/buddyRig/poseSources.test.ts` writes every
 *  source to `docs/design/buddy/poses/` and every contact sheet to its `sheets/`, and removes the
 *  ones the set no longer draws (gated on the variable, the shape `generate:schemes` has; a plain
 *  test run writes nothing). Every other test here reads what is committed: a source or a sheet
 *  that drifts from the set, a file the set does not draw, or a pose that breaks the phase contract
 *  each reds. The census of the sources against the brief, the parts, the colours and the 18 px
 *  check are `tests/rails/test_buddy_design_matrix.py`'s.
 */
import { describe, expect, it } from 'vitest'
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { SYSTEM_STATE_FAMILIES, WORK_PHASE_LOOKS } from '../lib/workPhase'
import { FAMILIES, FAMILY_STATES, TRAY_STATES, familyOf, poseSet, stillOf, type Frame } from './poseSet'
import { contactSheets, poseSources } from './poseSources'

const WRITE_ENV = 'PC_WRITE_BUDDY_POSES'
const POSES = join(process.cwd(), '..', 'docs', 'design', 'buddy', 'poses')
const SHEETS = join(POSES, 'sheets')

const svgs = (dir: string): string[] => readdirSync(dir).filter((f) => f.endsWith('.svg')).sort()

/** Two drawings are the same when their text is, number for number to within the second decimal
 *  place they are written to: a rounding that lands the other side of a half on another machine is
 *  not a change. */
function sameDrawing(a: string, b: string): boolean {
  const NUM = /-?\d+(?:\.\d+)?/g
  if (a.replace(NUM, '#') !== b.replace(NUM, '#')) return false
  const x = a.match(NUM) ?? []
  const y = b.match(NUM) ?? []
  return x.length === y.length && x.every((n, i) => Math.abs(Number(n) - Number(y[i])) <= 0.011)
}

describe('pose sources: generator', () => {
  it.runIf(process.env[WRITE_ENV] === '1')('writes every source and sheet from the pose set', () => {
    mkdirSync(SHEETS, { recursive: true })
    for (const [dir, files] of [[POSES, poseSources()], [SHEETS, contactSheets()]] as const) {
      for (const stale of svgs(dir).filter((f) => !(f in files))) rmSync(join(dir, stale))
      for (const [name, svg] of Object.entries(files)) writeFileSync(join(dir, name), svg, 'utf8')
    }
    expect(Object.keys(contactSheets())).toHaveLength(5)
  })

  it('every committed source and sheet is what the pose set draws, and there is no other', () => {
    for (const [dir, files] of [[POSES, poseSources()], [SHEETS, contactSheets()]] as const) {
      expect(svgs(dir), `${dir}: run the generator in this file's header`).toEqual(Object.keys(files).sort())
      for (const [name, svg] of Object.entries(files)) {
        expect(sameDrawing(readFileSync(join(dir, name), 'utf8'), svg), `${name} drifted from the pose set: run the generator in this file's header`).toBe(true)
      }
    }
  })

  it('a drawing that moves a dot, or changes a word, is not the same drawing', () => {
    const a = '<circle cx="1.25" cy="2" r="0.5"/>'
    expect(sameDrawing(a, '<circle cx="1.26" cy="2" r="0.5"/>')).toBe(true)
    expect(sameDrawing(a, '<circle cx="1.3" cy="2" r="0.5"/>')).toBe(false)
    expect(sameDrawing(a, '<circle cx="1.25" cy="2" r="0.5" opacity="1"/>')).toBe(false)
  })
})

describe('the pose set keeps to the phase contract', () => {
  const frames = (): Frame[] => poseSet().flatMap((p) => (p.key ? [p.still, p.key] : [p.still]))

  it('every family pose reads in its family, alive where an agent is and at rest where none is', () => {
    for (const family of FAMILIES) {
      for (const state of FAMILY_STATES[family]) {
        expect(familyOf(state), state).toBe(family)
        expect(stillOf(state).ink, state).toBe(family === 'active' || family === 'waiting' ? 'alive' : 'rest')
      }
    }
    expect(Object.keys(SYSTEM_STATE_FAMILIES).every((s) => FAMILY_STATES[SYSTEM_STATE_FAMILIES[s as keyof typeof SYSTEM_STATE_FAMILIES]].includes(s as never))).toBe(true)
  })

  it("queued is drawn alive in the scheme's primary, and listening's microphone keeps the warn tone", () => {
    expect(stillOf('queued')).toMatchObject({ ink: 'alive', tone: 'primary', mark: 'working' })
    expect(stillOf('listening')).toMatchObject({ ink: 'alive', tone: 'warn', mark: 'listening', drawing: 'listening' })
    expect(WORK_PHASE_LOOKS.listening.tone).toBe('warn')
  })

  it('an alert tone is only ever a badge or a wash, and a wash is only an alert', () => {
    for (const frame of frames()) {
      if (frame.wash > 0) expect(['warn', 'info', 'danger']).toContain(frame.tone)
    }
    expect(frames().some((f) => f.wash > 0)).toBe(true)
  })

  it('each tray still is a still of its family, as the brief maps them', () => {
    const families = Object.fromEntries(Object.entries(TRAY_STATES).map(([still, state]) => [still, familyOf(state)]))
    expect(families).toEqual({ idle: 'quiet', working: 'active', 'needs-you': 'waiting', error: 'blocked', listening: 'active', offline: 'blocked' })
  })

  it('a pose that moves has a motion frame that differs from its still, and nothing else has one', () => {
    for (const pose of poseSet()) {
      if (pose.key) expect(pose.key, pose.id).not.toEqual(pose.still)
      expect(Boolean(pose.key), pose.id).toBe(pose.motion !== 'holds still' && pose.motion !== 'holds still, dimmed')
    }
  })

  it('the orb only moves, turns or scales by one factor: it never squashes or stretches', () => {
    for (const frame of frames()) expect(typeof frame.swell).toBe('number')
    expect(Object.values(poseSources()).every((svg) => !/scale\([^)]*[ ,][^)]*\)/.test(svg) && !/matrix\(|skew/.test(svg))).toBe(true)
  })
})
