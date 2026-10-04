import { it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { LoopAgentCliSelfApproval } from '../../lib/api'

// ── An Unattended loop's agent CLI asks PersonalClaw, unless she lets that loop's CLI approve ────
//
// Beside "Runs on", an Unattended loop on an agent CLI says whether its CLI asks PersonalClaw about
// each call (the default) or approves its own. The switch that changes it is the loop's own, off by
// default; turning it on goes through the gateway's consent question (`withSecurityConsent`, inside
// `api.setLoopAgentCliSelfApproval`), and a CLI PersonalClaw has not measured keeps asking, with
// why. Driven through the real `LoopRunsOn` every loop page renders, against the write it sends.

const sent: Array<[string, boolean]> = []
let answer: () => Promise<{ ok: boolean; agent_cli_self_approval: LoopAgentCliSelfApproval }>

vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const api = new Proxy({} as Record<string, unknown>, {
    get(t, p: string) {
      if (p in t) return t[p]
      return (t[p] = () => Promise.resolve([]))
    },
  })
  Object.assign(api, {
    setLoopAgentCliSelfApproval: (id: string, allowed: boolean) => { sent.push([id, allowed]); return answer() },
  })
  return { ...real, api }
})

const ASKS: LoopAgentCliSelfApproval = { allowed: false, available: true, unavailable: '', cli: 'Claude Code' }
const APPROVES: LoopAgentCliSelfApproval = { ...ASKS, allowed: true }
const UNMEASURED: LoopAgentCliSelfApproval = {
  allowed: false, available: false, cli: 'Example Cli',
  unavailable: "PersonalClaw hasn't measured what Example Cli runs without asking, so it can't let it approve its own calls.",
}

beforeEach(() => {
  sent.length = 0
  answer = () => Promise.resolve({ ok: true, agent_cli_self_approval: APPROVES })
})

async function page(loop: Record<string, unknown>) {
  const { LoopRunsOn } = await import('./LoopRuntimePill')
  render(<LoopRunsOn loop={{ id: 'ab12cd34', provider: 'acp:claude-code', ...loop }} />)
}

it('says its CLI asks PersonalClaw, and her switch lets it approve its own calls', async () => {
  const user = userEvent.setup()
  await page({ attended: false, agent_cli_self_approval: ASKS })

  await user.click(screen.getByRole('button', { name: 'Its calls: Asks PersonalClaw' }))
  const toggle = screen.getByRole('switch', { name: 'Let Claude Code approve its own calls' })
  expect(toggle.getAttribute('aria-checked')).toBe('false')
  expect(screen.getByText(/Claude Code asks PersonalClaw about each call it makes/)).toBeTruthy()

  await user.click(toggle)

  await waitFor(() => expect(sent).toEqual([['ab12cd34', true]]))
  await screen.findByRole('button', { name: 'Its calls: Approves its own calls' })
})

it('takes it back the same way', async () => {
  const user = userEvent.setup()
  answer = () => Promise.resolve({ ok: true, agent_cli_self_approval: ASKS })
  await page({ attended: false, agent_cli_self_approval: APPROVES })

  await user.click(screen.getByRole('button', { name: 'Its calls: Approves its own calls' }))
  await user.click(screen.getByRole('switch', { name: 'Let Claude Code approve its own calls' }))

  await waitFor(() => expect(sent).toEqual([['ab12cd34', false]]))
  await screen.findByRole('button', { name: 'Its calls: Asks PersonalClaw' })
})

it('a CLI PersonalClaw has not measured keeps asking, and the switch says why', async () => {
  const user = userEvent.setup()
  await page({ provider: 'acp:example-cli', attended: false, agent_cli_self_approval: UNMEASURED })

  await user.click(screen.getByRole('button', { name: 'Its calls: Asks PersonalClaw' }))
  const toggle = screen.getByRole('switch', { name: 'Let Example Cli approve its own calls' })
  expect(toggle.getAttribute('aria-disabled')).toBe('true')
  expect(toggle.getAttribute('title')).toBe(UNMEASURED.unavailable)
  await user.click(toggle)
  expect(sent).toEqual([])
})

it('a declined consent changes nothing and says so', async () => {
  const user = userEvent.setup()
  const { ConsentDeclined } = await import('../../lib/securityConsent')
  const { notify } = await import('../../app/appSdk')
  answer = () => Promise.reject(new ConsentDeclined('loop.agent_cli_self_approval'))
  await page({ attended: false, agent_cli_self_approval: ASKS })

  await user.click(screen.getByRole('button', { name: 'Its calls: Asks PersonalClaw' }))
  await user.click(screen.getByRole('switch', { name: 'Let Claude Code approve its own calls' }))

  await waitFor(() => expect(notify).toHaveBeenCalledWith('Not changed — you kept the current setting.'))
  expect(screen.getByRole('button', { name: 'Its calls: Asks PersonalClaw' })).toBeTruthy()
})

it('an Attended loop, and a loop on PersonalClaw, show nothing of it', async () => {
  await page({ attended: true, agent_cli_self_approval: { ...ASKS, available: false } })
  expect(screen.queryByRole('button', { name: /^Its calls:/ })).toBeNull()
  document.body.innerHTML = ''
  await page({ provider: '', attended: false, agent_cli_self_approval: { ...ASKS, available: false, cli: '' } })
  expect(screen.queryByRole('button', { name: /^Its calls:/ })).toBeNull()
})
