import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'

// ── "Session Map reacts to audio" is off unless the setting says on (#4776) ──────────────────
//
// The audio presence is opt-in, so the page reads it as on ONLY when the gateway says `true`: a
// field that is absent (a gateway from before the setting), a read that failed, `false`, or any
// other value — a string `"true"` included — leaves it off.
//
// VACUITY FLOOR. Every "off" row sits beside the `true` row, which reads the same way and is on, so
// a hook that never turns it on cannot pass.

const h = vi.hoisted(() => ({ config: vi.fn(), tts: vi.fn() }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, personalclawConfig: h.config, useCaseSettings: h.tts } }
})
vi.mock('../../lib/useChatSocket', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useChatSocket: () => {},
}))

beforeEach(() => {
  resetDataStore()
  localStorage.clear()
  h.tts.mockReset().mockResolvedValue({ value: { enabled: false, auto_speak: false } })
})
afterEach(() => { cleanup() })

/** The hook's `mapAudio` once its read has settled. */
async function mapAudioFor(voice: unknown, fails = false): Promise<boolean> {
  h.config.mockReset()
  if (fails) h.config.mockRejectedValue(new Error('gateway away'))
  else h.config.mockResolvedValue({ voice })
  const { useVoiceConfig } = await import('./voiceConfig')
  const { result } = renderHook(() => useVoiceConfig())
  await waitFor(() => expect(h.config).toHaveBeenCalled())
  // Let the read settle (or fail) before reading the answer.
  await new Promise((r) => setTimeout(r, 0))
  return result.current.mapAudio
}

describe('the Session Map audio setting, as the page reads it', () => {
  it('🔑 is on only when the gateway says true', async () => {
    expect(await mapAudioFor({ session_map_audio_enabled: true })).toBe(true)
  })

  it.each<[string, Record<string, unknown>]>([
    ['the field is absent (a gateway from before it)', { duplex_mute_enabled: true }],
    ['the setting is false', { session_map_audio_enabled: false }],
    ['the value is the string "true"', { session_map_audio_enabled: 'true' }],
    ['the value is 1', { session_map_audio_enabled: 1 }],
  ])('is off when %s', async (_why, voice) => {
    expect(await mapAudioFor(voice)).toBe(false)
  })

  it('is off when the read fails', async () => {
    expect(await mapAudioFor(undefined, true)).toBe(false)
  })
})
