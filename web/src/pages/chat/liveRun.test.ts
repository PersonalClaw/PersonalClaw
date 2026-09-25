import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { streamingAtMount } from './liveRun'
import { resolveSendButton, sendButtonIsActive } from '../../ui/composer/sendButtonState'

// ── #3444: a message sent while the previous run is live was SILENTLY ABSORBED ─────────
//
// Measured from a Playwright trace driving six scripted turns: 5 user turns rendered for
// 6 sends. Prompt (2) was absent — not queued, not refused, no error. The button had been
// located and clicked under the name "Send message" while the backend run was still live.
//
// The window: the send that CREATES a session navigates `new` → `chat/<key>`, remounting
// ChatSession with a fresh `streaming = false` over a run that is still going. `send()`
// branches on that same flag, so a click in the window started a FRESH turn — painting a
// bubble for a turn the server never dispatched (it queued the message instead, and
// answered `{queued:true}`, which that path does not read).
//
// These are the decision-level assertions. The composer's own button is the user-visible
// consequence, so it is asserted THROUGH `resolveSendButton` rather than restated: the
// point of the fix is that the two now agree at the instant of the click.

const AT_THE_CLICK = { processing: false, canSend: true, canQueue: true, justSent: false }

describe('#3444 — what the composer advertises at the moment of the click', () => {
  it('CONTROL: a bare `false` at mount advertises Send over a live run', () => {
    // This is what shipped. The flag is the ONLY input that was wrong, so feeding the old
    // value through the unchanged resolver reproduces the exact label the trace clicked.
    const streaming = false
    expect(resolveSendButton({ ...AT_THE_CLICK, streaming })).toBe('send')
  })

  it('a handed-off run advertises Steer, so the click sends INTO the turn', () => {
    const streaming = streamingAtMount('chat-7', 'chat-7')
    expect(streaming).toBe(true)
    expect(resolveSendButton({ ...AT_THE_CLICK, streaming })).toBe('steer')
    // and it is clickable — a correct label on an inert control would refuse silently,
    // which is the same absence wearing a different face.
    expect(sendButtonIsActive(resolveSendButton({ ...AT_THE_CLICK, streaming }))).toBe(true)
  })

  it('still offers Stop, not Send, when there is nothing to steer', () => {
    const streaming = streamingAtMount('chat-7', 'chat-7')
    expect(resolveSendButton({ ...AT_THE_CLICK, streaming, canSend: false })).toBe('stop')
  })

  it('does not leak a handoff into a DIFFERENT session', () => {
    expect(streamingAtMount('chat-7', 'chat-8')).toBe(false)
  })

  it('starts a genuinely new chat idle', () => {
    // No handoff, or no session on screen → nothing to inherit. Claiming a run here would
    // be the mirror-image lie: Steer over an idle backend.
    expect(streamingAtMount('', null)).toBe(false)
    expect(streamingAtMount('', 'chat-7')).toBe(false)
    expect(streamingAtMount('chat-7', null)).toBe(false)
    expect(streamingAtMount('chat-7', '')).toBe(false)
  })
})

// ── The wiring rails ──────────────────────────────────────────────────────────────────
// The decision is only worth anything at its call sites, and none of them is reachable
// from a jsdom mount of the 5000-line CodeMirror transcript. So they are pinned from
// source, the way `sessionDelivery.test.ts` pins the session gate for the sibling defect
// that came out of this same remount boundary.

const CHAT_PAGE = readFileSync(join(process.cwd(), 'src', 'pages', 'ChatPage.tsx'), 'utf8')
const stripComments = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const CODE = stripComments(CHAT_PAGE)

describe('ChatPage wires the handoff', () => {
  it('initialises `streaming` from it, not from a bare false', () => {
    expect(
      CODE,
      'a fresh `false` on the remounted instance is the window the composer lies in (#3444)',
    ).not.toMatch(/const \[streaming, setStreaming\] = useState\(false\)/)
    expect(CODE).toMatch(
      /const \[streaming, setStreaming\] = useState\(\(\) => streamingAtMount\(liveRun, sessionId\)\)/,
    )
  })

  it('seeds `streamingRef` from the same value', () => {
    // send() branches on the REF, not the state. A `false` here would reopen the window
    // one layer below an honest button — the worst version, because the label would be
    // right and the behaviour still wrong.
    expect(CODE, 'streamingRef must seed from the resolved initial state').toMatch(/useRef\(streaming\)/)
    expect(CODE).not.toMatch(/const streamingRef = useRef\(false\)/)
  })

  it('owns the handoff ABOVE the remount boundary', () => {
    const page = CODE.indexOf('export function ChatPage(')
    const session = CODE.indexOf('function ChatSession(')
    expect(page).toBeGreaterThan(-1)
    expect(session).toBeGreaterThan(page)
    const decl = CODE.indexOf("const [liveRun, setLiveRun] = useState('')")
    expect(decl, 'the handoff must be declared in ChatPage').toBeGreaterThan(page)
    expect(decl, 'declared inside ChatSession, the remount destroys it').toBeLessThan(session)
    // every ChatSession render must receive it, or the branch it is missing from mounts idle
    const mounts = [...CODE.matchAll(/<ChatSession\s/g)].length
    const threaded = [...CODE.matchAll(/liveRun=\{liveRun\} setLiveRun=\{setLiveRun\}/g)].length
    expect(mounts).toBeGreaterThan(0)
    expect(threaded, 'every ChatSession mount needs the handoff').toBe(mounts)
  })

  it('records it BEFORE the navigate that causes the remount', () => {
    const set = CODE.indexOf('setLiveRun(created.key)')
    const nav = CODE.indexOf('navigate(`chat/${created.key}`')
    expect(set, 'ensureSession must hand the live run across').toBeGreaterThan(-1)
    expect(nav).toBeGreaterThan(-1)
    expect(set, 'recorded after the navigate, the replacement has already mounted idle').toBeLessThan(nav)
  })

  it('releases it when the run settles', () => {
    // Left set, a later mount of the same session would claim a finished run was live.
    expect(CODE, 'the handoff must be cleared as a turn settles').toMatch(/if \(!v && liveRun\) setLiveRun\(''\)/)
  })
})
