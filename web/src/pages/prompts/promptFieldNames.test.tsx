import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { VariableRow } from './VariableRow'

// ── Prompts controls: 14 unnamed, plus 4 names repeated once PER VARIABLE ───────
//
// Drove all 13 prompts surfaces (3 list tabs · 3 create kinds · view+edit for a user prompt, a
// system prompt and a snippet) and probed for controls with no resolvable accessible name:
//
//     edit-user / edit-system   4 each   title · description · tags · template
//     view-system               2        the "Try it" variable inputs
//     view-snippet              2        the snippet preview inputs
//     create-snippet            1        the snippet body textarea
//     edit-system / edit-snippet         dupes: Variable name / type / description / default value
//
// The dupes are the interesting half. `VariableRow` ALREADY carried a full set of aria-labels — but
// CONSTANT ones, on a component rendered once per variable. A prompt with two variables produced two
// boxes both announcing "Variable name": non-null, and still ambiguous. It already used `useId()` to
// make its `name=` attributes unique for autofill; the accessible name needed the same treatment.
//
// Two source-level traps this cycle exposed, both of which read as correct code:
//
//   1. `PromptDetail`'s Try-it inputs set `id={fid}` with a comment claiming that makes them
//      "identifiable to screen readers". NOTHING renders a `<label htmlFor={fid}>`, so it was a
//      DANGLING id — a naming mechanism in appearance only. Measured: no accessible name at all.
//   2. Several controls sit inside a `Field` that DOES publish a label id (`ui/forms`), but they are
//      RAW elements and only the form-family components read `FieldLabelCtx`. A correct wrapper does
//      not name a raw child. (`PromptEditFields` is a third variant: its `Section` is injected as a
//      PROP and renders a bare label div, so it publishes nothing at all.)
//
// Measured after, all 13 surfaces: 0 unnamed, 0 duplicate-name groups. Two variables read
// `Name of variable "bot_name"` vs `Name of variable "widget_block"`; two BLANK rows read
// `... row 1` vs `... row 2`.
//
// Deliberately NOT converged: `SnippetForm`'s body stays a raw <textarea> rather than `ui/forms`'
// `TextArea` (which would claim its Field's label automatically), because `taRef` drives
// cursor-position insertion for the snippet picker and `TextArea` does not forward a ref. Adding ref
// forwarding to the primitive is a wider change than this pass; it self-names instead.

const PROMPTS = join(process.cwd(), 'src/pages/prompts')

/** Source with comments stripped — the notes above name the very attributes under test, and a bare
 *  text search would count an explanation as compliance. */
