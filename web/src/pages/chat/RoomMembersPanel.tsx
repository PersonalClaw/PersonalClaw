import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, Plus, Trash2, UserPlus } from 'lucide-react'
import { Button } from '../../ui/Button'
import { IconButton } from '../../ui/IconButton'
import { Eyebrow } from '../../ui/Eyebrow'
import { StatusPill } from '../../ui/StatusPill'
import { EmptyState } from '../../ui/ListScaffold'
import { TextLink } from '../../ui/TextLink'
import { Field, FieldError, Select, TextInput } from '../../ui/forms'
import { fvs } from '../../design/fontWeight'
import {
  LISTEN_POLICIES,
  listenPolicyMeta,
  memberBudgetLabel,
  memberModelLabel,
  memberReachLabel,
  memberRuntimeLabel,
  memberStateMeta,
  roomMemberViews,
  type RoomMemberView,
} from './roomMeta'
import type { RoomDetail, RoomListenPolicy, SavedAgent } from '../../lib/api'

/** Per-member status, and the roster controls (`AGENT-ROOMS` C9 / `AR-8`).
 *
 *  Follows the activity panel's Subagents-tab pattern — a list of rows, one per participant,
 *  each reporting what that participant is and what it is doing — rather than minting a second
 *  panel kind. What a room adds over a subagent list is that a member's REACH differs from its
 *  peers': a read-only critic and a tool-bearing executor sharing one room is the feature, so
 *  the row has to say which is which.
 *
 *  ── WHAT EACH ROW REPORTS, AND WHERE IT COMES FROM ──
 *
 *  · **name + role blurb** — the room record's own member list.
 *  · **state badge** — derived in `roomMeta.roomMemberViews`; status beats configuration, and
 *    absent beats both (a member nothing is known about reads "Status unknown", never a policy
 *    label).
 *  · **listen policy** — `listen_policy`, with its meaning spelled out: "mention" alone does
 *    not tell a reader that peers can summon this member and that nobody else can.
 *  · **model + runtime** — `member_bindings`. A binding DELETED from `config.json` after the
 *    member was added reports "No agent by this name is configured" rather than the default
 *    agent's model, which is what the ordinary resolution path would have answered.
 *  · **tool reach + spend ceiling** — `member_posture`, the RESOLVED posture. A declaration
 *    that reaches past the room's is reported as refused, with the backend's reason, because
 *    that member will not take a turn at all.
 *  · **turn** — "Answering" for the member whose turn is open while a round runs, else its place
 *    in the queue the room owes, in FIFO order.
 *
 *  "Answering" is a READING, not an inference: the backend publishes the open turn
 *  (`room.speaking`) and whether a round is running it (`room.round_running`, off the live task).
 *  It used to publish neither, and the head of a queue this tab remembered was only PROBABLY the
 *  member talking — a failed turn advanced the drain without anything saying so — so the row said
 *  only "owed a turn". An open turn with no round running is one a stopped gateway cut off: that
 *  member reads as owed, first, never as answering. */
