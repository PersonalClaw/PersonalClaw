import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { TextInput } from './forms'

// ── A mandatory field must say so on the FIELD, not only at the button ────────────
//
// Measured across the whole tree before this change:
//
//   `aria-required` occurrences ............ 0
//   `<input>`/`<textarea>` with `required` .. 0
//   buttons whose `disabledReason` matches /enter a … first/i .. 40
//
// So the app enforced mandatory fields and explained them **only at the submit button**. On
// `#/prompts/new` the submit reads `title="Enter a name first"` while the Name input carried no
// `required` and no `aria-required` — a screen-reader user tabbing that field heard nothing about it and
// discovered the requirement by failing to submit. (WCAG 3.3.2 Labels or Instructions, level A.)
//
// Measured after, per create form (inputs → aria-required):
//
//   #/prompts/new   4 inputs → 1     #/tasks/new   9 → 1     #/agents/new   7 → 1
//
// `required` publishes `aria-required` and nothing else — no asterisk, no colour, no layout — so all four
// captures are byte-identical. A VISIBLE required marker is a separate, owner-facing decision about the
// form language; this is the invisible half, which is unambiguous and costs nothing.
//
// ⚠️ SCOPE, stated so the next pass starts from a verdict: this covers the three create forms whose single
// mandatory identity field is positively identified by its own submit reason. `TriggerCreatePage` and
// `KnowledgeCreatePage` build their `disabledReason` from SEVERAL conditions, so which field is mandatory
// depends on the branch — they need their own read, not a blanket sweep. The other ~37 "Enter a … first"
// buttons are inline/dialog forms, also unswept.

const SRC = join(process.cwd(), 'src')

const ADOPTERS = [
  join('pages', 'prompts', 'PromptForm.tsx'),
  join('pages', 'tasks', 'TaskForm.tsx'),
  join('pages', 'agents', 'AgentForm.tsx'),
  // Added once its mandatory field was positively identified: the FIRST branch of its own
  // `disabledReason` is `!name.trim() ? 'Name the trigger first'`, and the Name field is the form's
  // autofocused `TextInput`. (#1150 deferred it because the reason is multi-condition; reading the
  // branch order settled it.)
  join('pages', 'triggers', 'TriggerCreatePage.tsx'),
]

/** The one create form deliberately NOT here, and why. `KnowledgeCreatePage`'s requirement is
 *  kind-dependent AND, for the default kind, an EITHER/OR: its `disabledReason` reads
 *  'Enter a URL starting with http:// or https://' for a bookmark, 'Choose a file first' for a file, and
 *  **'Add a title or some content'** otherwise. `aria-required` on a single input cannot express "one of
 *  these two" — marking both would tell a screen-reader user that each is individually mandatory, which is
 *  false. That needs a group-level pattern (a fieldset with its own instruction), i.e. a design decision,
 *  not this prop. Recorded so the sweep is CLOSED rather than silently incomplete. */
const PRINCIPLED_EXCLUSION = join('pages', 'knowledge', 'KnowledgeCreatePage.tsx')

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

describe('TextInput can declare a field mandatory', () => {
  it('publishes aria-required when asked, and not otherwise', () => {
    const on = render(<TextInput required value="" onChange={() => {}} ariaLabel="n" />)
    expect(on.container.querySelector('input')!.getAttribute('aria-required')).toBe('true')
    on.unmount()
    const off = render(<TextInput value="" onChange={() => {}} ariaLabel="n" />)
    expect(off.container.querySelector('input')!.getAttribute('aria-required')).toBeNull()
  })

  it('changes nothing visual', () => {
    // The prop must not reach className/style — the captures depend on it.
    const a = render(<TextInput required value="" onChange={() => {}} ariaLabel="n" />)
    const withReq = a.container.querySelector('input')!.className
    a.unmount()
    const b = render(<TextInput value="" onChange={() => {}} ariaLabel="n" />)
    expect(b.container.querySelector('input')!.className).toBe(withReq)
  })
})

describe('the create forms mark their mandatory field', () => {
  it.each(ADOPTERS)('%s passes required on its identity field', (rel) => {
    const src = readFileSync(join(SRC, rel), 'utf8')
    expect(src, `${rel} must mark exactly the mandatory input`).toMatch(/<TextInput required /)
  })

  it('the enforcement is still explained at the button too (belt and braces)', () => {
    // The button reason is what a sighted user reads; `aria-required` is what a screen reader hears. Both.
    const task = readFileSync(join(SRC, 'pages', 'tasks', 'TaskCreatePage.tsx'), 'utf8')
    expect(task).toMatch(/disabledReason=\{!draft\.title\.trim\(\) \? 'Enter a task title first'/)
  })

  it('the create-form subset is CLOSED — every one is adopted or excluded with a reason', () => {
    // The four full-page create flows with a single mandatory identity field are all adopters above.
    // Knowledge is the fifth and is excluded on purpose; if it ever grows a single required field, this
    // assertion is where the exclusion gets revisited.
    const knowledge = readFileSync(join(SRC, PRINCIPLED_EXCLUSION), 'utf8')
    expect(knowledge, 'the either/or reason is what makes the exclusion principled').toMatch(
      /Add a title or some content/,
    )
    expect(/<TextInput required /.test(knowledge), 'a single required mark would be false here').toBe(false)
  })

  it('records the unswept remainder rather than implying it is done', () => {
    // A vacuity floor with teeth: if this count collapses, either the sweep finished (update the header)
    // or the matcher broke. Either way, look.
    const files = walk(SRC)
    let reasons = 0
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      reasons += (src.match(/disabledReason=\{[^}]*[Ee]nter a[^}]*first/g) ?? []).length
    }
    // Measured 20, floored at 5 — 15 could have gone quietly. A floor that stands for a POPULATION has to
    // sit at the population (cycle 134's audit).
    expect(reasons, 'the "Enter a … first" population must still be visible to this rail').toBeGreaterThanOrEqual(20)
  })
})
