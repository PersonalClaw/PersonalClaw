import { useCallback, useState } from 'react'
import { MessagesSquare, Plus, Users } from 'lucide-react'
import { api, hasApiCode, type RoomRecord } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { Button } from '../../ui/Button'
import { Eyebrow } from '../../ui/Eyebrow'
import { EmptyState, ListSkeleton, LoadError } from '../../ui/ListScaffold'
import { RowHitTarget } from '../../ui/RowHitTarget'
import { StatusPill } from '../../ui/StatusPill'
import { Field, TextInput } from '../../ui/forms'
import { fvs } from '../../design/fontWeight'
import {
  ROOM_ICON,
  parkedQueue,
  roomState,
  roomStateMeta,
  roundBudgetLabel,
} from './roomMeta'

/** The Rooms tab's body — the list of rooms, and the way to make the first one (`AR-8`).
 *
 *  It sits inside `ChatHistoryPage` under the fifth `origin` scope, which is AGENT-ROOMS C9's
 *  answer to "sidebar peer or a mode of the chat page": there is no sidebar (measured — the
 *  chat page's own comment says so), the session list is this page, and its origin Segmented is
 *  the navigation that does exist. So a room is a scope of it, reached at
 *  `#/chat/history?origin=room`, and one room opens at `#/chat/room/<id>`.
 *
 *  🔑 IT LISTS ROOMS AND NOT SESSIONS, and that is deliberate rather than a shortcut. Each
 *  member of a room holds a real provider session keyed `room:<room>:<member>`, and those are
 *  filtered out of `/api/chat/sessions` by the backend — a room is ONE room, not N chats, and
 *  surfacing the members as rows would answer "what rooms do I have" with the wrong noun. So
 *  this scope reads `/api/rooms`, and the scope's count comes from that read.
 */
export function RoomsScope({ rooms, error, loading, onRefresh, navigate }: {
  rooms: RoomRecord[] | undefined
  error: unknown
  loading: boolean
  onRefresh: () => void
  navigate: (path: string) => void
}) {
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)

  const create = useCallback(async () => {
    const clean = title.trim()
    if (!clean || busy) return
    setBusy(true)
    try {
      const res = await api.createRoom(clean)
      setTitle('')
      setCreating(false)
      onRefresh()
      navigate(`chat/room/${encodeURIComponent(res.room.id)}`)
    } catch (e) {
      notify(`Couldn't create the room: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      setBusy(false)
    }
  }, [title, busy, onRefresh, navigate])

  // `rooms_disabled` is the feature being OFF, not a read that failed — and the backend refuses
  // the reads too, deliberately, so a 403 here is the only signal there is. Rendering it as a
  // LoadError would tell the user their rooms are broken when they are switched off.
  if (rooms === undefined && error && hasApiCode(error, 'rooms_disabled')) {
    return (
      <EmptyState
        icon={ROOM_ICON}
        title="Agent Rooms is switched off"
        hint="A room is a standing conversation where several of your agents deliberate with you refereeing — each with its own role, its own provider session and its own tool reach."
        action={{ label: 'Turn it on in chat settings', onClick: () => navigate('settings/chat') }} />
    )
  }
  if (rooms === undefined && error) return <LoadError what="rooms" error={error} onRetry={onRefresh} />
  if (rooms === undefined || loading) return <ListSkeleton rows={3} what="rooms" />

  return (
    <div className="flex flex-col gap-l">
      {rooms.length === 0 && !creating ? (
        <EmptyState
          icon={ROOM_ICON}
          title="No rooms yet"
          hint="Put two of your agents in a room, give each a role, and referee. They answer in a deterministic order, and the room pauses to ask you once they have talked among themselves for a while."
          action={{ label: 'New room', onClick: () => setCreating(true), icon: Plus }} />
      ) : (
        <>
          <div className="flex items-center justify-between gap-s">
            <Eyebrow as="h2" id="rooms-heading">
              {rooms.length} room{rooms.length === 1 ? '' : 's'}
            </Eyebrow>
            {!creating && (
              <Button size="sm" variant="secondary" onClick={() => setCreating(true)}>
                <Plus size={14} aria-hidden /> New room
              </Button>
            )}
          </div>
          {rooms.length > 0 && (
            <ul aria-labelledby="rooms-heading" className="flex flex-col gap-s">
              {rooms.map((room) => (
                <RoomRow key={room.id} room={room} onOpen={() => navigate(`chat/room/${encodeURIComponent(room.id)}`)} />
              ))}
            </ul>
          )}
        </>
      )}

      {creating && (
        <div className="rounded-lg bg-surface-container px-l py-l">
          <Eyebrow as="h3">New room</Eyebrow>
          <div className="mt-s flex flex-col gap-m">
            <Field label="What is this room for?" hint="The title becomes the room's id, and the members see it in their prompt — so name the question, not the participants.">
              <TextInput
                value={title}
                onChange={setTitle}
                placeholder="Should we raise prices?"
                autoFocus
                maxLength={200}
                size="sm"
                surface="high"
                ariaLabel="Room title"
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void create() } }} />
            </Field>
            <div className="flex items-center gap-s">
              <Button
                size="sm"
                loading={busy}
                loadingLabel="Creating the room"
                disabled={!title.trim()}
                disabledReason="Give the room a title first"
                onClick={() => void create()}>
                <Plus size={14} aria-hidden /> Create the room
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setCreating(false); setTitle('') }}>Cancel</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function RoomRow({ room, onOpen }: { room: RoomRecord; onOpen: () => void }) {
  const state = roomState(room)
  const meta = roomStateMeta(state)
  const parked = parkedQueue(room)
  const Icon = ROOM_ICON
  return (
    <li>
      {/* `RowHitTarget`'s pattern: a stretched, named button as a SIBLING of the content, with
          the ring drawn on the row. A `role="button"` wrapper around this content would be
          axe's `nested-interactive`, which is the defect that pattern exists to fix. */}
      <div
        tabIndex={-1}
        onClick={onOpen}
        className="group relative flex items-center gap-m rounded-lg bg-surface-container px-m py-2.5 cursor-pointer hover:bg-surface-high transition-colors has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary">
        <RowHitTarget label={`Open the room ${room.title}`} />
        <span className="shrink-0 inline-flex size-10 items-center justify-center rounded-lg bg-surface-high">
          <Icon size={19} className="text-on-surface-var" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-s">
            <span data-type="title-m" className="truncate text-on-surface">{room.title}</span>
            {state !== 'active' && <StatusPill tone={meta.tone}>{meta.label}</StatusPill>}
          </div>
          <p data-type="body-s" className="mt-0.5 flex flex-wrap items-center gap-x-m gap-y-0.5 text-on-surface-low" style={fvs(400)}>
            <span className="inline-flex items-center gap-xs">
              <Users size={11} aria-hidden />
              {room.members.length} member{room.members.length === 1 ? '' : 's'}
            </span>
            <span className="inline-flex items-center gap-xs">
              <MessagesSquare size={11} aria-hidden />
              {roundBudgetLabel(room)}
            </span>
            {/* The parked queue on the ROW, because it is the one thing a paused room owes that a
                user cannot guess: "paused" alone does not say whether anyone is still waiting to
                speak, and that is the difference between replying and archiving. */}
            {parked.length > 0 && (
              <span>{parked.length} still owed a turn</span>
            )}
          </p>
        </div>
      </div>
    </li>
  )
}
