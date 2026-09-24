/** Genui component actions: DUAL PAYLOADS and producer routing (AMBIENT-SURFACES §5.4).
 *
 *  The clause that matters most here — and the one a naive test misses — is that a chat-born
 *  widget action becomes a turn showing the HUMAN message, **not raw JSON**. So every
 *  transcript assertion below is a PAIR: the human string is present AND the machine payload
 *  is absent from what the transcript renders. A single positive assertion ("the label is
 *  there") passes while the JSON sits right next to it, which is exactly the defect.
 *
 *  Each producer is driven through the REAL component (a `Form`'s submit, a `Button`'s click),
 *  never by calling the router directly: the router working while nothing calls it is the
 *  failure shape this repo keeps finding. */
import { render, act, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { GenUiWidget } from './GenUiWidget'
import { GenUiHostCtx, composeDualPayload, humanizeAction } from './actions'
import { registerCoreGenUiComponents } from './components'
import { WIDGET_ACTION_EVENT } from '../widget/actionTurn'

const resumeWorkflowRun = vi.fn(async () => ({ ok: true, approved: true }))
const tileWidgetAction = vi.fn(async () => ({ ok: true, outcome: 'tile-refired' }))

vi.mock('../../lib/api', () => ({
  api: {
    resumeWorkflowRun: (...a: unknown[]) => resumeWorkflowRun(...(a as [])),
    tileWidgetAction: (...a: unknown[]) => tileWidgetAction(...(a as [])),
  },
}))

registerCoreGenUiComponents()

/** Every `ne:widget-action` the widget published — the chat producer's whole output. */
const published: { text: string; label?: string }[] = []
function onPublished(e: Event) {
  const d = (e as CustomEvent).detail || {}
  published.push({ text: String(d.text ?? ''), label: d.label })
}

beforeEach(() => {
  published.length = 0
  resumeWorkflowRun.mockClear()
  tileWidgetAction.mockClear()
  window.addEventListener(WIDGET_ACTION_EVENT, onPublished)
})
afterEach(() => window.removeEventListener(WIDGET_ACTION_EVENT, onPublished))

const FORM = 'f = Form(title: "Log an expense", fields: ["amount", "vendor"], action: "log_expense", submit: "Log expense")'
const BUTTON = 'b = Button(label: "Refresh sales", action: "refresh")'

/** Type into a CONTROLLED input. `fireEvent.change` (not a hand-dispatched `input` event)
 *  because React tracks the node's value on its own and ignores a raw assignment — a
 *  hand-rolled version of this silently submitted an empty form. */
function type(el: HTMLElement, value: string) {
  fireEvent.change(el, { target: { value } })
}

// ── the pair itself ─────────────────────────────────────────────────────────

describe('dual payloads', () => {
  it('produces a machine message and a human message that DIFFER', () => {
    const dual = composeDualPayload({
      action: 'log_expense',
      label: 'Log expense',
      payload: { amount: '12.40', vendor: 'Acme' },
    })!
    expect(dual.humanFriendlyMessage).toBe('Log expense')
    expect(dual.llmFriendlyMessage).toContain('"amount":"12.40"')
    // The load-bearing negative: the two must not be the same string, or the pair is
    // decoration and the transcript shows the payload.
    expect(dual.humanFriendlyMessage).not.toContain('{')
    expect(dual.humanFriendlyMessage).not.toBe(dual.llmFriendlyMessage)
  })

  it('keeps the ONE turn dialect on the machine half', () => {
    const dual = composeDualPayload({ action: 'refresh', label: 'Refresh' })!
    expect(dual.llmFriendlyMessage.startsWith('[UI] ')).toBe(true)
  })

  it('humanizes an action when no label was declared, and never yields an empty label', () => {
    expect(humanizeAction('log_expense')).toBe('Log expense')
    expect(humanizeAction('')).toBe('Action')
    expect(composeDualPayload({ action: 'log_expense' })!.humanFriendlyMessage).toBe('Log expense')
  })

  it('refuses rather than throws on an unserializable payload', () => {
    const cyclic: Record<string, unknown> = { a: 1 }
    cyclic.self = cyclic
    expect(composeDualPayload({ action: 'x', payload: cyclic })).toBeNull()
  })

  it('names the saved artifact so a living view refreshes in place', () => {
    const dual = composeDualPayload({
      action: 'refresh',
      label: 'Refresh',
      live: { saved: true, slug: 'sales' },
    })!
    expect(dual.llmFriendlyMessage).toContain('refresh artifact "sales" in place')
    expect(dual.humanFriendlyMessage).toBe('Refresh')
  })
})

// ── chat-born: the next user turn shows the human message ───────────────────

describe('a chat-born widget action', () => {
  it('publishes the machine text and the human LABEL, so the bubble can show the label', async () => {
    const { getByLabelText, getByText } = render(<GenUiWidget content={FORM} title="Expense" />)
    type(getByLabelText('Amount'), '12.40')
    type(getByLabelText('Vendor'), 'Acme')
    act(() => { getByText('Log expense').closest('button')!.click() })

    await waitFor(() => expect(published.length).toBe(1))
    const turn = published[0]
    expect(turn.label).toBe('Log expense')
    expect(turn.text).toContain('"amount":"12.40"')
    // 🪤 THE PAIR. What the transcript renders is `label`; what the model reads is `text`. A
    // label carrying the payload is the defect, and asserting only the first line would miss
    // it entirely.
    expect(turn.label).not.toContain('amount')
    expect(turn.label).not.toContain('{')
  })

  it('sends every declared field, present or empty', async () => {
    const { getByLabelText, getByText } = render(<GenUiWidget content={FORM} title="Expense" />)
    type(getByLabelText('Amount'), '9')
    act(() => { getByText('Log expense').closest('button')!.click() })
    await waitFor(() => expect(published.length).toBe(1))
    expect(published[0].text).toContain('"vendor":""')
  })

  it('a Button sends its visible label as the human message', async () => {
    const { getByText } = render(<GenUiWidget content={BUTTON} title="Sales" />)
    act(() => { getByText('Refresh sales').closest('button')!.click() })
    await waitFor(() => expect(published.length).toBe(1))
    expect(published[0].label).toBe('Refresh sales')
    expect(published[0].text).toBe('[UI] refresh')
  })

  it('reaches NEITHER the gate nor the tile endpoint', async () => {
    const { getByText } = render(<GenUiWidget content={BUTTON} title="Sales" />)
    act(() => { getByText('Refresh sales').closest('button')!.click() })
    await waitFor(() => expect(published.length).toBe(1))
    expect(resumeWorkflowRun).not.toHaveBeenCalled()
    expect(tileWidgetAction).not.toHaveBeenCalled()
  })
})

// ── workflow gate: the submit answers the gate, not the chat ────────────────

describe('a gate-emitted widget action', () => {
  const gateHost = {
    producer: { kind: 'workflow-gate' as const, runId: 'run-7', token: 'tok-abc' },
  }

  it('answers THAT gate with the submitted values', async () => {
    const { getByLabelText, getByText } = render(
      <GenUiHostCtx.Provider value={gateHost}>
        <GenUiWidget content={FORM} title="Expense" />
      </GenUiHostCtx.Provider>,
    )
    type(getByLabelText('Amount'), '12.40')
    type(getByLabelText('Vendor'), 'Acme')
    act(() => { getByText('Log expense').closest('button')!.click() })

    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledTimes(1))
    expect(resumeWorkflowRun).toHaveBeenCalledWith('run-7', {
      answer: { amount: '12.40', vendor: 'Acme' },
      resume_token: 'tok-abc',
    })
  })

  it('does NOT also become a chat turn', async () => {
    const { getByText } = render(
      <GenUiHostCtx.Provider value={gateHost}>
        <GenUiWidget content={FORM} title="Expense" />
      </GenUiHostCtx.Provider>,
    )
    act(() => { getByText('Log expense').closest('button')!.click() })
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledTimes(1))
    // One activation, one destination. A submit that answered the gate AND opened a chat
    // would double-handle the user's one click.
    expect(published).toEqual([])
  })

  it('tells its host when the answer landed, and NOT when it was refused', async () => {
    const onResolved = vi.fn()
    const host = { ...gateHost, onResolved }
    const { getByText, rerender } = render(
      <GenUiHostCtx.Provider value={host}>
        <GenUiWidget content={FORM} title="Expense" />
      </GenUiHostCtx.Provider>,
    )
    act(() => { getByText('Log expense').closest('button')!.click() })
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(1))

    resumeWorkflowRun.mockResolvedValueOnce({ ok: false } as never)
    rerender(
      <GenUiHostCtx.Provider value={{ ...host }}>
        <GenUiWidget content={FORM} title="Expense refused" />
      </GenUiHostCtx.Provider>,
    )
    act(() => { getByText('Log expense').closest('button')!.click() })
    await waitFor(() => expect(resumeWorkflowRun).toHaveBeenCalledTimes(2))
    // Still ONE: closing an inbox row after a refused submit would hide a gate that is
    // still waiting for an answer.
    expect(onResolved).toHaveBeenCalledTimes(1)
  })

  it('surfaces a refusal next to the control that raised it', async () => {
    resumeWorkflowRun.mockResolvedValueOnce({ ok: false } as never)
    const { getByText, getByRole } = render(
      <GenUiHostCtx.Provider value={gateHost}>
        <GenUiWidget content={FORM} title="Expense" />
      </GenUiHostCtx.Provider>,
    )
    act(() => { getByText('Log expense').closest('button')!.click() })
    await waitFor(() => expect(getByRole('alert').textContent).toMatch(/could not be answered/i))
  })
})

