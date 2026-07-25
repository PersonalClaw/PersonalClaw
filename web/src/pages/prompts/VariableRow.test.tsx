import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { VariableRow } from './VariableRow'
import type { PromptVariable } from '../../lib/api'

const base: PromptVariable = { name: 'topic', type: 'text', description: '', required: false }

describe('VariableRow', () => {
  it('renders name/type/required/default with accessible names + autofill-defeating names', () => {
    const { getByLabelText, getByText } = render(
      <VariableRow v={base} onChange={() => {}} onRemove={() => {}} />,
    )
    // a11y names present on every field (the canonical copy's gain over the two duplicates).
    expect((getByLabelText('Variable name') as HTMLInputElement).value).toBe('topic')
    expect(getByLabelText('Variable type')).toBeTruthy()
    expect(getByLabelText('Variable description')).toBeTruthy()
    expect(getByLabelText('Variable default value')).toBeTruthy()
    // Each field carries a unique `name=` (defeats browser autofill); the useId() prefix
    // makes them field-specific.
    expect((getByLabelText('Variable name') as HTMLInputElement).name).toMatch(/^var-name-/)
    expect((getByLabelText('Variable type') as HTMLSelectElement).name).toMatch(/^var-type-/)
    // Required toggle shows its current state.
    expect(getByText('optional')).toBeTruthy()
  })

  it('defaults the description placeholder to the prompt wording; overrides for snippets', () => {
    const { getByLabelText, rerender } = render(
      <VariableRow v={base} onChange={() => {}} onRemove={() => {}} />,
    )
    expect((getByLabelText('Variable description') as HTMLInputElement).placeholder).toBe('Description (shown when invoked)')
    rerender(<VariableRow v={base} onChange={() => {}} onRemove={() => {}} descriptionPlaceholder="Description" />)
    expect((getByLabelText('Variable description') as HTMLInputElement).placeholder).toBe('Description')
  })

  it('reveals the choices field only for select-type variables', () => {
    const { queryByLabelText, rerender } = render(
      <VariableRow v={base} onChange={() => {}} onRemove={() => {}} />,
    )
    expect(queryByLabelText('Variable choices')).toBeNull()
    rerender(<VariableRow v={{ ...base, type: 'select', options: ['a', 'b'] }} onChange={() => {}} onRemove={() => {}} />)
    expect((queryByLabelText('Variable choices') as HTMLInputElement).value).toBe('a, b')
  })

  it('sanitizes the name, toggles required, and wires remove', () => {
    const onChange = vi.fn()
    const onRemove = vi.fn()
    const { getByLabelText, getByText, container } = render(
      <VariableRow v={base} onChange={onChange} onRemove={onRemove} />,
    )
    const nameInput = getByLabelText('Variable name') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'my var!' } })
    expect(onChange).toHaveBeenCalledWith({ name: 'my_var_' })

    getByText('optional').click()
    expect(onChange).toHaveBeenCalledWith({ required: true })

    // The trailing X button is the last <button> in the top row.
    const buttons = container.querySelectorAll('button')
    ;(buttons[buttons.length - 1] as HTMLButtonElement).click()
    expect(onRemove).toHaveBeenCalledTimes(1)
  })
})
