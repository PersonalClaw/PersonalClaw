import { Edit3, History, MessageSquare, PanelRight, Pencil, Sparkles, Link2, ArrowLeft, Bot, ShieldCheck, Shield, Eye, Zap, ClipboardList, Hammer, NotebookPen, FolderCog, ListTree, Send } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'
import { TopBar } from '../../../ui/TopBar'
import { HeaderActions, HeaderControl, HeaderModePill } from '../../../ui/HeaderActions'
import { chatContextChips } from '../ChatContextLine'
import { NeedsModelNote } from '../NoModelSetupState'
import { type ApprovalMode, type TaskMode } from '../../../lib/api'
import { sessionTitle } from '../../../lib/sessionTitle'
import type { ChatStream } from '../useChatStream'
import type { useChatComposer } from '../composer/useChatComposer'
import type { useTranscript } from '../transcript/useTranscript'
import type { ChatPanesState } from '../panes/useChatPanes'
import type { ChatHeaderState } from './useChatHeader'

// ── The chat header ────────────────────────────────────────────────────────────────────────────
//
// The bar above an open chat: the way back, the title and its rename and regenerate, the context
// line under it (what the chat is about and what it is waiting on), and the cluster on the right —
// the task and permission pills, the chat's actions, New chat, and the openers of the Session Map,
// the Activity rail and, on a new chat, the history panel. `useChatHeader` keeps what it does.

// Options for the chat-header segmented controls. Permission mirrors the
// composer's approval modes; memory mirrors MEMORY_MODES — both as the canonical
// Segmented slider rather than a menu.
// `title` carries the same explanatory hint the composer's ApprovalPill shows,
// so hovering a header tab (now the primary approval control) tells the user
// what e.g. "YOLO" or "Plan" actually does rather than just its name.
const APPROVAL_SLIDER = [
  { key: 'normal', label: 'Normal', icon: Shield, title: 'Normal — a tool that only reads runs; the rest ask when they need approval' },
  { key: 'trust_reads', label: 'Trust reads', icon: Eye, title: 'Trust reads — read-only shell commands run without asking too' },
  { key: 'trust', label: 'Trust', icon: ShieldCheck, title: 'Trust — auto-approve every tool in this chat' },
  { key: 'yolo', label: 'YOLO', icon: Zap, title: 'YOLO — auto-approve everywhere; auto-expires, re-enable to extend' },
]

// Task mode — ORTHOGONAL to approval (which gates *whether* a tool auto-approves).
// Task mode gates *which* tools run + how the agent frames the work, layered on the
// active agent. Plan moved here from the approval slider (it was never an approval
// posture — it suppresses execution). Applies live + mid-chat like approval.
const TASK_MODE_SLIDER = [
  { key: 'agent', label: 'Agent', icon: Bot, title: 'Agent — full execution (default)' },
  { key: 'ask', label: 'Ask', icon: MessageSquare, title: 'Ask — read-only Q&A; mutating tools are blocked' },
  { key: 'plan', label: 'Plan', icon: ClipboardList, title: 'Plan — plan the work without executing any tool' },
  { key: 'build', label: 'Build', icon: Hammer, title: 'Build — scoped to producing an artifact / widget / skill' },
]