// ── tile: the action re-fires the bound workflow, server-side ───────────────

describe('a tile-emitted widget action', () => {
  const tileHost = { producer: { kind: 'tile' as const, viewId: 'overview', ref: 'artifact:sales' } }

  it('re-fires THAT tile through the fenced endpoint', async () => {
    const { getByText } = render(
      <GenUiHostCtx.Provider value={tileHost}>
        <GenUiWidget content={BUTTON} title="Sales" />
      </GenUiHostCtx.Provider>,
    )
    act(() => { getByText('Refresh sales').closest('button')!.click() })
    await waitFor(() => expect(tileWidgetAction).toHaveBeenCalledTimes(1))
    expect(tileWidgetAction).toHaveBeenCalledWith('overview', {
      ref: 'artifact:sales',
      action: 'refresh',
      payload: undefined,
    })
    expect(published).toEqual([])
  })

  it('shows the server refusal verbatim rather than claiming success', async () => {
    tileWidgetAction.mockResolvedValueOnce({
      ok: false,
      code: 'tile_capability_refused',
      message: "action outside the tile's frozen capability set: providers=bash",
    } as never)
    const { getByText, getByRole } = render(
      <GenUiHostCtx.Provider value={tileHost}>
        <GenUiWidget content={BUTTON} title="Sales" />
      </GenUiHostCtx.Provider>,
    )
    act(() => { getByText('Refresh sales').closest('button')!.click() })
    await waitFor(() => expect(getByRole('alert').textContent).toContain('frozen capability set'))
  })
})
