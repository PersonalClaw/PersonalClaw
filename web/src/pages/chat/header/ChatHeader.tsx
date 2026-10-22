import { Edit3, History, PanelRight, Pencil, Sparkles, Link2, ArrowLeft, ListTree, Send, Share2 } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'
import { TopBar } from '../../../ui/TopBar'
import { HeaderActions, HeaderControl, HeaderMenu } from '../../../ui/HeaderActions'
import { bindingLabel } from '../../../app/keymap'
import { chatContextChips } from '../ChatContextLine'
import { NeedsModelNote } from '../NoModelSetupState'
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
// Share, the openers of the Session Map and the Activity rail (on a new chat, the history panel),
// and the `…` holding Save as starter and New chat. `useChatHeader` keeps what it does.

export function ChatHeader({ header, stream, composer, panes, transcript, started, navigate }: {
  header: ChatHeaderState
  stream: Pick<ChatStream, 'sessionRef' | 'title' | 'titleNeed' | 'branchedFrom' | 'startedBy' | 'investigateOrigin'>
  composer: Pick<ReturnType<typeof useChatComposer>, 'memoryMode' | 'screenShare' | 'projectId'>
  panes: Pick<ChatPanesState, 'activityOpen' | 'setActivityOpen' | 'historyOpen' | 'setHistoryOpen'>
  transcript: Pick<ReturnType<typeof useTranscript>, 'mapOpen' | 'setMapOpen'>
  started: boolean
  navigate: (p: string, opts?: { replace?: boolean }) => void
}) {
  const {
    projectName, renaming, setRenaming, renameVal, setRenameVal, regenningTitle, handoffChannels,
    saveAsTemplate, beginRename, commitRename, regenTitle, copyLink, handOff,
  } = header
  const { sessionRef, title, titleNeed, branchedFrom, startedBy, investigateOrigin } = stream
  const { memoryMode, screenShare, projectId } = composer
  const { activityOpen, setActivityOpen, historyOpen, setHistoryOpen } = panes
  const { mapOpen, setMapOpen } = transcript
  // The header's context line: what this conversation IS, under its title (`ChatContextLine`). What
  // it has cost is not on it: cost waits behind the composer's Context control (#3823).
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
      // The thread's own actions only (`docs/design/information-architecture.md`, "Inside a
      // thread"): Share, the Session map and Activity, and the `…` holding Save as starter and New
      // chat. What decides the next turn sits beside the composer: the task mode and the Execution
      // Boundary control, which holds the permission mode and the working folder. Brief the agent
      // is a row of the composer's `+` menu.
      //
      // Header ordering tenet: the side-panel opener is the RIGHTMOST control. On the new-chat page
      // that's "Chat history"; once started it's "Activity".
      <HeaderActions className="max-w-[70vw]">
        {/* The chat's link and the channels it can continue on, in one menu. A control like its
            neighbours, so it takes the cluster's ladder, and its rows join the `…` when tighter. */}
        {started && sessionRef.current && (
          <HeaderMenu icon={Share2} label="Share" items={[
            { label: 'Copy chat link', icon: Link2, onSelect: () => void copyLink() },
            ...handoffChannels.map((c) => ({
              label: `Continue on ${c.display_name}`, icon: Send,
              hint: c.owner?.id ? undefined : 'No owner paired yet', onSelect: () => void handOff(c),
            })),
          ]} />
        )}
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
        {/* Chat history is the same: it shows and hides the Chat history pane. */}
        {!started && (
          <HeaderControl icon={History} label="Chat history" active={historyOpen} ariaExpanded={historyOpen} onClick={() => setHistoryOpen(!historyOpen)} />
        )}
        {/* The overflow: kept one click away, never a slot on the row. */}
        {started && sessionRef.current && (
          <HeaderControl icon={Sparkles} label="Save as starter" inOverflow onClick={saveAsTemplate} />
        )}
        <HeaderControl icon={Edit3} label="New chat" hint={bindingLabel('new-chat')} inOverflow onClick={() => navigate('chat/new')} />
      </HeaderActions>} />
  )
}
