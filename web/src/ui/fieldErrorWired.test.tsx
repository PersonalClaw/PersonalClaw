import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, fireEvent, render } from '@testing-library/react'
import { useEffect, useState, type ReactElement } from 'react'
import { tokenValue } from '../design/tokenSheet'
import { Checkbox, ChipInput, DateInput, Field, FieldError, FieldErrorProvider, NumberField, Select, TextArea, TextInput } from './forms'

// ── AN ERROR BELONGS TO ITS CONTROL ─────────────────────────────────────────────────────────
//
// A Field publishes three ids: its label's (the control's NAME), its hint's and its error's (the
// control's DESCRIPTION, hint first). Before the third one existed, no form-family control set
// `aria-invalid`, and a `FieldError` beside a control announced itself once and then belonged to
// nothing: a screen-reader user who tabbed back to the field heard its name and its hint and not
// why it was refused.
//
// Every assertion here RESOLVES `aria-describedby` against the rendered document and compares
// text, as `fieldHintConsumed.test.tsx` does: an attribute that names a missing or wrong element
// passes a presence check while describing nothing.

/** The control's description, as accname builds it from `aria-describedby`: each id's text, in
 *  order. A dangling id surfaces loudly instead of reading as an empty description. */
function description(el: Element | null): string {
  const ids = el?.getAttribute('aria-describedby')
  if (!ids) return ''
  return ids.split(/\s+/).filter(Boolean).map((id) => {
    const target = document.getElementById(id)
    return target ? (target.textContent ?? '').replace(/\s+/g, ' ').trim() : `DANGLING:${id}`
  }).join(' | ')
}

/** The control's name, from the id `aria-labelledby` names or its own `aria-label`. */
function nameOf(el: Element | null): string {
  const by = el?.getAttribute('aria-labelledby')
  if (by) return document.getElementById(by)?.textContent?.trim() ?? `DANGLING:${by}`
  return el?.getAttribute('aria-label') ?? ''
}

const noop = () => {}
const HINT = 'Lowercase letters, digits and dashes.'
const ERROR = 'A name can’t contain spaces.'

/** Each family member, the element that carries its semantics, and how to render it. `Checkbox`
 *  claims the error and not the hint: it is a per-row tick, and a hint would describe every row
 *  identically (`fieldHintConsumed.test.tsx` records why). */
const FAMILY: Array<{ name: string; control: ReactElement; pick: (c: HTMLElement) => Element | null; noHint?: true }> = [
  { name: 'TextInput', control: <TextInput value="my name" onChange={noop} />, pick: (c) => c.querySelector('input') },
  { name: 'TextArea', control: <TextArea value="" onChange={noop} />, pick: (c) => c.querySelector('textarea') },
  { name: 'Select', control: <Select value="a" onChange={noop} options={[{ value: 'a', label: 'A' }]} />, pick: (c) => c.querySelector('select') },
  { name: 'NumberField', control: <NumberField value={3} onChange={noop} />, pick: (c) => c.querySelector('input[type="number"]') },
  { name: 'DateInput', control: <DateInput value="" onChange={noop} />, pick: (c) => c.querySelector('input[type="date"]') },
  { name: 'ChipInput', control: <ChipInput values={[]} onChange={noop} />, pick: (c) => c.querySelector('input') },
  { name: 'Checkbox', control: <Checkbox checked={false} onChange={noop} ariaLabel="Agree" />, pick: (c) => c.querySelector('input[type="checkbox"]'), noHint: true },
]

