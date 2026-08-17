import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A rung label is a PREDICATE, so every slot that interpolates one needs a subject ─────────────
//
// `RUNG_LABELS` (backend `guardrails/rungs.py`) is declared "in terms of BEHAVIOUR rather than of the
// ladder": `drafts only` · `asks first` · `runs with undo` · `runs on its own`. That is exactly right
// for `RungChip`, which renders the label alone as a badge, and right after a subject. Dropped into a
// noun slot it mangles — and this panel had it both ways at once:
//
//   ✅ line ~123   notify(`${t.key} now ${label}.`)        → "action.digest now runs on its own."
//   ✅ line ~133   notify(`${t.key} is back at ${label}.`) → "action.digest is back at drafts only."
//   ❌ line ~181   <Button>Promote to {label}</Button>     → "Promote to runs on its own"
//
// So the canonical form was already shipping in the same file, twenty lines from the defect. The button
// now reads "Promote so it runs on its own", converging on it rather than inventing a second (noun)
// rung vocabulary — which would have made three forms out of two.
//
// The backend half of this family (`_authority_sentence`'s three provenance branches and the inbox
// proposal title) is pinned in `tests/test_guardrails_ladder.py`; that sentence is Python, so no
// frontend rail can see it. This file owns the one slot that lives here.
//
// 🪤 DELIBERATELY NOT FLAGGED: "is back at drafts only" and the Hand-back title's "back to drafts
// only". `back at`/`back to` + a state name is idiomatic and reads correctly; rewriting them would
// change copy without fixing a defect. Scoped to what was measured.

const SRC = join(process.cwd(), 'src', 'pages', 'settings', 'GuardrailsPanel.tsx')
const raw = readFileSync(SRC, 'utf8')

describe('a rung label is never dropped into a noun slot', () => {
  it('the promote button gives the label a subject', () => {
    expect(raw, 'the button must read as a sentence').toContain(
      'Promote so it {rungMeta(t.next_rung, ladder).label}',
    )
    expect(raw, '"Promote to <predicate>" is the defect').not.toContain('Promote to {rungMeta')
  })

  it('the success toasts — the canonical form — are untouched', () => {
    // This is what the fix converged ONTO. If these change shape, the button's form loses its warrant.
    expect(raw).toContain('now ${rungMeta(r.rung, ladder ?? null).label}.')
    expect(raw).toContain('is back at ${rungMeta(t.floor, ladder ?? null).label}.')
  })

  it('no label interpolation directly follows "to " or "at " in a NEW slot', () => {
    // The ratchet. Every `rungMeta(...).label` in the file must be preceded either by a subject
    // (`now `, `so it `) or by one of the two idioms explicitly allowed above.
    const ALLOWED_PREFIXES = [
      'now ', // canonical predicate slot
      'so it ', // canonical predicate slot
      'is back at ', // idiom, deliberately kept
      'back to ', // idiom, deliberately kept (Hand-back title)
    ]
    const offenders: string[] = []
    for (const m of raw.matchAll(/rungMeta\([^)]*\)\.label/g)) {
      // 🪤 Strip BOTH interpolation openers: a template literal uses `${`, JSX a bare `{`. Handling
      // only `${` made the JSX slot — the one this test was written for — fail its own rule.
      const before = raw.slice(Math.max(0, m.index! - 60), m.index!).replace(/\$?\{$/, '')
      if (!ALLOWED_PREFIXES.some((p) => before.endsWith(p))) {
        offenders.push(`…${before.slice(-30)}» ${m[0]}`)
      }
    }
    expect(offenders, 'a rung label needs a subject, or one of the two allowed idioms').toEqual([])
  })

  it('the ratchet is not vacuous — it finds every label slot in the file', () => {
    // A regex that matched nothing would make the rule above look enforced while enforcing nothing.
    expect([...raw.matchAll(/rungMeta\([^)]*\)\.label/g)].length).toBeGreaterThanOrEqual(4)
  })
})
