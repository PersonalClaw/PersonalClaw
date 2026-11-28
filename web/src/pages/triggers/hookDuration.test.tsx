import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { api, type HookItem } from '../../lib/api'
import { fmtRunTime } from '../../lib/duration'
import { hookRunTime, hookToTrigger } from './triggerMeta'
import { LifecycleDetail } from './LifecycleDetail'

// ── A hook says how long its last run took ──────────────────────────────────────────────────────
//
// Each hook keeps how long its last run took (`last_duration_ms`), and its row and detail say it:
// "last ran in 1.2 s", or "120 ms" under a second. A hook that has never run here says nothing, and
// so does one whose run recorded no time (from another home, or from a gateway before hooks kept
// one).

vi.mock('../schedule/ScheduleDetail', () => ({ RunHistory: () => null }))

const hook = (over: Partial<HookItem> = {}): HookItem => ({
  id: 'h1', name: 'Check the commit message', event: 'PreToolUse', matcher: 'Bash',
  provider: 'bash', provider_config: { command: './check-message.sh' },
  timeout: 30, enabled: true, last_run: 1764000000, last_status: 'ok', run_count: 4, used_by: ['coder'],
  ...over,
})

const detail = (h: HookItem) => render(
  <LifecycleDetail
    hook={h}
    providers={[{ name: 'bash', display_name: 'Bash', supports_blocking: true, settingsSchema: {} }]}
    onSaved={() => {}}
    onDeleted={() => {}}
    editing={false}
    onEditingChange={() => {}}
  />,
)

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('fmtRunTime', () => {
  it('says milliseconds under a second and seconds to one decimal under a minute', () => {
    expect(fmtRunTime(120)).toBe('120 ms')
    expect(fmtRunTime(1)).toBe('1 ms')
    expect(fmtRunTime(1234)).toBe('1.2 s')
    expect(fmtRunTime(2000)).toBe('2.0 s')
    expect(fmtRunTime(12_340)).toBe('12.3 s')
  })

  it('rounds once at each step, so a time never reads as the next unit up', () => {
    expect(fmtRunTime(999.6)).toBe('1.0 s')
    expect(fmtRunTime(59_960)).toBe('1m 0s')
    expect(fmtRunTime(65_000)).toBe('1m 5s')
  })

  it('says nothing for nothing measured', () => {
    expect(fmtRunTime(0)).toBe('')
    expect(fmtRunTime(undefined)).toBe('')
    expect(fmtRunTime(-5)).toBe('')
  })
})

describe('hookRunTime', () => {
  it('is the last run time of a hook that has run', () => {
    expect(hookRunTime(hook({ last_duration_ms: 1200 }))).toBe('1.2 s')
  })

  it('is nothing for a hook that has never run, whatever time it carries', () => {
    expect(hookRunTime(hook({ run_count: 0, last_duration_ms: 1200 }))).toBe('')
  })

  it('is nothing for a run that recorded no time', () => {
    expect(hookRunTime(hook({ last_duration_ms: 0 }))).toBe('')
    expect(hookRunTime(hook())).toBe('')
    expect(hookRunTime(null)).toBe('')
  })
})

describe("the Automations list reads the hook's last run time", () => {
  it('carries last_duration_ms from the lifecycle row onto the hook, and onto its list row', async () => {
    const row = {
      kind: 'lifecycle', id: 'lifecycle:h1', raw_id: 'h1', name: 'Check the commit message', enabled: true,
      action: { provider: 'bash', config: { command: './check-message.sh' } },
      event: 'PreToolUse', matcher: 'Bash', timeout: 30, last_run: 1764000000, last_status: 'ok',
      run_count: 4, last_duration_ms: 120, used_by: ['coder'],
    }
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ triggers: [row], server_tz: 'UTC' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
    const { hooks } = await api.automations()
    expect(hooks[0].last_duration_ms).toBe(120)
    expect(hookRunTime(hookToTrigger(hooks[0]).hook)).toBe('120 ms')
  })

  it("the row says it beside the run count, for a lifecycle row only", () => {
    const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
    const list = strip(readFileSync(resolve(__dirname, 'TriggersListPage.tsx'), 'utf8'))
    expect(list).toMatch(/t\.kind === 'lifecycle' && hookRunTime\(t\.hook\) && <span>last ran in \{hookRunTime\(t\.hook\)\}<\/span>/)
  })
})

describe('LifecycleDetail', () => {
  it('says how long the last run took', () => {
    detail(hook({ last_duration_ms: 1234 }))
    expect(screen.getByText('Last ran in 1.2 s')).toBeTruthy()
  })

  it('says it in milliseconds under a second', () => {
    detail(hook({ last_duration_ms: 120 }))
    expect(screen.getByText('Last ran in 120 ms')).toBeTruthy()
  })

  it('says nothing for a hook that has never run', () => {
    detail(hook({ run_count: 0, last_run: 0, last_status: '', last_duration_ms: 0 }))
    expect(screen.queryByText(/Last ran in/)).toBeNull()
  })

  it('says nothing when the gateway sends no run time', () => {
    detail(hook())
    expect(screen.queryByText(/Last ran in/)).toBeNull()
  })
})
