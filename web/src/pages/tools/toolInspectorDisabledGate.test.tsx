import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { ToolItem } from '../../lib/api'
import { ToolInspector } from './ToolInspector'

// ── Issue 868 (surviving half): a disabled tool's Try-it flow led to a dead end ──
//
// The invoke endpoint refuses a disabled tool with 403 tool_disabled, but the
// inspector rendered the full Try it → Run tool → Confirm & run flow with the
// button enabled — the user only learned after filling arguments and confirming.
// `tool.disabled` was already in the component's props, just never read.
//
// (The issue's headline half — gating LOCKED tools — is moot on current main:
// the invoke endpoint deliberately EXEMPTS core-locked tools from refusal, so
// running them from the inspector is sanctioned; locked only means "can't be
// toggled off". These tests pin the disabled gate and guard the enabled path
// against vacuity.)

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, invokeTool: vi.fn() } }
})

function tool(over: Partial<ToolItem>): ToolItem {
  return {
    name: 'bash', provider: 'core', description: 'Run a command',
    risk_level: 'destructive', requires_approval: true,
    parameters: { type: 'object', properties: { command: { type: 'string' } }, required: ['command'] },
    ...over,
  } as unknown as ToolItem
}

describe('tool inspector disabled gate (issue 868)', () => {
  it('a disabled tool shows why it cannot run instead of an armed Run button', () => {
    render(<ToolInspector tool={tool({ disabled: true })} />)
    fireEvent.click(screen.getByRole('button', { name: /Try it/i }))
    expect(screen.queryByRole('button', { name: /Run tool/i })).toBeNull()
    expect(screen.getByText(/Disabled — turn it on in the tools list/)).toBeTruthy()
    // Glanceable before opening Try it: the header pill row says so too.
    expect(screen.getByText('Disabled')).toBeTruthy()
  })

  it('an enabled tool keeps the full Run flow (vacuity guard)', () => {
    render(<ToolInspector tool={tool({ disabled: false })} />)
    fireEvent.click(screen.getByRole('button', { name: /Try it/i }))
    expect(screen.getByRole('button', { name: /Run tool/i })).toBeTruthy()
    expect(screen.queryByText(/turn it on in the tools list/)).toBeNull()
    expect(screen.queryByText('Disabled')).toBeNull()
  })

  it('a locked-but-enabled tool keeps the Run flow — locked means non-toggleable, not non-runnable', () => {
    render(<ToolInspector tool={tool({ locked: true, disabled: false })} />)
    fireEvent.click(screen.getByRole('button', { name: /Try it/i }))
    expect(screen.getByRole('button', { name: /Run tool/i })).toBeTruthy()
  })
})
