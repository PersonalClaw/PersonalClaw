import { describe, it, expect } from 'vitest'
import { ledgerRowDetail, ledgerRowKey } from './ledgerRowDetail'

// The pure projection behind the inspector's ledger list (SELF-VERIFICATION SC#6). The RENDERED
// output is pinned in `NodeInspectorDrawer.test.tsx`; this file locks the projection's edges, which
// a DOM test cannot reach cleanly: a non-string field, a missing kind, and the key identity that
// keeps several rows sharing one node id from colliding.

describe('ledgerRowDetail', () => {
  it('reads the three fields a triage skip record carries', () => {
    expect(ledgerRowDetail({
      kind: 'step_skipped',
      sha: 'a1b2c3d4',
      impact: 'test',
      rationale: 'assertion maintenance only — 3 test file(s), no shipped code',
    })).toEqual({
      kind: 'step_skipped',
      sha: 'a1b2c3d4',
      impact: 'test',
      rationale: 'assertion maintenance only — 3 test file(s), no shipped code',
    })
  })

  it('projects an engine row that carries none of them to empty strings', () => {
    // The pre-SC#6 shape. It must survive unchanged: the renderer omits an element per empty
    // string, so a plain `step_completed` renders exactly as it always did.
    expect(ledgerRowDetail({ kind: 'step_completed', state: 'done' })).toEqual({
      kind: 'step_completed', sha: '', impact: '', rationale: '',
    })
  })

  it('does not stringify a non-string field', () => {
    // `[object Object]` rendered as the reason a commit was skipped would hide a producer bug
    // behind something that looks like content — so a non-string projects to absent.
    const d = ledgerRowDetail({ kind: 'step_skipped', sha: 12345, impact: null, rationale: { why: 'x' } })
    expect([d.sha, d.impact, d.rationale]).toEqual(['', '', ''])
  })

  it('trims surrounding whitespace but never clips the body', () => {
    const long = 'assertion maintenance only — 3 test file(s), no shipped code'
    expect(ledgerRowDetail({ kind: 'step_skipped', rationale: `  ${long}  ` }).rationale).toBe(long)
    // the whole reason survives — the projection has no length bound of any kind.
    expect(ledgerRowDetail({ kind: 'x', rationale: 'y'.repeat(500) }).rationale).toHaveLength(500)
  })

  it('falls back to "event" for a row with no readable kind', () => {
    expect(ledgerRowDetail({}).kind).toBe('event')
    expect(ledgerRowDetail({ kind: 7 }).kind).toBe('event')
  })
})

describe('ledgerRowKey', () => {
  it('keys on the ledger event id, so rows sharing a node id and an impact stay distinct', () => {
    // The exact collision the companion produces: two test-only commits, same kind, same node,
    // same instance path, same impact class. A content-derived key would fold them into one and
    // silently drop a skip from the DOM.
    const a = { kind: 'step_skipped', node_id: 'triage', instance_path: 'root.children[0]', impact: 'test', event_id: 'r-evt-1' }
    const b = { ...a, event_id: 'r-evt-2', sha: 'zzz' }
    expect(ledgerRowKey(a, 0)).not.toBe(ledgerRowKey(b, 1))
  })

  it('falls back to the index when a row has no event id', () => {
    const row = { kind: 'step_skipped', impact: 'test' }
    expect(ledgerRowKey(row, 0)).toBe('row-0')
    // identical CONTENT at two positions still yields two keys — positional, never content.
    expect(ledgerRowKey(row, 1)).toBe('row-1')
  })
})
