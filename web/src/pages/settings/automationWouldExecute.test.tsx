import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'

// ── The automation would-execute description (PLATFORM-RESILIENCE §3.3, PR2-7) ─────────────────
//
// §3.3 asks for five facts, RENDERED on the trust surface beside the surfacing simulator:
// resolved next-fire, the rendered `action_config` with `$vars` substituted, the target session
// key, the capability grants, and the observe-mode result. The backend suite pins the payload;
// this suite pins that a user can READ them — the endpoint existed for the surfacing half for a
// whole session with `doctorSimulateSurfacing` having ZERO frontend consumers, which is exactly
// the "shipped but inert" shape a payload-only test cannot see.
//
// So every test drives the real section: pick an automation in the real <select>, press the real
// button, assert on the rendered DOM. Each fact has a vacuity case that renders DIFFERENTLY, so
// none of these assertions can pass against a hard-coded string.

const TRIGGERS = [
  { kind: 'schedule', id: 'schedule:deploy', raw_id: 'deploy', name: 'Deploy check', enabled: true, action: {} },
]

const described = (over: Record<string, unknown> = {}) => ({
  trigger: {
    id: 'deploy', name: 'Deploy check', kind: 'clock', enabled: true, state: 'active',
    ok: true, issues: [],
  },
  next_fire: {
    cadence: 'At 9:00 AM EDT', at: '2031-03-04T14:00:00+00:00', epoch: 1930658400,
    source: 'armed', armed: true,
  },
  action_config: {
    provider: 'run-prompt', config: { prompt_id: 'deploy-check' },
    vars: { service: 'gateway' }, secret_refs: [], rendered: 'Check gateway in prod.',
    render_error: '',
  },
  session_key: { key: 'cron:deploy', declared: 'pinned:cron:deploy', mode: 'pinned' },
  capability_grants: {
    declared: {}, requested: { providers: ['run-prompt'] },
    needs_fence: { providers: ['run-prompt'] },
    refused: [], granted: true,
  },
  observe_mode: {
    provider: 'run-prompt', provider_known: true, supported: true, mode: 'observe',
    executed: false, ok: true, detail: 'Dry run of deploy.\n  nothing was executed.',
    gate_plan: { enforced: ['incident', 'screen', 'capability'], bypassed: ['quiet'], dry_run: true, executes: false },
  },
  dry_run: true,
  ...over,
})

/** Mount the REAL section and drive it: select → Describe. Returns the trigger id the client
 *  was actually called with, so a picker that sent the wrong id cannot pass. */
async function describeIt(over: Record<string, unknown> = {}, opts: { triggers?: unknown[]; fail?: boolean } = {}) {
  vi.resetModules()
  const calls: string[] = []
  vi.doMock('../../lib/api', () => ({
    api: {
      triggers: () => Promise.resolve({ triggers: opts.triggers ?? TRIGGERS, server_tz: 'UTC' }),
      doctorSimulateAutomation: (id: string) => {
        calls.push(id)
        return opts.fail ? Promise.reject(new Error('boom')) : Promise.resolve(described(over))
      },
      doctorSimulateSurfacing: () => Promise.resolve({ query: '', candidates: [] }),
      doctor: () => Promise.resolve(null),
      doctorRemediation: () => Promise.resolve(null),
    },
  }))
  const { SimulatorsSection } = await import('./DoctorPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<SimulatorsSection />)
    await new Promise((res) => setTimeout(res, 0))
  })
  const select = screen.queryByLabelText('An automation to describe') as HTMLSelectElement | null
  if (select) {
    await act(async () => {
      fireEvent.change(select, { target: { value: 'deploy' } })
      await new Promise((res) => setTimeout(res, 0))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /describe/i }))
      await new Promise((res) => setTimeout(res, 0))
    })
  }
  return { r, calls, body: () => r.container.textContent ?? '' }
}