export function RoomMembersPanel({ detail, agents, agentsError, onRetryAgents, busy, removing, onAdd, onRemove }: {
  detail: RoomDetail
  /** Every configured agent binding, for the picker. `undefined` while it is still loading. */
  agents: SavedAgent[] | undefined
  /** The `agents:list` rejection, when that read FAILED.
   *
   *  Required to tell "loading" from "could not load": `agents` is `undefined` in both cases, so
   *  without this the picker says `Loading…` forever on a failed read. */
  agentsError?: unknown
  /** Re-runs the agent read, for the retry the failed picker offers. */
  onRetryAgents?: () => void
  /** A roster write is in flight. Drives the ADD button's spinner only. */
  busy: boolean
  /** The member whose removal is in flight, or `''`.
   *
   *  A NAME rather than the panel-wide `busy`, because a remove control must say "working" about
   *  ITSELF: `disabled={busy}` on every row would dim five controls to 40% with
   *  `cursor: not-allowed` and announce `aria-disabled` — claiming four of them cannot be used
   *  when nothing is wrong with them — and `loading={busy}` would spin all five. Both are the same
   *  defect `IconButton.loading` exists to end, in opposite directions. */
  removing: string
  onAdd: (body: { name: string; role_blurb: string; listen_policy: RoomListenPolicy; profile_narrowing?: Record<string, unknown> }) => void
  onRemove: (name: string) => void
}) {
  const views = useMemo(() => roomMemberViews(detail), [detail])
  const [adding, setAdding] = useState(false)
  const full = detail.room.members.length > 0 && !detail.room.archived
  // The ceiling is the number the ADD ROUTE enforces (`rooms.max_members`, published on the wire),
  // not a restated default. At it, adding is refused here with the reason, rather than offered and
  // answered `room_member_limit` — the refusal every Retry would earn again.
  const ceiling = detail.room.max_members
  const atCeiling = !detail.room.archived && detail.room.members.length >= ceiling
  const ceilingReason = `This room holds its maximum of ${ceiling} member${ceiling === 1 ? '' : 's'}`
  // A room that fills while the form is open (another tab, a lowered ceiling) closes the form: the
  // offer it makes can no longer be kept. `formOpen` covers the render before the effect lands.
  useEffect(() => { if (atCeiling) setAdding(false) }, [atCeiling])
  const formOpen = adding && !atCeiling
  return (
    <div className="flex flex-col gap-l">
      <div className="flex flex-col gap-xs">
        <div className="flex items-center justify-between gap-s">
          <Eyebrow as="h3" id="room-members-heading">
            Members {views.length > 0 ? `(${views.length})` : ''}
          </Eyebrow>
          {!detail.room.archived && !formOpen && (
            <Button size="xs" variant="secondary" disabled={atCeiling} disabledReason={ceilingReason}
              onClick={() => setAdding(true)}>
              <UserPlus size={13} aria-hidden /> Add
            </Button>
          )}
        </div>
        {atCeiling && (
          <p data-type="body-s" className="text-on-surface-var" style={fvs(400)}>
            {ceilingReason}. Remove one to add another, or raise Members per room in{' '}
            <TextLink href="#/settings/chat" ink="emphasis">Settings › Chat</TextLink>.
          </p>
        )}
      </div>

      {views.length === 0 && !formOpen ? (
        <EmptyState
          icon={UserPlus}
          title="No members yet"
          hint="A member is one of your configured agents, with a role and a listen policy. Add two and they can argue in front of you."
          action={detail.room.archived ? undefined : { label: 'Add a member', onClick: () => setAdding(true), icon: UserPlus }}
        />
      ) : (
        <ul aria-labelledby="room-members-heading" className="flex flex-col gap-s">
          {views.map((view) => (
            <MemberRow
              key={view.member.name}
              view={view}
              removable={full}
              removing={removing === view.member.name}
              onRemove={() => onRemove(view.member.name)} />
          ))}
        </ul>
      )}

      {formOpen && (
        <AddMemberForm
          agents={agents}
          agentsError={agentsError}
          onRetryAgents={onRetryAgents}
          taken={detail.room.members.map((m) => m.name)}
          busy={busy}
          onCancel={() => setAdding(false)}
          onAdd={(body) => { onAdd(body); setAdding(false) }} />
      )}
    </div>
  )
}

