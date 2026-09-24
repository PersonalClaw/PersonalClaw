import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProposalCard, UpdatePreview } from './PacksPanel'
import type { PackProposalRec, PackUpdateRec } from '../../lib/api'

vi.mock('../../app/appSdk', () => ({ notify: vi.fn() }))

// ── The propose-only fingerprint card (AGENT-PACKS §7, AP-7) ──────────────────
//
// Three properties of this card are the atom, and each one is only observable by rendering it:
//
//   1. PROPOSE-ONLY. The card offers "Install" and "Not for this project". It must not carry
//      any affordance that installs without being asked, and the decline must be present —
//      a card you can only accept is not a proposal.
//   2. CONFIDENCE MEANS SOMETHING. The score renders WITH its derivation (how many of the
//      declared globs/signals matched, against what ceiling). A bare percentage is a number
//      the user has to trust; the atom's brief calls an unexplained score worse than none.
//   3. THE §3.1 INSPECT REPORT IS ON THE CARD. "Here's what it would install" is the whole
//      difference between a proposal and an ad, and it must survive being absent (project-
//      create omits it to keep creation latency independent of pack count).

const proposal: PackProposalRec = {
  project_id: 'proj-tf',
  pack: 'infra-ops',
  displayName: 'Infra Ops',
  description: 'Review infrastructure-as-code changes honestly.',
  version: '1.0.0',
  confidence: 0.68,
  matches: [
    {
      label: 'Terraform project',
      confidence: 0.68,
      declared_confidence: 0.9,
      matched_globs: ['*.tf'],
      matched_signals: ['terraform {', 'provider "'],
      declared_globs: ['*.tf', '*.tfvars'],
      declared_signals: ['terraform {', 'provider "'],
      evidence: ['main.tf', 'modules/vpc/main.tf'],
    },
  ],
  files_scanned: 42,
  inspect: {
    name: 'infra-ops',
    version: '1.0.0',
    blocked: false,
    needs_consent: false,
    components: [
      { kind: 'skill', orig_id: 'infra-plan-review', target_id: 'infra-plan-review', verdict: 'clean' },
      { kind: 'template', orig_id: 'infra-change-review', target_id: 'infra-change-review', verdict: 'clean' },
    ],
    requirements: [],
    staged_triggers: [],
  },
  inspect_error: '',
}

const renderCard = (p: PackProposalRec = proposal) => {
  const onInstall = vi.fn()
  const onReject = vi.fn()
  const r = render(<ProposalCard proposal={p} busy={false} onInstall={onInstall} onReject={onReject} />)
  return { ...r, onInstall, onReject }
}

describe('propose-only', () => {
  it('offers both an accept and a decline', () => {
    renderCard()
    expect(screen.getByRole('button', { name: 'Install' })).toBeTruthy()
    // The decline is the never-re-nag entry point. A card without it can only be accepted.
    expect(screen.getByRole('button', { name: 'Not for this project' })).toBeTruthy()
  })

  it('installs only when the install button is pressed', () => {
    const { onInstall, onReject } = renderCard()
    expect(onInstall).not.toHaveBeenCalled()
    screen.getByRole('button', { name: 'Install' }).click()
    expect(onInstall).toHaveBeenCalledWith(proposal)
    expect(onReject).not.toHaveBeenCalled()
  })

  it('reports the decline with the project AND the pack, so the memory is scoped', () => {
    const { onReject } = renderCard()
    screen.getByRole('button', { name: 'Not for this project' }).click()
    expect(onReject).toHaveBeenCalledWith(expect.objectContaining({ project_id: 'proj-tf', pack: 'infra-ops' }))
  })
})

describe('confidence carries its derivation', () => {
  it('shows the score as a percentage', () => {
    expect(renderCard().container.textContent).toContain('68% match')
  })

  it('shows how much of the rule matched, and the declared ceiling', () => {
    // Without this line the 68% is unexplained — the exact failure the atom's brief calls out.
    const t = renderCard().container.textContent ?? ''
    expect(t).toContain('1 of 2 file patterns')
    expect(t).toContain('2 of 2 content signals')
    expect(t).toContain('declared ceiling of 90%')
  })

  it('names the shape it thinks it found', () => {
    expect(renderCard().container.textContent).toContain('Looks like a terraform project')
  })

  it('shows example matched paths against the number of files scanned', () => {
    // A score with no example path is unreviewable: this is how a user confirms the scanner
    // read their project rather than a vendored copy.
    const t = renderCard().container.textContent ?? ''
    expect(t).toContain('main.tf')
    expect(t).toContain('modules/vpc/main.tf')
    expect(t).toContain('of 42 files scanned')
  })

  it('omits the content-signal clause for a rule that declares none', () => {
    // Rendering "0 of 0 content signals" would invent a weakness the rule never claimed.
    const noSignals = {
      ...proposal,
      matches: [{ ...proposal.matches[0], declared_signals: [], matched_signals: [] }],
    }
    expect(renderCard(noSignals).container.textContent).not.toContain('content signals')
  })
})

