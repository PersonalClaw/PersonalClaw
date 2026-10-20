import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import { MultiInstanceCard } from './MultiInstanceCard'
import { api, type SettingsProvider } from '../../lib/api'

// ── A provider's switch in Settings → Providers is its app's switch ──────────────────────────────
//
// The switch sent `POST /api/providers/{name}/enable|disable`, which toggled the provider's
// instances and nothing else. Measured on a dev gateway with a probe app whose every part names its
// version: switched off, its MCP server, its model type and its backend still answered, and the
// Apps page still said the app was on; switched on after its file changed on disk, it still ran the
// old code, because the module already loaded was handed back. A provider is its app's, so its
// switch turns the app off and on — the Apps page's own call, which unloads the app and loads it
// from its files.

function ext(over: Partial<SettingsProvider> = {}): SettingsProvider {
  return {
    name: 'boot-probe', displayName: 'Boot Probe', enabled: true, managed: true,
    provider: { type: 'tool', capabilities: [] }, availability: { state: 'available', reason: '', checkedAt: 1 },
    ...over,
  }
}

const toasts: { level: string; message: string }[] = []
const onToast = (e: Event) => { toasts.push((e as CustomEvent).detail) }

beforeEach(() => {
  toasts.length = 0
  window.addEventListener('ne:toast', onToast)
  vi.spyOn(api, 'providerSchema').mockResolvedValue({ type: 'object', properties: {} })
  vi.spyOn(api, 'providerInstances').mockResolvedValue([])
})
afterEach(() => { window.removeEventListener('ne:toast', onToast); cleanup(); vi.restoreAllMocks() })

const flip = async () => {
  await act(async () => { fireEvent.click(screen.getByRole('switch', { name: /toggle boot-probe/i })) })
}

describe("a provider's switch turns its app off and on", () => {
  it('off is the app turned off', async () => {
    const disable = vi.spyOn(api, 'disableApp').mockResolvedValue({ ok: true })
    const enable = vi.spyOn(api, 'enableApp').mockResolvedValue({ ok: true })
    const onChanged = vi.fn()
    render(<ProviderCard ext={ext()} open={false} onOpenChange={() => {}} onChanged={onChanged} />)
    await flip()
    expect(disable, 'the switch left the app on, its code loaded').toHaveBeenCalledWith('boot-probe')
    expect(enable).not.toHaveBeenCalled()
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('on is the app turned on, from its files', async () => {
    const enable = vi.spyOn(api, 'enableApp').mockResolvedValue({ ok: true, providerErrors: [] })
    const disable = vi.spyOn(api, 'disableApp').mockResolvedValue({ ok: true })
    render(<ProviderCard ext={ext({ enabled: false })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    await flip()
    expect(enable, 'the switch retried the provider on the module already loaded').toHaveBeenCalledWith('boot-probe')
    expect(disable).not.toHaveBeenCalled()
    expect(toasts.filter((t) => t.level === 'error')).toEqual([])
  })

  it('a provider of it that is refused on the way is said at once', async () => {
    const why = 'A tool of Boot Probe is named probe_version, which the Other app already offers.'
    vi.spyOn(api, 'enableApp').mockResolvedValue({ ok: true, providerErrors: [why] })
    render(<ProviderCard ext={ext({ enabled: false })} open={false} onOpenChange={() => {}} onChanged={() => {}} />)
    await flip()
    await waitFor(() => expect(toasts).toContainEqual(expect.objectContaining({ level: 'error', message: why })))
  })

  it("a multi-instance provider's switch is its app's too", async () => {
    const disable = vi.spyOn(api, 'disableApp').mockResolvedValue({ ok: true })
    render(<MultiInstanceCard ext={ext({ provider: { type: 'tool', multiInstance: true } })} onChanged={() => {}} />)
    await flip()
    expect(disable).toHaveBeenCalledWith('boot-probe')
  })
})
