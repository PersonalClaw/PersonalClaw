import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { TextInput, TextArea, Select, NumberField, Field, Checkbox, ChipInput } from './forms'

// ── Standard-field scale invariant (design-system consistency S2/T2.3) ──────
// The form family grew a principled size (sm/md/lg) × surface (container/high/
// base) scale — the family variants the app's height/fill spread collapses onto.
// This test locks the two guarantees that make the growth a *codification, not a
// redesign*:
//
//  1. DEFAULTS ARE BYTE-IDENTICAL. The prior family shipped a single fixed
//     chrome (h-10 / bg-surface-container / text-[0.9375rem]); every existing
//     adopter relies on that exact className. A default-props render must still
//     produce it token-for-token, so the migration moves zero pixels.
//  2. THE SCALE IS ON-RAMP. Every size step uses a DESIGN.md-blessed type size
//     (0.8125rem / 0.9375rem) and a real height rung (h-8/h-9/h-10) — never the
//     off-ramp 0.875rem drift we normalize away. If someone edits a size table
//     to an off-ramp value, this reddens before the impeccable hook would.
//
// Behavioral/structural props (type, mono, leadingIcon, …) are grown in lockstep
// with the first real adopter, so they are tested when they land — not here.
//
// Rendered-className assertions (not source scans) because the invariant is
// what the browser sees — the composed class string, defaults included.

function classOf(el: HTMLElement | null): Set<string> {
  return new Set((el?.className ?? '').trim().split(/\s+/).filter(Boolean))
}
function expectTokens(el: HTMLElement | null, tokens: string[]) {
  const have = classOf(el)
  for (const t of tokens) expect(have, `missing "${t}" in: ${[...have].join(' ')}`).toContain(t)
}

