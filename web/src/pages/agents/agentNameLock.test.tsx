import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'

// ── The name lock must be VISIBLE, not a no-op handler (#665) ────────────────────────────────────
//
// The edit form locked the Name field by discarding writes inside onChange: the field was
// focusable, carried no disabled/readOnly/aria cue, was pixel-identical to the editable
// Description below it, and silently threw keystrokes away. Locking the name IS correct (a
// rename would orphan every reference to the agent); the defect was that the lock was
// invisible. TextInput has since grown disabled + disabledReason (the primitive gap that
// forced the no-op hack) — these rails pin that the form actually USES them.

vi.mock('../../lib/api', () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve(null) }),
}))
vi.mock('../../lib/agents', () => ({
  useActiveChatModelOptions: () => ({ options: [], loading: false }),
}))

import { AgentForm, emptyDraft, type AgentDraft } from './AgentForm'

function Harness({ locked }: { locked: boolean }) {
  const [draft, setDraft] = useState<AgentDraft>({ ...emptyDraft(), name: 'research-assistant' })
  return <AgentForm draft={draft} onChange={setDraft} nameLocked={locked} />
}

const nameField = (container: HTMLElement) =>
  container.querySelector('input[placeholder="research-assistant"]') as HTMLInputElement

describe('agent Name lock is visible and real (#665)', () => {
  it('locked: the field is natively disabled, carries the reason, and the hint explains it', () => {
    const { container, getByText } = render(<Harness locked />)
    const field = nameField(container)
    expect(field.disabled).toBe(true) // out of the tab order, announced as disabled
    expect(field.title).toBe('Names are fixed after creation')
    // The Field hint explains WHY, replacing the format hint that invited typing.
    expect(getByText(/a rename would orphan every reference/)).toBeTruthy()
  })

  it('locked: typing cannot move the draft (the primitive blocks it, not a silent handler)', async () => {
    // userEvent respects `disabled` the way a real browser does (a disabled input
    // receives no input events); fireEvent.change would force-write the DOM node
    // and measure a jsdom artifact instead of the product behavior.
    const userEvent = (await import('@testing-library/user-event')).default
    const seen: string[] = []
    function Spy() {
      const [draft, setDraft] = useState<AgentDraft>({ ...emptyDraft(), name: 'research-assistant' })
      return <AgentForm draft={draft} onChange={(d) => { seen.push(d.name); setDraft(d) }} nameLocked />
    }
    const { container } = render(<Spy />)
    const field = nameField(container)
    await userEvent.type(field, 'zz-probe')
    expect(seen).toEqual([]) // no draft mutation reached the parent
    expect(field.value).toBe('research-assistant')
  })

  it('unlocked: typing still works and still normalizes to the slug shape', () => {
    const { container, getByText } = render(<Harness locked={false} />)
    const field = nameField(container)
    expect(field.disabled).toBe(false)
    fireEvent.change(field, { target: { value: 'My Cool Agent!' } })
    expect(field.value).toBe('my-cool-agent-')
    // The format hint is back for the editable case.
    expect(getByText(/Lowercase, hyphenated/)).toBeTruthy()
  })
})
