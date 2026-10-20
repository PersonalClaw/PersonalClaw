// @module-tag tree-scan
import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── No surface derives "running" from a flag of its own ──────────────────────────────────────────
//
// A chat's state reaches the web one way: its `run_state`, the last `work_phase` payload the gateway
// sent it (`GET /api/chat/sessions` rows, a chat's detail and a project's chats), and the
// `work_phase` frames after it. The flags it replaced are gone from the wire — the list's `running`,
// `stopping`, `stop_state`, `pending_approval`, `pending_approval_info` and `waiting_for_input`, the
// detail's `running`, `stopping`, `steerable`, `last_turn_outcome` and `pending_approval`, a
// project's chats' `running` — and this rail keeps them gone from the web too: the wire types carry
// none of them, and no file reads one or casts it back onto a chat type. Mission Control did exactly
// that, re-adding `stopping` and `pending_approval` to the list's type with a cast, because the type
// did not carry what the wire did.

const SRC = join(process.cwd(), 'src')
const API = join(SRC, 'lib', 'api.ts')

const ROW_FLAGS = ['running', 'stopping', 'stop_state', 'pending_approval', 'pending_approval_info', 'waiting_for_input']
const DETAIL_FLAGS = ['running', 'stopping', 'steerable', 'last_turn_outcome', 'pending_approval']

/** The keys a TypeScript object type declares, comments blanked first: a docstring that names a flag
 *  is not the type carrying it. */
function declaredKeys(body: string): string[] {
  const code = body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
  return [...code.matchAll(/(?:^|[{;,\s])(\w+)\??\s*:/g)].map((m) => m[1])
}

function interfaceBody(name: string): string {
  const text = readSource(API)
  const at = text.indexOf(`export interface ${name} {`)
  expect(at, `api.ts no longer declares ${name} — parser drift?`).toBeGreaterThan(-1)
  return text.slice(at, text.indexOf('\n}\n', at))
}

function detailType(): string {
  const text = readSource(API)
  const at = text.indexOf('chatSessionDetail: (')
  expect(at, 'api.ts no longer declares chatSessionDetail — parser drift?').toBeGreaterThan(-1)
  return text.slice(at, text.indexOf('}>(`/api/chat/sessions/', at))
}

/** The type of the chats a project lists (`projectLinked`'s `chats`). */
function projectChatsType(): string {
  const text = readSource(API)
  const at = text.indexOf('projectLinked: (')
  expect(at, 'api.ts no longer declares projectLinked — parser drift?').toBeGreaterThan(-1)
  const chats = text.indexOf('chats: {', at)
  expect(chats, 'projectLinked no longer lists chats — parser drift?').toBeGreaterThan(at)
  return text.slice(chats + 'chats: '.length, text.indexOf('}[]', chats))
}

/** A cast or an intersection that puts a retired flag back onto a chat session type. */
const CAST_BACK = new RegExp(
  String.raw`\b(?:ChatSession|ChatSessionSummary|ChatDetail)\b[^;\n]*&\s*\{[^}]*\b(?:${[...new Set([...ROW_FLAGS, ...DETAIL_FLAGS])].join('|')})\??\s*:`,
)

/** The retired flags no other vocabulary uses, read anywhere in the web's own code. (`steerable` and
 *  `stopping` live on as fields of the run state itself; a typed read of the detail's old ones is a
 *  compile error, since its type no longer carries them.) */
const DISTINCT = /\b(?:stop_state|pending_approval_info|waiting_for_input|last_turn_outcome)\b/

const nonTestSources = () => filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name))

describe('the chat wire types carry the run state, not the flags', () => {
  it('a list row carries none of the retired flags, and its run state', () => {
    for (const name of ['ChatSession', 'ChatSessionSummary']) {
      const keys = declaredKeys(interfaceBody(name))
      expect(keys.length, `parsed no keys out of ${name} — vacuous`).toBeGreaterThan(3)
      expect(keys.filter((k) => ROW_FLAGS.includes(k))).toEqual([])
      expect(keys).toContain('run_state')
    }
  })

  it('a chat\'s detail carries none of the retired flags, and its run state', () => {
    const keys = declaredKeys(detailType())
    expect(keys.length, 'parsed no keys out of chatSessionDetail — vacuous').toBeGreaterThan(3)
    expect(keys.filter((k) => DETAIL_FLAGS.includes(k))).toEqual([])
    expect(keys).toContain('run_state')
  })

  it('a project\'s chats carry their run state, and no running flag of their own', () => {
    const keys = declaredKeys(projectChatsType())
    expect(keys.length, 'parsed no keys out of projectLinked\'s chats — vacuous').toBeGreaterThan(1)
    expect(keys).not.toContain('running')
    expect(keys).toContain('run_state')
  })
})

describe('no file reads a retired flag or casts one back in', () => {
  it('the patterns catch what they are for (positive controls)', () => {
    expect(CAST_BACK.test('const wire = s as ChatSessionSummary & { stopping?: boolean }')).toBe(true)
    expect(CAST_BACK.test('const wire = s as ChatSessionSummary & { origin?: string }')).toBe(false)
    expect(DISTINCT.test('markStreaming(false, turnOutcomeOf(d.last_turn_outcome))')).toBe(true)
  })

  it('finds none in the web\'s own code', () => {
    const found = nonTestSources()
      .filter((path) => CAST_BACK.test(readSource(path)) || DISTINCT.test(readSource(path)))
      .map((path) => path.slice(SRC.length + 1))
    expect(found).toEqual([])
  })
})
