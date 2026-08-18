import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Runtime counters: every measured counter reaches a surface ─────────────────
//
// `/api/system`.stats and `/api/status`.stats both carry the Stats singleton's snapshot. The
// backend increments 11 counters on real runtime paths; UsagePanel used to read THREE of them
// (input_tokens, output_tokens, total_turns), leaving 8 counters that are genuinely measured with
// no reader anywhere in the app:
//
//   sessions_created · sessions_cleaned        session lifecycle
//   subagents_spawned · completed · failed     subagent lifecycle
//   cache_creation_tokens · cache_read_tokens  prompt-cache accounting
//   total_duration_ms                          cumulative model wall-clock
//
// That is the "backend truth, frontend silence" shape: sweep the wire field's READERS, not its
// writers. A field the backend faithfully computes and no surface renders is invisible work.
//
// The counterpart defect (fixed in the same change, gated by tests/test_stats.py) ran the other
// way: 7 counters were DECLARED and typed with no writer at all, so they reported a confident `0`
// forever — `messages_received/success/failed`, `tool_approvals/denials/auto_approved`, `timeouts`,
// plus an 8th on DashboardState. Those were deleted, not surfaced: rendering an unmeasured 0 is
// worse than not rendering it, because it is indistinguishable from a quiet system.

const PANEL = join(process.cwd(), 'src/pages/settings/UsagePanel.tsx')
const API = join(process.cwd(), 'src/lib/api.ts')

/** Every counter the backend increments on a real path — must all be read by the panel. */
const MEASURED = [
  'sessions_created', 'sessions_cleaned',
  'subagents_spawned', 'subagents_completed', 'subagents_failed',
  'input_tokens', 'output_tokens',
  'cache_creation_tokens', 'cache_read_tokens',
  'total_turns', 'total_duration_ms',
]

/** Deleted for having no writer. None may return to the type or the panel. */
const WRITERLESS = [
  'messages_received', 'messages_success', 'messages_failed',
  'tool_approvals', 'tool_denials', 'tool_auto_approved', 'timeouts',
]

describe('the wire type matches what the backend actually sends', () => {
  const api = readFileSync(API, 'utf8')
  const iface = api.slice(
    api.indexOf('export interface SystemAgentStats'),
    api.indexOf('export interface SystemInfo'),
  )

  it('declares every measured counter', () => {
    const missing = MEASURED.filter((f) => !new RegExp(`\\b${f}\\b`).test(iface))
    expect(missing, `SystemAgentStats is missing measured counter(s): ${missing.join(', ')}`).toEqual([])
  })

  it('declares no writerless counter', () => {
    // A typed field with no backend writer is a promise the API cannot keep.
    const zombies = WRITERLESS.filter((f) => new RegExp(`\\b${f}\\b`).test(iface))
    expect(zombies, `SystemAgentStats declares writerless counter(s): ${zombies.join(', ')}`).toEqual([])
  })
})

describe('UsagePanel reads every measured counter', () => {
  const src = readFileSync(PANEL, 'utf8')

  it('references all 11 on the stats object', () => {
    // `sys.` prefixed so a mention inside a comment does not satisfy the rail.
    const unread = MEASURED.filter((f) => !src.includes(`sys.${f}`))
    expect(unread, `measured but never rendered: ${unread.join(', ')}`).toEqual([])
  })

  it('gates the section on ANY counter moving, not just tokens', () => {
    // The old condition was `sys.input_tokens > 0 || sys.output_tokens > 0`, so a gateway that
    // created sessions or spawned subagents WITHOUT a chat turn hid every counter — the section
    // was invisible exactly when the lifecycle counters were the only interesting thing.
    expect(src).toMatch(/sessions_created > 0/)
    expect(src).toMatch(/subagents_spawned > 0/)
  })
})

describe('the rendered rows', () => {
  const stats = {
    sessions_created: 4, sessions_cleaned: 1,
    subagents_spawned: 7, subagents_completed: 6, subagents_failed: 1,
    input_tokens: 12_400, output_tokens: 3_100,
    cache_creation_tokens: 50, cache_read_tokens: 9_000,
    total_turns: 18, total_duration_ms: 3_930_000,
  }

  const mount = async (overrides: Partial<typeof stats> = {}) => {
    vi.resetModules()
    vi.doMock('../../lib/api', () => ({
      api: {
        usageTotals: () => Promise.resolve({ totals: null }),
        usageRollup: () => Promise.resolve({ rows: [] }),
        // The MRT-3 spend fold. Rejecting is the honest stub for "this install has no fold yet":
        // the panel catches it and renders nothing, which is what these row assertions assume.
        usageFold: () => Promise.reject(new Error('no fold')),
        personalclawConfig: () => Promise.resolve(null),
        system: () => Promise.resolve({ stats: { ...stats, ...overrides } }),
      },
    }))
    const { UsagePanel } = await import('./UsagePanel')
    let r!: ReturnType<typeof render>
    // useCachedData resolves async, so the render AND the flush both have to sit inside act() —
    // otherwise the state updates land outside it and React warns while the assertions still pass.
    await act(async () => {
      r = render(<UsagePanel query={{}} setQuery={() => {}} />)
      await new Promise((res) => setTimeout(res, 0))
    })
    return r
  }

  it('shows both session lifecycle counters', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('4 created / 1 cleaned')
  })

  it('shows subagent failures in warn ink only when non-zero', async () => {
    const failed = await mount()
    expect(failed.container.querySelector('.text-warn')?.textContent).toContain('1 failed')
    const clean = await mount({ subagents_failed: 0 })
    // A zero-failure gateway should not carry a permanent red-ish "0 failed".
    expect(clean.container.textContent).not.toContain('failed')
  })

  it('formats cumulative duration as a span, not raw ms', async () => {
    const { container } = await mount()
    // 3_930_000ms = 65m 30s → "1h 5m". Raw ms would read as a meaningless 3930000.
    expect(container.textContent).toContain('1h 5m')
    expect(container.textContent).not.toContain('3930000')
  })

  it('omits the prompt-cache row when no provider reported cached tokens', async () => {
    const { container } = await mount({ cache_read_tokens: 0, cache_creation_tokens: 0 })
    expect(container.textContent).not.toContain('Prompt cache')
  })
})