const code = (f: string) =>
  readFileSync(join(PROMPTS, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const nameOf = (el: Element) => el.getAttribute('aria-label')

describe('VariableRow names each row after the variable it edits', () => {
  it('two rows with different variables get different names', () => {
    const { container } = render(
      <>
        <VariableRow v={{ name: 'city', type: 'text' }} rowIndex={0} onChange={() => {}} onRemove={() => {}} />
        <VariableRow v={{ name: 'tone', type: 'text' }} rowIndex={1} onChange={() => {}} onRemove={() => {}} />
      </>,
    )
    const names = [...container.querySelectorAll('input, select')].map(nameOf)
    expect(names).toContain('Name of variable "city"')
    expect(names).toContain('Name of variable "tone"')
    // The whole point: no two controls share a name.
    expect(new Set(names).size, `duplicate names: ${names.join(' / ')}`).toBe(names.length)
  })

  it('two BLANK rows still differ — the rowIndex fallback', () => {
    // Without it, a user adding three empty variables gets three identical "Variable name" boxes.
    const { container } = render(
      <>
        <VariableRow v={{ name: '', type: 'text' }} rowIndex={0} onChange={() => {}} onRemove={() => {}} />
        <VariableRow v={{ name: '', type: 'text' }} rowIndex={1} onChange={() => {}} onRemove={() => {}} />
      </>,
    )
    const names = [...container.querySelectorAll('input, select')].map(nameOf)
    expect(names).toContain('Name of variable row 1')
    expect(names).toContain('Name of variable row 2')
    expect(new Set(names).size).toBe(names.length)
  })

  it('the Remove button and the choices field are scoped too', () => {
    const { container } = render(
      <VariableRow v={{ name: 'mode', type: 'select', options: ['a', 'b'] }} rowIndex={0} onChange={() => {}} onRemove={() => {}} />,
    )
    const all = [...container.querySelectorAll('input, select, button')].map(nameOf).filter(Boolean)
    expect(all).toContain('Remove variable "mode"')
    expect(all).toContain('Choices for variable "mode"')
  })

  it('a constant name would regress this — pinned at the source', () => {
    const src = code('VariableRow.tsx')
    for (const re of [
      /aria-label=\{`Name of variable \$\{which\}`\}/,
      /aria-label=\{`Type of variable \$\{which\}`\}/,
      /aria-label=\{`Description of variable \$\{which\}`\}/,
      /aria-label=\{`Default value of variable \$\{which\}`\}/,
    ]) expect(src).toMatch(re)
    // No constant survivors.
    expect(/aria-label="Variable (name|type|description|default value|choices)"/.test(src)).toBe(false)
  })

  it('every call site passes rowIndex, or blank rows collide again', () => {
    for (const f of ['PromptEditFields.tsx', 'PromptForm.tsx', 'SnippetForm.tsx']) {
      const tag = code(f).match(/<VariableRow\b[\s\S]*?\/>/)
      expect(tag, `${f} should mount VariableRow`).toBeTruthy()
      expect(tag![0], `${f} must pass rowIndex`).toMatch(/rowIndex=\{i\}/)
    }
  })
})

describe('the controls that had no name at all', () => {
  it('PromptEditFields names all four (its Section is an injected prop that publishes nothing)', () => {
    const src = code('PromptEditFields.tsx')
    for (const n of ['Prompt title', 'Prompt description', 'Prompt template']) {
      expect(src).toContain(`aria-label="${n}"`)
    }
    // Tags is a ChipInput (the same primitive PromptForm uses), which resolves its
    // accessible name from the ariaLabel prop when no Field label wraps it —
    // promptTagsChipInput.test.tsx proves getByLabelText('Prompt tags') resolves.
    expect(src).toContain(`ariaLabel="Prompt tags"`)
  })

  it('PromptDetail Try-it inputs name themselves — the id was DANGLING', () => {
    const src = code('PromptDetail.tsx')
    expect(src).toMatch(/const label = `\$\{v\.name\} value`/)
    // All four branches (select / textarea / number / text), not just the one that was noticed.
    expect((src.match(/aria-label=\{label\}/g) ?? []).length).toBe(4)
    // And the id it relied on still has no label pointing at it, which is why aria-label is required.
    expect(/htmlFor=\{fid\}/.test(src), 'no label[for] exists — do not claim the id names it').toBe(false)
  })

  it('SnippetDetail preview inputs match PromptDetail wording', () => {
    expect(code('SnippetDetail.tsx')).toMatch(/aria-label=\{`\$\{v\.name\} value`\}/)
  })

  it('SnippetForm body is named, and keeps its ref', () => {
    const src = code('SnippetForm.tsx')
    expect(src).toContain('aria-label="Snippet content"')
    // The reason it is not a TextArea: the ref drives cursor-position insertion.
    expect(src).toMatch(/<textarea ref=\{taRef\}/)
  })
})

describe('the rail is not vacuously green', () => {
  it('every file this cycle touched exists and is scanned', () => {
    const files = readdirSync(PROMPTS)
    for (const f of ['VariableRow.tsx', 'PromptEditFields.tsx', 'PromptDetail.tsx', 'SnippetDetail.tsx', 'SnippetForm.tsx', 'PromptForm.tsx']) {
      expect(files, `${f} must exist`).toContain(f)
      expect(code(f).length).toBeGreaterThan(200)
    }
  })
})
