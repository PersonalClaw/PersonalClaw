import { describe, it, expect } from 'vitest'
import { CalendarDays, Wrench } from 'lucide-react'
import { hydrateTurns, type HistMsg, type ToolSegment } from '../chatTypes'
import { iconForTool, labelForTool } from './native'

// A question about her plans now reads her calendar (`calendar_events`). Its card is the one she
// sees for that read, live and after a reload, so it is named as what it does: "Read the calendar"
// under a calendar, not the raw tool id under the wrench every unmapped tool falls back to.

/** The tool row the gateway persists for the call, in the shape a reload hydrates. */
const calendarRow: HistMsg = {
  role: 'tool',
  content: 'calendar_events',
  meta: { tool_call_id: 't1', input: '{"start":"2026-10-03","end":"2026-10-03"}' } as HistMsg['meta'],
}

const reloaded = (): ToolSegment => {
  const seg = hydrateTurns([calendarRow]).flatMap((t) => t.segments).find((s): s is ToolSegment => s.kind === 'tool')
  if (!seg) throw new Error('hydrateTurns produced no tool segment')
  return seg
}

describe('a calendar read is named as one', () => {
  it('reads "Read the calendar" under a calendar, after a reload too', () => {
    const seg = reloaded()
    expect(labelForTool(seg)).toBe('Read the calendar')
    expect(iconForTool(seg)).toBe(CalendarDays)
    expect(iconForTool(seg)).not.toBe(Wrench)
  })
})
