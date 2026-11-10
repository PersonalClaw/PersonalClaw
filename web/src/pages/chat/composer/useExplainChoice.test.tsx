import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

// ── The chat's explanations: restored from the backend's resolution, never derived here ────────
//
// `useReplyStyle`'s twin for Chat preferences' Explanations section. It adopts the backend's
// RESOLVED choice and scope (from the session detail and each PATCH's answer), holds a pick made
// before the chat exists and writes it when the chat is created, and hands each answer's
// reply-style state on, since its preview is the whole text the next turn appends.

const h = vi.hoisted(() => ({
  setSessionExplain: vi.fn(),
  personalclawConfig: vi.fn(),
  notify: vi.fn(),
}))

vi.mock('../../../lib/api', () => ({
  api: { setSessionExplain: h.setSessionExplain, personalclawConfig: h.personalclawConfig },
}))
vi.mock('../../../app/appSdk', () => ({ notify: h.notify }))

import { useExplainChoice } from './useExplainChoice'

function answered(explain: string, over: Record<string, unknown> = {}) {
  return {
    ok: true, explain, explain_effective: explain || 'briefly', explain_source: explain ? 'conversation' : 'platform',
    explain_default: 'briefly', reply_style: '', reply_style_effective: 'default', reply_style_source: 'platform',
    reply_style_agent_default: '', reply_style_preview: null, ...over,
  }
}

beforeEach(() => {
  h.setSessionExplain.mockReset()
  h.personalclawConfig.mockReset()
  h.notify.mockReset()
})

describe('useExplainChoice', () => {
  it('restores the RESOLVED state the session detail carries', () => {
    const { result } = renderHook(() => useExplainChoice(() => 's1', vi.fn()))
    act(() => result.current.restore({ explain: '', explain_effective: 'briefly', explain_source: 'platform', explain_default: 'briefly' }))
    expect(result.current.choice).toMatchObject({ choice: '', effective: 'briefly', source: 'platform', homeDefault: 'briefly' })
  })

  it('a pick PATCHes explain, adopts the answer, and hands its reply-style state on', async () => {
    h.setSessionExplain.mockResolvedValue(answered('step_by_step'))
    const onVoice = vi.fn()
    const { result } = renderHook(() => useExplainChoice(() => 's1', onVoice))
    let said = ''
    await act(async () => { said = await result.current.choice.onSelect('step_by_step') })
    expect(said).toBe('saved')
    expect(h.setSessionExplain).toHaveBeenCalledWith('s1', 'step_by_step')
    expect(result.current.choice).toMatchObject({ choice: 'step_by_step', effective: 'step_by_step', source: 'conversation' })
    expect(onVoice).toHaveBeenCalledWith(expect.objectContaining({ reply_style_effective: 'default' }))
  })

  it('a failed PATCH is told, and the section goes back to what it showed', async () => {
    h.setSessionExplain.mockRejectedValue(new Error('gateway refused'))
    const onVoice = vi.fn()
    const { result } = renderHook(() => useExplainChoice(() => 's1', onVoice))
    act(() => result.current.restore({ explain: 'briefly', explain_effective: 'briefly', explain_source: 'conversation', explain_default: 'default' }))
    let said = ''
    await act(async () => { said = await result.current.choice.onSelect('with_background') })
    expect(said).toBe('failed')
    expect(h.notify).toHaveBeenCalledWith("Couldn't save this chat's explanations: gateway refused", 'error')
    expect(result.current.choice.choice).toBe('briefly')
    expect(onVoice).not.toHaveBeenCalled()
  })

  it("a refused pick's line and notice say it in plain words, naming no route, field or id", async () => {
    // The server's own sentence for a value that is not one of the choices (`api_chat_session_explain`).
    const sentence = "That isn't one of the explanation choices. Pick one from the list."
    h.setSessionExplain.mockRejectedValue(new Error(sentence))
    const { result } = renderHook(() => useExplainChoice(() => 's1', vi.fn()))
    act(() => result.current.restore({ explain: 'briefly', explain_effective: 'briefly', explain_source: 'conversation', explain_default: 'default' }))
    await act(async () => { await result.current.choice.onSelect('with_background') })
    const line = result.current.choice.refused
    const notice = String(h.notify.mock.calls.at(-1)?.[0])
    for (const said of [line, notice]) for (const word of ['/api/', 'reply_style', 'explain must', 'session']) expect(said).not.toContain(word)
    expect([line, notice]).toEqual([sentence, `Couldn't save this chat's explanations: ${sentence}`])
  })

  it('picks reach the server one after another: an overtaken one is not sent, and a refused last one goes back to the one before it', async () => {
    const calls: { explain: string; ok: (v: unknown) => void; no: (e: Error) => void }[] = []
    h.setSessionExplain.mockImplementation((_s: string, explain: string) => new Promise((ok, no) => { calls.push({ explain, ok, no }) }))
    const onVoice = vi.fn()
    const { result } = renderHook(() => useExplainChoice(() => 's1', onVoice))
    let first: Promise<string> = Promise.resolve('')
    let second: Promise<string> = Promise.resolve('')
    let third: Promise<string> = Promise.resolve('')
    await act(async () => { first = result.current.choice.onSelect('briefly') })
    await act(async () => { second = result.current.choice.onSelect('step_by_step') })
    await act(async () => { third = result.current.choice.onSelect('with_background') })
    expect(calls.map((c) => c.explain)).toEqual(['briefly'])
    await act(async () => { calls[0].ok(answered('briefly')) })
    await waitFor(() => expect(calls.map((c) => c.explain)).toEqual(['briefly', 'with_background']))
    await act(async () => { calls[1].no(new Error('gateway refused')) })
    expect([await first, await second, await third]).toEqual(['stale', 'stale', 'failed'])
    expect(result.current.choice.choice).toBe('briefly')
    expect(result.current.choice.refused).toBe('gateway refused')
    expect(onVoice).toHaveBeenCalledTimes(1)
    expect(h.notify).toHaveBeenCalledTimes(1)
  })

  it('a pick before the chat exists is held, then written when it is created', async () => {
    h.setSessionExplain.mockResolvedValue(answered('briefly'))
    const onVoice = vi.fn()
    const { result } = renderHook(() => useExplainChoice(() => null, onVoice))
    let said = ''
    await act(async () => { said = await result.current.choice.onSelect('briefly') })
    expect(said).toBe('held')
    expect(h.setSessionExplain).not.toHaveBeenCalled()
    expect(result.current.choice).toMatchObject({ choice: 'briefly', source: '' })
    await act(async () => { await result.current.persistOnCreate('s-new') })
    expect(h.setSessionExplain).toHaveBeenCalledWith('s-new', 'briefly')
    expect(onVoice).toHaveBeenCalled()
  })

  it('a chat created with no pick writes nothing', async () => {
    const { result } = renderHook(() => useExplainChoice(() => null, vi.fn()))
    await act(async () => { await result.current.persistOnCreate('s-new') })
    expect(h.setSessionExplain).not.toHaveBeenCalled()
  })

  it('reads your default from the config for a chat not created yet', async () => {
    h.personalclawConfig.mockResolvedValue({ agent: { explain: 'with_background' } })
    const { result } = renderHook(() => useExplainChoice(() => null, vi.fn()))
    await act(async () => { await result.current.readHomeDefault() })
    expect(result.current.choice.homeDefault).toBe('with_background')
  })
})
