import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, act, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BUSY_REASON } from '../../../ui/unavailable'

// ── "How should I explain things?", asked once ──────────────────────────────────────────────
//
// A new chat after setup asks it once, in a small window over the hero, and records it as asked by
// the claim just before the window opens (`POST /api/onboarding/explain {"answer": "skip"}`), so a
// reload, a closed tab or a second window cannot bring it back. It waits for a moment: the page on
// screen, the tour not walking the app, no draft under way and nothing else holding the owner. A
// choice is the Explanations default (`agent.explain`, the Settings row's own write); Use the
// default, ×, Escape and the scrim write nothing more. Driven through ChatHero, where the new
// chat's hero mounts it, and fresh modules for each case, since the module keeps, for the page
// load, that the question was claimed.

const h = vi.hoisted(() => ({
  onboarding: vi.fn(), answerExplain: vi.fn(), patchConfig: vi.fn(), notify: vi.fn(), sessionTemplates: vi.fn(),
  /** Every other api call, by name: a dismiss must make none. */
  others: [] as string[],
}))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  const own: Record<string, unknown> = {
    onboarding: h.onboarding, answerExplain: h.answerExplain, patchConfig: h.patchConfig,
    suggestions: () => Promise.resolve({ suggestions: [], needs_model: '' }),
    sessionTemplates: h.sessionTemplates,
  }
  const base = { ...real.api, ...own } as Record<string, unknown>
  const api = new Proxy(base, {
    get: (t, p: string) => {
      const v = t[p]
      if (typeof v !== 'function' || p in own) return v
      return (...a: unknown[]) => { h.others.push(p); return (v as (...x: unknown[]) => unknown)(...a) }
    },
  })
  return { ...real, api }
})
vi.mock('../../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

const QUESTION = 'How should I explain things?'
const LEAD = 'I can shape how I explain things to you. That never changes a fact, a caveat or a refusal.'
let visibility: DocumentVisibilityState = 'visible'

async function hero(props: { draft?: string; draftUnderWay?: () => boolean; onExplainClosed?: () => void } = {}) {
  const { ChatHero } = await import('./ChatHero')
  const view = (p: typeof props) => (
    <ChatHero name="Alex Rivera" stage={<div data-testid="stage" />} onPickStarter={() => {}} onPickSuggestion={() => {}}
      draftUnderWay={p.draftUnderWay ?? (() => !!p.draft?.trim())} onExplainClosed={p.onExplainClosed ?? (() => {})} />
  )
  const r = render(view(props))
  return { ...r, again: (p: typeof props) => r.rerender(view(p)) }
}
const dialog = () => screen.queryByRole('dialog', { name: QUESTION })
const pause = (ms: number) => act(() => new Promise((r) => setTimeout(r, ms)))
const claims = () => h.answerExplain.mock.calls.length
/** Fake timers, for the cases that wait. Use tick() with getByRole or queryByRole only: findBy* and
 *  waitFor hang under a faked setTimeout, since RTL's asyncWrapper drains on a setTimeout(0). */
const fakeTimers = () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] }) })
  afterEach(() => { vi.useRealTimers() })
}
const tick = async (ms: number) => { await act(() => vi.advanceTimersByTimeAsync(ms)); await act(async () => {}) }
const otherDialog = (attrs: Record<string, string>) => {
  const el = document.createElement('div')
  for (const [k, v] of Object.entries({ ...attrs, 'data-other-dialog': '' })) el.setAttribute(k, v)
  return document.body.appendChild(el)
}

beforeEach(() => {
  vi.resetModules()
  for (const f of [h.onboarding, h.answerExplain, h.patchConfig, h.notify, h.sessionTemplates]) f.mockReset()
  h.others.length = 0
  h.onboarding.mockResolvedValue({ step: 'done', explain_asked: false })
  h.answerExplain.mockResolvedValue({ ok: true, state: { step: 'done', explain_asked: true } })
  h.patchConfig.mockResolvedValue({ ok: true })
  h.sessionTemplates.mockResolvedValue([])
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility })
})
afterEach(() => { cleanup(); document.querySelectorAll('[data-other-dialog]').forEach((d) => d.remove()) })

