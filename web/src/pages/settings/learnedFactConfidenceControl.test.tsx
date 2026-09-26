/**
 * Settings B10 — the learned-fact confidence has one control, and it writes what the store reads.
 *
 * Settings → Providers → Vector Memory offered a "Confidence Threshold" that saved into the app's
 * provider config, which nothing read; the memory store's learned-fact gate reads
 * `memory.semantic_confidence_threshold`, which no control wrote. The app field is gone and this is
 * the control: it renders the value the Memory settings read returns and writes that exact leaf
 * through the `_EDITABLE_CONFIG` PATCH (`tests/test_memory_confidence_threshold_is_one_setting.py`
 * drives the server half: the PATCH, the read, and the gate following it with no restart).
 *
 * Driven through the real panel, because the defect was a control on the wrong surface — a
 * handler-level test would pass with this control never rendered.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'

let patchConfig: ReturnType<typeof vi.fn>

async function mountSettings(threshold: number) {
  patchConfig = vi.fn(() => Promise.resolve({ ok: true }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySettings: () => Promise.resolve({
          history_idle_hours: 2, history_max_days: 90, semantic_confidence_threshold: threshold,
        }),
        memoryVaultStatus: () => Promise.resolve(null),
        memoryFacets: () => Promise.resolve([]),
        patchConfig,
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={{ tab: 'settings' }} setQuery={() => {}} />)
  return (await screen.findByRole('spinbutton', { name: 'Learned-fact confidence' })) as HTMLInputElement
}

beforeEach(() => {
  cleanup()
  vi.resetModules()
  sessionStorage.clear()
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => cleanup())

describe('Settings → Memory → Learned-fact confidence', () => {
  it('shows the value the server holds, not a default', async () => {
    const input = await mountSettings(0.65)
    await waitFor(() => expect(input.value).toBe('0.65'))
  })

  it('writes the leaf the memory store reads', async () => {
    const input = await mountSettings(0.8)
    await waitFor(() => expect(input.value).toBe('0.8'))
    fireEvent.change(input, { target: { value: '0.9' } })
    fireEvent.blur(input)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('memory.semantic_confidence_threshold', 0.9))
  })

  it('cannot send a value outside 0–1', async () => {
    const input = await mountSettings(0.8)
    fireEvent.change(input, { target: { value: '7' } })
    fireEvent.blur(input)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('memory.semantic_confidence_threshold', 1))
  })

  it('says what the number decides', async () => {
    await mountSettings(0.8)
    expect(screen.getByText(/Facts you add yourself are always kept/)).toBeTruthy()
  })
})
