import { describe, expect, it } from 'vitest'
import { availableSuggestions, intentKind, suggestTemplate } from './templateSuggest'

// ── "Start from template" intent suggestion (LOOPS-EVOLUTION criterion 11) ──
//
// The gap this closes: the templates tab lists bundled workflows by NAME, so a user who
// knows what they want to do ("fix this bug", "research a topic") has to already know that
// a coding job is called `code-implementation` and a research one `deep-research`. These
// are unit tests over the pure suggestion module — the interesting behaviour is which kind
// an intent maps to and which shipped template that kind resolves to, none of which needs a
// rendered picker to assert.

// The templates the bundled set actually ships (the alias targets). `code-project` is
// deliberately ABSENT — it was deferred and never shipped, and `code` resolves to
// `code-implementation` instead (see containerKey.KIND_TO_TEMPLATE).
const SHIPPED = new Set([
  'general-project', 'goal-pursuit-open-ended', 'goal-pursuit-verifiable',
  'code-implementation', 'design-project', 'deep-research',
])

describe('intentKind', () => {
  it('reads a coding intent as the code kind', () => {
    expect(intentKind('fix the login bug')).toBe('code')
    expect(intentKind('implement a new API endpoint')).toBe('code')
    expect(intentKind('refactor the parser')).toBe('code')
  })

  it('reads a research intent as the research kind', () => {
    expect(intentKind('research the best vector databases')).toBe('research')
    expect(intentKind('investigate and compare the options with sources')).toBe('research')
  })

  it('reads a design intent as the design kind', () => {
    expect(intentKind('design a settings UI mockup')).toBe('design')
  })

  it('returns empty when nothing matches, rather than defaulting', () => {
    // The caller distinguishes "matched nothing" (let the user browse) from "matched
    // general" (a real signal); collapsing them would hide that difference.
    expect(intentKind('')).toBe('')
    expect(intentKind('   ')).toBe('')
    expect(intentKind('the quick brown fox')).toBe('')
  })

  it('matches whole words only', () => {
    // "scode" is not "code"; a passing substring must not swing the suggestion.
    expect(intentKind('scode alembic')).toBe('')
  })

  it('lets the action verb win over its subject on a tie', () => {
    // "refactor the research pipeline" is a CODING job about research — code outranks.
    expect(intentKind('refactor the research pipeline')).toBe('code')
  })
})

describe('suggestTemplate', () => {
  it('suggests code-implementation for a coding intent (code-project was never shipped)', () => {
    // The plan names `code-project`; that template was deferred, and the alias points `code`
    // at the `code-implementation` template that DID ship. Suggesting the working template is
    // what keeps the menu entry from being dead.
    expect(suggestTemplate('fix the failing test', SHIPPED)).toBe('code-implementation')
  })

  it('suggests deep-research for a research intent', () => {
    expect(suggestTemplate('research the tradeoffs', SHIPPED)).toBe('deep-research')
  })

  it('drops a suggestion for a template that is not available', () => {
    // A resolved template absent from the picker's own list is a dead entry.
    expect(suggestTemplate('research the tradeoffs', new Set(['general-project']))).toBe('')
  })

  it('returns empty for an unrecognizable intent', () => {
    expect(suggestTemplate('hello there', SHIPPED)).toBe('')
    expect(suggestTemplate('', SHIPPED)).toBe('')
  })

  it('accepts an array as well as a set for the available templates', () => {
    expect(suggestTemplate('research it', [...SHIPPED])).toBe('deep-research')
  })
})

describe('availableSuggestions', () => {
  it('lists only kind→template pairs whose template ships', () => {
    const pairs = availableSuggestions(SHIPPED)
    const byKind = Object.fromEntries(pairs.map((p) => [p.kind, p.template]))
    expect(byKind.code).toBe('code-implementation')
    expect(byKind.research).toBe('deep-research')
    expect(byKind.design).toBe('design-project')
  })

  it('omits a kind whose template is not installed', () => {
    const pairs = availableSuggestions(new Set(['deep-research']))
    expect(pairs).toEqual([{ kind: 'research', template: 'deep-research' }])
  })
})