export function ChatHeader({ header, stream, composer, panes, transcript, started, navigate }: {
  header: ChatHeaderState
  stream: Pick<ChatStream, 'sessionRef' | 'title' | 'titleNeed' | 'sessionCost' | 'branchedFrom' | 'startedBy' | 'investigateOrigin'>
  composer: Pick<ReturnType<typeof useChatComposer>, 'selection' | 'applySelection' | 'memoryMode' | 'screenShare' | 'projectId'>
  panes: Pick<ChatPanesState, 'activityOpen' | 'setActivityOpen' | 'historyOpen' | 'setHistoryOpen'>
  transcript: Pick<ReturnType<typeof useTranscript>, 'mapOpen' | 'setMapOpen'>
  started: boolean
  navigate: (p: string, opts?: { replace?: boolean }) => void
}) {
  const {
    projectName, renaming, setRenaming, renameVal, setRenameVal, regenningTitle, handoffChannels,
    saveAsTemplate, beginRename, commitRename, regenTitle, copyLink, handOff, briefAgent, setWorkspaceDir,
  } = header
  const { sessionRef, title, titleNeed, sessionCost, branchedFrom, startedBy, investigateOrigin } = stream
  const { selection, applySelection, memoryMode, screenShare, projectId } = composer
  const { activityOpen, setActivityOpen, historyOpen, setHistoryOpen } = panes
  const { mapOpen, setMapOpen } = transcript
  // The header's context line: what this conversation IS, under its title (`ChatContextLine`).
  const contextChips = started ? chatContextChips({
    screenShare: screenShare.sharing ? { onStop: screenShare.toggle } : null,
    startedBy: startedBy?.name,
    project: projectName ? { name: projectName, open: () => navigate(`projects/${projectId}`) } : null,
    branchedFrom: branchedFrom
      ? { title: branchedFrom.title, open: () => navigate(`chat/${branchedFrom.key}`) }
      : null,
    investigate: investigateOrigin?.title
      ? {
          title: investigateOrigin.title,
          open: investigateOrigin.back_link
            ? () => navigate((investigateOrigin.back_link as string).replace(/^#\//, ''))
            : undefined,
        }
      : null,
    cost: sessionCost,
  }) : []

  // The title above reads "Untitled chat" for a reason the chat can fix: the line under it says so
  // first, in the words the other surfaces of these chores use, as the way to where it is fixed.
  const headerLine = started && titleNeed
    ? [<NeedsModelNote key="title-need" sentence={titleNeed} />, ...contextChips]
    : contextChips

  // keepCornerPadding: the chat's docked panels (Activity, File peek) are flex
  // siblings BELOW this bar — they never sit over the shell's fixed top-right
  // corner. So the header must always reserve the corner clearance, else its
  // right cluster slides UNDER the shell controls when a panel is open.
  return (
  <TopBar
    keepCornerPadding
    left={!started ? (
      // New-chat page: the title area is the body hero (greeting). The chat-history
      // panel opener lives in the RIGHT cluster (panel-opener = rightmost, per the
      // header ordering tenet), not here.
      undefined
    ) : (
      renaming ? (
        // `min-w-0 w-full`: an input's intrinsic width is ~20ch and a flex item will not shrink
        // below it, so a fixed 200px floor here overran a phone's whole row.
        // Raw: TextInput takes no onBlur, and the rename commits on blur.
        <input autoFocus aria-label="Rename this chat" value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') setRenaming(false) }}
          className="h-8 w-full min-w-0 max-w-[420px] rounded-md bg-surface-high px-2 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
      ) : (
        // The title's row holds only what names the chat: the way back, the title, and its
        // regenerate affordance. Everything ABOUT the chat is on the context line under it
        // (`below`), and "Copy chat link" is a control in the cluster — see `ChatContextLine`
        // for what sharing this row cost the title.
        <div className="flex items-center gap-1.5 min-w-0">
          {/* Back to the chat history list — replaces the separate right-side
              "Chat history" button (it sits left of the title, its natural home). */}
          <IconButton icon={ArrowLeft} label="Back to chat history" size={40} onClick={() => navigate('chat/history')} />
          {/* Raw: the title is its own rename control, and no primitive draws bare title-l text. */}
          <button type="button" onClick={beginRename} title="Rename chat"
            className="group inline-flex items-center gap-1.5 min-w-0 max-w-[420px] text-on-surface hover:text-on-surface-var transition-colors">
            <span data-type="title-l" className="truncate">{sessionTitle({ key: sessionRef.current ?? '', title })}</span>
            <Pencil size={13} className="shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" />
          </button>
          {/* Regenerate title — a small magic-stars affordance hugging the title's
              top-right edge, not a space-hungry header control. A chat that is not
              persistent is given to no model for its title, so it has none to ask for. */}
          {sessionRef.current && memoryMode === 'persistent' && (
            <IconButton icon={Sparkles} label="Regenerate title" onClick={regenTitle}
              loading={regenningTitle} disabled={regenningTitle} size={20} iconSize={12}
              className="shrink-0 -ml-0.5 self-start text-on-surface-low hover:text-primary" />
          )}
        </div>
      )
    )}
    below={headerLine.length > 0 ? headerLine : undefined}
    right={
      // Two live mode selectors (Task, Permission) + New chat / Regen / Activity.
      // Task + Permission are hover-expand mode pills (WidthPill idiom): each shows
      // ONLY the current selection at rest and expands to the full option list on
      // hover — so at rest the header spends width on ONE pill per axis, not all N
      // options. They live in the 4-tier cluster (primary, never-overflow) so they
      // collapse to icon-only when tight and always stay visible.
      //
      // Header ordering tenet: the side-panel opener is the RIGHTMOST control. On
      // the new-chat page that's "Chat history"; once started it's "Activity".
      <HeaderActions className="max-w-[70vw]">
        <HeaderModePill ariaLabel="Task mode" value={selection.taskMode ?? 'agent'}
          options={TASK_MODE_SLIDER} onChange={(v) => applySelection({ taskMode: v as TaskMode })} />
        {/* In an app's conversation the app's grant decides every turn and your choice here
            would be overwritten by it, so the pill shows that grant and is not yours to move
            (the composer says why). */}
        <HeaderModePill ariaLabel="Permission mode" value={selection.approval ?? 'normal'}
          disabled={!!startedBy}
          disabledReason={startedBy ? `${startedBy.name}'s permissions decide this chat, not yours` : undefined}
          options={APPROVAL_SLIDER} onChange={(v) => applySelection({ approval: v as ApprovalMode })} />
        {/* A control like its neighbours, so it takes the cluster's ladder — icon when the row
            is tight, a row of the `…` menu when tighter. Beside the title it was 40px nothing
            else could use, and past the viewport's edge at 1024px and below. */}
        {started && sessionRef.current && (
          <HeaderControl icon={Link2} label="Copy chat link" priority="low" onClick={copyLink} />
        )}
        {started && sessionRef.current && handoffChannels.map((c) => (
          <HeaderControl key={`handoff-${c.name}`} icon={Send} label={`Continue on ${c.display_name}`} priority="low"
            hint={c.owner?.id ? undefined : 'No owner paired yet'} onClick={() => void handOff(c)} />
        ))}
        {started && sessionRef.current && (
          <HeaderControl icon={NotebookPen} label="Brief the agent" priority="low" onClick={briefAgent} />
        )}
        {started && sessionRef.current && (
          <HeaderControl icon={FolderCog} label="Working directory" priority="low" onClick={setWorkspaceDir} />
        )}
        {started && sessionRef.current && (
          <HeaderControl icon={Sparkles} label="Save as starter" priority="low" onClick={saveAsTemplate} />
        )}
        <HeaderControl icon={Edit3} label="New chat" variant="primary" priority="primary" onClick={() => navigate('chat/new')} />
        {/* The Session Map's ONE control, on every viewport (the "one named control"):
            it shows/hides the gutter rail on a pointer device and opens/closes the drawer on
            the mobile form, so there is never a second name for "show me the map".
            `ariaExpanded`, not `active`: this is a disclosure, not an on/off setting.
            `priority="primary"` because on the mobile form this control IS the in-session
            navigation — shedding it into the overflow `…` menu is exactly the "mobile loses
            session nav" outcome this must prevent. */}
        {started && (
          <HeaderControl icon={ListTree} label="Session map" priority="primary"
            ariaExpanded={mapOpen} onClick={() => setMapOpen(!mapOpen)} />
        )}
        {/* Activity is a disclosure too: it shows and hides the Activity pane, so it keeps
            `active` for its look and passes `ariaExpanded`, which makes `HeaderControl` drop
            `aria-pressed`. */}
        {started && (
          <HeaderControl icon={PanelRight} label="Activity" active={activityOpen} ariaExpanded={activityOpen} onClick={() => setActivityOpen(!activityOpen)} />
        )}
        {!started && (
          <HeaderControl icon={History} label="Chat history" active={historyOpen} onClick={() => setHistoryOpen(!historyOpen)} />
        )}
      </HeaderActions>} />
  )
}