describe('the explain question opens once, as a window', () => {
  it('opens as a dialog named by its question, only once the claim has answered', async () => {
    let answer = (_v: unknown) => {}
    h.answerExplain.mockReturnValue(new Promise((r) => { answer = r }))
    await hero()
    await waitFor(() => expect(h.answerExplain).toHaveBeenCalledWith('skip'))
    expect(dialog(), 'shown before the question was recorded as asked').toBeNull()
    await act(async () => { answer({ ok: true }) })
    expect(await screen.findByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('says what it does, offers the three choices and the default, each described by its hint, and names where to change it', async () => {
    await hero()
    const win = await screen.findByRole('dialog', { name: QUESTION })
    expect(win.hasAttribute('aria-describedby'), 'the focused lead reads itself: a description would say it twice').toBe(false)
    expect(win.textContent).toContain(LEAD)
    expect(screen.getByRole('button', { name: 'Briefly' })).toHaveAccessibleDescription('The short answer first. Details when you ask.')
    expect(screen.getByRole('button', { name: 'Step by step' })).toHaveAccessibleDescription('Numbered steps you can follow.')
    expect(screen.getByRole('button', { name: 'With the background' })).toHaveAccessibleDescription('The why and the context, then the answer.')
    expect(screen.getByRole('button', { name: 'Use the default' })).toHaveAccessibleDescription('No preference: I explain things my usual way.')
    expect(win.textContent).toContain('Change this any time in Settings → Agent → Agent defaults → Explanations.')
    expect(win.textContent).toContain('For one chat only, use Chat preferences, the gear on the chat box.')
  })

  it("offers the table's choices in its order, each with its hint, then Use the default", async () => {
    const table = (await import('../../../../../src/personalclaw/explanations.json')).default as { id: string; label: string; hint: string }[]
    const answers = table.filter((r) => r.id !== 'default')
    await hero()
    const win = await screen.findByRole('dialog', { name: QUESTION })
    expect(within(win).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent))
      .toEqual(['Close', ...answers.map((r) => r.label), 'Use the default'])
    for (const r of answers) expect(win.textContent).toContain(r.hint)
  })

  it('never reads or claims again in one page load once it has opened', async () => {
    const first = await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    fireEvent.click(screen.getByRole('button', { name: 'Use the default' }))
    await waitFor(() => expect(dialog()).toBeNull())
    first.unmount()
    await hero()
    await pause(1200)
    expect(h.onboarding).toHaveBeenCalledTimes(1)
    expect(claims()).toBe(1)
    expect(dialog()).toBeNull()
  })

  it('a new chat that goes while its claim is out leaves the question to the next one', async () => {
    let answer = (_v: unknown) => {}
    h.answerExplain.mockReturnValueOnce(new Promise((r) => { answer = r }))
    const first = await hero()
    await waitFor(() => expect(claims()).toBe(1))
    first.unmount()
    await act(async () => { answer({ ok: true }) })
    await hero()
    expect(await screen.findByRole('dialog', { name: QUESTION }), 'claimed, never shown').toBeTruthy()
    expect(claims(), 'the next new chat claims nothing again').toBe(1)
    expect(h.onboarding, 'and reads nothing again').toHaveBeenCalledTimes(1)
  })
})

describe('after a claim that is refused', () => {
  fakeTimers()

  it('stays closed for good on a 409 question_answered: another window asked it', async () => {
    const { ApiError } = await import('../../../lib/api')
    h.answerExplain.mockRejectedValue(new ApiError('question_answered', 409, 'question_answered'))
    const first = await hero()
    await tick(400)
    expect(claims()).toBe(1)
    expect(dialog()).toBeNull()
    first.unmount()
    await hero()
    await tick(1200)
    expect(claims(), 'a later new chat in this page load claims again').toBe(1)
    expect(dialog()).toBeNull()
  })

  it('lets the next new chat try again after a 409 that is not question_answered', async () => {
    const { ApiError } = await import('../../../lib/api')
    h.answerExplain.mockRejectedValueOnce(new ApiError('unreadable', 409, 'store_unreadable'))
    const first = await hero()
    await tick(400)
    expect(claims()).toBe(1)
    first.unmount()
    await hero()
    await tick(400)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
    expect(claims()).toBe(2)
  })

  it('shows nothing when the claim fails, and the next new chat tries again', async () => {
    const { ApiError } = await import('../../../lib/api')
    h.answerExplain.mockRejectedValueOnce(new ApiError('gateway unavailable', 503, 'unavailable'))
    const first = await hero()
    await tick(400)
    expect(claims()).toBe(1)
    expect(dialog()).toBeNull()
    first.unmount()
    await hero()
    await tick(400)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
    expect(claims()).toBe(2)
  })
})

describe('an answer, or none', () => {
  it.each([
    ['Briefly', 'briefly'],
    ['Step by step', 'step_by_step'],
    ['With the background', 'with_background'],
  ])('%s sets the Explanations default to %s and closes the window', async (label, choice) => {
    await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    fireEvent.click(screen.getByRole('button', { name: label }))
    await waitFor(() => expect(h.patchConfig).toHaveBeenCalledWith('agent.explain', choice))
    await waitFor(() => expect(dialog()).toBeNull())
    expect(claims(), 'the claim before it opened is the one record of the question').toBe(1)
  })

  it('a refused choice keeps the window open with the server\'s sentence, which takes the focus and is said in a toast', async () => {
    h.patchConfig.mockRejectedValue(new Error('gateway refused'))
    await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    fireEvent.click(screen.getByRole('button', { name: 'Step by step' }))
    expect((await screen.findByRole('alert')).textContent).toBe('Not saved: gateway refused')
    expect(dialog()).toBeTruthy()
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('alert')))
    expect(h.notify).toHaveBeenCalledWith("Couldn't save how you like things explained: gateway refused", 'error')
  })

  it('while a choice saves, every other answer waits, Use the default included; after a refusal all are usable again', async () => {
    let refuse = (_e: unknown) => {}
    h.patchConfig.mockReturnValueOnce(new Promise((_r, j) => { refuse = j }))
    await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    fireEvent.click(screen.getByRole('button', { name: 'Step by step' }))
    expect(screen.getByRole('button', { name: 'Step by step' }).getAttribute('aria-busy')).toBe('true')
    for (const name of ['Briefly', 'With the background'])
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled, name).toBe(true)
    const keep = screen.getByRole('button', { name: 'Use the default' })
    expect(keep.getAttribute('aria-disabled'), 'Use the default waits too').toBe('true')
    expect(keep).toHaveAccessibleDescription(BUSY_REASON)
    fireEvent.click(keep)
    expect(dialog(), 'Use the default pressed while one saves').not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Briefly' }))
    expect(h.patchConfig, 'a second press while one saves').toHaveBeenCalledTimes(1)
    await act(async () => { refuse(new Error('gateway refused')) })
    await screen.findByRole('alert')
    for (const name of ['Briefly', 'Step by step', 'With the background', 'Use the default'])
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled, name).toBe(false)
    expect(screen.getByRole('button', { name: 'Use the default' }).getAttribute('aria-disabled'))
      .toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Briefly' }))
    await waitFor(() => expect(dialog()).toBeNull())
    expect(h.patchConfig).toHaveBeenLastCalledWith('agent.explain', 'briefly')
  })

  it('a refusal that lands after the window has closed is said in a toast', async () => {
    let refuse = (_e: unknown) => {}
    h.patchConfig.mockReturnValue(new Promise((_r, j) => { refuse = j }))
    await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    fireEvent.click(screen.getByRole('button', { name: 'Step by step' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(dialog()).toBeNull())
    await act(async () => { refuse(new Error('gateway refused')) })
    expect(h.notify).toHaveBeenCalledWith("Couldn't save how you like things explained: gateway refused", 'error')
  })

  it.each([
    ['Use the default', () => fireEvent.click(screen.getByRole('button', { name: 'Use the default' }))],
    ['×', () => fireEvent.click(screen.getByRole('button', { name: 'Close' }))],
    ['Escape', () => fireEvent.keyDown(window, { key: 'Escape' })],
    ['a scrim click', () => fireEvent.click(document.querySelector('[aria-modal="true"]')!.previousElementSibling!)],
  ] as const)('%s closes it and writes nothing more', async (_how, close) => {
    await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    const before = h.others.length
    close()
    await waitFor(() => expect(dialog()).toBeNull())
    expect(h.patchConfig).not.toHaveBeenCalled()
    expect(claims()).toBe(1)
    expect(h.others.slice(before), 'any other api call').toEqual([])
    expect(h.onboarding, 'no second read').toHaveBeenCalledTimes(1)
    expect(h.notify).not.toHaveBeenCalled()
  })
})

