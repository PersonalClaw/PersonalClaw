import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Incognito's copy may not promise more than the backend delivers (issue 367) ───────────────────
//
// Two surfaces claimed incognito blocks memory READS. It does not, and deliberately so:
// `_ChatSession.blocks_reads` is `memory_mode == 'temporary'`, so an incognito chat is injected
// with memory context exactly as a persistent one is. Only WRITES are suppressed
// (`is_restricted`, i.e. `!= 'persistent'`, which gates consolidation + lessons).
//
// The banner also claimed the chat "stays out of your history". Also false: `chat_persistence`
// reads `memory_mode` back out of SAVED session metadata on load, which is only possible for a
// transcript that was written to disk. History exclusion is the separate `ephemeral` flag, and
// `createChatSession` has no such parameter to send.
//
// This rails the COPY against the backend contract rather than against a fixed string, so if
// incognito is ever widened to block reads this test names the copy that must move with it.

const CHAT_PAGE = join(__dirname, '..', 'ChatPage.tsx')
const STATE_PY = join(__dirname, '..', '..', '..', '..', 'src', 'personalclaw', 'dashboard', 'state.py')

describe('incognito copy matches the backend contract', () => {
  it('the backend still blocks reads for `temporary` only', () => {
    const py = readFileSync(STATE_PY, 'utf8')
    // The premise this whole test rests on. If this line changes, re-read the copy below.
    expect(py).toContain('return self.memory_mode == "temporary"')
    expect(py).toContain('return self.memory_mode != "persistent"')
  })

  it('no incognito surface claims memory is not read', () => {
    const src = readFileSync(CHAT_PAGE, 'utf8')
    const claims = src
      .split('\n')
      .filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*'))
      .filter((l) => /[Ii]ncognito/.test(l))
      .filter((l) => /no memory is read|No memory read|not read|never read/i.test(l))
    expect(claims).toEqual([])
  })

  it('no incognito surface claims the chat stays out of history', () => {
    const src = readFileSync(CHAT_PAGE, 'utf8')
    const claims = src
      .split('\n')
      .filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*'))
      .filter((l) => /[Ii]ncognito/.test(l))
      .filter((l) => /out of your history|stays out of|not saved|never saved/i.test(l))
    expect(claims).toEqual([])
  })

  it('still tells the user writes are suppressed — the guarantee incognito does make', () => {
    const src = readFileSync(CHAT_PAGE, 'utf8')
    expect(src).toMatch(/writes nothing back/)
    expect(src).toMatch(/nothing from this chat is written back to it/)
  })
})