describe('Field wires its error to the control it wraps', () => {
  for (const { name, control, pick, noHint } of FAMILY) {
    const described = (...parts: string[]) => parts.filter((p) => !(noHint && p === HINT)).join(' | ')
    it(`${name}: an error makes it aria-invalid and its description`, () => {
      const { container } = render(<Field label="Name" error={ERROR}>{control}</Field>)
      const el = pick(container)
      expect(el?.getAttribute('aria-invalid')).toBe('true')
      expect(description(el)).toBe(ERROR)
    })

    it(`${name}: with a hint and an error, both describe it — the hint first`, () => {
      const { container } = render(<Field label="Name" hint={HINT} error={ERROR}>{control}</Field>)
      expect(description(pick(container))).toBe(described(HINT, ERROR))
    })

    it(`${name}: without an error, there is no aria-invalid at all`, () => {
      // Absent, not "false": an explicit false is announced by some screen readers, and a field
      // that has done nothing wrong should say nothing about validity.
      const { container } = render(<Field label="Name" hint={HINT}>{control}</Field>)
      const el = pick(container)
      expect(el?.hasAttribute('aria-invalid')).toBe(false)
      expect(description(el)).toBe(described(HINT))
    })
  }

  it('the error is a FieldError: it still announces, in the danger tone, under the hint', () => {
    const { container } = render(<Field label="Name" hint={HINT} error={ERROR}><TextInput value="" onChange={noop} /></Field>)
    const alert = container.querySelector('[role="alert"]')
    expect(alert?.textContent).toBe(ERROR)
    expect(alert?.className).toContain('text-danger')
    // Under the hint, so the hint does not move when an error comes and goes.
    const hint = [...container.querySelectorAll('p')].find((p) => p.textContent === HINT)!
    expect(hint.compareDocumentPosition(alert!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('an empty error publishes no id, so nothing points at an element that is not there', () => {
    for (const error of ['', null, undefined, false]) {
      const { container, unmount } = render(<Field label="Name" error={error}><TextInput value="" onChange={noop} /></Field>)
      const input = container.querySelector('input')
      expect(input?.hasAttribute('aria-invalid')).toBe(false)
      expect(input?.hasAttribute('aria-describedby')).toBe(false)
      expect(container.querySelector('[role="alert"]')).toBeNull()
      unmount()
    }
  })

  it('an advisory is a hint, not an error: the control stays valid', () => {
    // The schedule interval floor is the live case: the API accepts the value and only warns, so
    // the control must not announce "invalid" for a value that works.
    const { container } = render(
      <Field label="Every" hint="Under 15 minutes runs the model often — the save still goes through.">
        <NumberField value={5} onChange={noop} />
      </Field>,
    )
    expect(container.querySelector('input')?.hasAttribute('aria-invalid')).toBe(false)
  })

  it('a layout of its own wires an error through FieldErrorProvider and FieldError’s id', () => {
    // The shape of a control with no Field around it (a list editor's add box): the provider
    // publishes the id, the FieldError carries it.
    const { container } = render(
      <FieldErrorProvider value="host-error">
        <TextInput value="not a host" onChange={noop} ariaLabel="Add a host to allowed hosts" />
        <FieldError id="host-error">That isn’t a hostname.</FieldError>
      </FieldErrorProvider>,
    )
    const input = container.querySelector('input')
    expect(input?.getAttribute('aria-invalid')).toBe('true')
    expect(description(input)).toBe('That isn’t a hostname.')
    expect(nameOf(input)).toBe('Add a host to allowed hosts')
  })
})

// ── The acceptance matrix ───────────────────────────────────────────────────────────────────
//
// One case each over the Field family: long labels, delayed and server-side validation, native
// autofill, 200% text zoom, forced colours, high contrast, and editor or terminal surfaces. Every
// error stays associated with its control and is announced in words, never by colour alone.

/** A class that clips text instead of letting it wrap. At 200% text zoom a clipped label, hint or
 *  error loses words; a wrapping one only grows. */
const CLIPS = /^(truncate|text-ellipsis|whitespace-nowrap|line-clamp-\d+|overflow-hidden|overflow-clip|h-\d+|max-h-\d+|absolute|fixed)$/

function clippingClasses(el: Element): string[] {
  return (el.getAttribute('class') ?? '').split(/\s+/).filter((c) => CLIPS.test(c))
}

/** Remove every colour a stylesheet could carry — what a forced-colours mode does, and what a
 *  reader who cannot tell red from grey sees. What is left is words and relationships. */
function stripColour(root: HTMLElement) {
  const COLOUR = /^(?:[a-z-]+:)*(?:text|bg|ring|border|outline|accent|fill|stroke|from|to|via|placeholder|caret|decoration|shadow)-/
  for (const el of [root, ...root.querySelectorAll('*')]) {
    const kept = (el.getAttribute('class') ?? '').split(/\s+/).filter((c) => c && !COLOUR.test(c))
    el.setAttribute('class', kept.join(' '))
    ;(el as HTMLElement).style?.removeProperty('color')
    ;(el as HTMLElement).style?.removeProperty('background')
    ;(el as HTMLElement).style?.removeProperty('background-color')
  }
}

afterEach(() => { vi.useRealTimers() })

describe('acceptance matrix', () => {
  it('long labels: the whole label names the control and nothing clips it, the error included', () => {
    const label = 'The address the gateway calls back on when a remote runner finishes a job it was handed by an automation that started outside working hours'
    const longError = 'This address is refused because it resolves to a host on the deny list, which the security settings keep for addresses the gateway must never call, and a callback is a call.'
    const { container } = render(<Field label={label} hint={HINT} error={longError}><TextInput value="http://10.0.0.1" onChange={noop} /></Field>)
    const input = container.querySelector('input')
    expect(nameOf(input)).toBe(label)
    expect(description(input)).toBe(`${HINT} | ${longError}`)
    const labelEl = document.getElementById(input!.getAttribute('aria-labelledby')!)!
    for (const el of [labelEl, labelEl.parentElement!, container.querySelector('[role="alert"]')!]) {
      expect(clippingClasses(el), `clips: ${el.outerHTML.slice(0, 80)}`).toEqual([])
    }
  })

  it('delayed validation: an error that settles after typing stops is still wired', () => {
    vi.useFakeTimers()
    function Debounced() {
      const [value, setValue] = useState('')
      const [error, setError] = useState('')
      useEffect(() => {
        const t = setTimeout(() => setError(/\s/.test(value) ? ERROR : ''), 400)
        return () => clearTimeout(t)
      }, [value])
      return <Field label="Name" hint={HINT} error={error}><TextInput value={value} onChange={setValue} /></Field>
    }
    const { container } = render(<Debounced />)
    const input = () => container.querySelector('input')
    fireEvent.change(input()!, { target: { value: 'two words' } })
    // Before the check settles the field has said nothing about validity.
    expect(input()?.hasAttribute('aria-invalid')).toBe(false)
    act(() => { vi.advanceTimersByTime(400) })
    expect(input()?.getAttribute('aria-invalid')).toBe('true')
    expect(description(input())).toBe(`${HINT} | ${ERROR}`)
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(ERROR)
    fireEvent.change(input()!, { target: { value: 'one-word' } })
    act(() => { vi.advanceTimersByTime(400) })
    expect(input()?.hasAttribute('aria-invalid')).toBe(false)
    expect(description(input())).toBe(HINT)
  })

  it('server-side validation: the refusal the save returns becomes the control’s description', async () => {
    const save = vi.fn(async (_name: string): Promise<void> => {
      throw new Error('A project with that name already exists.')
    })
    function Form() {
      const [name, setName] = useState('notes')
      const [error, setError] = useState('')
      const submit = async () => {
        try { await save(name); setError('') } catch (e) { setError((e as Error).message) }
      }
      return (
        <form onSubmit={(e) => { e.preventDefault(); void submit() }}>
          <Field label="Project name" error={error}><TextInput value={name} onChange={setName} /></Field>
          <button type="submit">Create</button>
        </form>
      )
    }
    const { container, getByText } = render(<Form />)
    await act(async () => { fireEvent.click(getByText('Create')) })
    const input = container.querySelector('input')
    expect(save).toHaveBeenCalledWith('notes')
    expect(input?.getAttribute('aria-invalid')).toBe('true')
    expect(description(input)).toBe('A project with that name already exists.')
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('A project with that name already exists.')
  })

  it('native autofill: a filled field keeps its token, its name and its error wiring', () => {
    // Autofill writes the value and fires `input`/`change` with no key events, so the check runs off
    // the value, never off a keystroke.
    function Email() {
      const [value, setValue] = useState('')
      const error = value && !value.includes('@') ? 'Enter an email address.' : ''
      return <Field label="Email" error={error}><TextInput value={value} onChange={setValue} autoComplete="email" /></Field>
    }
    const { container } = render(<Email />)
    const input = () => container.querySelector('input')!
    expect(input().getAttribute('autocomplete')).toBe('email')
    expect(input().id).not.toBe('')
    fireEvent.change(input(), { target: { value: 'owner.example.com' } })
    expect(input().getAttribute('aria-invalid')).toBe('true')
    expect(description(input())).toBe('Enter an email address.')
    fireEvent.change(input(), { target: { value: 'owner@example.com' } })
    expect(input().hasAttribute('aria-invalid')).toBe(false)
    expect(input().getAttribute('autocomplete')).toBe('email')
    expect(nameOf(input())).toBe('Email')
  })

  it('200% text zoom: the label, hint and error reflow in normal flow instead of clipping', () => {
    const { container } = render(
      <Field label="Name" hint={HINT} error={ERROR}><TextArea value="" onChange={noop} /></Field>,
    )
    const root = container.firstElementChild!
    const label = container.querySelector('[id]')!
    const hint = [...container.querySelectorAll('p')].find((p) => p.textContent === HINT)!
    const error = container.querySelector('[role="alert"]')!
    for (const el of [root, label.parentElement!, label, hint, error]) {
      expect(clippingClasses(el), `clips at zoom: ${el.outerHTML.slice(0, 80)}`).toEqual([])
    }
    // Sized by the type role (rem), which is what scales with the reader's text size.
    expect(error.getAttribute('data-type')).toBe('body-s')
  })

  it('forced colours: with every colour removed, the error is still words tied to the control', () => {
    // 🔑 The case the approved update names: "announced without relying on color". It goes red if
    // the error loses its text or the control loses its description link.
    for (const { name, control, pick, noHint } of FAMILY) {
      const { container, unmount } = render(<Field label="Name" hint={HINT} error={ERROR}>{control}</Field>)
      stripColour(container)
      const el = pick(container)
      expect(el?.getAttribute('aria-invalid'), name).toBe('true')
      expect(description(el), name).toBe(noHint ? ERROR : `${HINT} | ${ERROR}`)
      const alert = container.querySelector('[role="alert"]')
      expect(alert?.textContent, name).toBe(ERROR)
      expect(alert?.className ?? '', name).not.toMatch(/danger/)
      unmount()
    }
  })

  it('high contrast: the error tone reads at AA on every surface a Field sits on, in both modes', () => {
    // The value each mode paints, as the sheets resolve it (`design/tokenSheet.ts`).
    const token = (mode: 'dark' | 'light', name: string) => tokenValue(`--color-${name}`, mode)
    const lum = (hex: string) => {
      const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
      return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    }
    const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
    for (const mode of ['dark', 'light'] as const) {
      const danger = token(mode, 'danger')
      for (const surface of ['surface', 'surface-container', 'surface-high']) {
        expect(ratio(danger, token(mode, surface)), `${mode} danger on ${surface}`).toBeGreaterThanOrEqual(4.5)
      }
    }
    // And the error is never the hint in a different colour: its own element, its own words, and
    // the one that announces.
    const { container } = render(<Field label="Name" hint={HINT} error={ERROR}><TextInput value="" onChange={noop} /></Field>)
    const paragraphs = [...container.querySelectorAll('p')]
    expect(paragraphs.map((p) => [p.textContent, p.getAttribute('role')])).toEqual([[HINT, null], [ERROR, 'alert']])
  })

  it('editor and terminal surfaces: a mono, shortcut-driven field keeps its error and its keys', () => {
    const run = vi.fn()
    const { container } = render(
      <>
        <Field label="Payload" hint="JSON sent with the request." error="Unexpected token at line 2.">
          <TextArea mono value={'{\n  "a": }'} onChange={noop} onKeyDown={(e) => { if (e.metaKey && e.key === 'Enter') run() }} />
        </Field>
        <Field label="Command" error="The command is empty.">
          <TextInput mono surface="base" value="" onChange={noop} />
        </Field>
      </>,
    )
    const editor = container.querySelector('textarea')!
    expect(editor.getAttribute('aria-invalid')).toBe('true')
    expect(description(editor)).toBe('JSON sent with the request. | Unexpected token at line 2.')
    fireEvent.keyDown(editor, { key: 'Enter', metaKey: true })
    expect(run).toHaveBeenCalledOnce()
    const command = container.querySelector('input')!
    expect(command.className).toContain('font-mono')
    expect(command.getAttribute('aria-invalid')).toBe('true')
    expect(description(command)).toBe('The command is empty.')
  })
})
