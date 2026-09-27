/**
 * THE THREE CUE POINTS.
 *
 * `soundCues.test.ts` proves the synth is silent when it should be. This file
 * proves the three moments the plan names actually reach it, and — just as
 * important — that nothing ELSE does:
 *
 *   turn ended         → `ChatPage`'s `sayTurnEnded`, where a turn's ending is said
 *   approval requested → `useApprovalToasts`, after its dedupe/active-session guards
 *   error toast        → `Toaster`, for `level: 'error'` only
 *
 * The first is asserted against SOURCE rather than a render: `ChatPage` is a large
 * page with a live socket, so a mounted assertion would be a fixture, not evidence.
 * The assertion is still about the CALL SITE, not about the string existing somewhere
 * in the file — it extracts `sayTurnEnded` and requires the cue inside it, behind the
 * guard that says each turn's ending once, with a vacuity check that the extraction
 * found real code. Comments are stripped first: a rail that a comment can satisfy is
 * a rail that proves nothing.
 *
 * The cue moved there from `markStreaming`'s settle branch, which chimed "a turn
 * finished" for every settle: a Stop, and a failed turn, which is "something failed".
 * The ending is now known where it is said (`chat/turnOutcome.ts`), so the cue can
 * match it.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, act } from '@testing-library/react'
import { renderHook } from '@testing-library/react'

import { Toaster } from '../ui/Toaster'
import { useApprovalToasts } from './useApprovalToasts'
import type { WsMessage } from '../lib/useChatSocket'

const playCue = vi.fn()
vi.mock('../design/soundCues', () => ({
  playCue: (name: string) => playCue(name),
  armCueAudio: () => {},
  soundCuesEnabled: () => false,
  setSoundCuesEnabled: () => {},
}))

// The socket handler, captured so a test can deliver a frame synchronously.
let onMessage: ((m: WsMessage) => void) | null = null
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (cb: (m: WsMessage) => void) => {
    onMessage = cb
  },
}))

beforeEach(() => {
  playCue.mockClear()
  onMessage = null
})

const SRC = join(process.cwd(), 'src')

/** Strip block and line comments so a rail cannot be satisfied by prose.
 *  (This file's own subject writes comments naming the cues, which is exactly the
 *  false pass this guards against.) */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}

/** The body of a brace-balanced block starting at `from`. */
function blockAt(src: string, from: number): string {
  const open = src.indexOf('{', from)
  let depth = 0
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth++
    else if (src[i] === '}') {
      depth--
      if (depth === 0) return src.slice(open + 1, i)
    }
  }
  throw new Error('unbalanced block')
}

describe('turn ended → ChatPage', () => {
  const code = stripComments(readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8'))

  it('the comment stripper actually strips (or every assertion below is vacuous)', () => {
    expect(stripComments('a /* playCue("x") */ b')).not.toMatch(/playCue/)
    expect(stripComments('a // playCue("x")\nb')).not.toMatch(/playCue/)
    // …and does not eat live code, which would make the rail red for the wrong reason.
    expect(stripComments("playCue('turn_complete')")).toMatch(/playCue\('turn_complete'\)/)
  })

  it('imports the cue from the design module, not a local re-implementation', () => {
    expect(code).toMatch(/import \{ playCue \} from '\.\.\/design\/soundCues'/)
  })

  it('fires where a turn’s ending is said, once, with the cue that ending calls for', () => {
    const at = code.indexOf('const sayTurnEnded = ')
    expect(at, 'sayTurnEnded must still exist — the ending is the whole cue site').toBeGreaterThan(0)
    const body = blockAt(code, at)
    // Vacuity floor: a failed extraction returns something short and would otherwise
    // sail through the assertions below.
    expect(body, 'the extracted body must be real code').toMatch(/setSrAnnounce\(turnEndedSentence\(outcome\)\)/)

    // Once per turn, not on every render: behind the guard that says each ending once.
    const guard = body.indexOf('if (!outcome || !endUnsaidRef.current) return')
    expect(guard, 'the once-per-turn guard must be in front of the cue').toBeGreaterThan(-1)
    expect(guard).toBeLessThan(body.indexOf('playCue('))
    // A failed turn is "something failed"; every other ending is "a turn finished".
    expect(body).toMatch(/playCue\(outcome === 'error' \? 'error' : 'turn_complete'\)/)
    // …and every settle path reaches it with what it knows.
    const settle = blockAt(code, code.indexOf('const markStreaming = '))
    expect(settle).toMatch(/if \(!v\) sayTurnEnded\(outcome\)/)
    // Exactly one cue in the whole page: a second call site would mean a cue on a
    // path nobody reasoned about.
    expect(code.match(/playCue\(/g)?.length).toBe(1)
  })
})

describe('approval requested → useApprovalToasts', () => {
  const frame = (over: Record<string, unknown> = {}): WsMessage => ({
    type: 'approval',
    data: { session: 'other-session', id: 'ap-1', tool: 'Bash', ...over },
  })

  it('cues when an approval lands for a session the user is not looking at', () => {
    renderHook(() => useApprovalToasts(''))
    expect(onMessage, 'the socket handler must be captured or nothing is driven').not.toBeNull()
    act(() => onMessage!(frame()))
    expect(playCue).toHaveBeenCalledWith('approval_needed')
  })

  it('stays silent for the session already on screen — the card is right there', () => {
    renderHook(() => useApprovalToasts('other-session'))
    act(() => onMessage!(frame()))
    expect(playCue).not.toHaveBeenCalled()
  })

  it('cues ONCE per approval, not again on a reconnect re-broadcast', () => {
    renderHook(() => useApprovalToasts(''))
    act(() => onMessage!(frame()))
    act(() => onMessage!(frame()))
    expect(playCue).toHaveBeenCalledTimes(1)
  })

  it('stays silent for a non-approval frame', () => {
    renderHook(() => useApprovalToasts(''))
    act(() => onMessage!({ type: 'token', data: { session: 's', text: 'hi' } }))
    expect(playCue).not.toHaveBeenCalled()
  })
})

describe('error toast → Toaster', () => {
  const toast = (level: string, message = 'something failed') => {
    act(() => {
      window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level, message } }))
    })
  }

  it('cues on an error toast', () => {
    render(<Toaster />)
    toast('error')
    expect(playCue).toHaveBeenCalledWith('error')
  })

  it('does NOT cue on info or success — a chime on every "Saved" is unusable', () => {
    render(<Toaster />)
    toast('info')
    toast('success')
    // An unknown level falls back to 'info' in the host; it must fall back to silence too.
    toast('banana')
    expect(playCue).not.toHaveBeenCalled()
  })

  it('cues per error, matching the toast the user sees', () => {
    render(<Toaster />)
    toast('error', 'first')
    toast('error', 'second')
    expect(playCue).toHaveBeenCalledTimes(2)
  })

  it('stays silent for an empty message the host drops anyway', () => {
    render(<Toaster />)
    toast('error', '')
    expect(playCue).not.toHaveBeenCalled()
  })
})

describe('the shell arms the gesture primer', () => {
  it('App calls armCueAudio in an effect — cues need a gesture the cue points are not', () => {
    const code = stripComments(readFileSync(join(SRC, 'app/App.tsx'), 'utf8'))
    expect(code).toMatch(/import \{ armCueAudio \} from '\.\.\/design\/soundCues'/)
    expect(code).toMatch(/useEffect\(\(\) => \{ armCueAudio\(\) \}, \[\]\)/)
  })
})
