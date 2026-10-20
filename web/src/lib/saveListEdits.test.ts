import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

// ── A list of names is saved as its edits, never as the page's copy ──────────────────────────
//
// `StrListField`, the voice phrase chips and the external-access allow-list used to PATCH the whole
// list they painted. A tab opened before another tab added a name then dropped that name the next
// time it touched any chip. `saveListEdits` sends only the difference — each removed name, then each
// added one — which the gateway applies to what is stored when it lands.

const reply = (o: unknown) => new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

describe('api.saveListEdits', () => {
  it('sends one remove and one add — the list itself never goes over the wire', async () => {
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(String(init.body)))
      // What is stored: another tab had added `z` meanwhile, and it survives both edits.
      return reply({ voice: { exit_phrases: bodies.length === 1 ? ['a', 'z'] : ['a', 'z', 'c'] } })
    }))
    const stored = await api.saveListEdits('voice.exit_phrases', ['a', 'b'], ['a', 'c'])
    expect(bodies).toEqual([
      { path: 'voice.exit_phrases', remove: 'b' },
      { path: 'voice.exit_phrases', add: 'c' },
    ])
    expect(bodies.some((b) => 'value' in b), 'no whole-list write').toBe(false)
    expect(stored, 'the list as stored after the last edit, not the page’s next copy').toEqual(['a', 'z', 'c'])
  })

  it('sends nothing when nothing changed, and answers with the list it was given', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)
    expect(await api.saveListEdits('voice.exit_phrases', ['a'], ['a'])).toEqual(['a'])
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
