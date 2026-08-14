import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ApprovalCard } from './ApprovalCard'
import { approvalOutcome, type ApprovalResolution } from './approvalOutcome'
import { hydrateTurns, type ApprovalSegment, type HistMsg } from './chatTypes'

// #541 (rendering half). A tool approved via "Allow for this chat" rendered as
// DENIED with a Ban icon: the card tested `resolved === 'approved'` and took the
// else-branch for everything else, while the backend persists five values
// (approved / rejected / trust / trust_reads / yolo). The transcript is the
// permanent record of a security decision, so this asserts the ICON and the
// wording per outcome, not merely that something rendered.

// Lucide renders a stable per-icon class (`lucide-check` / `lucide-ban` /
// `lucide-circle-question-mark`), which is what makes the ICON assertable rather
// than just the text. Probed against the installed lucide-react, not assumed:
// lucide-react 1.x renders the `HelpCircle` glyph (unchanged) under the renamed
// class `lucide-circle-question-mark` (was `lucide-circle-help` in 0.x).
const ICON_CLASS = { check: 'lucide-check', ban: 'lucide-ban', unknown: 'lucide-circle-question-mark' } as const

/** The outcome line a settled card actually paints: its icon and its text. */
function paintSettled(resolved: string): { icon: string | null; text: string } {
  const seg: ApprovalSegment = { kind: 'approval', id: 'a1', tool: 'Terminal', resolved }
  const { container } = render(<ApprovalCard seg={seg} onAct={() => {}} />)
  const svg = container.querySelector('svg')
  return {
    icon: svg?.getAttribute('class')?.split(/\s+/).find((c) => c.startsWith('lucide-')) ?? null,
    text: container.textContent ?? '',
  }
}

// Every value the backend's four writers can persist, and the treatment it must get.
// `approved: false` here would mean the transcript claims the tool was blocked.
const CASES: { resolved: ApprovalResolution; approved: boolean; says: string }[] = [
  { resolved: 'approved', approved: true, says: 'approved' },
  { resolved: 'trust', approved: true, says: 'this chat' },
  { resolved: 'trust_reads', approved: true, says: 'reads' },
  { resolved: 'yolo', approved: true, says: 'YOLO' },
  { resolved: 'rejected', approved: false, says: 'denied' },
]

describe('approvalOutcome mapping', () => {
  it('renders every trust/YOLO grant as an APPROVAL, never as denied (#541)', () => {
    for (const c of CASES.filter((x) => x.approved)) {
      const o = approvalOutcome(c.resolved)
      expect(o.icon.name ?? o.icon.displayName, c.resolved).not.toBe('Ban')
      expect(o.tone, c.resolved).toBe('var(--color-ok)')
      expect(o.label, c.resolved).not.toContain('denied')
    }
  })

  it('names the SCOPE so an auditor can tell a standing grant from a confirmation', () => {
    // The whole reason these are not all just "approved": a transcript must show that a
    // call was auto-approved by a standing grant rather than individually confirmed.
    expect(approvalOutcome('approved').label).toBe('approved')
    for (const r of ['trust', 'trust_reads', 'yolo'] as const) {
      expect(approvalOutcome(r).label, r).toContain('auto-approved')
    }
    // ...and each scope is distinguishable from the others, not one shared blurb.
    const labels = (['approved', 'trust', 'trust_reads', 'yolo'] as const).map((r) => approvalOutcome(r).label)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('maps a denial to denied', () => {
    expect(approvalOutcome('rejected').label).toBe('denied')
  })

  it('does not read an UNKNOWN outcome as denied or as approved', () => {
    // A session written by another build can carry a value this one doesn't know.
    // Claiming "denied" is the bug being fixed; claiming "approved" is worse.
    const o = approvalOutcome('trust_project')
    expect(o.label).not.toContain('denied')
    expect(o.label).not.toContain('approved')
    expect(o.label).toContain('trust_project')  // shows the literal value, asserts nothing
    expect(o.tone).not.toBe('var(--color-ok)')
  })

  it('caps a pathological persisted value so it cannot stretch the chat column', () => {
    expect(approvalOutcome('x'.repeat(500)).label.length).toBeLessThan(60)
  })
})

describe('ApprovalCard settled line', () => {
  it('shows a check for every approval and a ban only for a denial (#541)', () => {
    for (const c of CASES) {
      const { icon, text } = paintSettled(c.resolved)
      expect(icon, c.resolved).toBe(c.approved ? ICON_CLASS.check : ICON_CLASS.ban)
      expect(text, c.resolved).toContain('Terminal')
      expect(text, c.resolved).toContain(c.says)
      // The regression, stated directly: an approved call never says "denied".
      if (c.approved) expect(text, c.resolved).not.toContain('denied')
    }
  })

  it('shows neither a check nor a ban for an unknown outcome', () => {
    const { icon, text } = paintSettled('trust_project')
    expect(icon).toBe(ICON_CLASS.unknown)
    expect(text).not.toContain('denied')
  })

  it('still renders the actionable picker while PENDING', () => {
    // The `if (seg.resolved)` guard was never the bug — an unresolved card must keep
    // offering a working decision. Since OU-8 that decision is Allow/Deny plus a
    // remember-scope strip (the scope moved off the verb row and into its own zone, so the
    // breadth of a grant is stated rather than encoded in which button you press).
    const { container } = render(<ApprovalCard seg={{ kind: 'approval', id: 'a1', tool: 'Terminal' }} onAct={() => {}} />)
    const labels = [...container.querySelectorAll('button')].map((b) => b.textContent?.trim())
    expect(labels).toEqual(['Just this once', 'This chat', 'This agent', 'Allow', 'Deny'])
  })
})

describe('history hydration parity', () => {
  const permRow = (resolved?: string): HistMsg => ({
    role: 'permission', content: 'Terminal',
    meta: { approval_id: 'a1', tool: 'Terminal', ...(resolved ? { resolved } : {}) },
  })
  const segOf = (m: HistMsg) => hydrateTurns([m], false)[0].segments[0] as ApprovalSegment

  it('carries every outcome through reload unchanged, so a reloaded transcript matches the live one', () => {
    // hydrateTurns used to allowlist approved/rejected, so trust/trust_reads/yolo fell to
    // undefined — a settled call came back from history as PENDING, re-arming live
    // Allow/Deny buttons for a tool that had already run.
    for (const c of CASES) {
      expect(segOf(permRow(c.resolved)).resolved, c.resolved).toBe(c.resolved)
      expect(paintSettled(segOf(permRow(c.resolved)).resolved!).icon, c.resolved)
        .toBe(c.approved ? ICON_CLASS.check : ICON_CLASS.ban)
    }
  })

  it('preserves an unknown outcome instead of downgrading it to pending', () => {
    expect(segOf(permRow('trust_project')).resolved).toBe('trust_project')
  })

  it('leaves a genuinely pending row unresolved', () => {
    // Only ABSENCE means pending — that distinction is what keeps a persisted-but-
    // unanswered request actionable after a reload.
    expect(segOf(permRow(undefined)).resolved).toBeUndefined()
    expect(segOf({ role: 'permission', content: 'Terminal', meta: { approval_id: 'a1', resolved: '' } }).resolved).toBeUndefined()
  })
})
