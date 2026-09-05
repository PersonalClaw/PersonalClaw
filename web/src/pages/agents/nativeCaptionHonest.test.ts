import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The Native section caption must not promise what its rows refuse ─────────────────────────────
//
// The Native group renders BOTH populations in one section: reserved built-ins (definition
// fixed — the backend answers DELETE with 403 and `handlers/agents.py` sets
// `editable: not is_reserved_agent`) and user-created agents (fully editable). On a fresh
// instance the built-ins are the MAJORITY of rows. The section subtitle read "Your
// PersonalClaw agent definitions — fully editable." — a blanket promise the very rows under
// it contradict with their own Lock + built-in chips, and that `AgentDetail` corrects two
// clicks later ("Its definition is fixed…"). Row-level presentation was already honest;
// only the section promise lied.

const src = readFileSync(join(__dirname, 'AgentsListPage.tsx'), 'utf-8')

describe('the Native section caption is honest about built-ins', () => {
  it('names both populations and their real capabilities', () => {
    expect(src).toContain(
      'subtitle="Built-ins run the platform — definition fixed, model swappable. Agents you create are fully editable."',
    )
  })

  it('no blanket editability claim rides over a group that renders reserved rows', () => {
    // The structural version of the fix: as long as this page renders reserved agents
    // in-group (the Lock/built-in chip path), no caption may claim everything is editable.
    expect(src, 'this page still renders reserved built-ins in the group').toContain('isReservedAgent(agent)')
    const captions = [...src.matchAll(/subtitle="([^"]*)"/g)].map((m) => m[1])
    expect(captions.length, 'the section captions this scan is protecting').toBeGreaterThan(0)
    for (const c of captions) {
      // "…are fully editable" scoped to "Agents you create" is fine; an UNSCOPED blanket
      // form ("definitions — fully editable") is the defect shape this pins out.
      expect(c, `caption over-promises: "${c}"`).not.toMatch(/definitions\s*—\s*fully editable/i)
    }
  })
})
