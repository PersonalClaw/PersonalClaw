import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A failure toast is not an info toast ─────────────────────────────────────────────────────────
//
// `notify(message, level = 'info')` defaults to a neutral toast: no error styling, no assertive
// announcement. The 2026-09-05 audit found the whole workflows family reporting failures through
// that default — a failed start/delete/steer was visually indistinguishable from "Refiner
// started", while every sibling surface (notifications, the settings panels) passes 'error'.
// This scan holds the family: any `notify(` in pages/workflows whose message reads as a failure
// ("Could not …", "… failed", "… rejected") must carry the 'error' level. The failure WORDING is
// the discriminator — informational guards ("That gate has no pending question.") explain a
// no-op, not a failed action, and correctly stay neutral.
const DIR = join(process.cwd(), 'src/pages/workflows')
const FAILURE_WORDS = /Could not |Couldn't | failed| rejected/

describe('workflow failure toasts carry the error level', () => {
  it('every failure-worded notify passes error', () => {
    const offenders: string[] = []
    let failureCalls = 0
    for (const name of readdirSync(DIR)) {
      if (!/\.tsx?$/.test(name) || /\.test\./.test(name)) continue
      const src = readFileSync(join(DIR, name), 'utf8')
      for (const m of src.matchAll(/notify\(/g)) {
        // Paren-match the call's argument list, so a comment or a neighbouring call
        // cannot pad or truncate what we judge (the lesson loadErrorState records).
        let i = m.index! + m[0].length
        let depth = 1
        while (i < src.length && depth > 0) {
          if (src[i] === '(') depth++
          else if (src[i] === ')') depth--
          i++
        }
        const args = src.slice(m.index! + m[0].length, i - 1)
        if (!FAILURE_WORDS.test(args)) continue
        failureCalls++
        const line = src.slice(0, m.index).split('\n').length
        if (!/'error'/.test(args)) offenders.push(`${name}:${line} — notify(${args.slice(0, 60)}…)`)
      }
    }
    expect(offenders, `a failure that looks like an info toast gets missed:\n${offenders.join('\n')}`).toEqual([])
    // Vacuity floor: the family had FOURTEEN failure-worded calls when this rail landed. If the
    // scan stops seeing them (notify renamed, messages reworded wholesale), red beats silent-green.
    expect(failureCalls, 'the scan must actually find the failure calls').toBeGreaterThanOrEqual(14)
  })
})
