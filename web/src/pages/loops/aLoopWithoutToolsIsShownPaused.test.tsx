/**
 * A loop whose worker ran a cycle without tools reads as PAUSED, with the gateway's sentence naming
 * the model, where another is chosen, and Resume — never a question to answer.
 *
 * 🔴 A loop's worker does all its work with tools. On a model that can't use them it wrote its
 * findings in prose that reached no file, and the loop ran on with nothing on its page. The gateway
 * now holds the loop (`LoopWatchdog.hold_without_tools`): `needs_input`, with
 * `pending_question.no_tools` and the sentence, and a `no_tools` event on the loop's stream.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { CodeProject } from '../../lib/api'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>()
  return { ...actual, api: { ...actual.api, approvals: () => Promise.resolve([]), uLoopAction: vi.fn(() => Promise.resolve({})) } }
})
vi.mock('../../lib/useChatSocket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/useChatSocket')>()
  return { ...actual, useChatSocket: () => {} }
})

const { NoToolsPause } = await import('./NoToolsPause')
const { RUN_LIFECYCLE } = await import('./useRunStream')
const { ProjectFooter } = await import('../code/CodeCockpitPage')
const { api } = await import('../../lib/api')

const SAID = '“Pocket:tiny-1” can’t use tools, and a cycle counts only for the finding it writes. Choose a model that uses tools for Loops in Settings → Models. Then resume the loop.'
const WHY = 'Its worker writes each finding with tools; without them a cycle writes nothing.'

afterEach(cleanup)

describe('the pause', () => {
  it('🔴 the event that says it is one the page listens for', () => {
    expect(RUN_LIFECYCLE).toContain('no_tools')
  })

  it('says it is paused for its model, with the sentence, where to choose another and Resume', async () => {
    const onResume = vi.fn()
    render(<NoToolsPause question={SAID} why={WHY} onResume={onResume} />)

    const band = screen.getByRole('status')
    expect(band.textContent).toContain('Paused: its model can’t use tools')
    expect(screen.getByText(SAID)).toBeTruthy()
    expect(screen.getByText(WHY)).toBeTruthy()
    expect(screen.getByRole('link', { name: /open settings → models/i }).getAttribute('href')).toBe('#/settings/models')
    await userEvent.click(screen.getByRole('button', { name: /resume/i }))
    expect(onResume).toHaveBeenCalledTimes(1)
  })

  it('leaves Resume out where another control resumes the loop', () => {
    render(<NoToolsPause question={SAID} />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe("a running loop's cockpit", () => {
  const project = (extra: Record<string, unknown>): CodeProject => ({
    id: '6a6fcaf6', name: 'Fix the digest', status: 'needs_input', kind: 'code', stages: [],
    total_cycles: 2, max_cycles: 30, elapsed_seconds: 600,
    pending_question: { question: SAID, why: WHY, ...extra },
  }) as unknown as CodeProject

  it('🔴 shows the pause and Resume, not a question to answer', async () => {
    const onNudged = vi.fn()
    render(<ProjectFooter project={project({ no_tools: true })} gateFail={null} stalled={null} onNudged={onNudged} />)

    expect(await screen.findByText('Paused: its model can’t use tools')).toBeTruthy()
    expect(screen.getByText(SAID)).toBeTruthy()
    expect(screen.queryByText(/needs your input/i), 'it is shown as a question to answer').toBeNull()
    expect(screen.queryByRole('button', { name: 'Use your best judgment' })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /^resume$/i }))
    expect(api.uLoopAction).toHaveBeenCalledWith('6a6fcaf6', 'resume')
    expect(onNudged).toHaveBeenCalled()
  })

  it('a question its worker asked is still one to answer — the control', async () => {
    render(<ProjectFooter project={project({})} gateFail={null} stalled={null} onNudged={vi.fn()} />)

    expect(await screen.findByText(SAID)).toBeTruthy()
    expect(screen.queryByText('Paused: its model can’t use tools')).toBeNull()
  })
})
