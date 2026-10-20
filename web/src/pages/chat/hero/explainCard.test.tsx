import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── "How should I explain things?" — asked once, after onboarding, never blocking ────────────
//
// The card shows on a new chat once onboarding is done and the question has not been answered or
// skipped (`explain_asked` in the onboarding state). An answer writes a pinned `style` preference
// on the server; Skip writes nothing. Either way the server records it as asked, so the card does
// not come back. It is a card in the new chat's hero, not a dialog: the composer stays usable.

const h = vi.hoisted(() => ({ onboarding: vi.fn(), answerExplain: vi.fn(), notify: vi.fn() }))
vi.mock('../../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../../lib/api')>()
  return { ...real, api: { ...real.api, onboarding: h.onboarding, answerExplain: h.answerExplain } }
})
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

import { ExplainCard } from './ExplainCard'
import { noteProductTourRunning } from '../../../app/onboarding/tourLaunch'

beforeEach(() => {
  for (const f of Object.values(h)) f.mockReset()
  h.onboarding.mockResolvedValue({ step: 'done', explain_asked: false })
  h.answerExplain.mockResolvedValue({ ok: true })
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
    ['Skip', 'skip'],
  ])('%s sends %s and the card goes away', async (label, answer) => {
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: label }))
    await waitFor(() => expect(h.answerExplain).toHaveBeenCalledWith(answer))
    await waitFor(() => expect(screen.queryByText('How should I explain things?')).toBeNull())
  })

  it('an answer says where the preference lives', async () => {
    render(<ExplainCard />)
    expect(await screen.findByText(/change or forget it under Memory/)).toBeTruthy()
  })

  it('while an answer is saved its button says so, and none can be pressed again', async () => {
    let done = (_v: unknown) => {}
    h.answerExplain.mockReturnValue(new Promise((r) => { done = r }))
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Step by step' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Step by step' }).getAttribute('aria-busy')).toBe('true'))
    for (const name of ['Briefly', 'With the background', 'Skip'])
      expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled, name).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(h.answerExplain).toHaveBeenCalledTimes(1)
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

  it('a failed answer is told and the card stays', async () => {
    h.answerExplain.mockRejectedValue(new Error('gateway refused'))
    render(<ExplainCard />)
    fireEvent.click(await screen.findByRole('button', { name: 'Briefly' }))
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