describe('standard-field scale', () => {
  it('TextInput default render is the prior fixed chrome, byte-for-byte', () => {
    const { container } = render(<TextInput value="" onChange={() => {}} />)
    const input = container.querySelector('input')
    // The exact token set the family shipped before the scale existed.
    expectTokens(input, [
      'w-full', 'h-10', 'rounded-md', 'bg-surface-container', 'px-m',
      'text-on-surface', 'text-[0.9375rem]', 'placeholder:text-on-surface-low',
      'outline-none', 'focus:ring-2', 'focus:ring-inset', 'focus:ring-primary',
    ])
    // No explicit type attribute (native default = text) — byte-identical to the
    // pre-scale field, which set none either.
    expect(input?.getAttribute('type')).toBeNull()
  })

  it('TextInput size steps ride the height ladder and the on-ramp type sizes', () => {
    const sm = render(<TextInput value="" onChange={() => {}} size="sm" />).container.querySelector('input')
    const md = render(<TextInput value="" onChange={() => {}} size="md" />).container.querySelector('input')
    const lg = render(<TextInput value="" onChange={() => {}} size="lg" />).container.querySelector('input')
    expectTokens(sm, ['h-8', 'text-[0.8125rem]'])
    expectTokens(md, ['h-9', 'text-[0.8125rem]'])
    expectTokens(lg, ['h-10', 'text-[0.9375rem]'])
    // No size may introduce the off-ramp 0.875rem (14px) drift.
    for (const el of [sm, md, lg]) expect(classOf(el)).not.toContain('text-[0.875rem]')
  })

  it('TextInput surface steps swap only the fill token', () => {
    const container_ = render(<TextInput value="" onChange={() => {}} surface="container" />).container.querySelector('input')
    const high = render(<TextInput value="" onChange={() => {}} surface="high" />).container.querySelector('input')
    const base = render(<TextInput value="" onChange={() => {}} surface="base" />).container.querySelector('input')
    expect(classOf(container_)).toContain('bg-surface-container')
    expect(classOf(high)).toContain('bg-surface-high')
    expect(classOf(base)).toContain('bg-surface')
  })

  it('TextInput leadingIcon adds the canonical inset and wraps the icon', () => {
    const { container } = render(
      <TextInput value="" onChange={() => {}} leadingIcon={<svg data-testid="glyph" />} />,
    )
    const input = container.querySelector('input')
    // pl-9 clears the fixed left-3 icon; pr-m keeps the canonical right pad —
    // and px-m must NOT also emit (that padding-inline would fight pl-9).
    expectTokens(input, ['pl-9', 'pr-m'])
    expect(classOf(input)).not.toContain('px-m')
    // The icon is pinned at left-3 with the muted tone, pointer-events off.
    const iconSpan = container.querySelector<HTMLElement>('span.absolute')
    expectTokens(iconSpan, ['left-3', 'text-on-surface-low', 'pointer-events-none'])
    expect(iconSpan?.querySelector('[data-testid="glyph"]')).not.toBeNull()
    // Default (no icon) stays byte-identical: px-m, no pl-9.
    const plain = render(<TextInput value="" onChange={() => {}} />).container.querySelector('input')
    expect(classOf(plain)).toContain('px-m')
    expect(classOf(plain)).not.toContain('pl-9')
  })

  it('TextInput ariaLabel survives a name (a name is not an accessible name)', () => {
    // The autofill-suppressed picker (DependencyEditor) passes BOTH a random name
    // and an explicit ariaLabel; the name must never suppress the label, or the
    // control loses its accessible name for screen readers.
    const input = render(
      <TextInput value="" onChange={() => {}} name="dep-search-x" ariaLabel="Find a prerequisite task" />,
    ).container.querySelector('input')
    expect(input?.getAttribute('aria-label')).toBe('Find a prerequisite task')
    expect(input?.getAttribute('name')).toBe('dep-search-x')
  })

  it('TextArea default render is the prior fixed chrome', () => {
    const { container } = render(<TextArea value="" onChange={() => {}} />)
    expectTokens(container.querySelector('textarea'), [
      'w-full', 'rounded-md', 'bg-surface-container', 'text-[0.9375rem]', 'resize-y',
    ])
  })

  it('TextArea size axis: sm/md are the dense on-ramp size, lg the page-form size', () => {
    const sm = render(<TextArea value="" onChange={() => {}} size="sm" />).container.querySelector('textarea')
    const md = render(<TextArea value="" onChange={() => {}} size="md" />).container.querySelector('textarea')
    const lg = render(<TextArea value="" onChange={() => {}} size="lg" />).container.querySelector('textarea')
    expectTokens(sm, ['text-[0.8125rem]'])
    expectTokens(md, ['text-[0.8125rem]'])
    expectTokens(lg, ['text-[0.9375rem]'])
    // No size may introduce the off-ramp 0.875rem (14px) drift.
    for (const el of [sm, md, lg]) expect(classOf(el)).not.toContain('text-[0.875rem]')
  })

  it('TextArea mono stays byte-identical (font-mono + dense text, regardless of size)', () => {
    // The mono branch protects every existing mono adopter: it appends
    // `font-mono text-[0.8125rem]` AFTER the size text, so the effective size is
    // always dense mono — the pre-scale behavior — even at the default lg.
    const monoLg = render(<TextArea value="" onChange={() => {}} mono />).container.querySelector('textarea')
    expectTokens(monoLg, ['font-mono', 'text-[0.8125rem]'])
  })

  it('Select default render is the prior fixed chrome and carries options', () => {
    const { container } = render(
      <Select value="a" onChange={() => {}} options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]} />,
    )
    expectTokens(container.querySelector('select'), [
      'w-full', 'h-10', 'appearance-none', 'rounded-md', 'bg-surface-container', 'text-[0.9375rem]',
    ])
    expect(container.querySelectorAll('option')).toHaveLength(2)
  })
})

