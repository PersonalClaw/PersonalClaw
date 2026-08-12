import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProjectKnowledgeList, SHARING_POLICY_LABEL } from './ProjectsSection'
import type { ProjectKnowledgeItem, SharingPolicy } from '../../lib/api'

// ── The project Knowledge view's rendering contract (WORK-CONTAINERS §1.6) ──────
// `sharing_policy` is a CLOSED enum, so these assert from rendered DOM that:
//   1. BOTH members render a label — enumerated, not spot-checked, so a member added to
//      the wire type without a label here fails the test instead of rendering blank;
//   2. an item the backend surfaced from ANOTHER project says whose it is — a shared item
//      must never read as something this project produced.

function item(over: Partial<ProjectKnowledgeItem> = {}): ProjectKnowledgeItem {
  return {
    id: 'k1', title: 'Cold start latency', kind: 'fact', summary: '', updated_at: '2026-08-11T00:00:00Z',
    project_id: 'p-alpha', run_id: 'r-1', sharing_policy: 'private', source_project: '', ...over,
  }
}

describe('ProjectKnowledgeList', () => {
  it('labels every sharing policy the wire type allows', () => {
    const policies = Object.keys(SHARING_POLICY_LABEL) as SharingPolicy[]
    expect(policies.sort()).toEqual(['private', 'shared'])
    render(<ProjectKnowledgeList items={policies.map((p, i) => item({ id: `k${i}`, title: `Item ${p}`, sharing_policy: p }))} />)
    for (const p of policies) expect(screen.getByText(SHARING_POLICY_LABEL[p])).toBeTruthy()
  })

  it('names the owning project on a shared cross-container item', () => {
    render(<ProjectKnowledgeList items={[
      item({ id: 'own', title: 'Mine' }),
      item({ id: 'foreign', title: 'Theirs', project_id: 'p-beta', sharing_policy: 'shared', source_project: 'Beta' }),
    ]} />)
    expect(screen.getByText('Theirs')).toBeTruthy()
    expect(screen.getByText('Beta')).toBeTruthy()
    expect(screen.getByTitle('Shared from Beta')).toBeTruthy()
    // The project's OWN item carries no source label — it was not shared in from anywhere.
    expect(screen.queryByTitle('Shared from Mine')).toBeNull()
  })
})
