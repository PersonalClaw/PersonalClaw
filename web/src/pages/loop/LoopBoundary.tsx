import { useId, useRef } from 'react'
import { Eye, Shield } from 'lucide-react'
import { Eyebrow } from '../../ui/Eyebrow'
import { MenuRow, Popover } from '../../ui/Popover'
import { PillButton } from '../../ui/composer/controls'
import { menuCursorKeydown, useMenuCursor } from '../../lib/useMenuCursor'
import { loopModeLabel, loopModeMeaning } from '../../lib/loopMode'
import { Fact, said, useBoundaryReading } from '../chat/composer/ExecutionBoundary'

// ── Where a new loop may act, beside its composer ─────────────────────────────────────────────────
//
// Wherever the agent can act, its permission mode is next to the input (the information
// architecture's rule 2). A loop's is its Mode: Attended, whose planner's and workers' tool calls
// ask as a chat's do, or Unattended, whose run on a standing grant. So the loop composer's footer
// holds the loop's Execution Boundary control, as the chat's does: the Mode, which it changes, and
// where the loop will work, the sandbox its commands run in and the network its work reaches, which
// it shows. The sandbox and the network are the gateway's words for a loop not created yet, on the
// runtime it is set to and for the posture its Mode gives every session it runs (`GET
// /api/chat/boundary?loop=`); they are read again when either changes.
//
// The menu is a `role="menu"` of radio items with the shared cursor (`lib/useMenuCursor`), as the
// chat's: it opens on the checked Mode, the arrows move without choosing, Enter or Space chooses,
// and Escape or Tab closes it with focus back on the pill.

/** The two Modes, Unattended first as the dial had them. */
const MODES = [false, true] as const

/** Where the loop will work, in words: the folder the composer knows, else where the gateway puts
 *  it when it starts. A General loop runs as a workflow run, which works in a scratch folder of
 *  its own (`workflows.provisioning`). Any other, created under a project, takes the project's
 *  folder if it has one (`loop_routes`); with none, a Code loop works in a folder of its own and
 *  any other loop in its project's shared folder, or in the workspace new chats start in
 *  (`loop.effective_dir`). */
export function loopFolderSaid({ folder, kind, projectId }: { folder: string; kind: string; projectId: string }): string {
  if (folder) return folder
  if (kind === 'general') return 'A folder of its own, made when it starts'
  if (kind === 'code') return projectId ? 'Its project’s folder, or a folder of its own if the project has none' : 'A folder of its own, made when it starts'
  return projectId ? 'Its project’s folder' : 'The workspace, where a new chat starts'
}

/** The loop's Execution Boundary control: its Mode, and where it may act once it runs.
 *
 *  `runtime` is the agent CLI the loop runs on (`acp:<cli>`), or `''` for PersonalClaw's own
 *  worker; `folder` the folder the composer knows it will work in (an inherited or typed
 *  codebase), or `''`. */
export function LoopBoundary({ attended, onAttended, kind, runtime, folder, projectId, disabled }: {
  attended: boolean
  onAttended: (attended: boolean) => void
  kind: string
  runtime: string
  folder: string
  projectId: string
  disabled?: boolean
}) {
  const reading = useBoundaryReading(null, true, runtime, attended ? 'attended' : 'unattended', kind)
  const sandbox = said(reading, 'sandbox')
  const reach = said(reading, 'reach')
  const where = loopFolderSaid({ folder, kind, projectId })
  const label = loopModeLabel(attended)
  const summary = [`Mode: ${label}`, `Working folder: ${where}`, `Sandbox: ${sandbox.label}`, `Network: ${reach.label}`].join(' · ')
  return (
    <Popover portal width={360} trigger={(open, toggle) => (
      <PillButton icon={attended ? <Eye size={16} strokeWidth={2} /> : <Shield size={16} strokeWidth={2} />}
        label={label} dimension="Execution boundary" open={open} title={summary}
        disabledReason={disabled ? 'The loop is being set up' : undefined} toggle={toggle} />
    )}>
      {(close) => (
        <LoopBoundaryPanel attended={attended} kind={kind} where={where} sandbox={sandbox.said} reach={reach.said} close={close}
          onPick={(a) => { close(); if (!disabled) onAttended(a) }} />
      )}
    </Popover>
  )
}

function LoopBoundaryPanel({ attended, kind, where, sandbox, reach, close, onPick }: {
  attended: boolean; kind: string; where: string; sandbox: string; reach: string; close: () => void; onPick: (attended: boolean) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const factsId = useId()
  const { move, tabIndexFor } = useMenuCursor({
    containerRef: ref, count: MODES.length, openKey: 'open', initialIndex: MODES.indexOf(attended),
  })
  return (
    <div className="flex max-h-[480px] flex-col overflow-y-auto">
      <div ref={ref} role="menu" aria-label="Execution boundary" aria-describedby={factsId} className="flex flex-col gap-0.5"
        onKeyDown={(e) => { menuCursorKeydown(e.nativeEvent, { move, dismiss: close }) }}>
        <div role="group" aria-label="Mode">
          <div aria-hidden><Eyebrow className="px-m pt-s pb-1">Mode</Eyebrow></div>
          {MODES.map((a, i) => {
            const Icon = a ? Eye : Shield
            const meaning = loopModeMeaning(a, kind)
            return (
              <MenuRow key={String(a)} role="menuitemradio" tabIndex={tabIndexFor(i)} label={loopModeLabel(a)} hint={meaning} title={meaning} nameIsLabel
                selected={a === attended} icon={<Icon size={16} />} onClick={() => onPick(a)} />
            )
          })}
        </div>
      </div>
      <dl id={factsId} data-type="caption" className="mt-xs flex flex-col gap-xs border-t border-outline-variant/30 px-m pt-s pb-xs text-on-surface-var">
        <Fact term="Working folder" said={where} />
        <Fact term="Sandbox" said={sandbox} />
        <Fact term="Network" said={reach} />
      </dl>
    </div>
  )
}
