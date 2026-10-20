import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Issue #658 — the Vocabulary section's corrections need a real writer ──
//
// `api.lexiconAddCorrection` was defined in `api.ts` with EXACTLY ONE reference in the
// whole frontend: its own definition. The section's copy promised corrections would be
// "captured from your transcript edits" — a flow that exists nowhere — so the read side
// (list + auto-apply toggles, fully built) could only ever render an empty list, and the
// empty state instructed the user to do something unimplemented.
//
// The fix gives the promise a real mechanism: an add-a-fix form in the Learned
// corrections subsection wired to `api.lexiconAddCorrection`, with copy that describes
// what the form actually does. This rail is source-level (the house pattern for this
// panel, which can't be unit-imported without dragging the whole settings tree) and pins:
//
//   1. the call site exists — the exact defect metric the issue measured (grep count),
//   2. the dead-promise copy is gone in both places it appeared,
//   3. the form's two labelled inputs exist for it.

const panel = readFileSync(join(__dirname, 'VoicePanel.tsx'), 'utf8')
const api = readFileSync(join(__dirname, '..', '..', 'lib', 'api.ts'), 'utf8')

describe('VoicePanel corrections writer (issue #658)', () => {
  it('lexiconAddCorrection has a real call site in the panel', () => {
    expect(api).toContain('lexiconAddCorrection:')
    expect(panel).toContain('api.lexiconAddCorrection(')
  })

  it('no copy promises the unbuilt transcript-edit capture flow', () => {
    expect(panel).not.toContain('captured from your transcript edits')
    expect(panel).not.toContain('When you fix a mis-heard term in a transcript')
  })

  it('the add-a-fix form exposes labelled heard/meant inputs', () => {
    // 🪤 `ariaLabel`, not `aria-label`: the row is built from the shell `TextInput`
    // primitive rather than raw <input>s, because `primitiveAdoption`'s baseline is a
    // ceiling that may only shrink and a bespoke row would have to raise it. The
    // primitive spells the prop `ariaLabel` and publishes `aria-label` itself, so this
    // rail pins the same property at the only spelling the call site can carry.
    expect(panel).toContain('ariaLabel="Mis-heard word"')
    expect(panel).toContain('ariaLabel="Correct word"')
  })
})
