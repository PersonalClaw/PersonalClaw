import { describe, expect, it } from 'vitest'
import type { RuntimeGroup } from '../../lib/agents'
import { showRuntime } from './loopRuntime'

// ── A loop's page names what it runs on, and says when that CLI can't run it now ──────────────
//
// The loop pages (Code cockpit strip, loop and design status bars, both Plan Reviews) read this one
// sentence. A runtime that went away, is not ready, or no longer lists the chosen agent is said,
// never shown as if the loop runs there; while the runtimes have not been read nothing is claimed.

const READY: RuntimeGroup = {
  providerId: 'acp:example-cli', ready: true, state: 'ready', detail: '', failure: '',
  agents: [{ id: 'acp:example-cli/careful-coder', name: 'Careful coder', runtime: 'acp:example-cli', description: '', provider_agent: 'careful-coder', reasoning_effort: '', models: [] }],
}
const ON_IT = { provider: 'acp:example-cli', provider_agent: 'careful-coder' }

describe('what a loop runs on, as its page says it', () => {
  it('PersonalClaw, and a ready CLI as its agent, with nothing unavailable', () => {
    expect(showRuntime({ provider: '', provider_agent: '' }, [READY])).toMatchObject({ label: 'PersonalClaw', unavailable: '' })
    expect(showRuntime(ON_IT, [READY])).toMatchObject({ label: 'Example Cli · Careful coder', unavailable: '' })
  })

  it('a CLI that is no longer set up here', () => {
    expect(showRuntime(ON_IT, []).unavailable).toBe('Example Cli isn’t set up here: its agent app is not installed or not enabled.')
  })

  it('a CLI that is not ready, in its own words', () => {
    const signedOut = { ...READY, ready: false, state: 'needs_login', detail: 'Sign in to Example Cli first.', agents: [] }
    expect(showRuntime(ON_IT, [signedOut]).unavailable).toBe('Example Cli isn’t ready: Sign in to Example Cli first.')
  })

  it('a CLI whose last Test no longer lists the chosen agent', () => {
    expect(showRuntime(ON_IT, [{ ...READY, agents: [] }]).unavailable).toBe('Example Cli no longer lists careful-coder among its agents.')
  })

  it('claims nothing before the runtimes are read', () => {
    expect(showRuntime(ON_IT, undefined)).toMatchObject({ label: 'Example Cli · careful-coder', unavailable: '' })
  })
})
