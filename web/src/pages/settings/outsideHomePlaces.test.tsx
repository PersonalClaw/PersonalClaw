// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import type { OutsideHomeState } from '../../lib/api'

// ── Settings → Security → Outside PersonalClaw's home ──────────────────────────────────────────
//
// PersonalClaw reads and writes inside its home. A place outside it (the skills other AI tools
// share, the Hugging Face folder, another CLI's sign-in) is read only once the owner turns it on
// here, per place. So the switch has to say where the place is, ask before it turns on, send the
// whole allowed list with just that one place changed, and never ask to turn one off.

const outsideHome = vi.fn()
const setOutsideHome = vi.fn()
const confirm = vi.fn()
vi.mock('../../lib/api', () => ({
  api: {
    outsideHome: (...a: unknown[]) => outsideHome(...a),
    setOutsideHome: (...a: unknown[]) => setOutsideHome(...a),
  },
}))
vi.mock('../../ui/dialog', () => ({ confirm: (...a: unknown[]) => confirm(...a) }))

/** A fresh module per test: `useQuery`'s cache is module state, and a place list cached by one
 *  test would stand in for the failed read another test makes. */
async function mount() {
  vi.resetModules()
  const { OutsideHomeEditor } = await import('./SecurityPanel')
  return render(<OutsideHomeEditor />)
}

const SKILLS = '/Users/me/.agents/skills'
const HF = '/Users/me/.cache/huggingface'

const state = (allowed: string[]): OutsideHomeState => ({
  places: [
    { id: 'agent-skills', label: 'Skills other AI tools share', paths: [SKILLS], detail: 'Your agents can also use these skills.', allowed: allowed.includes('agent-skills') },
    { id: 'huggingface-cache', label: 'The Hugging Face folder other tools share', paths: [HF], detail: 'Model apps can use models already downloaded here.', allowed: allowed.includes('huggingface-cache') },
  ],
  allowed,
})

const toggle = (name: RegExp) => screen.getByRole('switch', { name })

beforeEach(() => {
  sessionStorage.clear()
  outsideHome.mockReset()
  setOutsideHome.mockReset().mockResolvedValue({})
  confirm.mockReset()
})

describe("the places outside PersonalClaw's home", () => {
  it('lists each place with where it is, and is off until allowed', async () => {
    outsideHome.mockResolvedValue(state([]))
    await mount()
    expect(await screen.findByText(SKILLS)).toBeTruthy()
    expect(screen.getByText(HF)).toBeTruthy()
    expect(toggle(/skills other ai tools share/i).getAttribute('aria-checked')).toBe('false')
    expect(toggle(/hugging face/i).getAttribute('aria-checked')).toBe('false')
  })

  it('asks before turning one on, then sends the list with it added', async () => {
    outsideHome.mockResolvedValue(state(['sign-in:gone-app']))
    confirm.mockResolvedValue(true)
    await mount()
    fireEvent.click(await screen.findByRole('switch', { name: /hugging face/i }))
    await waitFor(() => expect(setOutsideHome).toHaveBeenCalledTimes(1))
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(confirm.mock.calls[0][0].title).toMatch(/let personalclaw read the hugging face folder/i)
    // A place no longer offered (an app since removed) is kept, not dropped by this write.
    expect(setOutsideHome).toHaveBeenCalledWith(['sign-in:gone-app', 'huggingface-cache'], true)
  })

  it('writes nothing when the question is declined', async () => {
    outsideHome.mockResolvedValue(state([]))
    confirm.mockResolvedValue(false)
    await mount()
    fireEvent.click(await screen.findByRole('switch', { name: /skills other ai tools share/i }))
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    // The label keeps its capitals in the question ("AI", not "ai").
    expect(confirm.mock.calls[0][0].title).toBe('Let PersonalClaw read skills other AI tools share?')
    expect(setOutsideHome).not.toHaveBeenCalled()
  })

  it('turns one off without asking, and keeps the others', async () => {
    outsideHome.mockResolvedValue(state(['agent-skills', 'huggingface-cache']))
    await mount()
    fireEvent.click(await screen.findByRole('switch', { name: /skills other ai tools share/i }))
    await waitFor(() => expect(setOutsideHome).toHaveBeenCalledWith(['huggingface-cache'], false))
    expect(confirm).not.toHaveBeenCalled()
  })

  it('says a failed read failed, instead of showing every place as off', async () => {
    outsideHome.mockRejectedValue(new Error('probe-induced 500 on /api/security/outside-home'))
    await mount()
    expect(await screen.findByText(/couldn.t load your places outside personalclaw.s home/i)).toBeTruthy()
    expect(screen.queryByRole('switch')).toBeNull()
  })
})