function MemberRow({ view, removable, removing, onRemove }: {
  view: RoomMemberView
  removable: boolean
  removing: boolean
  onRemove: () => void
}) {
  const state = memberStateMeta(view.state)
  const policy = listenPolicyMeta(view.member.listen_policy)
  const model = memberModelLabel(view.binding)
  const runtime = memberRuntimeLabel(view.binding)
  const reach = memberReachLabel(view.posture)
  const budget = memberBudgetLabel(view.posture)
  const PolicyIcon = policy.icon
  return (
    <li className="rounded-lg bg-surface-container px-m py-m">
      <div className="flex items-start gap-m">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-s">
            <span data-type="title-m" className="truncate text-on-surface">{view.member.name}</span>
            {/* A badge only when there is something to report. The label is in WORDS as well as a
                tone, because a tone alone is not a reading — and a healthy member carries none at
                all, so the three that mean something are not competing with "nothing is wrong".
                The listen policy is a labelled FACT below, not a badge: it is configuration. */}
            {state && (
              <StatusPill tone={state.tone} className="gap-xs">
                <state.icon size={11} aria-hidden /> {state.label}
              </StatusPill>
            )}
            {/* Not for the member that is answering: it is #1 by definition, and "Answering · #1 in
                the queue" says one fact twice. */}
            {view.queuePosition > 0 && view.state !== 'answering' && (
              <span data-type="caption" className="text-on-surface-low">
                #{view.queuePosition} in the queue
              </span>
            )}
          </div>
          {view.member.role_blurb && (
            <p data-type="body-s" className="mt-0.5 text-on-surface-var" style={fvs(400)}>
              {view.member.role_blurb}
            </p>
          )}
          {/* The refusal's own sentence, from the backend. Shown in full rather than summarised:
              it names the axis that reached too far, which is the only actionable part. */}
          {view.posture?.refused && view.posture.detail && (
            <p data-type="body-s" className="mt-xs text-danger" style={fvs(400)}>
              {view.posture.detail}
            </p>
          )}
          <dl className="mt-s flex flex-wrap items-center gap-x-l gap-y-xs">
            <MemberFact label="Listens" value={policy.label} title={policy.hint} icon={<PolicyIcon size={11} aria-hidden />} />
            {model && <MemberFact label="Model" value={model} />}
            {runtime && <MemberFact label="Runtime" value={runtime} />}
            {reach && <MemberFact label="Tools" value={reach} />}
            {budget && <MemberFact label="Budget" value={budget} />}
          </dl>
        </div>
        {removable && (
          <IconButton
            icon={Trash2}
            label={`Remove ${view.member.name} from this room`}
            title="Remove from this room"
            size={30}
            tone="danger"
            loading={removing}
            onClick={onRemove} />
        )}
      </div>
    </li>
  )
}

/** One labelled fact on a member row. A real `<dt>`/`<dd>` pair, so "Model" is bound to its
 *  value for a screen reader rather than being two adjacent strings a sighted reader groups by
 *  proximity. */
function MemberFact({ label, value, title, icon }: {
  label: string
  value: string
  title?: string
  icon?: React.ReactNode
}) {
  return (
    <div className="inline-flex items-baseline gap-xs" title={title}>
      <dt data-type="caption" className="text-on-surface-low">{label}</dt>
      <dd data-type="body-s" className="inline-flex items-center gap-xs text-on-surface-var">
        {icon}{value}
      </dd>
    </div>
  )
}

/** What the agent picker says when it has no options to offer — and the whole point is that
 *  there are THREE reasons, not two. `agents` is `undefined` both while the read is in flight and
 *  after it rejected, so keying the placeholder off `agents` alone reports a permanent `Loading…`
 *  for a failure. The rejection is the only thing that separates them. */
function pickerPlaceholder(agents: SavedAgent[] | undefined, agentsError: unknown): string {
  if (agentsError) return "Couldn't load your agents"
  if (agents === undefined) return 'Loading…'
  return 'No agents configured'
}

/** Add a member: which binding, what role, and how it listens.
 *
 *  The binding is a PICKER over the configured agents rather than a free-text field, because
 *  the backend fails closed on an unknown name (`room_member_unknown_agent`) — offering a text
 *  box would be offering a refusal. Already-added agents are present but disabled with the
 *  reason, which is more honest than hiding them: "why is my writer not in this list" has an
 *  answer, and it is "because it is already in the room".
 *
 *  `profile_narrowing` is deliberately NOT offered here. The three axes a member may narrow
 *  (`tool_grants`, `tool_allowlist`, `budget`) are a safety declaration whose refusals are
 *  argued per-axis by `rooms.posture`, and a room's DEFAULT is already the narrowest tier — a
 *  member added with no declaration is the read-only one. So the add form cannot produce an
 *  over-reaching member, and the axes belong to a posture editor rather than to a two-field
 *  add. What the surface must not do is offer the three axes the backend REFUSES by name
 *  (`egress_tier`, `denylist_extra`, `path_allowlist`): their narrowing is discarded at the
 *  enforcement point, so a control for one would be a false ceiling — the inert-surface defect
 *  wearing a safety label. */
