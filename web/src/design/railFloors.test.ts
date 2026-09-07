import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The audit of our own floors: 106 of them, and four had rotted ─────────────────────────────
//
// Cycle 118 found `expect(users.length).toBeGreaterThan(20)` guarding **28** settings panels — two panels
// could lose their page title with the rail still green. That was filed as "audit the `>N` rail floors",
// and this is that audit, done by MEASUREMENT rather than by reading: a temporary setup file wrapped
// `toBeGreaterThan`/`toBeGreaterThanOrEqual` for one full-suite run and logged every floor with the value
// it actually received (306 assertions, 104 distinct sites).
//
// 🔑 THE RESULT IS A TAXONOMY, NOT A SWEEP. Slack alone is not the signal — three different kinds of floor
// look identical in a diff and want opposite treatment:
//
//   (a) **A MEASURED POPULATION** — "there are N of these, and the rail should notice if they leave."
//       Slack here is rot: the assertion stops detecting the regression it was written for. **Tighten to
//       the measurement.** Four had rotted, all tightened in this change:
//
//         configReadNotFabricated.test.ts  floor 3  actual **31**   ← its own comment claimed to record
//                                                                     the count, and recorded a tenth
//         requiredFieldMarked.test.tsx     floor 5  actual **20**
//         controlNameFloor.test.ts         floor 6  actual **18**   ← 6 was true the day it was written;
//                                                                     12 more inputs shipped unprotected
//         escapeDismissContract.test.tsx   floor 4  actual **10**
//
//   (b) **A VACUITY GUARD ON A GROWING TREE** — `walk(SRC).length > 200`, `files.length > 50`,
//       `raw.length > 4000`. Its job is "the scan found the tree at all". Slack is *deliberate*: tightening
//       it makes CI fail every time someone adds a file, and the rail is not measuring the population
//       anyway. **Left alone — 60-odd of them.** `widgetsReachable.test.ts`'s `modules.length > 5` (actual
//       11) is the clearest example: it exists so an empty directory listing cannot pass vacuously.
//
//   (c) **A QUALITY MARGIN** — `expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)`. The slack IS the
//       product: `schemeContrast` measures up to 15.5 against an AA floor of 4.5, and "tightening" that to
//       15.5 would ratchet a design value into a gate. **Never tighten.**
//
// 🪤 CYCLE 118'S MISS WAS TYPE (a) MISFILED AS TYPE (b) — a population count wearing a vacuity guard's
// clothes. The tell is in the assertion's own message: "the matcher must find the tree" is (b); "the
// primitive must actually be in use" / "the population must still be visible" is (a) and must sit at the
// number.
//
// This rail keeps the four tightened floors tightened, because the easiest way to make a red rail green is
// to lower the number it asserts.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