// ── NumberField: the canonical numeric stepper (design-system consistency) ────
// The settings panels hand-rolled this stepper THREE times verbatim — same chrome
// AND the same fiddly clamp-on-commit behavior. This locks the one home for that
// role: the byte-identical chrome (so the migrated call-sites moved zero pixels),
// the width axis the panels vary (w-24 default, w-20 opt), and the commit contract
// (clamp on blur/Enter, revert empty/NaN, commit only on change) that a pure
// chrome primitive would have quietly dropped.
describe('NumberField', () => {
  it('renders the prior hand-rolled chrome, byte-for-byte (w-24 default)', () => {
    const input = render(<NumberField value={3} onChange={() => {}} />).container.querySelector('input')
    expectTokens(input, [
      'h-8', 'w-24', 'rounded-md', 'bg-surface-high', 'px-2', 'text-right',
      'text-[0.8125rem]', 'text-on-surface', 'tabular-nums', 'outline-none',
      'focus:ring-2', 'focus:ring-inset', 'focus:ring-primary',
    ])
    expect(input?.getAttribute('type')).toBe('number')
  })

  it('swaps only the width token when width is overridden', () => {
    const input = render(<NumberField value={3} onChange={() => {}} width="w-20" />).container.querySelector('input')
    expect(classOf(input)).toContain('w-20')
    expect(classOf(input)).not.toContain('w-24')
  })

  it('clamps to [min,max] and commits on blur, only when changed', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={5} min={0} max={10} onChange={onChange} />)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '42' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith(10) // clamped to max
    expect(input.value).toBe('10')
  })

  it('does not commit when the clamped value is unchanged', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={5} min={0} max={10} onChange={onChange} />)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '5' } })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('reverts an empty or NaN entry to the last good value without committing', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={7} onChange={onChange} />)
    const input = container.querySelector('input')!
    fireEvent.change(input, { target: { value: '' } })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
    expect(input.value).toBe('7')
  })

  it('commits on Enter (blurs the input)', () => {
    const onChange = vi.fn()
    const { container } = render(<NumberField value={1} min={0} max={100} onChange={onChange} />)
    const input = container.querySelector('input')!
    input.focus() // Enter calls input.blur(); jsdom only fires the blur event if focused.
    fireEvent.change(input, { target: { value: '9' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith(9)
  })

  it('resyncs local state when the committed value changes externally', () => {
    const { container, rerender } = render(<NumberField value={2} onChange={() => {}} />)
    const input = container.querySelector('input')!
    expect(input.value).toBe('2')
    rerender(<NumberField value={8} onChange={() => {}} />)
    expect(input.value).toBe('8')
  })

  it('takes its accessible name from an explicit ariaLabel', () => {
    const input = render(<NumberField value={1} onChange={() => {}} ariaLabel="Retention (days)" />).container.querySelector('input')
    expect(input?.getAttribute('aria-label')).toBe('Retention (days)')
  })

  it('claims a wrapping Field label via aria-labelledby when it has no ariaLabel', () => {
    const { container } = render(
      <Field label="Warm pool size"><NumberField value={1} onChange={() => {}} /></Field>,
    )
    const input = container.querySelector('input')!
    const labelledby = input.getAttribute('aria-labelledby')
    expect(labelledby).toBeTruthy()
    expect(input.getAttribute('aria-label')).toBeNull()
    // Attribute selector, not `#id`: useId() ids contain colons (invalid in a #selector).
    expect(container.querySelector(`[id="${labelledby}"]`)?.textContent).toBe('Warm pool size')
  })
})

// ── ChipInput: the accessible name of the draft field (#523) ──────────────────
// ChipInput hardcoded its fallback name to 'Add a tag' and took no ariaLabel, so
// its three non-tag call-sites (entity aliases, notification keywords, and the
// two bare knowledge tag fields' non-tag siblings) announced "Add a tag".
//
// The precedence is three-deep, and which rung a call-site lands on depends on
// WHICH `Field` wraps it — the load-bearing detail behind the bug:
//   • `ui/forms`' Field publishes a label id via FieldLabelCtx → aria-labelledby
//     wins, and ariaLabel is correctly not even emitted.
//   • `pages/settings/settingsUI`' Field is a plain div with NO context provider,
//     so a ChipInput inside one is effectively bare — labelId is undefined and
//     the fallback literal is the ONLY name. That is why the aliases/keywords
//     fields announced "Add a tag" despite looking labelled on screen.
// So ariaLabel is the rung that rescues the settingsUI-Field and bare sites, and
// is deliberately inert under a ui/forms Field.

describe('ChipInput accessible name', () => {
  const nameOf = (c: HTMLElement) => {
    const input = c.querySelector('input')!
    const lb = input.getAttribute('aria-labelledby')
    return {
      labelledby: lb,
      ariaLabel: input.getAttribute('aria-label'),
      // What a screen reader actually announces: aria-labelledby's resolved text
      // outranks aria-label.
      announced: lb ? (c.querySelector(`[id="${lb}"]`)?.textContent ?? null) : input.getAttribute('aria-label'),
    }
  }

  it('falls back to "Add a tag" when bare and given no ariaLabel (preserved behavior)', () => {
    // The two knowledge tag fields render outside any Field — genuinely tags, so
    // the literal is correct there and must not shift.
    const r = nameOf(render(<ChipInput values={[]} onChange={() => {}} />).container)
    expect(r.labelledby).toBeNull()
    expect(r.announced).toBe('Add a tag')
  })

  it('an explicit ariaLabel replaces the hardcoded literal when there is no Field label', () => {
    const r = nameOf(render(<ChipInput values={[]} onChange={() => {}} ariaLabel="Add an alias" />).container)
    expect(r.ariaLabel).toBe('Add an alias')
    expect(r.announced).toBe('Add an alias')
  })

  it("a ui/forms Field's published label still outranks ariaLabel", () => {
    // Existing precedence is unchanged: the visible label is the better name, so
    // aria-label is not emitted at all (two competing names would be worse).
    const r = nameOf(render(
      <Field label="Tags"><ChipInput values={[]} onChange={() => {}} ariaLabel="Add an alias" /></Field>,
    ).container)
    expect(r.labelledby).toBeTruthy()
    expect(r.ariaLabel).toBeNull()
    expect(r.announced).toBe('Tags')
  })

  it('names the field even once a chip exists and blanks the placeholder', () => {
    // placeholder={values.length ? '' : placeholder} — with a chip present the
    // placeholder is gone, so the accessible name is the ONLY remaining name.
    const { container } = render(
      <ChipInput values={['nickname']} onChange={() => {}} placeholder="Alias, then Enter" ariaLabel="Add an alias" />,
    )
    expect(container.querySelector('input')!.getAttribute('placeholder')).toBe('')
    expect(nameOf(container).announced).toBe('Add an alias')
  })
})

// ── Checkbox ──────────────────────────────────────────────────────────────────
// The propagation guard is the whole reason this is a primitive: these live inside
// clickable list rows, and a tick that also activates the row is a bug every call
// site would otherwise have to remember not to write.

describe('Checkbox', () => {
  it('reports the next boolean', () => {
    const onChange = vi.fn()
    render(<Checkbox checked={false} onChange={onChange} ariaLabel="Select the thing" />)
    fireEvent.click(screen.getByLabelText('Select the thing'))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('does not activate the clickable row it sits inside', () => {
    const rowClick = vi.fn()
    const onChange = vi.fn()
    render(
      <div onClick={rowClick}>
        <Checkbox checked={false} onChange={onChange} ariaLabel="Select row" />
      </div>,
    )
    fireEvent.click(screen.getByLabelText('Select row'))
    expect(onChange).toHaveBeenCalled()
    expect(rowClick).not.toHaveBeenCalled()
  })

  it('carries an accessible name', () => {
    render(<Checkbox checked onChange={() => {}} ariaLabel="Select chat about pears" />)
    const box = screen.getByLabelText('Select chat about pears') as HTMLInputElement
    expect(box.type).toBe('checkbox')
    expect(box.checked).toBe(true)
  })
})
