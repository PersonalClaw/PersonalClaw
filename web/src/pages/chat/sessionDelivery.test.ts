import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { deliverableToOpenSession } from './sessionDelivery'

// ── Agent routing never surfaced on a new chat's FIRST message (issue 569) ─────────────────────
//
// Measured against a live gateway, browser-driven, same message and same agent:
//
//   new chat (session created BY the send)  backend suggested (score 1.000)   chip: NO
//   existing session, same message          backend suggested (score 1.000)   chip: YES
//
// The mechanism is a TRANSPORT gap, not a stale comparison. `/api/chat` broadcasts
// `routing_suggestion` synchronously, before the run task's first await, so it is the earliest
// frame of a send. Meanwhile the frontend creates the session and navigates, which re-keys
// ChatSession (`new-<epoch>` → the session key) — the remount CLOSES the ChatPage socket and its
// replacement is still handshaking. The recorded socket lifecycle for that send reads:
//
//   ctor#1..#5  open#1 open#2 open#3 open#4  close-called#4  ctor#6   ← #4 = ChatPage's socket
//   routing_suggestion delivered to #1 #2 #3 … and to neither #4 (closed) nor #6 (connecting)
//
// So the frame reached the page and NOT the one consumer that renders it. The SEL audit logged a
// suggestion as surfaced while nothing rendered — and the frequency cap (one suggestion per five
// user turns) then silenced routing for the next five turns of that conversation.
//
// 🔑 THE FIX IS A SECOND TRANSPORT, NOT A BUFFER OR A RETRY. The send RESPONSE carries the same
// payload; a response exists only because its request did, so it cannot be raced by anything. The
// pending payload is held by ChatPage, ABOVE the remount that destroyed the previous holder.
//
// 🔑 AND THEREFORE EXACTLY ONE PLACE RESOLVES IDENTITY. A second delivery route is precisely where
// a second, subtly different notion of "is this mine?" gets invented — and this file's other half
// is the rail against that. Before this change `onWs` carried ELEVEN copies of
// `if (d.session !== sessionRef.current) break`, plus three near-misses that each compared
// something slightly different. The server owns session identity (it stamps the session on the
// payload); this side only decides whether that session is the one on screen, in one function.

describe('deliverableToOpenSession — the one session gate', () => {
  it('delivers a payload the server named for the open session', () => {
    expect(deliverableToOpenSession('chat-7', 'chat-7')).toBe(true)
  })

  it('REFUSES a payload named for a different session', () => {
    // The wrong-session direction. A suggestion computed for another chat must never be
    // applied to this one — dropping it is correct, delivering it is a cross-session leak.
    expect(deliverableToOpenSession('chat-8', 'chat-7')).toBe(false)
  })

  it('delivers id-keyed payloads that name no session (approval / voice / side chat)', () => {
    expect(deliverableToOpenSession(undefined, 'chat-7')).toBe(true)
  })

  it('delivers nothing when no session is open', () => {
    expect(deliverableToOpenSession('chat-7', null)).toBe(false)
    expect(deliverableToOpenSession(undefined, null)).toBe(false)
    expect(deliverableToOpenSession('chat-7', '')).toBe(false)
  })

  it('treats a null/empty/non-string session as foreign rather than as "unscoped"', () => {
    // `undefined` is the ONLY "not session-scoped" signal. A null or blank session is a
    // producer bug, and reading it as "deliverable anywhere" is how a payload lands in the
    // wrong chat — the exact failure this gate exists to make impossible.
    expect(deliverableToOpenSession(null, 'chat-7')).toBe(false)
    expect(deliverableToOpenSession('', 'chat-7')).toBe(false)
    expect(deliverableToOpenSession(0, 'chat-7')).toBe(false)
  })
})

const CHAT_PAGE = readFileSync(join(process.cwd(), 'src', 'pages', 'ChatPage.tsx'), 'utf8')
/** Comments describe the old duplication on purpose, so the rail must read CODE only. */
const stripComments = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
/** The `onWs` callback body — where every WS frame is dispatched. */
function onWsBody(src: string): string {
  const start = src.indexOf('const onWs = useCallback(')
  expect(start, 'onWs must exist in ChatPage — the rail is keyed on it').toBeGreaterThan(-1)
  const end = src.indexOf('\n  }, [])', start)
  expect(end, 'onWs must end with the empty-dep close').toBeGreaterThan(start)
  return src.slice(start, end)
}

