import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { NeedCard, needSubject, timeLeft, type CardActions } from './NeedCards'
import { needsYou, type NoteInput } from '../../lib/needsYou'
import type { WaitingItem, WaitingList } from '../../lib/api'

// ── Needs you's cards lead with what they ask ────────────────────────────────────────────────────
//
// A card's first row is its tag: its kind's noun (the registry's, `needsYouKinds`), then the thing
// it is about or the work that asks, and at its end the time left to answer. Under it, what it asks
// as its headline and one line of detail. A proposal's or a note's lines are clamped, since its
// panel holds them whole; what an ask's answer decides on never is, since that answer acts in one
// press. Its name for assistive tech is cut nowhere: the thing leads, a source follows after ", ".

const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), 'src/test/fixtures/needsYouKinds.json'), 'utf8')) as {
  learning: { variant: string; card: Record<string, unknown> }[]
}

const actions: CardActions = {
  approval: vi.fn(), question: vi.fn(), grant: vi.fn(), bridge: vi.fn(), proposal: vi.fn(),
  reply: vi.fn(async () => true), trusted: vi.fn(),
}

const NOW = Date.parse('2026-11-15T12:00:00Z')

function learningRow(variant: string) {
  const { card } = FIXTURE.learning.find((e) => e.variant === variant)!
  const note: NoteInput = {
    id: `row-${variant}`, item_kind: 'proposal', status: 'pending', message: '', context_summary: '',
    created_at: 1, refs: { learning_proposal: `lp-${variant}`, card }, sender_name: 'learning', channel_name: 'learning',
  } as NoteInput
  return needsYou(null, [note], [])[0]
}

function askRow(item: Partial<WaitingItem>) {
  const list = { waiting: [{ id: 'gate:r1', kind: 'gate', work: '', session: '', brief: {}, consequence: 'changes', risk: '',
    answer: null, route: '#/workflows/runs/r1', asked_at: null, expires_at: null, current: true, ...item }], unreadable: [] } as unknown as WaitingList
  return needsYou(list, [], [])[0]
}

const draw = (row: ReturnType<typeof askRow>) =>
  render(<NeedCard row={row} outcome={undefined} actions={actions} now={NOW} selected={false} onOpen={() => {}} />)

describe('a proposal leads with what it does, the thing it is about in the tag row', () => {
  it('a named template: its tag, its name, its headline and its steps', () => {
    const row = learningRow('template.named')
    draw(row)
    expect(screen.getByText('New template')).toBeInTheDocument()
    expect(screen.getByText('weekly-status-report')).toBeInTheDocument()
    const headline = screen.getByText("Draft the Friday status email from the week's merged changes")
    expect(headline.closest('p')!.getAttribute('data-type')).toBe('title-m')
    expect(headline.closest('p')!.className).toContain('line-clamp-2')
    expect(needSubject(row)).toBe("New template weekly-status-report: Draft the Friday status email from the week's merged changes")
  })

  it('a lesson names no thing, and its line says what Accept does', () => {
    const row = learningRow('lesson.failed_run')
    draw(row)
    expect(screen.getByText('Lesson')).toBeInTheDocument()
    expect(screen.getByText('fetch-metrics: The metrics query returned an empty result')).toBeInTheDocument()
    expect(screen.getByText('Accepting it changes nothing yet').closest('p')!.className).toContain('line-clamp-1')
  })
})

describe("an ask's facts are never clamped, and its tag row ends with the time left", () => {
  it('a gate says its kind, its workflow and its deadline, its lines whole', () => {
    const row = askRow({ brief: { workflow: 'release', ask: { prompt: 'Ship v0.3.9 to production?' } },
      expires_at: new Date(NOW + 4 * 60_000).toISOString() })
    const { container } = draw(row)
    expect(screen.getByText('Workflow')).toBeInTheDocument()
    expect(screen.getByText('release')).toBeInTheDocument()
    expect(screen.getByText('4 min left')).toBeInTheDocument()
    const headline = within(container).getByText('Ship v0.3.9 to production?').closest('p')!
    expect(headline.className).not.toContain('line-clamp')
    expect(needSubject(row)).toBe('Ship v0.3.9 to production?, release')
  })

  it('beyond 15 minutes it says when to answer by, and nothing once there is no deadline', () => {
    expect(timeLeft({ expires_at: new Date(NOW + 60 * 60_000).toISOString() }, NOW)).toMatch(/^Answer by /)
    expect(timeLeft({ expires_at: new Date(NOW - 1).toISOString() }, NOW)).toBe('')
    expect(timeLeft({ expires_at: null }, NOW)).toBe('')
  })
})
