import { describe, expect, it } from 'vitest'
import { advisoryBadge, type Trigger } from './triggerMeta'
import type { Trigger as WireTrigger } from '../../lib/api'

// ── A file automation's badge says its folder is missing ──────────────────────────────────────────
//
// A file automation whose folder is not on this machine (a home restored from another one) carries
// a warning saying so, and the list badged every warning "check schedule": a file automation has
// no schedule to check, so the badge sent the owner to the wrong place. Measured on a twin after a
// replace restore: "Summarize new notes · check schedule · file". It now says "folder missing",
// as a web watch that cannot fire says "can't fire".

function row(store: Partial<WireTrigger>): Pick<Trigger, 'store'> {
  return { store: { warnings: ['a warning'], ...store } as WireTrigger }
}

describe("a row's warning badge", () => {
  it('says a file automation watches a folder that is not here', () => {
    expect(advisoryBadge(row({ store_kind: 'file', folders_not_here: ['/Users/ada/notes'] }))).toBe('folder missing')
    expect(advisoryBadge(row({ store_kind: 'file', folders_not_here: ['/a', '/b'] }))).toBe('folders missing')
  })

  it("keeps a web watch's can't fire, which says more", () => {
    expect(advisoryBadge(row({ last_check: { can_fire: false } as WireTrigger['last_check'], folders_not_here: [] }))).toBe("can't fire")
  })

  it('says check schedule for any other advisory, a file automation whose folders are all here included', () => {
    expect(advisoryBadge(row({ store_kind: 'file', folders_not_here: [] }))).toBe('check schedule')
    expect(advisoryBadge({})).toBe('check schedule')
  })
})
