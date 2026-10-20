/** Settings › Models › Background sets the three limits on background work, and they come back as
 *  saved.
 *
 *  How long a background task may run, the most it may write, and how long a reply waits for a
 *  busy local model were constants in the gateway, so a slow machine or a model that needs more
 *  room had no way to change them. These assert the control a person reaches: each limit is under
 *  the Background chain, offers exactly the window the save path accepts, writes its own
 *  `background.*` path, and shows the stored value when the page is opened again. */
import { render, screen, within, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { resetDataStore } from '../../lib/data/store'

/** The gateway's stored `background` section: what a save writes and a read returns. */
const stored: Record<string, number> = {}
const patchConfig = vi.fn((path: string, value: unknown) => {
  const [section, key] = path.split('.')
  if (section === 'background') stored[key] = value as number
  return Promise.resolve({})
})

vi.mock('../../lib/api', async (orig) => {
  const actual = await orig<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: {
      modelsAvailable: () => Promise.resolve([]),
      activeChains: () => Promise.resolve({}),
      activeChain: () => Promise.resolve({ value: [], revision: '' }),
      modelDownloads: () => Promise.resolve([]),
      modelsHealth: () => Promise.resolve({ providers: [] }),
      embeddingReindexJobs: () => Promise.resolve({ jobs: [], active: null }),
      judgeBench: () => Promise.resolve({ ran: false }),
      modelDownloadCleanupCandidates: () => Promise.resolve({ candidates: [], total_bytes: 0 }),
      hfTokenStatus: () => Promise.resolve({ sources: [] }),
      modelsLoaded: () => Promise.resolve({
        loaded: [], providers: [],
        pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 85, warn: false, source: 'unavailable' },
      }),
      personalclawConfig: () => Promise.resolve({
        agent: { prompt_cache_enabled: true }, local_models: {}, background: { ...stored },
      }),
      patchConfig: (path: string, value: unknown) => patchConfig(path, value),
    },
  }
})
vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

import { ModelsPanel } from './ModelsPanel'

const SHIPPED = { call_timeout_secs: 300, max_output_tokens: 4096, busy_model_wait_secs: 15 }

beforeEach(() => {
  cleanup()
  resetDataStore()
  sessionStorage.clear()
  patchConfig.mockClear()
  for (const key of Object.keys(stored)) delete stored[key]
  Object.assign(stored, SHIPPED)
})

/** The Models page with its Background chain opened, and the limits section under it. */
async function openBackground() {
  render(<ModelsPanel />)
  await userEvent.click(await screen.findByRole('button', { name: /^Background/ }))
  const heading = await screen.findByRole('heading', { name: 'Limits' })
  return within(heading.parentElement as HTMLElement)
}

describe('Settings › Models › Background limits', () => {
  it('shows each limit with its unit, its shipped value and the window the save path accepts', async () => {
    const limits = await openBackground()
    const time = await limits.findByRole('spinbutton', { name: 'Time limit (seconds)' })
    const output = limits.getByRole('spinbutton', { name: 'Output limit (tokens)' })
    const wait = limits.getByRole('spinbutton', { name: 'Wait for a busy local model (seconds)' })

    expect([time, output, wait].map((el) => (el as HTMLInputElement).value)).toEqual(['300', '4096', '15'])
    // `_EDITABLE_CONFIG`'s windows: a control that offers a value the save path refuses is the same
    // defect as no control, one step further along.
    expect([time.getAttribute('min'), time.getAttribute('max')]).toEqual(['30', '3600'])
    expect([output.getAttribute('min'), output.getAttribute('max')]).toEqual(['512', '65536'])
    expect([wait.getAttribute('min'), wait.getAttribute('max')]).toEqual(['0', '300'])
    // Each says what it limits and its default, in words.
    expect(limits.getByText(/How long a background task may run before it is stopped.*Default 300/)).toBeInTheDocument()
    expect(limits.getByText(/Most text a background task may write.*Default 4096/)).toBeInTheDocument()
    expect(limits.getByText(/How long a reply waits for a busy local model before asking the next one.*Default 15/)).toBeInTheDocument()
  })

  it('saves each limit to its own path and shows the saved values when the page opens again', async () => {
    let limits = await openBackground()
    const time = await limits.findByRole('spinbutton', { name: 'Time limit (seconds)' })
    await userEvent.clear(time)
    await userEvent.type(time, '1200{Enter}')
    const output = limits.getByRole('spinbutton', { name: 'Output limit (tokens)' })
    await userEvent.clear(output)
    await userEvent.type(output, '16384{Enter}')
    const wait = limits.getByRole('spinbutton', { name: 'Wait for a busy local model (seconds)' })
    await userEvent.clear(wait)
    await userEvent.type(wait, '0{Enter}')

    expect(patchConfig.mock.calls).toEqual([
      ['background.call_timeout_secs', 1200],
      ['background.max_output_tokens', 16384],
      ['background.busy_model_wait_secs', 0],
    ])

    // Opened again, as after a reload: the page reads what was stored, not what it had on screen.
    cleanup()
    resetDataStore()
    limits = await openBackground()
    expect((await limits.findByRole('spinbutton', { name: 'Time limit (seconds)' }) as HTMLInputElement).value).toBe('1200')
    expect((limits.getByRole('spinbutton', { name: 'Output limit (tokens)' }) as HTMLInputElement).value).toBe('16384')
    expect((limits.getByRole('spinbutton', { name: 'Wait for a busy local model (seconds)' }) as HTMLInputElement).value).toBe('0')
  })

  it('a value outside the window is held to its end before it is sent', async () => {
    const limits = await openBackground()
    const time = await limits.findByRole('spinbutton', { name: 'Time limit (seconds)' })
    await userEvent.clear(time)
    await userEvent.type(time, '5{Enter}')
    expect(patchConfig).toHaveBeenCalledWith('background.call_timeout_secs', 30)
  })

  it('only the Background chain carries the limits', async () => {
    render(<ModelsPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /^Chat/ }))
    expect(screen.queryByRole('heading', { name: 'Limits' })).toBeNull()
  })
})