describe('the five facts §3.3 names are all on screen', () => {
  it('renders a labelled row for every one of the five', async () => {
    const { body } = await describeIt()
    const text = body()
    for (const label of ['Next fire', 'Action', 'Session', 'Allowed to', 'Observe-mode dry fire']) {
      expect(text).toContain(label)
    }
  })

  it('sends the STORE id, not the namespaced wire id', async () => {
    // `/api/triggers` prefixes ids (`schedule:deploy`) as its migration map; the unified store is
    // keyed by `raw_id`. Sending the wire id would 404 on every schedule trigger.
    const { calls } = await describeIt()
    expect(calls).toEqual(['deploy'])
  })
})

describe('fact 1 — resolved next fire', () => {
  it('shows the cadence and marks an ARMED row as armed', async () => {
    const { body } = await describeIt()
    expect(body()).toContain('At 9:00 AM EDT')
    expect(body()).toContain('armed')
  })

  it('VACUITY: an unarmed row says so instead of reading as a scheduled time', async () => {
    const { body } = await describeIt({
      next_fire: { cadence: 'every 300s', at: '2031-03-04T14:00:00+00:00', epoch: 1, source: 'computed', armed: false },
    })
    expect(body()).toContain('not armed yet')
  })

  it('VACUITY: a row with no fire at all renders the third state, not a blank', async () => {
    const { body } = await describeIt({
      next_fire: { cadence: 'manual', at: '', epoch: null, source: 'none', armed: false },
    })
    expect(body()).toContain('no scheduled fire')
  })
})

describe('fact 2 — the rendered action_config with $vars', () => {
  it('shows the provider and the prompt rendered from the trigger vars', async () => {
    const { body } = await describeIt()
    expect(body()).toContain('run-prompt')
    expect(body()).toContain('Check gateway in prod.')
  })

  it('VACUITY: a render failure is announced, not swallowed into a plausible prompt', async () => {
    const { r, body } = await describeIt({
      action_config: {
        provider: 'run-prompt', config: {}, vars: {}, secret_refs: [], rendered: '',
        render_error: "variable 'env' is required",
      },
    })
    expect(body()).toContain('Would fail to render')
    // Unrequested bad news that changes what the screen means — the same reason the panel's
    // load-failure sentence announces.
    expect(r.container.querySelector('[role="alert"]')).not.toBeNull()
  })

  it('names a referenced secret and never shows a value', async () => {
    const { body } = await describeIt({
      action_config: {
        provider: 'bash',
        config: { command: 'curl -H "Authorization: Bearer «secret:DEPLOY_TOKEN — not resolved by a preview»"' },
        vars: {}, secret_refs: ['DEPLOY_TOKEN'], rendered: '', render_error: '',
      },
    })
    expect(body()).toContain('uses DEPLOY_TOKEN')
    expect(body()).toContain('not resolved by a preview')
  })
})

describe('fact 3 — the target session key', () => {
  it('shows the resolved key and its mode', async () => {
    const { body } = await describeIt()
    expect(body()).toContain('cron:deploy')
    expect(body()).toContain('pinned')
  })

  it('VACUITY: a fresh session renders a different mode', async () => {
    const { body } = await describeIt({
      session_key: { key: 'cron:deploy', declared: '', mode: 'fresh' },
    })
    expect(body()).toContain('fresh')
    expect(body()).not.toContain('pinned')
  })
})

describe('fact 4 — capability grants', () => {
  it('says YES for a granted action', async () => {
    const { body } = await describeIt()
    expect(body()).toContain('the frozen set grants run-prompt')
  })

  it('VACUITY: a refused action renders the refusal AND its reason', async () => {
    const { body } = await describeIt({
      capability_grants: {
        declared: {}, requested: { providers: ['bash'] }, needs_fence: { providers: ['bash'] },
        refused: [{ key: 'providers', value: 'bash', reason: 'this trigger declares no capabilities, so nothing is permitted' }],
        granted: false,
      },
    })
    expect(body()).toContain('Refused: bash')
    // A refusal a user cannot explain is one they work around by widening the allowlist.
    expect(body()).toContain('declares no capabilities')
  })

  it('VACUITY: the read-only default is a distinct third rendering', async () => {
    const { body } = await describeIt({
      capability_grants: {
        declared: {}, requested: { providers: ['notify'] }, needs_fence: {}, refused: [], granted: true,
      },
    })
    expect(body()).toContain('a read-only action needs no opt-in')
  })
})

