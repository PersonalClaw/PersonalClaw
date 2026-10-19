import { describe, it, expect } from 'vitest'
import { toDraft, draftToPayload, emptyDraft } from './AgentForm'
import type { SavedAgent } from '../../lib/api'

// ── An agent's reply style travels with it, and "" means it follows the platform default ────

const AGENT: SavedAgent = { name: 'scout', provider: '', reply_style: 'terse', revision: 'r1' }

describe('the agent form carries the reply style', () => {
  it('a new agent states no style, so it follows the platform default', () => {
    expect(emptyDraft().reply_style).toBe('')
  })

  it('an agent’s stored style is read and sent back as it is', () => {
    expect(toDraft(AGENT).reply_style).toBe('terse')
    expect(draftToPayload(toDraft(AGENT)).reply_style).toBe('terse')
  })

  it('an agent with no style sends "" (reset to the platform default)', () => {
    const { reply_style: _gone, ...rest } = AGENT
    expect(draftToPayload(toDraft(rest as SavedAgent)).reply_style).toBe('')
  })

  it('no natural-voice field is left in the draft or the payload', () => {
    expect(toDraft(AGENT)).not.toHaveProperty('natural_voice')
    expect(draftToPayload(toDraft(AGENT))).not.toHaveProperty('natural_voice')
  })
})
