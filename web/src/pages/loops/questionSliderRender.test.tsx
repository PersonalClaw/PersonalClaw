// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { QuestionSlider } from './QuestionSlider'
import type { SliderQuestion } from './sliderState'

// ── WF2UNI-10: the deep-rigor Round renders as a QuestionSlider stepper ─────────────────────────
//
// sliderState.test.ts pins the reducer (advance/back/gate/custom/submit) as pure logic; this mounts
// the COMPONENT and asserts the clause's render contract: typed kinds, one question at a time, the
// mandated custom-answer escape hatch on a `choice`, and a single gated Submit at the end.

const q = (over: Partial<SliderQuestion> = {}): SliderQuestion => ({
  id: 'k', prompt: 'why?', kind: 'text', required: true, ...over,
})

describe('QuestionSlider stepper (WF2UNI-10 render)', () => {
  it('shows one question at a time, with its typed control', () => {
    render(<QuestionSlider
      questions={[q({ id: 'c', prompt: 'pick one', kind: 'choice', choices: ['x', 'y'] }), q({ id: 't', prompt: 'second question' })]}
      onSubmit={() => {}} />)
    expect(screen.getByText('Question 1 of 2')).toBeInTheDocument()
    expect(screen.getByText('pick one')).toBeInTheDocument()
    // one-at-a-time: the second question is not on screen yet
    expect(screen.queryByText('second question')).toBeNull()
    // typed `choice` control: the closed options + the protocol-mandated Other… escape hatch
    expect(screen.getByRole('tab', { name: 'x' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /Other/ })).toBeInTheDocument()
  })

  it('the choice escape hatch reveals a freeform custom-answer box', () => {
    render(<QuestionSlider questions={[q({ id: 'c', prompt: 'pick', kind: 'choice', choices: ['x'] })]} onSubmit={() => {}} />)
    expect(screen.queryByRole('textbox', { name: 'Your custom answer' })).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: /Other/ }))
    expect(screen.getByRole('textbox', { name: 'Your custom answer' })).toBeInTheDocument()
  })

  it('a slider question renders a range control', () => {
    render(<QuestionSlider questions={[q({ id: 's', prompt: 'how many', kind: 'slider', min: 0, max: 10 })]} onSubmit={() => {}} />)
    expect(screen.getByRole('slider', { name: 'how many' })).toBeInTheDocument()
  })

  it('ends in a single Submit that gates on the required answer and returns the answers', () => {
    const onSubmit = vi.fn()
    render(<QuestionSlider questions={[q({ id: 'k', prompt: 'why?' })]} onSubmit={onSubmit} />)
    // one required question ⇒ it is the last ⇒ Submit shows (no Next). Gated until answered:
    // a disabled Button swallows the click, so this asserts the gate behaviorally.
    fireEvent.click(screen.getByRole('button', { name: /Submit answers/ }))
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox', { name: 'why?' }), { target: { value: 'because' } })
    fireEvent.click(screen.getByRole('button', { name: /Submit answers/ }))
    expect(onSubmit).toHaveBeenCalledWith({ k: 'because' })
  })
})
