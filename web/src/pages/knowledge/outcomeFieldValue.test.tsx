import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { OutcomeFieldValue } from './KnowledgeDetail'

// ── One type-dispatch table for an extracted outcome field ────────────────────
//
// `OutcomeFieldValue` maps an intent outcome's declared `type` to a rendering. It existed TWICE —
// `KnowledgeDetail.tsx` and `KnowledgeListPage.tsx` — with **all 8 lines byte-identical, every
// branch**, differing only in that the list page re-declared the prop as an inline
// `{ type: string; value: unknown }` instead of naming `IntentOutcomeField`. That inline type was
// a needless weakening: `IntentOutcome.fields` is already `IntentOutcomeField[]`, and the list page
// passes `o.fields!.map(f => …)`, so it was always handing over the real thing — which the
// typecheck confirms now that the shared version demands it.
//
// Two copies of a type-dispatch table is the shape that rots QUIETLY. Adding a `date` or
// `currency` case to one leaves the other falling through to `String(value)`, and nothing fails:
// no type error, no test, just one surface rendering a raw ISO string while the other formats it.
// That is why this test pins the dispatch itself and not merely the dedup — the second assertion
// would catch a future case added to the shared copy but unreachable from a re-forked one.
//
// Scope: the OTHER `type === 'boolean'` dispatches in the app (`tools/schema.tsx`,
// `settings/ProviderConfigForm`, `chat/PromptPalette`, `prompts/PromptPreviewPane`,
// `triggers/ActionConfig`, `workflows/WorkflowAsk`) build EDITABLE controls from a JSON schema.
// Read-only display and input rendering are different roles; converging them would be flattening,
// not deduplication.

const KNOWLEDGE = join(process.cwd(), 'src/pages/knowledge')

describe('OutcomeFieldValue is defined once', () => {
  it('no other file in the knowledge area declares it', () => {
    const definers = readdirSync(KNOWLEDGE)
      .filter((f) => /\.tsx$/.test(f) && !/\.test\.tsx$/.test(f))
      .filter((f) => /function OutcomeFieldValue\b/.test(readFileSync(join(KNOWLEDGE, f), 'utf8')))
    // Exactly one home. A second definition is how the dispatch tables drift apart.
    expect(definers, `declared in: ${definers.join(', ')}`).toEqual(['KnowledgeDetail.tsx'])
  })

  it('the list page imports it rather than re-declaring', () => {
    const src = readFileSync(join(KNOWLEDGE, 'KnowledgeListPage.tsx'), 'utf8')
    expect(src).toMatch(/import \{[^}]*OutcomeFieldValue[^}]*\} from '\.\/KnowledgeDetail'/)
  })
})

describe('the dispatch table itself', () => {
  // Pinning every branch, so a case added to the shared copy cannot be silently dropped by a
  // future re-fork — and so the migration is provably behaviour-preserving rather than assumed.
  const cases: Array<[string, { type: string; value: unknown }, (t: string) => void]> = [
    ['empty string → em dash', { type: 'text', value: '' }, (t) => expect(t).toBe('—')],
    ['null → em dash', { type: 'text', value: null }, (t) => expect(t).toBe('—')],
    ['boolean true → Yes', { type: 'boolean', value: true }, (t) => expect(t).toBe('Yes')],
    ['boolean false → No', { type: 'boolean', value: false }, (t) => expect(t).toBe('No')],
    ['number → the digits', { type: 'number', value: 42 }, (t) => expect(t).toBe('42')],
    ['unknown type → String(value)', { type: 'wat', value: 'raw' }, (t) => expect(t).toBe('raw')],
  ]

  for (const [name, field, assert] of cases) {
    it(name, () => {
      const { container } = render(<OutcomeFieldValue field={field as never} />)
      assert((container.textContent ?? '').trim())
    })
  }

  it('url renders a safe external link', () => {
    const { container } = render(
      <OutcomeFieldValue field={{ type: 'url', value: 'https://example.com/x' } as never} />,
    )
    const a = container.querySelector('a')
    expect(a?.getAttribute('href')).toBe('https://example.com/x')
    // `noreferrer` on a target=_blank link is the security-relevant half, not decoration.
    expect(a?.getAttribute('target')).toBe('_blank')
    expect(a?.getAttribute('rel')).toContain('noreferrer')
  })

  it('tags render one chip per entry', () => {
    const { container } = render(
      <OutcomeFieldValue field={{ type: 'tags', value: ['a', 'b', 'c'] } as never} />,
    )
    // The chip count is the invariant; a `tags` value rendered via String() would be one node.
    expect(container.querySelectorAll('span.rounded-pill')).toHaveLength(3)
  })

  it('a tags TYPE with a non-array value falls through instead of crashing', () => {
    // The branch guards on `Array.isArray`, and a malformed payload is exactly what a backend
    // change would send first. Falling through beats throwing inside a card.
    const { container } = render(
      <OutcomeFieldValue field={{ type: 'tags', value: 'not-an-array' } as never} />,
    )
    expect((container.textContent ?? '').trim()).toBe('not-an-array')
  })
})