describe('the session gate is resolved in ONE place', () => {
  it('onWs gates through the shared resolver, exactly once', () => {
    const body = stripComments(onWsBody(CHAT_PAGE))
    const calls = [...body.matchAll(/deliverableToOpenSession\s*\(/g)]
    // One for the frame gate; one for session_title, whose payload names the session in
    // `key` rather than `session` so it cannot be gated by the frame check above — but it
    // resolves through the SAME function rather than growing its own comparison.
    expect(calls.length, 'onWs must resolve identity only via the shared resolver').toBe(2)
    expect(body).toContain('if (!deliverableToOpenSession(d.session, s)) return')
  })

  it('no case inside onWs re-derives session identity', () => {
    // 🪤 Keyed on the POPULATION of comparisons, not on the compliant pattern: a rail that
    // greps for the fixed form can only ever visit sites that already comply, which is how
    // eleven copies of this rule survived in the first place.
    const body = stripComments(onWsBody(CHAT_PAGE))
    const lines = body.split('\n')
    const offenders: string[] = []
    for (const line of lines) {
      // Any comparison of a frame field against the open-session ref is a second gate.
      // `const sk = sessionRef.current` + a later `sk !== sessionRef.current` is NOT one:
      // that is a navigated-away-mid-fetch check on an async continuation, so it is
      // allowed explicitly rather than by accident.
      if (/\bsk\s*!==\s*sessionRef\.current\b/.test(line)) continue
      if (/(d\.\w+|\bkey\b)\s*(===|!==)\s*sessionRef\.current/.test(line)) offenders.push(line.trim())
      if (/sessionRef\.current\s*(===|!==)\s*(d\.\w+|\bkey\b)\b/.test(line)) offenders.push(line.trim())
    }
    expect(offenders, `a second session gate reappeared:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the routing chip is rendered from a DERIVED value, never from raw pending state', () => {
    const src = stripComments(CHAT_PAGE)
    // The render gate is the proof that a suggestion cannot be shown for the wrong session:
    // `routingSuggestion` only exists when the resolver says the payload is ours.
    expect(src).toMatch(
      /const routingSuggestion = deliverableToOpenSession\(pendingRouting\?\.session, sessionId\)/,
    )
    expect(src, 'ChatSession must not own the pending payload — the remount destroys it')
      .not.toMatch(/useState<RoutingSuggestion \| null>\(null\)[\s\S]{0,200}function ChatSession/)
  })

  it('the pending payload is owned ABOVE the remount boundary', () => {
    const src = stripComments(CHAT_PAGE)
    const chatPageStart = src.indexOf('export function ChatPage(')
    const chatSessionStart = src.indexOf('function ChatSession(')
    expect(chatPageStart).toBeGreaterThan(-1)
    expect(chatSessionStart).toBeGreaterThan(chatPageStart)
    const outer = src.slice(chatPageStart, chatSessionStart)
    // ChatPage survives `chat/new` → `chat/<key>` (the route wrapper is keyed on the route
    // name); ChatSession does not. State declared here is what lets the send's own response
    // still find a home after the send's component has been unmounted.
    expect(outer, 'ChatPage must hold the pending routing suggestion').toMatch(
      /const \[routing, setRouting\] = useState<RoutingSuggestion \| null>\(null\)/,
    )
    // Every ChatSession mount must receive it — a call site that forgot would silently lose
    // the first-message suggestion again for that entry point (project launch, /chat/new, deep link).
    const mounts = [...outer.matchAll(/<ChatSession\b/g)]
    expect(mounts.length, 'ChatPage mounts ChatSession from three routes').toBe(3)
    const threaded = [...outer.matchAll(/routing=\{routing\} setRouting=\{setRouting\}/g)]
    expect(threaded.length, 'every ChatSession mount must be handed the pending payload').toBe(
      mounts.length,
    )
  })

  it('the send response applies the suggestion, with no identity logic of its own', () => {
    const src = stripComments(CHAT_PAGE)
    expect(src, 'the send response is the transport that reaches a just-created session').toMatch(
      /if \(sent\?\.routing_suggestion\?\.agent\) setRoutingSuggestion\(sent\.routing_suggestion\)/,
    )
    // The payload names its own session and the render gate resolves it; a comparison here
    // would be a third dialect.
    const sendStart = src.indexOf('const sent = await api.sendChat(')
    const window = src.slice(sendStart, src.indexOf('\n    catch (e) {', sendStart))
    expect(window.length, 'the send block must be bounded by its own catch').toBeGreaterThan(0)
    expect(window).not.toMatch(/sessionRef\.current|=== sid|!== sid/)
  })
})
