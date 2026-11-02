import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { render, screen, fireEvent } from '@testing-library/react'
import { RecallLine } from './transcript/RecallLine'
import { insertActivity } from './coalesceReducers'
import { RECALL_ACTIVITY_KINDS, type ActivitySegment } from './chatTypes'

// ── A turn that read none of your memory says so where it says what it recalled ──────────────
//
// A Temporary chat's turn, and a turn of a conversation an app started without the memory
// permission, are assembled with none of your memory (`memory_reads`). Their details still said
// "Recalled relevant context — saved memories, learned lessons, earlier conversation, and episodic
// history", the one sentence there was, so a Temporary chat claimed memories it never read. Such a
// turn's context line is its own kind, `context_without_memory`, carrying the gateway's own
// sentence (`memory_reads.fed`), and the turn's recall line (`transcript/RecallLine`, #5876) says
// that sentence instead, and offers no review of a memory it did not read.
//
// The sentences are the ones `memory_reads.fed` composes, matched by the backend's own tests
// (`tests/test_memory_is_read_only_where_its_work_may_read_it.py`).

const TEMPORARY = 'Injected 312 chars of context, none of it from your memory: this is a Temporary chat'
const APP = 'Injected 498 chars of context, none of it from your memory: the app allotment-planner cannot read your memory'
const MEMORY = 'Injected 1,204 chars of context (memory, lessons, history, episodic)'

const line = (text: string, activityKind: string): ActivitySegment => ({ kind: 'activity', text, activityKind })

describe('the recall line says what fed the turn', () => {
  it('a turn that read none of your memory says so, and why, and makes no memory claim', () => {
    for (const fed of [TEMPORARY, APP]) {
      const { unmount } = render(<RecallLine seg={line(fed, 'context_without_memory')} />)
      const chip = screen.getByRole('button')
      expect(chip.textContent).toContain('No memory recalled')
      expect(chip.textContent).not.toContain('Recalled context')
      fireEvent.click(chip)
      expect(screen.getByText(`${fed}.`)).toBeTruthy()
      expect(screen.queryByText(/saved memories/)).toBeNull()
      expect(screen.queryByRole('link', { name: 'Review what’s recalled' })).toBeNull()
      unmount()
    }
  })

  it('a turn that read your memory still says what it recalled, and where to review it', () => {
    render(<RecallLine seg={line(MEMORY, 'context')} />)
    const chip = screen.getByRole('button')
    expect(chip.textContent).toContain('Recalled context · 1,204 chars')
    fireEvent.click(chip)
    expect(screen.getByText(/saved memories, learned lessons, earlier conversation, and episodic history/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Review what’s recalled' })).toBeTruthy()
  })
})

describe('the fold carries the kind from the live line to the turn', () => {
  it('the live line of such a turn is a recall line, kept with tool cards', () => {
    expect(RECALL_ACTIVITY_KINDS).toContain('context_without_memory')
    const tool = { kind: 'tool' as const, id: 't1', title: 'read_file', status: 'completed' as const }
    const segs = insertActivity([tool as never], TEMPORARY, 'context_without_memory', false)
    const added = segs.find((s) => s.kind === 'activity') as ActivitySegment | undefined
    expect(added).toEqual({ kind: 'activity', text: TEMPORARY, activityKind: 'context_without_memory' })
  })

  describe('the call site in the transcript', () => {
    // Read as the ledger rails read it (`turnTelemetryReader.test.tsx`): the fold lives in the
    // transcript's assistant turn (`transcript/AssistantSegments.tsx`, moved out of ChatPage.tsx).
    // The path goes through a parameter, so Vite does not take the `new URL(…, import.meta.url)`
    // for an asset to rewrite.
    const read = (p: string) => readFileSync(new URL(p, import.meta.url), 'utf8')
    const chatPage = read('./transcript/AssistantSegments.tsx')

    it('hands the recall line the segment, kind and all', () => {
      expect(chatPage.length).toBeGreaterThan(10_000)
      expect(chatPage).toContain('isRecall(seg) ? <RecallLine')
      expect(chatPage).toContain('seg={seg} />')
    })
  })
})
