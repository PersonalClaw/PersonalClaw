import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── "How should I explain things?" — asked once, after onboarding, never blocking ────────────
//
// The card shows on a new chat once onboarding is done and the question has not been answered or
// skipped (`explain_asked` in the onboarding state). An answer is the owner's Explanations default
// (`agent.explain`, the Settings row's own write), and the server records the question as asked
// with it; Skip only claims the question. Either way the card does not come back. Its answers are
// the table's (`explanations.json`). It is a card in the new chat's hero, not a dialog: the
// composer stays usable.

const h = vi.hoisted(() => ({ onboarding: vi.fn(), answerExplain: vi.fn(), patchConfig: vi.fn(), notify: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, onboarding: h.onboarding, answerExplain: h.answerExplain, patchConfig: h.patchConfig } }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

import { ExplainCard } from './ExplainCard'
import { noteProductTourRunning } from '../../../app/onboarding/tourLaunch'
import explanationsTable from '../../../../../src/personalclaw/explanations.json'

beforeEach(() => {
  for (const f of Object.values(h)) f.mockReset()
  h.onboarding.mockResolvedValue({ step: 'done', explain_asked: false })
  h.answerExplain.mockResolvedValue({ ok: true })
  h.patchConfig.mockResolvedValue({ ok: true })
})
afterEach(() => { cleanup(); noteProductTourRunning(false) })

describe('ExplainCard', () => {
  it('asks once onboarding is done and the question is still open', async () => {
    render(<ExplainCard />)
    expect(await screen.findByText('How should I explain things?')).toBeTruthy()
    for (const name of ['Briefly', 'Step by step', 'With the background', 'Skip'])
      expect(screen.getByRole('button', { name })).toBeTruthy()
    expect(screen.queryByRole('dialog'), 'a card, never a dialog over the composer').toBeNull()
  })

  it('offers the table\'s choices, in its order, and no "No preference"', async () => {
    render(<ExplainCard />)
    await screen.findByText('How should I explain things?')
    const offered = screen.getAllByRole('button').map((b) => b.textContent)
    const table = (explanationsTable as { id: string; label: string }[]).filter((r) => r.id !== 'default').map((r) => r.label)
    expect(offered).toEqual([...table, 'Skip'])
  })

  it('stays hidden once it was answered or skipped', async () => {
    h.onboarding.mockResolvedValue({ step: 'done', explain_asked: true })
    render(<ExplainCard />)
    await waitFor(() => expect(h.onboarding).toHaveBeenCalled())
    expect(screen.queryByText('How should I explain things?')).toBeNull()
  })

  it('stays hidden while onboarding is not finished', async () => {
    h.onboarding.mockResolvedValue({ step: 'essentials', explain_asked: false })
    render(<ExplainCard />)
    await waitFor(() => expect(h.onboarding).toHaveBeenCalled())
    expect(screen.queryByText('How should I explain things?')).toBeNull()
  })

  it('stays hidden when the state cannot be read, rather than asking blind', async () => {
    h.onboarding.mockRejectedValue(new Error('offline'))
    render(<ExplainCard />)
    await waitFor(() => expect(h.onboarding).toHaveBeenCalled())
    expect(screen.queryByText('How should I explain things?')).toBeNull()
  })

  it.each([
    ['Briefly', 'briefly'],
    ['Step by step', 'step_by_step'],
    ['With the background', 'with_background'],
  ])('%s sets the Explanations default to %s and the card goes away', async (label, choice) => {
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: label }))
    await waitFor(() => expect(h.patchConfig).toHaveBeenCalledWith('agent.explain', choice))
    await waitFor(() => expect(screen.queryByText('How should I explain things?')).toBeNull())
    expect(h.answerExplain, 'the default\'s write records the question as asked').not.toHaveBeenCalled()
  })

  it('Skip only claims the question and writes no default', async () => {
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Skip' }))
    await waitFor(() => expect(h.answerExplain).toHaveBeenCalledWith('skip'))
    await waitFor(() => expect(screen.queryByText('How should I explain things?')).toBeNull())
    expect(h.patchConfig).not.toHaveBeenCalled()
  })

  it('says the answer becomes the Explanations default, and where to change it', async () => {
    render(<ExplainCard />)
    expect(await screen.findByText(
      'Your answer becomes your Explanations default. Change it any time in Settings → Agent → Agent defaults → Explanations.',
    )).toBeTruthy()
    expect(screen.queryByText(/under Memory/)).toBeNull()
  })

  it('while an answer is saved its button says so, and none can be pressed again', async () => {
    let done = (_v: unknown) => {}
    h.patchConfig.mockReturnValue(new Promise((r) => { done = r }))
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Step by step' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Step by step' }).getAttribute('aria-busy')).toBe('true'))
    for (const name of ['Briefly', 'With the background', 'Skip'])
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled, name).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(h.patchConfig).toHaveBeenCalledTimes(1)
    expect(h.answerExplain).not.toHaveBeenCalled()
    done({ ok: true })
    await waitFor(() => expect(screen.queryByText('How should I explain things?')).toBeNull())
  })

  it('asks nothing while the product tour is walking the app, of the owner or the gateway', async () => {
    noteProductTourRunning(true)
    render(<ExplainCard />)
    await Promise.resolve()
    expect(h.onboarding).not.toHaveBeenCalled()
    expect(screen.queryByText('How should I explain things?')).toBeNull()
  })

  it.each([
    ['an answer', 'Briefly', 'patchConfig'],
    ['a skip', 'Skip', 'answerExplain'],
  ] as const)('%s that fails is told and the card stays', async (_what, label, call) => {
    h[call].mockRejectedValue(new Error('gateway refused'))
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: label }))
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Couldn't save how you like things explained: gateway refused", 'error'))
    expect(screen.getByText('How should I explain things?')).toBeTruthy()
  })
})

describe('the new chat hero carries the card', () => {
  it('ChatHero renders it above the starters', () => {
    const src = readFileSync(join(process.cwd(), 'src/pages/chat/hero/ChatHero.tsx'), 'utf8')
    expect(src).toMatch(/<ExplainCard \/>/)
    expect(src.indexOf('<ExplainCard />')).toBeLessThan(src.indexOf('<StarterChips'))
  })
})
