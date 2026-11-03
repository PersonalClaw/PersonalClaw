/**
 * A seeded skill a disable kept because its owner changed it is said out loud.
 *
 * The gateway keeps such a skill in the library as the user's own, and its answer names it (`kept`).
 * Without a word here the skill would stay with nothing saying it outlived its app.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const notified: Array<[string, string | undefined]> = []
const disableApp = vi.fn()

vi.mock('../lib/api', () => ({ api: { disableApp: (...a: unknown[]) => disableApp(...a), enableApp: vi.fn() } }))
vi.mock('./appSdk', () => ({ notify: (message: string, level?: string) => { notified.push([message, level]) } }))

const { setActivation, sayKept } = await import('./appActivation')

beforeEach(() => {
  notified.length = 0
  disableApp.mockReset()
})

describe('a kept skill is said', () => {
  it('a disable that kept a changed skill says so', async () => {
    disableApp.mockResolvedValue({ ok: true, kept: ['Kept the skill deploy: you changed it, so it stays yours.'] })
    await setActivation({ name: 'skilly', enabled: true })
    expect(notified).toEqual([['Kept the skill deploy: you changed it, so it stays yours.', 'info']])
  })

  it('says nothing when nothing was kept, or the answer names nothing', async () => {
    disableApp.mockResolvedValue({ ok: true, kept: [] })
    await setActivation({ name: 'skilly', enabled: true })
    disableApp.mockResolvedValue({ ok: true })
    await setActivation({ name: 'skilly', enabled: true })
    sayKept(undefined)
    expect(notified).toEqual([])
  })
})
