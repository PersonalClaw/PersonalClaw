import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { SourcesPanel } from './SourcesPanel'

// ── The watched-scratchpad control (WF2UNI-9, config point 5) ─────────────────
//
// The backend field is the fifth wiring point of the config contract, so it needs a control a user
// can actually find. Two things are asserted because each has bitten this repo before:
//
//  · the control READS `planning.scratchpad_path` off the config payload. A panel that renders an
//    always-empty input looks identical to one whose backend field is unset — "backend truth,
//    frontend silence".
//  · it PATCHes the allowlisted dotted path on save, not on every keystroke. Per-keystroke saves
//    would canonicalize a half-typed path server-side and fight the user as they type.

const patchConfig = vi.fn((_path: string, _value: unknown) => Promise.resolve({}))
const personalclawConfig = vi.fn()

vi.mock('../../lib/api', () => ({
  api: {
    personalclawConfig: () => personalclawConfig(),
    patchConfig: (path: string, value: unknown) => patchConfig(path, value),
  },
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../lib/useCachedData', () => ({
  useCachedData: (_k: string, fn: () => Promise<unknown>) => {
    const [data, setData] = useState<unknown>(null)
    useEffect(() => { fn().then(setData) }, [])
    return { data }
  },
}))

describe('the watched-scratchpad path control', () => {
  beforeEach(() => {
    patchConfig.mockClear()
    personalclawConfig.mockResolvedValue({
      sources: { enabled: true },
      planning: { scratchpad_path: '/Users/me/notes/today.md' },
    })
  })

  it('renders the configured path — a field nobody reads is indistinguishable from an unset one', async () => {
    render(<SourcesPanel />)
    const input = await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    expect(input).toBeTruthy()
  })

  it('names itself for assistive tech via the Field it sits in', async () => {
    render(<SourcesPanel />)
    await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    // settingsUI's Field publishes its label id, and TextInput claims it via aria-labelledby —
    // so probing the ACCESSIBLE NAME (not a duplicate aria-label) is the real assertion.
    expect(screen.getByRole('textbox', { name: /scratchpad path/i })).toBeTruthy()
  })

  it('PATCHes the allowlisted dotted path on Save, and not per keystroke', async () => {
    render(<SourcesPanel />)
    const input = await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    fireEvent.change(input, { target: { value: '/Users/me/notes/inbox.md' } })
    expect(patchConfig).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(patchConfig).toHaveBeenCalledWith('planning.scratchpad_path', '/Users/me/notes/inbox.md')
  })

  it('an emptied field saves "" — that is how the user turns intake off', async () => {
    render(<SourcesPanel />)
    const input = await waitFor(() => screen.getByDisplayValue('/Users/me/notes/today.md'))
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))
    expect(patchConfig).toHaveBeenCalledWith('planning.scratchpad_path', '')
  })
})