describe('the §3.1 inspect report', () => {
  it('lists what installing would put on this machine', () => {
    const t = renderCard().container.textContent ?? ''
    expect(t).toContain('Would install')
    expect(t).toContain('skill:infra-plan-review')
    expect(t).toContain('template:infra-change-review')
  })

  it('renders without a report at all (project-create omits it)', () => {
    const t = renderCard({ ...proposal, inspect: null }).container.textContent ?? ''
    expect(t).toContain('Infra Ops')
    expect(t).not.toContain('Would install')
  })

  it('says why the preview is missing rather than staying silent', () => {
    const t = renderCard({ ...proposal, inspect: null, inspect_error: 'BundledPackError: bad tree' })
      .container.textContent ?? ''
    expect(t).toContain("Couldn't preview what this would install")
    expect(t).toContain('BundledPackError: bad tree')
  })

  it('survives a match list that is empty', () => {
    // A proposal with no match rows should not crash the panel; the pack identity still renders.
    const t = renderCard({ ...proposal, matches: [] }).container.textContent ?? ''
    expect(t).toContain('Infra Ops')
  })
})

// ── The §1 update preview ─────────────────────────────────────────────────────
//
// The load-bearing assertion is the DRIFT NOTE. An update that skipped a user-edited component
// silently is indistinguishable, to anyone reading the result, from one that clobbered it — so
// the note is part of the contract. The second assertion is ordering: the note renders before
// the apply button exists, so the decision to apply is an informed one.

const update: PackUpdateRec = {
  pack: 'infra-ops',
  from_version: '1.0.0',
  to_version: '1.1.0',
  applied: false,
  components: [
    { ref: 'skill:infra-drift-audit', action: 'overwrite', reason: 'pack-owned and unmodified since install', pack_path: 'skills/infra-drift-audit/SKILL.md', home_path: 'skills/infra-drift-audit' },
    { ref: 'skill:infra-plan-review', action: 'skip_drift', reason: 'edited since install (content hash differs from the install lock) — your version was kept, the pack\'s update was not applied', pack_path: 'skills/infra-plan-review/SKILL.md', home_path: 'skills/infra-plan-review' },
    { ref: 'template:infra-change-review', action: 'skip_not_pack_owned', reason: "'templates/infra-change-review.json' matches no pack_owned pattern", pack_path: 'templates/infra-change-review.json', home_path: '' },
  ],
  drift_notes: ['skill:infra-plan-review: edited since install'],
  overwritten: ['skill:infra-drift-audit'],
  skipped: ['skill:infra-plan-review', 'template:infra-change-review'],
}

describe('the update preview makes the skip visible', () => {
  it('names each kept component with the reason it was kept', () => {
    const t = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />).container.textContent ?? ''
    expect(t).toContain('skill:infra-plan-review')
    expect(t).toContain('edited since install')
    expect(t).toContain('your version was kept')
  })

  it('tones the kept component as a warning so it is not read as a success', () => {
    const { container } = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />)
    const warned = [...container.querySelectorAll('.text-warn')].map((e) => e.textContent)
    expect(warned).toContain('skill:infra-plan-review')
    expect(warned).not.toContain('skill:infra-drift-audit')
  })

  it('separates "you edited it" from "the pack does not own it"', () => {
    // Two different facts with two different remedies; collapsing them would tell a user their
    // edit was respected when in truth the pack never claimed the file.
    const t = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />).container.textContent ?? ''
    expect(t).toContain('Not owned by this pack, so untouched: template:infra-change-review')
  })

  it('shows the version transition and the counts', () => {
    const t = render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />).container.textContent ?? ''
    expect(t).toContain('1.0.0 → 1.1.0')
    expect(t).toContain('1 to replace, 2 to keep')
  })

  it('offers Apply only while the update has NOT been applied', () => {
    render(<UpdatePreview update={update} busy={false} onApply={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Apply update' })).toBeTruthy()
  })

  it('drops the Apply button once applied, and says Updated', () => {
    const { container } = render(<UpdatePreview update={{ ...update, applied: true }} busy={false} onApply={vi.fn()} />)
    expect(container.querySelector('button')).toBeNull()
    expect(container.textContent).toContain('Updated')
    expect(container.textContent).toContain('Replaced')
  })

  it('offers no Apply button when there is nothing to replace', () => {
    // Every component drifted or is unowned: an "Apply update" that would write nothing is a
    // button that lies about having an effect.
    const nothing: PackUpdateRec = { ...update, overwritten: [], components: update.components.filter((c) => c.action !== 'overwrite') }
    const { container } = render(<UpdatePreview update={nothing} busy={false} onApply={vi.fn()} />)
    expect(container.querySelector('button')).toBeNull()
  })
})