function AddMemberForm({ agents, agentsError, onRetryAgents, taken, busy, onAdd, onCancel }: {
  agents: SavedAgent[] | undefined
  /** The `agents:list` rejection. See `RoomMembersPanel`'s copy of this prop: `agents` alone
   *  cannot distinguish a read still in flight from one that failed, and the picker's placeholder
   *  has to say which. */
  agentsError?: unknown
  onRetryAgents?: () => void
  taken: string[]
  busy: boolean
  onAdd: (body: { name: string; role_blurb: string; listen_policy: RoomListenPolicy }) => void
  onCancel: () => void
}) {
  const options = useMemo(() => {
    const rows = (agents ?? []).map((a) => ({
      value: a.name,
      label: a.name,
      disabled: taken.includes(a.name),
      title: taken.includes(a.name) ? 'Already a member of this room' : a.description || undefined,
    }))
    return rows
  }, [agents, taken])
  const firstFree = options.find((o) => !o.disabled)?.value ?? ''
  const [name, setName] = useState(firstFree)
  const [blurb, setBlurb] = useState('')
  const [policy, setPolicy] = useState<RoomListenPolicy>('all')
  const chosen = name || firstFree
  // 🪤 `options.length > 0` is load-bearing: `[].every(…)` is VACUOUSLY TRUE, so a user with no
  // agents at all was told "Every agent you have configured is already in this room" — a sentence
  // about a set that is empty — and the `No agents configured` placeholder below was unreachable.
  // "All of them are taken" and "there are none" are different facts and need different sentences.
  const noneFree = agents !== undefined && options.length > 0 && options.every((o) => o.disabled)
  return (
    <div className="rounded-lg bg-surface-container px-m py-m">
      <Eyebrow as="h3">Add a member</Eyebrow>
      {/* Agents are created on the Agents page (`#/agents/new`), NOT in Settings — Settings ›
          Agent holds defaults, runners and subagents and has no create control, so the old
          "Create another agent in Settings" sent the user somewhere that cannot do it. */}
      {noneFree ? (
        <p data-type="body-s" className="mt-xs text-on-surface-var" style={fvs(400)}>
          Every agent you have configured is already in this room.{' '}
          <TextLink href="#/agents/new" ink="emphasis" icon={ArrowRight} iconPosition="trailing">
            Create another agent
          </TextLink>
        </p>
      ) : (
        <div className="mt-s flex flex-col gap-m">
          <Field
            label="Agent"
            hint="One of your configured agents. It keeps its own provider session."
            right={agentsError && onRetryAgents ? (
              <Button size="xs" variant="ghost" onClick={onRetryAgents}>Retry</Button>
            ) : undefined}>
            <Select
              value={chosen}
              onChange={setName}
              options={options.length ? options : [{ value: '', label: pickerPlaceholder(agents, agentsError), disabled: true }]}
              size="sm"
              surface="high" />
            {agentsError ? (
              <FieldError className="mt-xs">
                {agentsError instanceof Error ? agentsError.message : 'The agent list could not be read.'}
              </FieldError>
            ) : null}
          </Field>
          <Field label="Role" hint="One line. It rides along with every line this member writes, so the others can read its position next to the role it argues from.">
            <TextInput
              value={blurb}
              onChange={setBlurb}
              placeholder="argues from the numbers"
              maxLength={500}
              size="sm"
              surface="high"
              ariaLabel="Role" />
          </Field>
          <Field label="Listen policy" hint={listenPolicyMeta(policy).hint}>
            <Select
              value={policy}
              onChange={(v) => setPolicy(v as RoomListenPolicy)}
              options={LISTEN_POLICIES.map((p) => ({ value: p.key, label: p.label }))}
              size="sm"
              surface="high" />
          </Field>
          <div className="flex items-center gap-s">
            <Button
              size="sm"
              loading={busy}
              loadingLabel="Adding the member"
              disabled={!chosen}
              disabledReason="Pick which agent joins the room"
              onClick={() => onAdd({ name: chosen, role_blurb: blurb.trim(), listen_policy: policy })}>
              <Plus size={14} aria-hidden /> Add to the room
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
          </div>
        </div>
      )}
      {noneFree && (
        <div className="mt-s">
          <Button size="sm" variant="ghost" onClick={onCancel}>Close</Button>
        </div>
      )}
    </div>
  )
}
