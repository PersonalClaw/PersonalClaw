/**
 * Retry asks first when the attempt it replaces finished steps that may have changed something.
 *
 * Retry runs a turn that ended without its answer again, and the attempt it replaces goes, the
 * calls it finished included, so the turn asked again may make them again. The gateway answers
 * such a Retry with its question (`retry_repeats_steps`, `dashboard/repeated_steps.py`) instead of
 * running it. The page shows it, each step in the words its card in the chat uses, with its tool
 * and its target, and sends the Retry again only on her yes, carrying the question's `confirm`.
 * The last block drives the real `api.regenerate` through a stubbed `fetch`, so what is asserted
 * is the wire: the first Retry carries nothing, and only a yes sends a second one that confirms.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { api, ApiError } from '../../lib/api'
import { askToRepeat, RepeatedSteps, repeatsAsked, type RepeatsAsked } from './repeatedSteps'

const confirmSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => true))
vi.mock('../../ui/dialog', () => ({ confirm: (opts: unknown) => confirmSpy(opts) }))

const SAID = 'This turn finished 4 steps that may have changed something. Running the turn again replaces this attempt and may repeat them.'
const DETAIL = {
  title: 'Run this turn again?',
  said: SAID,
  steps: [
    { tool: 'bash', target: 'git push origin main' },
    { tool: 'write_file', target: 'notes/plan.md' },
    { tool: 'team_post', target: '#plans' },
    { tool: 'bash', target: 'make deploy', failed: true },
  ],
  more: 0,
  confirm: 'a1b2c3d4e5f60718293a4b5c6d7e8f90',
}
const MESSAGE = 'This turn finished 4 steps that may have changed something: bash (git push origin main), write_file (notes/plan.md) and 2 more. Running the turn again replaces this attempt and may repeat them. To run it anyway, send this request again with its confirmation.'
const question = (detail: unknown = DETAIL) => new ApiError(MESSAGE, 409, 'retry_repeats_steps', detail)

afterEach(() => {
  cleanup()
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => true)
  vi.unstubAllGlobals()
})

describe('repeatsAsked', () => {
  it('reads the gateway question, and nothing else', () => {
    expect(repeatsAsked(question())).toEqual(DETAIL)
    expect(repeatsAsked(new ApiError('session is running', 409, ''))).toBeNull()
    // An app is refused without the question: there is nothing for this page to ask with.
    expect(repeatsAsked(new ApiError(MESSAGE, 409, 'retry_repeats_steps'))).toBeNull()
    expect(repeatsAsked(question({ ...DETAIL, confirm: '' }))).toBeNull()
    expect(repeatsAsked(new Error('network'))).toBeNull()
  })
})

describe('the question', () => {
  it('lists each step in words, with its tool and its target, and says which failed', () => {
    render(<RepeatedSteps asked={DETAIL as RepeatsAsked} />)
    expect(screen.getByText(SAID)).toBeTruthy()
    const items = within(screen.getByRole('list', { name: 'Steps that may repeat' })).getAllByRole('listitem')
    expect(items.map((li) => li.textContent)).toEqual([
      // The words each card in the chat uses for its tool, then what the call named, then the tool.
      'Run commandgit push origin mainbash',
      'Writenotes/plan.mdwrite_file',
      'Team post#plans',
      // A failed step is still asked about, and says it failed, as its card does.
      'Run commandmake deploybash· failed',
    ])
  })

  it('counts the steps it does not list', () => {
    render(<RepeatedSteps asked={{ ...(DETAIL as RepeatsAsked), more: 2 }} />)
    expect(screen.getByText('…and 2 more.')).toBeTruthy()
  })
})

describe('a Retry the gateway asks about', () => {
  function gateway() {
    const sent: Array<Record<string, unknown> | null> = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      const body = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : null
      sent.push(body)
      if (body?.confirm !== DETAIL.confirm) {
        // The question, as the gateway answers a page that says it asks (`consent_ask.py`).
        return new Response(JSON.stringify({ error: { code: 'retry_repeats_steps', message: MESSAGE, detail: DETAIL } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json', 'X-PersonalClaw-Consent-Asked': '1' },
        })
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    }))
    return sent
  }

  async function retry() {
    const ran: string[] = []
    const again = async (confirmed?: string) => {
      try { await api.regenerate('s1', confirmed); ran.push(confirmed ?? '') }
      catch (e) { if (!askToRepeat(e, (yes) => again(yes))) throw e }
    }
    await again()
    await vi.waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    return ran
  }

  it('runs again only on her yes, sending the question back', async () => {
    const sent = gateway()
    const ran = await retry()
    await vi.waitFor(() => expect(ran).toEqual([DETAIL.confirm]))
    expect(sent).toEqual([null, { confirm: DETAIL.confirm }])
    expect(confirmSpy.mock.calls[0][0]).toMatchObject({ title: 'Run this turn again?', confirmLabel: 'Run it again' })
  })

  it('a No runs nothing', async () => {
    confirmSpy.mockImplementation(async () => false)
    const sent = gateway()
    const ran = await retry()
    await new Promise((r) => setTimeout(r, 0))
    expect(ran).toEqual([])
    expect(sent).toEqual([null])
  })
})
