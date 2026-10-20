/** A time the gateway serves is read as ONE instant here, whatever zone the browser is in.
 *
 *  Measured: seven watched folders, each checked every 5 minutes, read "polled just now · next in
 *  3h" right after their first poll. The gateway, in America/Toronto, served
 *  `next_poll_at: "2026-10-01T01:22:09"`, a wall-clock reading with no offset, and this browser, in
 *  America/Los_Angeles, parsed it as ITS local time: three hours later than meant. The gateway
 *  now serves every stamp with its offset. A stamp without one names no instant this page can
 *  know, so the parser refuses it rather than guess, and a time the USER typed into a
 *  `datetime-local` picker, which is local by construction, has a parser of its own.
 *
 *  TZ is set before vitest loads the modules; V8 re-reads it on Date calls.
 */
process.env.TZ = 'America/Los_Angeles'

import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { epochSeconds, localDateTimeInput, localDateTimeSeconds } from './epoch'
import { relFuture, relPast, scheduleWhenMet } from '../pages/schedule/scheduleMeta'
import { journalDayHasPassed, relTime as knowledgeRelTime } from '../pages/knowledge/knowledgeMeta'
import { relTime as taskRelTime } from '../pages/tasks/taskMeta'
import { relTime as fileRelTime } from '../pages/files/fileMeta'
import { bucketOf, clockTime as notificationClock, relTime as notificationRelTime } from '../pages/notifications/notificationMeta'
import { SourceRow } from '../pages/knowledge/SourcesPage'
import type { WatchedSource } from './api'

afterAll(() => { process.env.TZ = 'America/Los_Angeles' })

// The moment the folders were polled: 01:17:09 in Toronto, 22:17:09 the evening before here.
const POLLED = '2026-10-01T05:17:09.493101+00:00'
const NOW = Date.parse(POLLED)

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW) })
afterEach(() => { vi.useRealTimers() })

describe('the zone this suite runs in', () => {
  it('is not the gateway\'s, so a zone-less stamp would be read hours off', () => {
    expect(new Date(NOW).getHours(), 'Los Angeles, not Toronto or UTC').toBe(22)
  })
})

describe('a stamp the gateway serves', () => {
  it('reads as its instant: the folder is due in five minutes, polled just now', () => {
    expect(relFuture('2026-10-01T05:22:09.493101+00:00')).toBe('in 5m')
    expect(relPast(POLLED)).toBe('just now')
  })

  it('without an offset names no instant, so it reads as nothing rather than "in 3h"', () => {
    // What the gateway used to serve for that same folder.
    expect(epochSeconds('2026-10-01T01:22:09')).toBeUndefined()
    expect(epochSeconds('2026-10-01 01:22:09.5')).toBeUndefined()
    expect(relFuture('2026-10-01T01:22:09')).toBe('')
  })

  it('keeps every shape that does say where it is', () => {
    expect(epochSeconds('2026-10-01T05:17:09Z')).toBe(Date.UTC(2026, 9, 1, 5, 17, 9) / 1000)
    expect(epochSeconds('2026-10-01T01:17:09-04:00')).toBe(Date.UTC(2026, 9, 1, 5, 17, 9) / 1000)
    expect(epochSeconds('Thu, 01 Oct 2026 05:17:09 GMT')).toBe(Date.UTC(2026, 9, 1, 5, 17, 9) / 1000)
    expect(epochSeconds(1790831829)).toBe(1790831829)
  })

  it('goes through that one reading in every relative-time helper', () => {
    const zoneLess = '2026-10-01T01:17:09'
    for (const [name, read] of [
      ['knowledge', knowledgeRelTime], ['tasks', taskRelTime], ['files', fileRelTime],
      ['notifications', (s: string) => notificationRelTime(s, NOW)],
      ['notification clock', notificationClock],
    ] as [string, (s: string) => string][]) {
      expect(read(POLLED), `${name} reads the instant`).not.toBe('')
      expect(read(zoneLess), `${name} refuses a stamp with no zone`).toBe('')
    }
    expect(bucketOf(POLLED, NOW)).toBe('Today')
  })
})

describe("a journal's editing day", () => {
  // 20:30 on 30 September here; already 1 October in UTC, which is how the gateway stores it.
  const writtenThisEvening = '2026-10-01T03:30:00+00:00'

  it('is the day the entry was written HERE, not the UTC date its stamp starts with', () => {
    expect(new Date(NOW).getDate(), 'still the 30th here').toBe(30)
    expect(journalDayHasPassed(writtenThisEvening, NOW)).toBe(false)
  })

  it('has passed for an entry written yesterday', () => {
    expect(journalDayHasPassed('2026-09-30T03:30:00+00:00', NOW)).toBe(true)
  })

  it('is not decided here when the stamp cannot be read: the gateway decides, and it allows', () => {
    expect(journalDayHasPassed('', NOW)).toBe(false)
    expect(journalDayHasPassed('2026-09-29T20:30:00', NOW)).toBe(false)
  })
})

describe('a time the user typed into a datetime-local picker', () => {
  it('is local by construction, and reads in this browser\'s zone', () => {
    expect(localDateTimeSeconds('2026-10-05T09:50')).toBe(Date.UTC(2026, 9, 5, 16, 50) / 1000)
    expect(localDateTimeSeconds(localDateTimeInput(1790831829))).toBe(1790831820)
  })

  it('is complete enough to save a one-shot schedule', () => {
    expect(scheduleWhenMet('at', '2026-10-05T09:50')).toBe(true)
    expect(scheduleWhenMet('at', '')).toBe(false)
  })

  it('is refused when it is not a picker value', () => {
    for (const v of ['', 'tomorrow', '2026-10-05', POLLED]) {
      expect(localDateTimeSeconds(v), v).toBeUndefined()
    }
  })
})

describe('the Sources row', () => {
  const source: WatchedSource = {
    id: 'src-1', name: 'Daily', provider: 'watched-dir', kind: 'dir',
    spec: { path: '/home/user/Notes/Daily' }, budget: {}, revision: 'r-src-1',
    enrichment: 'full', poll_interval_secs: 300, poll_every_secs: 300, item_type: 'note', enabled: true,
    health_status: 'ok', last_error_summary: '', last_escalations: [], last_new_count: 5,
    last_poll_at: POLLED, next_poll_at: '2026-10-01T05:22:09.493101+00:00', enrolled: true,
    remediation: { kind: '', guidance: '', detail: '', action: '' },
  }

  it('says a five-minute folder polled just now is next in five minutes', () => {
    const { container } = render(
      <SourceRow source={source} kinds={{ 'watched-dir': { display_name: 'Watched Directory', form: 'dir' } }} onChanged={() => {}} />,
    )
    expect(container.textContent).toContain('Watched Directory · every 5 min · polled just now · 5 new last time · next in 5m')
  })
})
