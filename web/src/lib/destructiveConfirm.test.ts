import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

/** The client half of the destructive-confirm gate (#606, #604).
 *
 *  🔑 THE SERVER GATE AND THE CLIENT CALL ARE ONE CHANGE, AND THEY FAIL IN OPPOSITE WAYS. Adding
 *  `confirm: true` server-side without sending it turns two working buttons into a 400 for every
 *  user; sending it without the server gate leaves the route open to any other caller. So the
 *  request bodies are asserted here rather than assumed — the UI prompt is what asks the human, and
 *  this is what carries that answer to a route that now refuses without it.
 *
 *  🪤 The prompt itself is NOT what this checks. A test that only proved the flag is sent would
 *  pass just as happily if the dialog were deleted and the flag hardcoded — which is precisely the
 *  ungated shape the issues describe. The dialog call is asserted at its own call sites; this file
 *  is only about the wire. */
describe('destructive routes are called with an explicit confirm', () => {
  let calls: Array<{ url: string; body: unknown }>

  beforeEach(() => {
    calls = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined })
        return new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
  })

  afterEach(() => vi.unstubAllGlobals())

  it('mergeKnowledgeTag sends confirm, because merging deletes the source tag', async () => {
    const { api } = await import('./api')
    await api.mergeKnowledgeTag(1, 2)
    const call = calls.find((c) => c.url.includes('/merge'))
    expect(call, 'no merge request was made').toBeTruthy()
    expect(call!.body).toMatchObject({ into: 2, confirm: true })
  })

  it('resetTaskList sends confirm, because reset clears execution notes', async () => {
    const { api } = await import('./api')
    await api.resetTaskList('list-1')
    const call = calls.find((c) => c.url.includes('/reset'))
    expect(call, 'no reset request was made').toBeTruthy()
    expect(call!.body).toMatchObject({ confirm: true })
  })
})
