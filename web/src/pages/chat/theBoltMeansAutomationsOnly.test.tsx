import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { ShieldOff, Zap } from 'lucide-react'
import { ToolCard } from './ToolCard'
import { PERMISSION_MODES } from './composer/ExecutionBoundary'
import { RUNG_PRESENTATION } from '../../lib/rungs'
import { AUTOMATIONS_TABS } from '../automations/automationTabs'

// ── The lightning bolt means Automations, and only Automations ──────────────────────────────────
//
// The bolt (`Zap`) marked the Automations page in the sidebar and on its tab, and also YOLO, the
// mode that approves without asking, in a chat's permission menu, a tool step that ran without
// asking, and the autonomy rung of an automation that acts on its own. A page of routines and a
// mode that skips approvals looked alike, and a step approved without asking read as if an
// automation ran it. Approvals switched off now wear `ShieldOff`, beside the menu's other shields
// (Normal's `Shield`, Trust's `ShieldCheck`), and the bolt stays with Automations.

describe('the bolt', () => {
  it('🔴 is not YOLO in the permission menu', () => {
    const yolo = PERMISSION_MODES.find((m) => m.id === 'yolo')
    expect(yolo?.icon).toBe(ShieldOff)
    expect(PERMISSION_MODES.some((m) => m.icon === Zap)).toBe(false)
  })

  it('🔴 is not the mark of a step that ran without asking', () => {
    const { container } = render(<ToolCard seg={{ kind: 'tool', id: 't1', tool: 'shell', done: true, ok: true, auto: true }} />)
    expect(container.querySelector('svg.lucide-shield-off')).not.toBeNull()
    expect(container.querySelector('svg.lucide-zap')).toBeNull()
  })

  it('🔴 is not the rung of an automation that acts on its own', () => {
    expect(RUNG_PRESENTATION.autonomous.icon).toBe(ShieldOff)
    expect(Object.values(RUNG_PRESENTATION).some((r) => r.icon === Zap)).toBe(false)
  })

  it('stays on Automations', () => {
    expect(AUTOMATIONS_TABS.find((t) => t.id === 'automations')?.icon).toBe(Zap)
  })
})
