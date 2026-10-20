import { describe, expect, it, vi, afterEach } from 'vitest'
import { act, render, screen, cleanup } from '@testing-library/react'
import type { ConfirmOptions } from '../../ui/dialog'

// Settings → Security → Git push (`security.allow_git_push`): off by default, and turning it on is
// the loosening direction, so it asks first and the write carries the consent; turning it off is
// one click. The gateway asks the same consent itself of a writer that skips this dialog.

const flush = () => new Promise((r) => setTimeout(r, 0))

afterEach(() => { cleanup(); vi.resetModules() })

const mount = async (stored: unknown, confirmed: boolean) => {
  vi.resetModules()
  sessionStorage.clear()
  const patchConfig = vi.fn(() => Promise.resolve({}))
  const confirmSpy = vi.fn((_req: ConfirmOptions) => Promise.resolve(confirmed))
  vi.doMock('../../ui/dialog', () => ({ confirm: confirmSpy }))
  vi.doMock('../../lib/api', () => ({
    api: {
      personalclawConfig: () => Promise.resolve({ security: { allow_git_push: stored } }),
      patchConfig,
    },
  }))
  const { BranchPublishSwitch, BRANCH_PUBLISH_CONSENT } = await import('./SecurityPanel')
  await act(async () => { render(<BranchPublishSwitch />); await flush() })
  return { patchConfig, confirmSpy, BRANCH_PUBLISH_CONSENT }
}

const toggle = () => screen.getByRole('switch', { name: 'Let agents push a branch' })

describe('BranchPublishSwitch', () => {
  it('reads as off unless the config holds a real true', async () => {
    await mount('true', true)
    expect(toggle().getAttribute('aria-checked')).toBe('false')
  })

  it('turning it on asks first, and a decline writes nothing', async () => {
    const { patchConfig, confirmSpy } = await mount(false, false)
    await act(async () => { toggle().click(); await flush() })
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(patchConfig).not.toHaveBeenCalled()
  })

  it('confirming writes it on once, carrying the consent, and the dialog says what stays refused', async () => {
    const { patchConfig, confirmSpy, BRANCH_PUBLISH_CONSENT } = await mount(false, true)
    await act(async () => { toggle().click(); await flush() })
    expect(confirmSpy.mock.calls[0][0].title).toBe(BRANCH_PUBLISH_CONSENT.title)
    expect(String(confirmSpy.mock.calls[0][0].body)).toContain('still asks you first')
    expect(String(confirmSpy.mock.calls[0][0].body)).toContain('--no-verify stay refused')
    expect(patchConfig).toHaveBeenCalledTimes(1)
    expect(patchConfig).toHaveBeenCalledWith('security.allow_git_push', true, true)
  })

  it('turning it off never asks', async () => {
    const { patchConfig, confirmSpy } = await mount(true, true)
    await act(async () => { toggle().click(); await flush() })
    expect(confirmSpy).not.toHaveBeenCalled()
    expect(patchConfig).toHaveBeenCalledWith('security.allow_git_push', false, false)
  })
})