describe('it waits for its moment', () => {
  fakeTimers()

  it('while a draft is under way, then asks once the draft is gone', async () => {
    const view = await hero({ draft: 'half a thought' })
    await tick(1200)
    expect(h.onboarding, 'read while a draft is under way').not.toHaveBeenCalled()
    expect(dialog()).toBeNull()
    view.again({ draft: '' })
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('does not open over a draft typed while the state is read', async () => {
    let read = (_v: unknown) => {}
    h.onboarding.mockReturnValue(new Promise((r) => { read = r }))
    const view = await hero()
    await tick(400)
    expect(h.onboarding).toHaveBeenCalledTimes(1)
    view.again({ draft: 'half a thought' })
    await act(async () => { read({ step: 'done', explain_asked: false }) })
    await tick(500)
    expect(h.answerExplain, 'claimed while a draft was being typed').not.toHaveBeenCalled()
    expect(dialog(), 'opened over a draft being typed').toBeNull()
  })

  it('a claim that answers over a draft opens the window once the draft is gone, with no second claim', async () => {
    let answer = (_v: unknown) => {}
    h.answerExplain.mockReturnValueOnce(new Promise((r) => { answer = r }))
    const view = await hero()
    await tick(400)
    expect(claims()).toBe(1)
    view.again({ draft: 'half a thought' })
    await act(async () => { answer({ ok: true }) })
    await tick(1000)
    expect(dialog(), 'opened over a draft typed while the claim was out').toBeNull()
    view.again({ draft: '' })
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
    expect(claims(), 'no second claim').toBe(1)
    expect(h.onboarding, 'no second read').toHaveBeenCalledTimes(1)
  })

  it('while another dialog is open', async () => {
    const other = otherDialog({ role: 'dialog', 'aria-modal': 'true' })
    await hero()
    await tick(1200)
    expect(h.onboarding).not.toHaveBeenCalled()
    other.remove()
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('does not open over a dialog opened while the state is read', async () => {
    let read = (_v: unknown) => {}
    h.onboarding.mockReturnValue(new Promise((r) => { read = r }))
    await hero()
    await tick(400)
    expect(h.onboarding).toHaveBeenCalledTimes(1)
    otherDialog({ role: 'dialog', 'aria-modal': 'true' })
    await act(async () => { read({ step: 'done', explain_asked: false }) })
    await tick(500)
    expect(h.answerExplain, 'claimed over another dialog').not.toHaveBeenCalled()
    expect(dialog(), 'opened over another dialog').toBeNull()
  })

  it('while the phone\'s navigation drawer is open, a dialog with no aria-modal', async () => {
    const drawer = otherDialog({ role: 'dialog', 'aria-label': 'Navigation' })
    await hero()
    await tick(1200)
    expect(h.onboarding, 'read with the drawer open').not.toHaveBeenCalled()
    drawer.setAttribute('inert', '')
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION }), 'a closed drawer, inert, holds nothing up').toBeTruthy()
  })

  it.each([
    ['the palette', { 'data-command-palette': '' }],
    ['a menu', { 'data-flyout': '' }],
  ])('while %s is open, looking again once a second, then asks once it has closed', async (_what, attrs) => {
    const open = otherDialog(attrs)
    const looked = vi.spyOn(document, 'querySelector')
    try {
      await hero()
      await tick(5000)
      expect(h.onboarding, 'read with it open').not.toHaveBeenCalled()
      const looks = looked.mock.calls.filter(([sel]) => String(sel).includes('[data-flyout]')).length
      expect(looks, 'the looks in 5 s: one at 400 ms, then one a second').toBeLessThanOrEqual(5)
    } finally { looked.mockRestore() }
    open.remove()
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('while another text field has the focus', async () => {
    const field = document.body.appendChild(document.createElement('input'))
    field.setAttribute('data-other-dialog', '')
    field.focus()
    await hero()
    await tick(1200)
    expect(h.onboarding, 'read while another field was being typed in').not.toHaveBeenCalled()
    field.blur()
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('while the page is hidden, and asks once it has been on screen a moment', async () => {
    visibility = 'hidden'
    await hero()
    await tick(1200)
    expect(h.onboarding).not.toHaveBeenCalled()
    visibility = 'visible'
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    await tick(400)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('asks nothing while the product tour walks the app, of the owner or the gateway, and asks once it has ended', async () => {
    const { noteProductTourRunning } = await import('../../../app/onboarding/tourLaunch')
    noteProductTourRunning(true)
    try {
      await hero()
      await tick(1200)
      expect(h.onboarding).not.toHaveBeenCalled()
      expect(h.answerExplain).not.toHaveBeenCalled()
      expect(screen.queryByText(QUESTION)).toBeNull()
    } finally { noteProductTourRunning(false) }
    await tick(1000)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('looks again at most once a second while it waits', async () => {
    const looked = vi.fn(() => true)
    await hero({ draftUnderWay: looked })
    await tick(5000)
    // Settled at 400 ms, then once a second: 400, 1400, 2400, 3400 and 4400.
    expect(looked, 'it looks while it waits').toHaveBeenCalled()
    expect(looked.mock.calls.length, 'looks in 5 s').toBeLessThanOrEqual(5)
  })

  it('shows nothing when the state cannot be read, rather than asking blind', async () => {
    h.onboarding.mockRejectedValue(new Error('offline'))
    await hero()
    await tick(400)
    expect(h.onboarding).toHaveBeenCalled()
    await tick(500)
    expect(h.answerExplain).not.toHaveBeenCalled()
    expect(screen.queryByText(QUESTION)).toBeNull()
  })

  it.each([
    ['before setup is done', { step: 'essentials', explain_asked: false }],
    ['once it was asked', { step: 'done', explain_asked: true }],
  ])('shows nothing %s', async (_when, state) => {
    h.onboarding.mockResolvedValue(state)
    await hero()
    await tick(400)
    expect(h.onboarding).toHaveBeenCalled()
    await tick(500)
    expect(h.answerExplain).not.toHaveBeenCalled()
    expect(screen.queryByText(QUESTION)).toBeNull()
  })
})

describe('the 400 ms settle', () => {
  fakeTimers()

  it('reads nothing before the page has been on screen 400 ms', async () => {
    await hero()
    await tick(399)
    expect(h.onboarding).not.toHaveBeenCalled()
    await tick(1)
    expect(h.onboarding).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('dialog', { name: QUESTION })).toBeTruthy()
  })

  it('a new chat left within 400 ms reads and claims nothing', async () => {
    const view = await hero()
    await tick(300)
    view.unmount()
    await tick(2000)
    expect(h.onboarding).not.toHaveBeenCalled()
    expect(claims()).toBe(0)
  })

  it('waits 400 ms more once a hidden page is shown', async () => {
    visibility = 'hidden'
    await hero()
    await tick(2000)
    visibility = 'visible'
    act(() => { document.dispatchEvent(new Event('visibilitychange')) })
    await tick(399)
    expect(h.onboarding).not.toHaveBeenCalled()
    await tick(1)
    expect(h.onboarding).toHaveBeenCalledTimes(1)
  })

  it('settles on its own timer, whatever the clock says', async () => {
    vi.useRealTimers()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await hero()
    await tick(400)
    expect(h.onboarding, 'never read while the clock stands still').toHaveBeenCalledTimes(1)
  })
})

describe('where it sits and where focus goes', () => {
  it('opens with the focus on its first paragraph, and tells the page once it has gone', async () => {
    const closed = vi.fn()
    await hero({ onExplainClosed: closed })
    const win = await screen.findByRole('dialog', { name: QUESTION })
    await waitFor(() => expect(document.activeElement?.textContent).toBe(LEAD))
    expect(win.contains(document.activeElement)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(closed).toHaveBeenCalledTimes(1))
  })

  it('moves the focus with Tab from the first paragraph through the answers to Close, and round again', async () => {
    const laidOut = vi.spyOn(HTMLElement.prototype, 'offsetParent', 'get').mockImplementation(function (this: HTMLElement) { return this.isConnected ? document.body : null })
    try {
      const user = userEvent.setup()
      await hero()
      const win = await screen.findByRole('dialog', { name: QUESTION })
      await waitFor(() => expect(document.activeElement?.textContent).toBe(LEAD))
      const order = ['Briefly', 'Step by step', 'With the background', 'Use the default', 'Close', 'Briefly']
      const reached: string[] = []
      for (let i = 0; i < order.length; i++) {
        await user.tab()
        const a = document.activeElement
        reached.push((a?.getAttribute('aria-label') || a?.textContent || a?.tagName || '').trim())
      }
      expect(win.contains(document.activeElement)).toBe(true)
      expect(reached).toEqual(order)
    } finally { laidOut.mockRestore() }
  })

  it('renders nothing in the hero between the composer and the starters', async () => {
    h.sessionTemplates.mockResolvedValue([{ id: 't-1', name: 'Research deep dive', agent: 'researcher', model: '', reasoning_effort: 'high', first_prompt: 'Dig into' }])
    const { container } = await hero()
    await screen.findByRole('dialog', { name: QUESTION })
    expect(container.textContent, 'the hero itself carries the question').not.toContain(QUESTION)
    await waitFor(() => expect(screen.getByTestId('stage').nextElementSibling?.textContent).toContain('Your starters'))
  })
})
