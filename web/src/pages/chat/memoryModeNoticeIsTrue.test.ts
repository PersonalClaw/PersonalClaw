import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { MEMORY_MODES, MEMORY_MODE_NOTICE } from './memoryModeCopy'

// ── A chat that is not persistent says what is kept, and nothing else ───────────────────────────
//
// The incognito notice ended "The chat itself is saved in your history." A user who went looking
// for the chat found it in neither the chat list nor its search: both keep incognito and temporary
// chats out on purpose (`api_chat_sessions` skips them live and on disk, and `search_sessions`
// and the search index refuse them — "restricted sessions promise to stay out of history"). What
// IS true is that the transcript is still written: `save_session_to_history` saves every mode,
// pinned by `test_restricted_session_still_saves_conversation_log`. The temporary notice had the
// opposite fault, "forgotten when the session ends", about a transcript that stays and is restored
// on reopen.

const read = (p: string): string => readFileSync(join(process.cwd(), p), 'utf8')
const GATEWAY = join('..', 'src', 'personalclaw')
/** Only rendered text can mislead a user; comments that quote the old claim do not. */
const strip = (raw: string) =>
  raw.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

describe('a chat that is not persistent says what is kept', () => {
  it('the chat page promises a restricted chat no place in the history that lists none of them', () => {
    const page = strip(read('src/pages/ChatPage.tsx'))
    expect(page).not.toMatch(/saved in your history/)
    expect(page).not.toMatch(/forgotten when the session ends|Forget when the session ends/)
  })

  it('each notice says both halves: out of the history and its search, yet the transcript is kept', () => {
    for (const [mode, text] of Object.entries(MEMORY_MODE_NOTICE)) {
      expect(text, mode).toContain('The chat stays out of your chat history and search')
      expect(text, mode).toContain('PersonalClaw still keeps its transcript')
    }
    expect(MEMORY_MODE_NOTICE.incognito).toContain('memory is still read for context')
    expect(MEMORY_MODE_NOTICE.temporary).toContain('memory is neither read nor written')
  })

  it('no mode promises the chat a place in the history, or that it is forgotten', () => {
    const words = [
      ...Object.values(MEMORY_MODE_NOTICE),
      ...MEMORY_MODES.filter((m) => m.id !== 'persistent').map((m) => m.hint),
    ]
    for (const text of words) expect(text).not.toMatch(/saved in your history|forgot|forget/i)
  })

  it('the chat page states none of it itself: the picker and the notice read the owner', () => {
    const page = strip(read('src/pages/ChatPage.tsx'))
    expect(page).toMatch(/from '\.\/chat\/memoryModeCopy'/)
    expect(page).toMatch(/MEMORY_MODE_NOTICE\[memoryMode\]/)
    expect(page, 'a mode sentence spelled at the call site again').not.toMatch(/'(Incognito|Temporary) — /)
  })

  it('the backend facts the notices rest on still hold', () => {
    const handlers = read(join(GATEWAY, 'dashboard', 'chat_handlers.py'))
    // The chat list skips both modes, for resident chats and for chats read off disk.
    expect(handlers.match(/memory_mode[^\n]*in \("incognito", "temporary"\)/g)?.length ?? 0).toBeGreaterThanOrEqual(2)
    const history = read(join(GATEWAY, 'history.py'))
    expect(history, 'content search refuses them').toMatch(/memory_mode"\) in \("incognito", "temporary"\):\s*\n\s*continue/)
  })
})
