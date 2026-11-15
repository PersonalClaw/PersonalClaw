import { describe, expect, it } from 'vitest'
import { refLabel, refTarget } from './inboxMeta'

// ── A scheduled report's Inbox finding opens its write-up ──────────────────────────────────────
//
// A report's run files one Inbox row for what it found, carrying the report and the write-up it
// rewrote (`refs.research_report_id`, `refs.knowledge_item_id`,
// `action_providers/knowledge_report_provider.py`). The row had no branch for either, so its
// "Go to source" opened nothing. It opens the write-up's page now, and says so.

const WRITE_UP = '0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d'
const row = (refs: Record<string, unknown>) => ({ refs })

describe("a report's Inbox finding", () => {
  it("🔑 opens its write-up's page", () => {
    const it_ = row({ research_report_id: 'rpt-1a2b3c4d', knowledge_item_id: WRITE_UP })
    expect(refTarget(it_)).toBe(`knowledge/item/${WRITE_UP}`)
    expect(refLabel(it_)).toBe('Open the write-up')
  })

  it('a finding whose run kept no write-up has nowhere to go, as before', () => {
    expect(refTarget(row({ research_report_id: 'rpt-1a2b3c4d' }))).toBe('')
  })

  it('🔑 the write-up the persist step made, 12 hex characters, opens too', () => {
    // A report's run writes its write-up through the persist step, which gives a new item
    // `uuid4().hex[:12]`: the shape a real report's finding carries.
    expect(refTarget(row({ research_report_id: 'rpt-1a2b3c4d', knowledge_item_id: '3f9a0c1d2e4b' }))).toBe('knowledge/item/3f9a0c1d2e4b')
  })

  it('🔑 an id in no shape the store mints opens nothing: the notification\'s one rule', () => {
    for (const id of ['', '../../settings', 'a b', 42, 'k-0a1b2c3d', '3F9A0C1D2E4B', '3f9a0c1d2e4', `${WRITE_UP}x`]) {
      expect(refTarget(row({ knowledge_item_id: id })), String(id)).toBe('')
    }
  })

  it('every other ref resolves as it did: a chat, a run and an artifact go first', () => {
    expect(refTarget(row({ session: 'chat-7', knowledge_item_id: WRITE_UP }))).toBe('chat/chat-7')
    expect(refTarget(row({ artifact: 'identity-report', knowledge_item_id: WRITE_UP }))).toBe('artifacts/identity-report')
  })
})