/** [file, the assertion's tail, the measured population it must sit at] */
const TIGHTENED: [string, RegExp, number][] = [
  // 🔻 31 → 30 (cycle ux-673). Read this before assuming the number was lowered to make a red go away:
  // the taxonomy above calls this floor a type (a) MEASURED POPULATION whose rule is "sit at the
  // measurement". The measurement moved because two of the counted substitutions were DELETED — the
  // `settings:doctor` and `settings:incident` tiles stopped mapping a rejection to `null` — which is the
  // outcome the inner rail exists to encourage, and its own comment says "de-swallowing one of these is a
  // real change, so lower it in that PR". Measured on both sides in that PR: 32 before (the floor had
  // drifted BELOW the real count again), 30 after. The scan still finds 30, so it did not stop guarding;
  // the population is genuinely smaller. A floor moved for any other reason is the rot this rail is for.
  // 🔻 30 → 33, because THIS RAIL WAS RECORDING A NUMBER BELOW THE FLOOR IT POLICES. The subject's floor
  // was 32 while this table said 30, so a 32 → 30 lowering passed the very meta-rail that exists to stop
  // a floor being lowered. The subject's real measured population was **34** — the fourth drift of that
  // one floor (its history logs 3 → 31 → 30 → 32) — and this table sitting two steps further back is how
  // those drifts stayed invisible. This PR de-swallows one, taking the measurement to 33; both agree now.
  //
  // 🪤 UPDATED IN THE SAME COMMIT AS THE SUBJECT, ON PURPOSE. The assertion below is
  // `subject_floor >= recorded`, so the two are coupled: correcting this table on its own branch would
  // RED main until the subject's PR landed (32 >= 33 fails), and correcting the subject alone leaves the
  // hole open. An ordering hazard, not a formatting preference.
  //
  // 🪤 AND THE MATCHER IS NOW SPECIFIC, LIKE EVERY SIBLING BELOW. It was a bare
  // `/toBeGreaterThanOrEqual\((\d+)\)/`, which takes the FIRST match in the subject file — and this rail
  // reads raw text with no comment stripping. So any prose above the real assertion that spelled
  // `toBeGreaterThanOrEqual(` plus digits would silently become the number being policed. The subject's
  // new comment discusses that matcher by name and escaped only because it omits the parenthesised
  // digits: luck, not design. Anchoring on the assertion's own message removes the coincidence.
  //
  // One hazard left, recorded not fixed because it reaches every entry: the table header calls the third
  // element "the measured population it must SIT AT", while the assertion is `toBeGreaterThanOrEqual` —
  // "at least", not "at". For a type (a) measured population the taxonomy above prescribes sitting AT the
  // measurement, so the header claims a stronger property than the code checks. Some entries are
  // genuinely type (b) anti-vacuity floors where `>=` is correct, so the real fix is splitting the two
  // kinds rather than a blanket `toBe`.
  [
    'pages/settings/configReadNotFabricated.test.ts',
    /the decorating fallbacks in these five files, measured'\)\s*\.toBeGreaterThanOrEqual\((\d+)\)/,
    33,
  ],
  ['ui/requiredFieldMarked.test.tsx', /population must still be visible to this rail'\)\.toBeGreaterThanOrEqual\((\d+)\)/, 20],
  ['design/controlNameFloor.test.ts', /expected the inline rename\/edit inputs'\)\.toBeGreaterThanOrEqual\((\d+)\)/, 18],
  ['ui/escapeDismissContract.test.tsx', /scrim-bearing overlays'\)\.toBeGreaterThanOrEqual\((\d+)\)/, 10],
]

describe('a floor that stands for a population sits at the population', () => {
  for (const [rel, re, measured] of TIGHTENED) {
    it(`${rel.split('/').pop()} floors at its measured ${measured}`, () => {
      const m = read(rel).match(re)
      expect(m, `${rel} must still carry the floor this rail is about`).not.toBeNull()
      expect(Number(m![1]), 'lowering this is how a rail stops guarding').toBeGreaterThanOrEqual(measured)
    })
  }

  it('none of the four reverted to a loose `toBeGreaterThan`', () => {
    // `>N` on a population is off-by-one against the count as well as loose; these four are `>=`.
    expect(read('ui/requiredFieldMarked.test.tsx')).not.toMatch(/population must still be visible to this rail'\)\.toBeGreaterThan\(/)
    expect(read('ui/escapeDismissContract.test.tsx')).not.toMatch(/scrim-bearing overlays'\)\.toBeGreaterThan\(/)
  })

  it('the vacuity guards were deliberately LEFT loose', () => {
    // Type (b): if a future pass "finishes the audit" by tightening these, every added file breaks CI.
    expect(read('pages/dashboard/widgets/widgetsReachable.test.ts'), 'a directory listing guard stays loose')
      .toMatch(/expect\(modules\.length\)\.toBeGreaterThan\(5\)/)
    expect(read('ui/loadErrorState.test.tsx'), 'a tree-walk guard stays loose')
      .toMatch(/walk\(SRC\)\.length, 'the walker must find the tree'\)\.toBeGreaterThan\(200\)/)
  })

  it('the quality margins were not touched', () => {
    // Type (c): the AA floor is the requirement; the measured 15.5 is headroom, not a target.
    const contrast = read('design/schemeContrast.test.ts')
    expect(contrast).toMatch(/toBeGreaterThanOrEqual\(AA\)/)
    expect(contrast, 'no scheme contrast may be pinned to its current measurement').not.toMatch(/toBeGreaterThanOrEqual\(1[0-9]\.\d/)
  })

  it('the audit itself is repeatable — every floor in the suite is still countable', () => {
    // Not vacuous, and it is the handle for re-running this: 106 numeric floors across 75 test files when
    // measured. If that collapses, the scan broke rather than the floors vanishing.
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.test\.tsx?$/.test(n) ? [p] : []
      })
    const floors = walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(/\.toBeGreaterThan(?:OrEqual)?\(\d+\)/g)])
    expect(floors.length, 'the floor census must still find the suite').toBeGreaterThanOrEqual(100)
  })
})
