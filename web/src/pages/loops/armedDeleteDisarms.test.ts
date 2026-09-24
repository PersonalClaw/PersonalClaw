import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── An armed destroy that never disarms is a live one-click destroy ────────────────────────────────
//
// The two-stage "armed" reveal makes a destructive click safe by requiring two of them. **The whole
// safety property is that the arm EXPIRES.** Without a timer the control simply sits there relabelled,
// and the user's next click on that spot — minutes later, for any reason, having forgotten — destroys.
//
// `DesignCockpitPage` shipped exactly that. `del()` armed on the first click and the header control
// relabelled "Delete" → "Confirm delete?"; nothing in the file ever reset it. What it destroys is the
// loop's findings, deliverable and history, with no undo.
//
// 🔑 IT WAS ONE OF TWO SITES THAT LOST THE TIMER WHEN THE PATTERN WAS COPIED, and that is the real
// lesson: there is no `ArmedButton` primitive, so all eight armed controls in the tree are independent
// hand-rolled copies of `useState` + a timeout. Copy-paste does not carry an invariant. This rail is
// the cheap stand-in for the primitive — it cannot make the pattern reusable, but it can stop the next
// copy from losing the one part that matters.
//
// 🪤 THERE ARE TWO LEGITIMATE DISARM MECHANISMS, AND A RAIL THAT KNOWS ONLY ONE IS WORSE THAN NONE:
//
//   (a) an effect keyed on the armed state — `LoopCockpitPage`, `formControls`, `SdlcProgressCard`:
//         useEffect(() => { if (!armed) return
//           const t = window.setTimeout(() => setArmed(false), 4000); return () => clearTimeout(t) }, [armed])
//
//   (b) an inline timeout in the ARMING handler with a functional identity guard — `LoopsListPage:101`:
//         if (confirmDelete !== id) { setConfirmDelete(id)
//           window.setTimeout(() => setConfirmDelete((c) => (c === id ? null : c)), 4000); return }
//
// (b) exists because that arm is per-ROW: it must clear only if the same row is still the armed one,
// which an effect keyed on a changing id would get wrong. Demanding (a) everywhere would flag correct
// code, so this asserts the PROPERTY — a 4000ms timeout that resets this file's own armed setter —
// rather than either spelling.
//
// 🪤 AND IT MUST TIE THE TIMEOUT TO THE SETTER, not merely find `4000` in the file.
// `LoopsListPage.tsx:82` is `useVisiblePoll(refresh, hasLive ? 4000 : null)` — a poll interval. A rail
// that grepped for the number alone would pass vacuously on a file whose only 4000 is a poll.

const SRC = join(process.cwd(), 'src')

const strip = (s: string) =>
  s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

/** Each entry: the file, and the state setter its armed flag uses. */
const ARMED: { rel: string; setter: string; what: string }[] = [
  { rel: 'pages/loops/DesignCockpitPage.tsx', setter: 'setConfirmDelete', what: "the design loop's findings, deliverable and history" },
  { rel: 'pages/loops/LoopCockpitPage.tsx', setter: 'setConfirmDelete', what: "the loop's findings, deliverable and history" },
  { rel: 'pages/loops/LoopCockpitPage.tsx', setter: 'setConfirmStop', what: 'a run that then cannot resume' },
  { rel: 'pages/loops/LoopsListPage.tsx', setter: 'setConfirmDelete', what: 'a loop, from its row' },
  { rel: 'pages/chat/SdlcProgressCard.tsx', setter: 'setConfirmDel', what: 'a loop, from the chat card' },
  { rel: 'pages/workflows/WorkflowsListPage.tsx', setter: 'setArmed', what: 'a workflow run and its artifacts' },
  { rel: 'pages/tasks/formControls.tsx', setter: 'setArmed', what: 'a checklist row the user typed' },
]

function read(rel: string): string {
  return strip(readFileSync(join(SRC, rel), 'utf8'))
}

describe('every armed destroy disarms itself', () => {
  it.each(ARMED)('$rel — $setter (destroys $what)', ({ rel, setter }) => {
    const body = read(rel)
    expect(body, `${rel} must still declare ${setter}`).toContain(`${setter}(`)

    // A 4000ms timeout whose callback resets THIS armed setter. Both mechanisms above satisfy it:
    // the effect form resets to a literal, the per-row form resets through a functional updater.
    const disarm = new RegExp(
      `setTimeout\\(\\s*\\(\\)\\s*=>\\s*${setter}\\([\\s\\S]{0,80}?\\)\\s*,\\s*4000\\s*\\)`,
    )
    expect(
      disarm.test(body),
      `${rel}: ${setter} arms a destructive control but nothing disarms it after 4000ms — ` +
      'the arm is the only thing making two clicks safer than one, and it must expire',
    ).toBe(true)
  })

  it('the census is real — every listed file exists and is non-trivial', () => {
    // A vacuity floor: a typo'd path would otherwise make its row pass by never being read.
    for (const { rel } of ARMED) {
      expect(read(rel).length, `${rel} must be substantial`).toBeGreaterThan(500)
    }
    expect(ARMED.length, 'the armed-control census').toBeGreaterThanOrEqual(7)
  })

  it('4000ms is the one interval, so two armed controls never expire differently', () => {
    // Not cosmetic: a user who learns the arm lasts "about four seconds" on one surface carries that
    // expectation to the next. Two intervals means the safety window is unpredictable.
    for (const { rel, setter } of ARMED) {
      const body = read(rel)
      const others = [...body.matchAll(new RegExp(`setTimeout\\(\\s*\\(\\)\\s*=>\\s*${setter}\\([\\s\\S]{0,80}?\\)\\s*,\\s*(\\d+)\\s*\\)`, 'g'))]
        .map((m) => m[1])
        .filter((ms) => ms !== '4000')
      expect(others, `${rel}: ${setter} disarms on a non-standard interval (${others.join(', ')})`).toEqual([])
    }
  })
})

describe('the pattern that has no primitive', () => {
  it('is recorded as hand-rolled, so the count is visible when someone builds one', () => {
    // 🔑 NOT a ratchet — this is a marker. Eight independent copies of one safety invariant is the
    // reason a timer went missing at all. If an `ArmedButton` primitive ever lands, this census is the
    // migration list, and this assertion is what will fail and point at it.
    const files = new Set(ARMED.map((a) => a.rel))
    expect(files.size, 'files hand-rolling the armed-destroy pattern').toBeGreaterThanOrEqual(6)
    // There is deliberately no `ui/ArmedButton` yet; if one appears, this reminds the author to migrate.
    let hasPrimitive = false
    try {
      readFileSync(join(SRC, 'ui/ArmedButton.tsx'), 'utf8')
      hasPrimitive = true
    } catch { hasPrimitive = false }
    expect(
      hasPrimitive,
      'ui/ArmedButton.tsx now exists — migrate the census above to it and delete this assertion',
    ).toBe(false)
  })
})
