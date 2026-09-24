/**
 * `AGENT-ROOMS` AR-8 — per-member status.
 *
 * The clause asks for four facts per member: whether it is listening or muted (AR-3's listen
 * policy), its model binding (AR-3), its posture and the axes that actually bind (AR-6), and
 * whether it is currently owed a turn. These tests assert each one is on screen, and — the harder
 * half — that nothing is INVENTED when the backend has not said it.
 *
 * The three axes AR-6 narrowed to are `tool_grants`, `tool_allowlist` and `budget`. The other three
 * (`egress_tier`, `denylist_extra`, `path_allowlist`) are refused BY NAME because their enforcement
 * points re-resolve the profile and would discard a member's narrowing, so a control for one would
 * be a false ceiling. The last test here is that rail: the add form offers none of them.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { RoomMembersPanel } from './RoomMembersPanel'
import type { RoomDetail, RoomMemberRecord, RoomRecord, SavedAgent } from '../../lib/api'

function member(name: string, extra: Partial<RoomMemberRecord> = {}): RoomMemberRecord {
  return { name, role_blurb: '', listen_policy: 'all', profile_narrowing: {}, ...extra }
}

function room(extra: Partial<RoomRecord> = {}): RoomRecord {
  return {
    id: 'pricing-debate',
    title: 'Should we raise prices?',
    created_at: '2026-09-23T00:00:00',
    archived: false,
    paused: false,
    rounds_used: 0,
    round_budget: 0,
    pending_queue: [],
    members: [],
    effective_round_budget: 6,
    max_round_budget: 100,
    transcript_path: '/rooms/pricing-debate/transcript.jsonl',
    ...extra,
  }
}

const AGENTS: SavedAgent[] = [
  { name: 'analyst', provider: 'native', model: 'gemma4:12b' },
  { name: 'skeptic', provider: 'acp:claude-code', model: '' },
  { name: 'writer', provider: 'native', model: 'gpt-5' },
]

function panel(detail: RoomDetail, owed: string[] = [], onAdd = vi.fn(), onRemove = vi.fn()) {
  render(
    <RoomMembersPanel detail={detail} owed={owed} agents={AGENTS} busy={false} removing=""
      onAdd={onAdd} onRemove={onRemove} />,
  )
}

describe('a member row reports what that member IS', () => {
  it('names the member, its role, its listen policy, its model, its runtime and its reach', () => {
    panel({
      room: room({ members: [member('analyst', { role_blurb: 'argues from the numbers' })] }),
      member_posture: [{
        name: 'analyst', declared: { tool_grants: 'read' }, approval: 'ask',
        tool_grants: 'read', tool_allowlist: [], budget: { max_tokens: 50000, max_dollars: 0 },
      }],
      member_bindings: [{ name: 'analyst', configured: true, model: 'gemma4:12b', provider: 'native' }],
      messages: [],
    })
    expect(screen.getByText('analyst')).toBeTruthy()
    expect(screen.getByText('argues from the numbers')).toBeTruthy()
    expect(screen.getByText('Everything')).toBeTruthy()
    expect(screen.getByText('gemma4:12b')).toBeTruthy()
    expect(screen.getByText('Native')).toBeTruthy()
    expect(screen.getByText('Read-only tools')).toBeTruthy()
    expect(screen.getByText('Up to 50,000 tokens')).toBeTruthy()
  })

  it('distinguishes a read-only critic from a writing executor IN THE SAME ROOM', () => {
    // The whole reason per-member posture exists. If the row could not say which is which, the
    // feature would be unobservable from the UI.
    panel({
      room: room({ members: [member('critic'), member('executor')] }),
      member_posture: [
        { name: 'critic', declared: { tool_grants: 'read' }, tool_grants: 'read', tool_allowlist: [] },
        { name: 'executor', declared: {}, tool_grants: 'read_write', tool_allowlist: [] },
      ],
      member_bindings: [
        { name: 'critic', configured: true, model: 'a', provider: 'native' },
        { name: 'executor', configured: true, model: 'b', provider: 'native' },
      ],
      messages: [],
    })
    expect(screen.getByText('Read-only tools')).toBeTruthy()
    expect(screen.getByText('Read and write tools')).toBeTruthy()
  })

  it('reads UNKNOWN, not healthy, for a member the backend said nothing about', () => {
    panel({ room: room({ members: [member('ghost')] }), member_posture: [], member_bindings: [], messages: [] })
    expect(screen.getByText('Status unknown')).toBeTruthy()
  })

  it("reports a DELETED binding instead of the default agent's model", () => {
    // `resolve_agent_bindings` falls back to `default_agent` for an unknown name, so asking it
    // would report another agent's model as this member's. The backend deliberately does not.
    panel({
      room: room({ members: [member('writer')] }),
      member_posture: [{ name: 'writer', declared: {}, tool_grants: 'read', tool_allowlist: [] }],
      member_bindings: [{ name: 'writer', configured: false, model: '', provider: '' }],
      messages: [],
    })
    expect(screen.getByText('Binding missing')).toBeTruthy()
    expect(screen.getByText('No agent by this name is configured')).toBeTruthy()
  })

  it("shows a refused posture with the backend's own reason", () => {
    panel({
      room: room({ members: [member('critic')] }),
      member_posture: [{
        name: 'critic', declared: { tool_grants: 'read_write' },
        refused: 'room_member_posture_widens',
        detail: "'critic' declares a posture wider than the room's on tool_grants.",
      }],
      member_bindings: [{ name: 'critic', configured: true, model: 'a', provider: 'native' }],
      messages: [],
    })
    expect(screen.getByText('Cannot speak')).toBeTruthy()
    expect(screen.getByText("'critic' declares a posture wider than the room's on tool_grants.")).toBeTruthy()
  })

  it('says which member is owed a turn, and where in the queue', () => {
    panel({
      room: room({ members: [member('analyst'), member('skeptic')] }),
      member_posture: [
        { name: 'analyst', declared: {}, tool_grants: 'read', tool_allowlist: [] },
        { name: 'skeptic', declared: {}, tool_grants: 'read', tool_allowlist: [] },
      ],
      member_bindings: [
        { name: 'analyst', configured: true, model: 'a', provider: 'native' },
        { name: 'skeptic', configured: true, model: 'b', provider: 'native' },
      ],
      messages: [],
    }, ['skeptic', 'analyst'])
    expect(screen.getAllByText('Owed a turn')).toHaveLength(2)
    expect(screen.getByText('#1 in the queue')).toBeTruthy()
    expect(screen.getByText('#2 in the queue')).toBeTruthy()
  })

  it('never claims a member is SPEAKING — the backend publishes no such field', () => {
    panel({
      room: room({ members: [member('analyst')] }),
      member_posture: [{ name: 'analyst', declared: {}, tool_grants: 'read', tool_allowlist: [] }],
      member_bindings: [{ name: 'analyst', configured: true, model: 'a', provider: 'native' }],
      messages: [],
    }, ['analyst'])
    // The head of the queue is only PROBABLY the member whose turn is running: a failed turn
    // advances the drain with nothing saying so. A badge that is usually right about who is
    // talking is a fabricated value with a friendly face.
    expect(screen.queryByText(/speaking/i)).toBeNull()
  })
})

describe('the roster controls', () => {
  it('offers an on-ramp when the room has no members, naming what a member is', () => {
    const onAdd = vi.fn()
    panel({ room: room(), member_posture: [], member_bindings: [], messages: [] }, [], onAdd)
    expect(screen.getByText('No members yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Add a member/ })).toBeTruthy()
  })

  it('offers NO add control on an archived room, where the backend refuses it', () => {
    panel({ room: room({ archived: true }), member_posture: [], member_bindings: [], messages: [] })
    expect(screen.queryByRole('button', { name: /Add/ })).toBeNull()
  })

  it('picks a member from the configured agents, and marks the ones already in the room', async () => {
    // A free-text field would be offering a refusal: the backend fails closed on an unknown
    // binding name (`room_member_unknown_agent`).
    const onAdd = vi.fn()
    panel({
      room: room({ members: [member('analyst')] }),
      member_posture: [{ name: 'analyst', declared: {}, tool_grants: 'read', tool_allowlist: [] }],
      member_bindings: [{ name: 'analyst', configured: true, model: 'a', provider: 'native' }],
      messages: [],
    }, [], onAdd)
    await userEvent.click(screen.getByRole('button', { name: /Add/ }))
    const select = screen.getByRole('combobox', { name: 'Agent' })
    // Already-added agents are present but disabled, which answers "why is my analyst not in the
    // list" instead of silently hiding it.
    const taken = screen.getByRole('option', { name: 'analyst' }) as HTMLOptionElement
    expect(taken.disabled).toBe(true)
    await userEvent.selectOptions(select, 'skeptic')
    await userEvent.click(screen.getByRole('button', { name: /Add to the room/ }))
    expect(onAdd).toHaveBeenCalledWith({ name: 'skeptic', role_blurb: '', listen_policy: 'all' })
  })

  it('carries all three listen policies, with what each one means', async () => {
    panel({ room: room(), member_posture: [], member_bindings: [], messages: [] })
    await userEvent.click(screen.getByRole('button', { name: /Add a member/ }))
    const select = screen.getByRole('combobox', { name: 'Listen policy' })
    await userEvent.selectOptions(select, 'mention')
    expect(screen.getByText(/another member writes @its-name/)).toBeTruthy()
    await userEvent.selectOptions(select, 'silent')
    expect(screen.getByText(/Peers cannot summon it/)).toBeTruthy()
  })

  it('says so when every configured agent is already a member', async () => {
    const members = AGENTS.map((a) => member(a.name))
    panel({
      room: room({ members }),
      member_posture: members.map((m) => ({ name: m.name, declared: {}, tool_grants: 'read', tool_allowlist: [] })),
      member_bindings: members.map((m) => ({ name: m.name, configured: true, model: 'x', provider: 'native' })),
      messages: [],
    })
    await userEvent.click(screen.getByRole('button', { name: /Add/ }))
    expect(screen.getByText(/Every agent you have configured is already in this room/)).toBeTruthy()
    expect(screen.queryByRole('combobox', { name: 'Agent' })).toBeNull()
  })

  it('removes a member by name', async () => {
    const onRemove = vi.fn()
    panel({
      room: room({ members: [member('analyst')] }),
      member_posture: [{ name: 'analyst', declared: {}, tool_grants: 'read', tool_allowlist: [] }],
      member_bindings: [{ name: 'analyst', configured: true, model: 'a', provider: 'native' }],
      messages: [],
    }, [], vi.fn(), onRemove)
    await userEvent.click(screen.getByRole('button', { name: 'Remove analyst from this room' }))
    expect(onRemove).toHaveBeenCalledWith('analyst')
  })
})

describe('🔴 the three REFUSED safety axes are offered nowhere', () => {
  it('no control names an axis whose narrowing the enforcement point would discard', () => {
    // AR-6 measured it: `web/fetch.py` and `guardrails/denylist.py` RE-RESOLVE the profile from the
    // session key at enforcement time, so a member narrowing `egress_tier`, `denylist_extra` or
    // `path_allowlist` is handed the ROOM's base at the moment the rule is applied. The
    // declaration reads as binding and binds nothing — a false ceiling, which is worse than an
    // absent control because it is a safety claim nobody enforces. Derived over the source rather
    // than asserted in prose, so a later "let's expose the other axes" edit reds here.
    const src = readFileSync(join(process.cwd(), 'src', 'pages', 'chat', 'RoomMembersPanel.tsx'), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
    for (const axis of ['egress_tier', 'denylist_extra', 'path_allowlist']) {
      expect(code, `${axis} must not reach a control`).not.toContain(axis)
    }
    // `approval` likewise: it is the ROOM's and always the ask posture, so a member cannot restate
    // it — and `posture.member_posture` refuses a base that resolved to anything else.
    expect(code, 'approval is not a member axis').not.toMatch(/approval['"\s]*[:=]/)
    // The control that IS offered, so this is not vacuously green.
    expect(code).toContain('listen_policy')
  })
})