describe('fact 5 — the observe-mode result', () => {
  it('labels a real observe-mode dry fire and shows the gate plan', async () => {
    const { body } = await describeIt()
    expect(body()).toContain('Observe-mode dry fire')
    expect(body()).toContain('nothing was executed')
    expect(body()).toContain('Gates enforced: incident, screen, capability')
  })

  it('VACUITY (T9 honesty): a deterministic provider is labelled a PREVIEW, not an observe run', async () => {
    const { body } = await describeIt({
      observe_mode: {
        provider: 'bash', provider_known: true, supported: false, mode: 'preview', executed: false,
        ok: true, detail: 'Dry run of deploy.\n  nothing was executed.', gate_plan: { enforced: ['screen'] },
      },
    })
    expect(body()).toContain('Preview (no observe mode)')
    expect(body()).toContain('has no observe mode')
    expect(body()).not.toContain('Observe-mode dry fire')
  })

  it('VACUITY: an unregistered provider reads as broken, not as "no observe mode"', async () => {
    const { body } = await describeIt({
      observe_mode: {
        provider: 'no-such', provider_known: false, supported: false, mode: 'preview',
        executed: false, ok: false, detail: 'x', gate_plan: {},
      },
    })
    expect(body()).toContain('is registered')
    expect(body()).toContain('cannot run')
  })
})

describe('AUTO-R15 issue records reach the surface', () => {
  it('renders the closest-match suggestion beside the problem', async () => {
    const { body } = await describeIt({
      trigger: {
        id: 'deploy', name: 'Deploy check', kind: 'clock', enabled: true, state: 'active', ok: true,
        issues: [{ path: 'gates.debounce_seconds', message: 'unknown key', severity: 'warning', closest: 'debounce_secs' }],
      },
    })
    expect(body()).toContain('did you mean debounce_secs?')
  })
})

describe('the surfaces around it', () => {
  it('sits beside the surfacing simulator, both under one Simulators heading', async () => {
    await describeIt()
    expect(screen.getByRole('heading', { name: /simulators/i })).toBeTruthy()
    expect(screen.getByLabelText('A message to simulate skill surfacing for')).toBeTruthy()
    expect(screen.getByLabelText('An automation to describe')).toBeTruthy()
  })

  it('an endpoint failure is announced instead of leaving a stale description on screen', async () => {
    const { r, body } = await describeIt({}, { fail: true })
    expect(body()).toContain("Couldn't describe that automation")
    expect(r.container.querySelector('[role="alert"]')).not.toBeNull()
    expect(body()).not.toContain('Next fire')
  })

  it('with no automations the picker says so and the button explains why it is off', async () => {
    const { body } = await describeIt({}, { triggers: [] })
    expect(body()).toContain('No automations yet')
    // `Button` routes `disabledReason` into `title` + `aria-disabled` (reachable-but-unavailable),
    // so a natively-disabled button with no reason would fail here — which is the point: a
    // control that is off for a reason the user cannot read is the shape the disabled-reason
    // census exists to keep out.
    const btn = screen.getByRole('button', { name: /describe/i })
    expect(btn.getAttribute('title')).toContain('no automations yet')
    expect(btn.getAttribute('aria-disabled')).toBe('true')
  })

  it('VACUITY: with an automation available but none picked, the reason is the OTHER one', async () => {
    // Same disabled button, two different explanations. A constant reason would read as
    // "you have no automations" on a machine that has one — non-null and still wrong.
    vi.resetModules()
    vi.doMock('../../lib/api', () => ({
      api: {
        triggers: () => Promise.resolve({ triggers: TRIGGERS, server_tz: 'UTC' }),
        doctorSimulateAutomation: () => Promise.resolve(described()),
        doctorSimulateSurfacing: () => Promise.resolve({ query: '', candidates: [] }),
      },
    }))
    const { SimulatorsSection } = await import('./DoctorPanel')
    await act(async () => {
      render(<SimulatorsSection />)
      await new Promise((res) => setTimeout(res, 0))
    })
    const btn = screen.getByRole('button', { name: /describe/i })
    expect(btn.getAttribute('title')).toContain('Pick an automation first')
  })
})
