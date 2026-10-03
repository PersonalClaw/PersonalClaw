import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { MEMORY_MODE_NOTICE } from './memoryModeCopy'

// ── Incognito's copy may not promise more than the backend delivers (issue 367) ───────────────────
//
// Two surfaces claimed incognito blocks memory READS. It does not, and deliberately so:
// `_ChatSession.blocks_reads` is `memory_mode == 'temporary'`, so an incognito chat is injected
// with memory context exactly as a persistent one is. Only WRITES are suppressed
// (`is_restricted`, i.e. `!= 'persistent'`, which gates consolidation + lessons).
//
// The banner also claimed the chat "stays out of your history", which was false when this was
// written. It is true now: the chat list and its search leave incognito and temporary chats out,
// while their transcript is still saved — so the notice says both halves, and
// `memoryModeNoticeIsTrue.test.ts` holds those words to the backend lines they rest on. This file
// keeps the half about memory: reads and writes.
//
// This rails the COPY against the backend contract rather than against a fixed string, so if
// incognito is ever widened to block reads this test names the copy that must move with it. The
// words live in `memoryModeCopy.ts`, the one owner the picker and the notice both read.

const CHAT_PAGE = join(__dirname, '..', 'ChatPage.tsx')
const COPY = join(__dirname, 'memoryModeCopy.ts')
const STATE_PY = join(__dirname, '..', '..', '..', '..', 'src', 'personalclaw', 'dashboard', 'state.py')
/** Every surface a mode's words can be written on: the owner, and the page that renders them. */
const surfaces = () => [readFileSync(COPY, 'utf8'), readFileSync(CHAT_PAGE, 'utf8')].join('\n')

describe('incognito copy matches the backend contract', () => {
  it('the backend still blocks reads for `temporary` only', () => {
    const py = readFileSync(STATE_PY, 'utf8')
    // The premise this whole test rests on. If this line changes, re-read the copy below.
    expect(py).toContain('return self.memory_mode == "temporary"')
    expect(py).toContain('return self.memory_mode != "persistent"')
  })

  it('no incognito surface claims memory is not read', () => {
    const src = surfaces()
    const claims = src
      .split('\n')
      .filter((l) => !l.trim().startsWith('//') && !l.trim().startsWith('*'))
      .filter((l) => /[Ii]ncognito/.test(l))
      .filter((l) => /no memory is read|No memory read|not read|never read/i.test(l))
    expect(claims).toEqual([])
  })

  it('still tells the user writes are suppressed — the guarantee incognito does make', () => {
    const src = surfaces()
    expect(src).toMatch(/writes nothing back/)
    expect(src).toMatch(/nothing from this chat is written back to it/)
  })

  it('says nothing of the chat reaches a model but its own, and every seam asks the one answer', () => {
    for (const [mode, text] of Object.entries(MEMORY_MODE_NOTICE)) {
      expect(text, mode).toMatch(/sent to any model but the one it runs on: a tool that needs a model uses this one or says it can't/)
    }
    expect(MEMORY_MODE_NOTICE.incognito).toMatch(/memory is still read for context, searched by keyword/)
    const gateway = join(__dirname, '..', '..', '..', '..', 'src', 'personalclaw')
    const read = (...p: string[]) => readFileSync(join(gateway, ...p), 'utf8')
    // Both embedding builders (one text, a batch) answer no vector before the model is asked, so a
    // restricted chat's memory is searched by keyword.
    expect(read('embedding_providers', 'registry.py').match(/if not memory_writes\.model_may_read\(ref\):/g)?.length).toBe(2)
    // Every model built for anything but a person's own turn passes the guard, which asks it.
    expect(read('providers', 'provider_bridge.py')).toMatch(/memory_writes\.require_model\(f"\{provider_name\}:\{model\}"\)/)
    // A tool's one-shot call runs on the model the chat's turn named.
    expect(read('llm_helpers.py')).toMatch(/own = memory_writes\.own_model\(\)/)
    expect(read('dashboard', 'chat_runner.py')).toMatch(/memory_writes\.answered_by\(str\(getattr\(client, "served_model_ref", ""\) or ""\)\)/)
    // Every consolidation pass runs as deriving from its session and is skipped for one.
    expect(read('history.py')).toMatch(/with memory_writes\.derived_from\(key, memory_mode=self\._log\.recorded_memory_mode\(key\)\):\s*\n\s*if memory_writes\.writes_refused\(\):/)
    // The tools an agent CLI runs, in a process of their own, run as the chat they serve.
    expect(read('mcp_core.py')).toMatch(/run_mcp_stdio_loop\("personalclaw-core", "1\.0\.0", _aggregated_list_tools, _call_as_its_session\)/)
  })

  it('says what the person gives the chat is still read by the models set up for it, and only that', () => {
    for (const [mode, text] of Object.entries(MEMORY_MODE_NOTICE)) {
      expect(text, mode).toMatch(/Files you attach, a screen you share, your dictation and replies read aloud still use the models you set up for them\./)
    }
    const gateway = join(__dirname, '..', '..', '..', '..', 'src', 'personalclaw')
    const read = (...p: string[]) => readFileSync(join(gateway, ...p), 'utf8')
    // An attachment's reading and a shared screen's description are the readings that take it.
    expect(read('dashboard', 'attachment_extract.py').match(/with memory_writes\.reading_their_input\(\):/g)?.length).toBe(2)
    expect(read('dashboard', 'chat_runner.py').match(/with memory_writes\.reading_their_input\(\):/g)?.length).toBe(1)
    // Dictation and read-aloud are the page's own requests, made as the dashboard and not as the
    // chat, so no chat's scope reaches them.
    expect(read('dashboard', 'memory_write_gate.py')).toMatch(/if not session_key or session_key == _DASHBOARD_UI:\s*\n\s*return await handler\(request\)/)
    const client = readFileSync(join(__dirname, '..', '..', 'lib', 'api.ts'), 'utf8')
    expect(client).toMatch(/const SK = \{ 'X-Session-Key': 'dashboard:ui'/)
    expect(client).toMatch(/fetch\(url, \{ method: 'POST', headers: \{ \.\.\.SK \}, body: fd \}\)/)
    expect(client).toMatch(/voiceSynthesize: \(text: string, session: string, request: string\) => post</)
  })

  it('says no background model reads the chat, and each such chore asks the one answer first', () => {
    for (const [mode, text] of Object.entries(MEMORY_MODE_NOTICE)) {
      expect(text, mode).toMatch(/no background model reads it for a title, follow-ups or suggestions/)
    }
    const gateway = join(__dirname, '..', '..', '..', '..', 'src', 'personalclaw')
    const read = (...p: string[]) => readFileSync(join(gateway, ...p), 'utf8')
    // A title, on the first turn and when one is asked for again, is the mode's, made without a model.
    const title = read('dashboard', 'chat_title.py')
    expect(title.match(/if keeps_to_its_own_model\(state, session\):\s*\n(?:\s*#[^\n]*\n)*\s*(?:_apply_title\(state, session, _title_without_a_model|title = _title_without_a_model)/g)?.length).toBe(2)
    expect(read('dashboard', 'chat_followups.py')).toMatch(/if keeps_to_its_own_model\(state, session\):\s*\n\s*return/)
    // A condensed history and the suggestions built from recent chats ask the same answer.
    expect(read('context.py')).toMatch(/if memory_writes\.blocks_background_models\(session_key\):\s*\n\s*return None/)
    expect(read('suggestions.py')).toMatch(/memory_writes\.blocks_background_models\(\s*key, memory_mode=s\.get\("memory_mode"\)/)
  })
})
