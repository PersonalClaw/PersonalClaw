import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { render, screen, fireEvent } from '@testing-library/react'
import { ContextLedger } from './ContextLedger'
import { insertActivity } from './coalesceReducers'
import { LEDGER_ACTIVITY_KINDS, type ActivitySegment } from './chatTypes'

// ── A turn that read none of your memory says so in its details ───────────────────────────
//
// A Temporary chat's turn, and a turn of a conversation an app started without the memory
// permission, are assembled with none of your memory (`memory_reads`). Their "Fed this turn" row
// still said "Recalled relevant context — saved memories, learned lessons, earlier conversation,
// and episodic history", the one sentence the ledger had, so a Temporary chat claimed memories
// it never read. Such a turn's context line is its own kind, `context_without_memory`, carrying
// the gateway's own sentence (`memory_reads.fed`), and the ledger says that sentence instead.
//
// The sentences are the ones `memory_reads.fed` composes, matched by the backend's own tests
// (`tests/test_memory_is_read_only_where_its_work_may_read_it.py`).

const TEMPORARY = 'Injected 312 chars of context, none of it from your memory: this is a Temporary chat'
const APP = 'Injected 498 chars of context, none of it from your memory: the app allotment-planner cannot read your memory'
const MEMORY = 'Injected 1,204 chars of context (memory, lessons, history, episodic)'

const fedRow = (): string => screen.getByText('Fed this turn:').parentElement?.textContent ?? ''

describe('the ledger says what fed the turn', () => {
  it('a turn that read none of your memory says so, and why, and makes no memory claim', () => {
    for (const fed of [TEMPORARY, APP]) {
      const { unmount } = render(<ContextLedger fed={fed} fedNoMemory />)
      const chip = screen.getByRole('button')
      expect(chip.textContent).toContain('context, no memory')
      expect(chip.textContent).not.toContain('recalled context')
      fireEvent.click(chip)
      const row = fedRow()
      expect(row).toContain(`${fed}.`)
      expect(row).not.toContain('saved memories')
      expect(row).not.toContain('Recalled relevant context')
      unmount()
    }
  })

  it('a turn that read your memory still says what it recalled', () => {
    render(<ContextLedger fed={MEMORY} />)
    const chip = screen.getByRole('button')
    expect(chip.textContent).toContain('recalled context')
    fireEvent.click(chip)
    expect(fedRow()).toContain('Recalled relevant context · 1,204 chars — saved memories')
  })
})

describe('the fold carries the kind from the live line to the ledger', () => {
  it('the live line of such a turn is a ledger line, kept out of the turn and kept with tool cards', () => {
    expect(LEDGER_ACTIVITY_KINDS).toContain('context_without_memory')
    const tool = { kind: 'tool' as const, id: 't1', title: 'read_file', status: 'completed' as const }
    const segs = insertActivity([tool as never], TEMPORARY, 'context_without_memory', false)
    const line = segs.find((s) => s.kind === 'activity') as ActivitySegment | undefined
    expect(line).toEqual({ kind: 'activity', text: TEMPORARY, activityKind: 'context_without_memory' })
  })

  describe('the call site in ChatPage.tsx', () => {
    // Read as the other ledger rails read it (`turnTelemetryReader.test.tsx`): the fold lives inside
    // a page too large to mount here. The path goes through a parameter, so Vite does not take the
    // `new URL(…, import.meta.url)` for an asset to rewrite.
    const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
    const chatPage = read('../ChatPage.tsx')

    it('hands the ledger the kind with the line', () => {
      expect(chatPage.length).toBeGreaterThan(100_000)
      expect(chatPage).toContain("if (ak === 'context' || ak === 'context_without_memory') {")
      expect(chatPage).toContain("ledger.fedNoMemory = ak === 'context_without_memory'")
      expect(chatPage).toContain('fedNoMemory={ledger.fedNoMemory}')
    })
  })
})
