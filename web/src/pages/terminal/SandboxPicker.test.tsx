import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SandboxPicker, type SandboxProvider } from './SandboxPicker'

const PROVIDERS: SandboxProvider[] = [
  { name: 'none', display_name: 'No isolation (host)', available: true },
  { name: 'lima', display_name: 'Lima (VM)', available: false },
  { name: 'docker', display_name: 'Docker (bind-mount container)', available: true },
]

describe('SandboxPicker (EI-4 §1.3(3))', () => {
  it('renders one option per provider, greying unavailable tiers so they cannot be chosen', () => {
    render(<SandboxPicker providers={PROVIDERS} value="none" onChange={() => {}} />)
    const opts = screen.getAllByRole('option') as HTMLOptionElement[]
    expect(opts.map((o) => o.value)).toEqual(['none', 'lima', 'docker'])
    const lima = opts.find((o) => o.value === 'lima')!
    // SC3 "greyed-out-with-reason": a stopped tier is disabled and says so.
    expect(lima.disabled).toBe(true)
    expect(lima.textContent).toContain('unavailable')
    expect(opts.find((o) => o.value === 'docker')!.disabled).toBe(false)
  })

  it('fires onChange with the selected provider name', () => {
    const onChange = vi.fn()
    render(<SandboxPicker providers={PROVIDERS} value="none" onChange={onChange} />)
    const select = screen.getByRole('combobox', { name: /sandbox for new terminal sessions/i })
    fireEvent.change(select, { target: { value: 'docker' } })
    expect(onChange).toHaveBeenCalledWith('docker')
  })

  it('reflects the controlled value', () => {
    render(<SandboxPicker providers={PROVIDERS} value="docker" onChange={() => {}} />)
    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('docker')
  })

  it('disables the whole control when asked', () => {
    render(<SandboxPicker providers={PROVIDERS} value="none" onChange={() => {}} busy />)
    expect((screen.getByRole('combobox') as HTMLSelectElement).disabled).toBe(true)
  })
})
