import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { scanDrift, countInlineFontWeights } from './consistencyAudit.report'

// ── Primitive-adoption ratchet (design-system consistency C1 / T3.4) ────────
// The third consistency rail (alongside token-lint-strict in tokenLint.test.ts
// and the a11y axe scan in e2e/): flag NEW bespoke chrome. Raw <button>,
// <input>/<textarea>/<select>, and ad-hoc dialogs outside web/src/ui/ should
// migrate to the shell primitives (Button/IconButton, the shared form family
// Field/TextInput/TextArea/Select, Modal). This test asserts the counts may only DECREASE from a committed
// baseline — a new raw element turns CI red, but the existing S2 backlog
// (~420 buttons, ~206 inputs) doesn't block. As migrations land, ratchet the
// baseline down IN THE SAME COMMIT (primitiveAdoption.baseline.json).
//
// This runs in the existing CI `web` job (vitest) — no browser needed — so the
// ratchet is live without the (auth-blocked) Playwright harness.

interface Baseline { rawButton: number; rawInput: number; rawDialog: number; inlineFontWeight: number }

function loadBaseline(): Baseline {
  const raw = readFileSync(join(process.cwd(), 'src/design/primitiveAdoption.baseline.json'), 'utf8')
  const j = JSON.parse(raw)
  return { rawButton: j.rawButton, rawInput: j.rawInput, rawDialog: j.rawDialog, inlineFontWeight: j.inlineFontWeight }
}

function liveCounts() {
  const res = scanDrift()
  let rawButton = 0, rawInput = 0, rawDialog = 0
  for (const f in res.primitivesByFile) {
    const p = res.primitivesByFile[f]
    rawButton += p['raw-button']
    rawInput += p['raw-input']
    rawDialog += p['raw-dialog']
  }
  return { rawButton, rawInput, rawDialog }
}

describe('primitive-adoption ratchet (bespoke chrome may only shrink)', () => {
  const base = loadBaseline()
  const live = liveCounts()

  it(`raw <button> count must not exceed the baseline (${base.rawButton})`, () => {
    expect(
      live.rawButton,
      `New bespoke <button>(s) detected (${live.rawButton} > ${base.rawButton}). ` +
        `Use the Button/IconButton primitive, or if this is an intentional migration DOWN, ` +
        `lower rawButton in src/design/primitiveAdoption.baseline.json.`,
    ).toBeLessThanOrEqual(base.rawButton)
  })

  it(`raw <input>/<textarea>/<select> count must not exceed the baseline (${base.rawInput})`, () => {
    expect(
      live.rawInput,
      `New bespoke form element(s) detected (${live.rawInput} > ${base.rawInput}). ` +
        `Use the shared form family (Field/TextInput/TextArea/Select), or lower rawInput in the baseline if migrating down.`,
    ).toBeLessThanOrEqual(base.rawInput)
  })

  it(`ad-hoc dialogs must stay at the baseline (${base.rawDialog}) — Modal is canonical`, () => {
    expect(
      live.rawDialog,
      `New ad-hoc dialog markup detected (${live.rawDialog} > ${base.rawDialog}). Use the Modal primitive.`,
    ).toBeLessThanOrEqual(base.rawDialog)
  })

  it(`inline font-weight count must not exceed the baseline (${base.inlineFontWeight})`, () => {
    const live = countInlineFontWeights().total
    expect(
      live,
      `New inline font-variation-settings "wght" detected (${live} > ${base.inlineFontWeight}). ` +
        `Use fvs()/withWeight() (design/fontWeight.ts) or a .fw-* class, or lower inlineFontWeight ` +
        `in src/design/primitiveAdoption.baseline.json if migrating down.`,
    ).toBeLessThanOrEqual(base.inlineFontWeight)
  })

  it('baseline is not stale (live counts have not silently dropped >20 below it without a ratchet)', () => {
    // A soft nudge: if a migration dropped the real count well below the baseline,
    // the baseline should be ratcheted down in that commit. Warn, don't fail hard,
    // to avoid blocking unrelated work — but keep the drift visible.
    const slack = 20
    if (live.rawButton + slack < base.rawButton || live.rawInput + slack < base.rawInput) {
      // eslint-disable-next-line no-console
      console.warn(
        `[primitive-adoption] live counts are well below baseline ` +
          `(button ${live.rawButton}/${base.rawButton}, input ${live.rawInput}/${base.rawInput}) — ` +
          `ratchet primitiveAdoption.baseline.json DOWN to lock in the gain.`,
      )
    }
    expect(true).toBe(true)
  })
})
