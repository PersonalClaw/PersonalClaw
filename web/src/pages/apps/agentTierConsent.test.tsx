import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import type { AgentTier } from '../../lib/api'
import { AGENT_TIER_SENTENCE, PermissionList, permissionRows } from './installConsent'

// An app's `agent` permission names a tier (`apps/agent_tiers`), and install consent says
// what that tier lets the app's agent work use. It was one sentence for one boolean, "Run
// background agents that use any tool without asking you", so an app that only summarises the
// text it sends asked her for every tool. `PermissionList` is what the install dialog, the Store's
// detail panel and the installed app's panel all render, so these words are on all three.

const text = (el: HTMLElement) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ')

const TIERS: AgentTier[] = ['text', 'read', 'tools']

/** What each tier must say, in the words the gateway holds the work to. */
const SAID: Record<AgentTier, RegExp[]> = {
  text: [
    /Run AI tasks on the text it sends/,
    /the model is handed only that text, with no tools/,
    /can’t read your files or memory, change anything, run commands or send messages/,
  ],
  read: [
    /Run background agents with read-only tools/,
    /can read your files and data, and can’t change anything or send messages/,
  ],
  tools: [
    /Run background agents that use your tools/,
    /they can change files, run commands and send messages/,
    /the app can’t approve their calls, so each one that needs approval asks you/,
  ],
}

describe('install consent words an app’s agent permission by its tier', () => {
  for (const tier of TIERS) {
    it(`says what a "${tier}" tier lets the app’s agent use`, () => {
      render(<PermissionList perms={{ agent: tier }} />)
      for (const said of SAID[tier]) expect(text(document.body)).toMatch(said)
      expect(permissionRows({ agent: tier })).toEqual([AGENT_TIER_SENTENCE[tier]])
    })
  }

  it('words no tier as agents that act without asking: none lets the app approve a call', () => {
    for (const tier of TIERS) expect(AGENT_TIER_SENTENCE[tier]).not.toMatch(/without asking/)
  })

  it('words each tier on its own, so an update that changes the tier shows as a change', () => {
    expect(new Set(TIERS.map((tier) => AGENT_TIER_SENTENCE[tier])).size).toBe(TIERS.length)
  })

  it('says nothing about agent work for an app that declares none', () => {
    expect(permissionRows({ api: ['/api/knowledge'] })).toEqual(['API: /api/knowledge'])
  })
})
