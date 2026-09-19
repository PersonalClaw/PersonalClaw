import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The feedback surfaces promise only the gate that EXISTS ─────────────────────────────────────
//
// `#648`. Settings said a wrong AI source "stops surfacing", unqualified, for all four producer
// kinds it names (inbox triage, drafts, digests, loop findings). Only ONE of them has a surfacing
// gate: `feedback.suppressed_producers()` is consulted by the skills loader
// (`skills/loader.py` → `_suppressed_producers`, read where a skill BODY is resolved), and nowhere
// else. For a draft, a digest or a loop finding, nothing reads suppression, so the sentence
// described an enforcement that does not run.
//
// The panel half was fixed first — `FeedbackPanel.tsx` now scopes it — and the Settings-HOME tile
// kept making the unqualified claim twice in `settingsWidgets.tsx` (the card `description` and the
// empty-state body). Same sentence, two surfaces, one fixed: exactly the enumerated-fix partial
// this programme keeps finding, so the rail covers BOTH files rather than the one that was wrong.
//
// 🪤 THIS IS A COPY RAIL AND THE COPY IS ALLOWED TO CHANGE. It pins the PROPERTY — no surface may
// promise unconditional suppression — not a wording. Rewrite the sentence freely; just do not
// re-assert a gate for producer kinds that have none.

const FILES = [
  'src/pages/settings/settingsWidgets.tsx',
  'src/pages/settings/FeedbackPanel.tsx',
]

/** The unqualified promise: "stops surfacing" with no scoping clause anywhere near it. */
function unqualifiedPromises(text: string): string[] {
  const bad: string[] = []
  for (const line of text.split('\n')) {
    if (!line.includes('stops surfacing')) continue
    // A scoped claim names the condition. `FeedbackPanel`'s fixed form is the model:
    // "…where that kind of source has a surfacing gate (today, skills) it also stops surfacing."
    const scoped = /surfacing gate|where that kind|today, skills/i.test(line)
    if (!scoped) bad.push(line.trim())
  }
  return bad
}

describe('no feedback surface promises suppression it cannot enforce', () => {
  for (const rel of FILES) {
    it(`${rel} scopes every "stops surfacing" claim`, () => {
      const text = readFileSync(join(process.cwd(), rel), 'utf8')
      expect(unqualifiedPromises(text)).toEqual([])
    })
  }

  it('the premise holds — the claim is still made somewhere, just scoped', () => {
    // Guards the lazy fix: deleting the sentence outright would also pass the assertions above,
    // and the suppression gate IS real for skills, so the product should still say so.
    const all = FILES.map((r) => readFileSync(join(process.cwd(), r), 'utf8')).join('\n')
    expect(all).toMatch(/stops surfacing/)
    expect(all).toMatch(/surfacing gate/)
  })
})
