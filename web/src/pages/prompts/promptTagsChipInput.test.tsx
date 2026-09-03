import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'
import type { ReactNode } from 'react'

// ── The edit-mode Tags field must be able to hold more than one tag (#681) ───────────────────────
//
// PromptEditFields' Tags field was a raw input whose value was a parse of itself:
// `value={draft.tags.join(', ')}` with `onChange` doing split → trim → filter(Boolean). The
// filter dropped the empty string a trailing comma produces and the join re-rendered without the
// comma — so it was eaten the moment it was typed, and "red,green" became the single tag
// "redgreen". The sibling PromptForm already used ChipInput for the SAME field; this pins the
// edit form to that primitive (commits a chip on comma/Enter, drafts locally between commits).
//
// Same class as the VariableRow choices fix one file over: a controlled input whose value is
// derived from an unstable parse of partial input. The stateful wrapper below reproduces the real
// call-site feedback loop (parent folds onChange back into the rendered draft) — a stub onChange
// would hide exactly the defect this locks.

// PromptPreviewPane/SyntaxReference call the api on mount; the Tags rail doesn't care what they
// return, so every method resolves to null.
vi.mock('../../lib/api', () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve(null) }),
}))

import { PromptEditFields } from './PromptEditFields'
import { emptyDraft, type PromptDraft } from './PromptForm'

function Section({ label, children }: { label: string; children?: ReactNode }) {
  return (
    <div>
      <div>{label}</div>
      {children}
    </div>
  )
}

function Harness({ onDraft }: { onDraft?: (d: PromptDraft) => void }) {
  const [draft, setDraft] = useState<PromptDraft>(emptyDraft())
  return (
    <PromptEditFields draft={draft} Section={Section}
      onChange={(next) => { setDraft(next); onDraft?.(next) }} />
  )
}

describe('prompt Tags in edit mode (ChipInput, #681)', () => {
  it('typing "red,green" yields TWO tags, not the single tag "redgreen"', () => {
    const seen: PromptDraft[] = []
    const { getByLabelText } = render(<Harness onDraft={(d) => seen.push(d)} />)
    const field = getByLabelText('Prompt tags') as HTMLInputElement

    // "red" then the comma — ChipInput commits the chip on the comma key.
    fireEvent.change(field, { target: { value: 'red' } })
    fireEvent.keyDown(field, { key: ',' })
    expect(seen.at(-1)?.tags).toEqual(['red'])

    // "green" then Enter — second chip. The old parse produced ["redgreen"].
    fireEvent.change(field, { target: { value: 'green' } })
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(seen.at(-1)?.tags).toEqual(['red', 'green'])
  })

  it('a trailing comma cannot delete typed state (the original symptom)', () => {
    const seen: PromptDraft[] = []
    const { getByLabelText } = render(<Harness onDraft={(d) => seen.push(d)} />)
    const field = getByLabelText('Prompt tags') as HTMLInputElement
    // Draft text with a trailing comma commits cleanly (ChipInput strips it).
    fireEvent.change(field, { target: { value: 'blue,' } })
    fireEvent.blur(field)
    expect(seen.at(-1)?.tags).toEqual(['blue'])
    // And the committed chip survives further typing in the (now empty) draft field.
    fireEvent.change(field, { target: { value: 'x' } })
    expect(seen.at(-1)?.tags).toEqual(['blue'])
  })
})
