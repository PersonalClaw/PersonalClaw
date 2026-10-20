import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import { ApiError } from '../../lib/api'
import { userTurn, assistantTurn, type ChatTurn } from './chatTypes'

// ── The chat's read-aloud, as a controller of its own ─────────────────────────────────────────
//
// `useChatVoice` plays a reply on Speak, reads out a reply to a message this tab sent when
// "Speak replies aloud" is on, and says on the composer's notice line why a reading could not play.

const SESSION = 'chat-5-voice'

const h = vi.hoisted(() => ({
  voiceSynthesize: vi.fn(),
  voice: { voiceCfg: { confirmation_phrases: ['send it'], exit_phrases: ['never mind'], duplex_mute_enabled: true }, speakReplies: false },
}))

vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, voiceSynthesize: h.voiceSynthesize } }
})
vi.mock('./voiceConfig', () => ({ useVoiceConfig: () => h.voice }))

let mod: typeof import('./useChatVoice')

const TURNS: ChatTurn[] = [userTurn('Read me the summary.'), assistantTurn('The feed refetches because it ignores its validators.')]

function mount() {
  const notice = { notice: null, showError: vi.fn(), showInfo: vi.fn(), clear: vi.fn() }
  const hook = renderHook(({ turns }) => mod.useChatVoice({ sessionRef: { current: SESSION }, turns, notice }), { initialProps: { turns: TURNS } })
  return { ...hook, notice }
}

beforeEach(async () => {
  h.voiceSynthesize.mockReset().mockResolvedValue({})
  h.voice.speakReplies = false
  mod = await import('./useChatVoice')
  mod.repliesToSpeak.clear()
})
afterEach(() => cleanup())

describe('useChatVoice', () => {
  it('reads a reply on Speak, and a second Speak on it stops the reading', async () => {
    const { result } = mount()
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    expect(h.voiceSynthesize).toHaveBeenCalledWith('The feed refetches.', SESSION, expect.stringMatching(/^read-/))
    expect(result.current.speakingTurn).toBe(1)
    expect(result.current.readingRef.current?.auto).toBe(false)
    act(() => { void result.current.speak('The feed refetches.', 1) })
    expect(result.current.speakingTurn).toBeNull()
    expect(result.current.readingRef.current).toBeNull()
  })

  it('says why a reading could not play, in the server’s words, and stops claiming it plays', async () => {
    h.voiceSynthesize.mockRejectedValue(new ApiError('No text-to-speech model is set up.', 409, 'tts_unbound'))
    const { result, notice } = mount()
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    expect(notice.showError).toHaveBeenCalledWith('No text-to-speech model is set up.', 'speak')
    expect(result.current.speakingTurn).toBeNull()
  })

  it('reads out the reply a send was owed once it finishes, when Speak replies aloud is on', async () => {
    h.voice.speakReplies = true
    const { result } = mount()
    act(() => result.current.oweSpokenReply(SESSION))
    expect(mod.repliesToSpeak.get(SESSION)).toBe(1)
    act(() => result.current.replyFinished(SESSION, { last: true }))
    await waitFor(() => expect(h.voiceSynthesize).toHaveBeenCalledWith(TURNS[1].segments.map((s) => (s.kind === 'text' ? s.text : '')).join(''), SESSION, expect.any(String)))
    expect(result.current.readingRef.current?.auto).toBe(true)
    expect(mod.repliesToSpeak.has(SESSION)).toBe(false)
  })

  it('reads nothing on its own while the setting is off, and counts down a queue it owes', () => {
    const { result } = mount()
    act(() => { result.current.oweSpokenReply(SESSION); result.current.oweSpokenReply(SESSION) })
    act(() => result.current.replyFinished(SESSION, { last: false }))
    expect(mod.repliesToSpeak.get(SESSION)).toBe(1)
    expect(h.voiceSynthesize).not.toHaveBeenCalled()
  })
})
