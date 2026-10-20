import { describe, expect, it } from 'vitest'
import {
  BASELINE_FILE,
  baselineLine,
  compareFindings,
  parseBaseline,
  type Finding,
} from '../../scripts/designDetectorBaseline.mjs'

// ── The design detector's ratchet: no new findings, and a fixed one lowers its line ─────────────
// The design detector's deterministic rules run over web/src, and what they find is compared
// exactly with a committed baseline (scripts/designDetectorBaseline.mjs): one JSON line per
// (file, rule), sorted, with the count of that rule's findings in that file. Paths are relative to
// web/, and line numbers are not part of the key, so an edit above a finding leaves its line alone.
//
// Every case here is planted: the findings and the baseline are written out in the test, so the
// comparator is proven to fail on each way the tree can move before it is pointed at the tree.

const at = (file: string, rule: string, line = 1): Finding => ({ file, rule, line })

const BASELINE = [
  baselineLine({ file: 'src/pages/Home.tsx', rule: 'gradient-text', count: 2 }),
  baselineLine({ file: 'src/pages/Home.tsx', rule: 'side-tab', count: 1 }),
  baselineLine({ file: 'src/pages/Tasks.tsx', rule: 'side-tab', count: 1 }),
  '',
].join('\n')

/** The findings the baseline above describes, one per counted finding. */
const MATCHING: Finding[] = [
  at('src/pages/Tasks.tsx', 'side-tab', 40),
  at('src/pages/Home.tsx', 'gradient-text', 12),
  at('src/pages/Home.tsx', 'side-tab', 30),
  at('src/pages/Home.tsx', 'gradient-text', 88),
]

describe('design detector ratchet: the comparator', () => {
  it('passes when the findings are exactly the baseline, in any order and at any line', () => {
    expect(compareFindings(MATCHING, BASELINE)).toEqual([])
    const moved = [...MATCHING].reverse().map((f) => ({ ...f, line: (f.line ?? 0) + 7 }))
    expect(compareFindings(moved, BASELINE)).toEqual([])
  })

  it('fails on a new (file, rule) pair, in a new file or a baselined one', () => {
    expect(compareFindings([...MATCHING, at('src/pages/Chat.tsx', 'side-tab')], BASELINE)).toEqual([
      'src/pages/Chat.tsx: 1 new side-tab finding, where the baseline has none; fix it',
    ])
    expect(compareFindings([...MATCHING, at('src/pages/Tasks.tsx', 'gradient-text'), at('src/pages/Tasks.tsx', 'gradient-text', 9)], BASELINE)).toEqual([
      'src/pages/Tasks.tsx: 2 new gradient-text findings, where the baseline has none; fix them',
    ])
  })

  it('fails on a count that rose', () => {
    expect(compareFindings([...MATCHING, at('src/pages/Home.tsx', 'side-tab', 31)], BASELINE)).toEqual([
      'src/pages/Home.tsx: side-tab rose from 1 to 2; fix the new finding',
    ])
  })

  it('fails on a fixed finding whose line was not lowered, and prints the line to write', () => {
    const oneGradientFixed = MATCHING.filter((f) => f.line !== 88)
    expect(compareFindings(oneGradientFixed, BASELINE)).toEqual([
      `src/pages/Home.tsx: gradient-text fell from 2 to 1: change its line in ${BASELINE_FILE} to ` +
        baselineLine({ file: 'src/pages/Home.tsx', rule: 'gradient-text', count: 1 }),
    ])
    const tasksFixed = MATCHING.filter((f) => f.file !== 'src/pages/Tasks.tsx')
    expect(compareFindings(tasksFixed, BASELINE)).toEqual([
      `src/pages/Tasks.tsx: side-tab is fixed: delete its line from ${BASELINE_FILE}`,
    ])
    // Once the baseline says so, it passes.
    const lowered = BASELINE.split('\n').filter((l) => !l.includes('Tasks.tsx')).join('\n')
    expect(compareFindings(tasksFixed, lowered)).toEqual([])
  })

  it('fails on an empty result against a non-empty baseline, as a run that read nothing', () => {
    expect(compareFindings([], BASELINE)).toEqual([
      `the detector reported no findings, but ${BASELINE_FILE} lists 4 in 2 files: a run that read ` +
        'no files must not pass. If every finding really is fixed, empty the baseline in the same commit',
    ])
    // An empty baseline with nothing found is a clean tree, not a vacuous run.
    expect(compareFindings([], '')).toEqual([])
  })

  it('fails on a finding named by an absolute path, which would never match a baseline line', () => {
    expect(compareFindings([...MATCHING, at('/home/runner/work/web/src/pages/Home.tsx', 'side-tab')], BASELINE)).toEqual([
      'a finding names /home/runner/work/web/src/pages/Home.tsx: findings are named relative to web/',
    ])
  })

  it('fails on a pair listed twice, out of order, with no findings or malformed', () => {
    const [home1, home2, tasks] = BASELINE.trimEnd().split('\n')
    expect(parseBaseline(`${home1}\n${home2}\n${tasks}\n${home2}\n`).problems).toEqual([
      `${BASELINE_FILE} line 4 lists src/pages/Home.tsx side-tab a second time: keep one line per (file, rule)`,
    ])
    expect(parseBaseline(`${home2}\n${home1}\n`).problems).toEqual([
      `${BASELINE_FILE} line 2 is out of order: the lines are sorted by file, then rule`,
    ])
    expect(parseBaseline(baselineLine({ file: 'src/a.tsx', rule: 'side-tab', count: 0 })).problems).toEqual([
      `${BASELINE_FILE} line 1 must hold a file, a rule and a count of at least 1: ` +
        baselineLine({ file: 'src/a.tsx', rule: 'side-tab', count: 0 }),
    ])
    expect(parseBaseline('not json\n{"file":"src/a.tsx","rule":"side-tab"}\n').problems).toHaveLength(2)
    // A broken baseline fails the comparison too, not only its parse.
    expect(compareFindings(MATCHING, `${BASELINE}${home1}\n`)).toEqual([
      `${BASELINE_FILE} line 4 lists src/pages/Home.tsx gradient-text a second time: keep one line per (file, rule)`,
    ])
  })
})
