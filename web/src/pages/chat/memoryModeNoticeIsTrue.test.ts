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
// IS true of an incognito chat is that its transcript is still written. A Temporary chat promises
// more, that it is forgotten when its session ends, and the gateway keeps that promise: the last
// save before it stops deletes the chat instead, the next start deletes any it left behind, and
// nothing reopens one (`dashboard/chat_forget.py`).

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

  it('each notice says both halves: out of the history and its search, and what is kept', () => {
    for (const [mode, text] of Object.entries(MEMORY_MODE_NOTICE)) {
      expect(text, mode).toMatch(/stays out of your chat history and search/)
    }
    expect(MEMORY_MODE_NOTICE.incognito).toContain('memory is still read for context')
    expect(MEMORY_MODE_NOTICE.incognito).toContain('PersonalClaw still keeps its transcript')
    expect(MEMORY_MODE_NOTICE.temporary).toContain('memory is neither read nor written')
    expect(MEMORY_MODE_NOTICE.temporary).toContain('this chat is forgotten when its session ends')
    expect(MEMORY_MODE_NOTICE.temporary).toContain(
      'its messages, the files attached to it and the workflow runs it started are deleted',
    )
    expect(MEMORY_MODE_NOTICE.temporary).not.toMatch(/keeps its transcript/)
  })

  it('no mode promises the chat a place in the history, and only Temporary says it is forgotten', () => {
    const incognito = [MEMORY_MODE_NOTICE.incognito, MEMORY_MODES.find((m) => m.id === 'incognito')!.hint]
    for (const text of incognito) expect(text).not.toMatch(/saved in your history|forgot|forget/i)
    const temporary = [MEMORY_MODE_NOTICE.temporary, MEMORY_MODES.find((m) => m.id === 'temporary')!.hint]
    for (const text of temporary) {
      expect(text).not.toMatch(/saved in your history/)
      expect(text).toMatch(/forgotten when (its|the) session ends/)
    }
  })

  it('the chat page states none of it itself: the picker and the notice read the owner', () => {
    const page = strip(read('src/pages/ChatPage.tsx'))
    expect(page).toMatch(/from '\.\/chat\/memoryModeCopy'/)
    expect(page).toMatch(/MEMORY_MODE_NOTICE\[memoryMode\]/)
    expect(page, 'a mode sentence spelled at the call site again').not.toMatch(/'(Incognito|Temporary) — /)
  })

  it('the chat page keeps no copy of a Temporary chat that outlives the page', () => {
    const page = strip(read('src/pages/ChatPage.tsx'))
    // The one writer of the transcript cache persists every mode but Temporary to session storage.
    expect(page).toMatch(/writeQuery\(detailKey\(key\), d, d\.memory_mode !== 'temporary'\)/)
    expect(page.match(/writeQuery\(detailKey\(/g)?.length, 'a second writer of the transcript cache').toBe(1)
    // The first message of a new chat is seeded with its mode, so that seed is not persisted either.
    expect(page).toMatch(/messages: seedMessages, running: false, memory_mode: memoryMode \}/)
    // A chat that is gone drops what the page held of it.
    expect(page).toMatch(/status === 404\) \{ invalidateKeys\(detailKey\(sessionId\)\); setMissing\(true\) \}/)
  })

  it('the backend facts the notices rest on still hold', () => {
    const handlers = read(join(GATEWAY, 'dashboard', 'chat_handlers.py'))
    // The chat list skips both modes, for resident chats and for chats read off disk.
    expect(handlers.match(/memory_mode[^\n]*in \("incognito", "temporary"\)/g)?.length ?? 0).toBeGreaterThanOrEqual(2)
    const history = read(join(GATEWAY, 'history.py'))
    expect(history, 'content search refuses them').toMatch(/memory_mode"\) in \("incognito", "temporary"\):\s*\n\s*continue/)
    // A Temporary chat is forgotten, not saved, when its gateway stops; the next start forgets
    // any a crash left; a read of one that ended forgets it and finds nothing.
    const persistence = read(join(GATEWAY, 'dashboard', 'chat_persistence.py'))
    expect(persistence).toMatch(/memory_mode == TEMPORARY:\s*\n\s*forget_temporary_chat\(/)
    expect(persistence).toMatch(/forget_ended_temporary_chats\(state\)/)
    expect(persistence.match(/if forget_if_ended\(state, /g)?.length ?? 0).toBeGreaterThanOrEqual(2)
    // The workflow runs a Temporary chat started end with it (an Incognito chat's, when it is
    // deleted): each poll of the workflow supervisor stops those whose chat ended and deletes the
    // ones that have.
    const watchdog = read(join(GATEWAY, 'workflows', 'watchdog.py'))
    expect(watchdog).toMatch(/self\._end_runs_whose_private_chat_ended\(\)/)
    expect(watchdog).toMatch(/await self\._remove_runs_whose_private_chat_ended\(\)/)
  })
})
