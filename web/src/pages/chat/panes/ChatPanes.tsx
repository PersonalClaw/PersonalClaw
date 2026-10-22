import { AnimatePresence } from 'framer-motion'
import { History, Activity, Loader2, ListTree, FolderTree } from 'lucide-react'
import { SidePanel } from '../../../ui/SidePanel'
import { withoutFence } from '../../../lib/untrustedFence'
import { ChatFilePanel } from '../ChatFilePanel'
import { sameSessionTarget, type CommentTarget } from '../../../ui/content/commentTarget'
import { ChatActivityPanel } from '../ChatActivityPanel'
import { detailOf } from '../beside/detailBeside'
import { WorkingFolderTree } from './WorkingFolderTree'
import { Modal } from '../../../ui/Modal'
import type { useTranscript } from '../transcript/useTranscript'
import { SessionMapDrawer } from '../SessionMapDrawer'
import { ChatHistorySidePanelBody } from '../history/ChatHistorySidePanel'
import { type RouteProps } from '../../../app/useQueryState'
import type { ChatStream } from '../useChatStream'
import type { ChatPanesState } from './useChatPanes'

// ── The chat's docked panes ────────────────────────────────────────────────────────────────────
//
// The panels docked beside the chat column, each a flex sibling that pushes the chat narrower:
// the open file, the Session Map's drawer on the mobile form, the Activity rail, the chat's working
// folder and the new chat's history. And a tool's full result, in a modal over the page.
// `useChatPanes` keeps their state.

/** File panel wrapper (kept out of the big return for clarity). No `key={path}`:
 *  switching from one file to another while the panel is open swaps content IN
 *  PLACE (FileViewer re-fetches on path change) rather than running a jarring
 *  collapse-and-reexpand. AnimatePresence still animates the true open/close. */
function AnimatePresenceFilePanel({ path, onClose, commentTarget }: { path: string | null; onClose: () => void; commentTarget?: CommentTarget }) {
  return (
    <AnimatePresence>
      {path && <ChatFilePanel path={path} onClose={onClose} commentTarget={commentTarget} />}
    </AnimatePresence>
  )
}

export function ChatPanes({ panes, stream, transcript, started, setQuery, navigate, send }: {
  panes: ChatPanesState
  stream: Pick<ChatStream, 'subagents' | 'sideMsgs' | 'sideBusy' | 'workingDir' | 'streaming' | 'sessionRef'>
  transcript: Pick<ReturnType<typeof useTranscript>, 'mapOpen' | 'isMobile' | 'setMapOpen' | 'sessionEntries' | 'pendingMapJump' | 'runPendingMapJump'>
  started: boolean
  setQuery: RouteProps['setQuery']
  navigate: (p: string, opts?: { replace?: boolean }) => void
  send: (text: string) => unknown
}) {
  const { openFile, setOpenFile, activityOpen: activityAsked, setActivityOpen, treeOpen, setTreeOpen, historyOpen, setHistoryOpen, activity, outputs, beside, setBeside, killFanout, openSide, askSide } = panes
  const { subagents, sideMsgs, sideBusy, workingDir, streaming, sessionRef } = stream
  const { mapOpen, isMobile, setMapOpen, sessionEntries, pendingMapJump, runPendingMapJump } = transcript
  // On a phone a detail open beside the reading takes the inspector's place; the address keeps both,
  // and the detail's Back to Activity brings the inspector back.
  const activityOpen = activityAsked && !(isMobile && detailOf(beside))
  return (
    <>
      {/* A file opened INSIDE this chat: comments route to THIS session (not a
          new one) so the agent the user is already talking to picks up the
          feedback in context. */}
      <AnimatePresenceFilePanel path={openFile} onClose={() => setOpenFile(null)}
        commentTarget={sameSessionTarget((msg) => { send(msg) })} />

      {/* ── SESSION MAP, COARSE-POINTER FORM ────────────────────────────────────────────────
          On the mobile form the gutter rail is replaced by this drawer: the SHARED `SidePanel`,
          so the map's WIDTH persists through the primitive's own `storeKey` mechanism
          and the panel matches every other dock in the app. Same `?map` flag and same header
          control as the rail — one open/closed state, two forms.

          CLOSED AT REST on this form — see the per-form default where `mapFlag` is read. This
          drawer is a docked column whose fit-width fills a phone viewport, so an open resting
          state puts it over the composer before the user has typed anything.

          🪤 NO `urlKey`, DELIBERATELY, and the reason survives the per-form default. `SidePanel`'s
          `urlKey` close path writes `{key: null}`, i.e. "back to the default" — which is the
          right value here but the wrong MECHANISM: the flag is shared with the rail, whose
          default is the opposite, so a close routed through `urlKey` would depend on which form
          happened to be mounted. `onClose` writes the closed value explicitly, so close ⇒ URL
          updated holds identically in both forms.

          A tap jumps AND closes: the drawer covers the transcript at this width, so a jump the
          user cannot see is not a jump. It closes FIRST and scrolls after — see
          `pendingMapJump`, where the order is the difference between a tap that navigates and
          one that resolves an offset in a 32px-wide transcript and lands nowhere. */}
      <AnimatePresence onExitComplete={runPendingMapJump}>
        {mapOpen && isMobile && started && (
          <SidePanel title="Session map" icon={<ListTree size={18} className="text-primary" />} storeKey="session-map-w"
            fillHeight onClose={() => setMapOpen(false)}>
            <SessionMapDrawer entries={sessionEntries}
              onJumpTo={(coord) => { pendingMapJump.current = coord; setMapOpen(false) }} />
          </SidePanel>
        )}
      </AnimatePresence>
      {/* Activity rail — the chat's session inspector (`ChatActivityPanel`), a standard right-docked
          SidePanel, a flex sibling that pushes the chat narrower (not a floating overlay).
          URL-bound: ?activity=1 (Back closes; refresh restores). Its rows open their detail in the
          tile beside the thread (?beside). On a phone the tile takes the inspector's place while it
          is open, and its Back to Activity brings the inspector back: the address keeps both. */}
      <AnimatePresence>
        {activityOpen && started && (
          <SidePanel title="Activity" icon={<Activity size={18} className="text-primary" />} storeKey="chat-activity-w"
            fillHeight urlKey={{ key: 'activity', setQuery }} onClose={() => setActivityOpen(false)}>
            <ChatActivityPanel activity={activity} onOpenFile={setOpenFile} subagents={subagents}
              onKillFanout={killFanout}
              side={{ msgs: sideMsgs, busy: sideBusy, onAsk: askSide, onOpen: openSide }}
              inspector={{
                session: sessionRef.current ?? undefined, outputs, openDetail: setBeside, detail: beside,
                compact: streaming ? undefined : () => { void send('/compact') }, navigate, canMove: !isMobile,
              }} />
          </SidePanel>
        )}
      </AnimatePresence>
      {/* The chat's working folder (⌘⇧E): its files beside the conversation, a click opening one in
          the file peek. Docked like the Activity rail, and URL-bound the same way (?tree=1). */}
      <AnimatePresence>
        {treeOpen && started && (
          <SidePanel title="Working folder" icon={<FolderTree size={18} className="text-primary" />} storeKey="chat-tree-w"
            fillHeight urlKey={{ key: 'tree', setQuery }} onClose={() => setTreeOpen(false)}>
            <WorkingFolderTree dir={workingDir} openFile={openFile} onOpenFile={setOpenFile} streaming={streaming} />
          </SidePanel>
        )}
      </AnimatePresence>
      {/* New-chat page: a right-docked chat-history panel via the SHARED SidePanel
          primitive (matches Activity/File panels app-wide). Lists recent chats to
          resume + a "View all" link to the full history page. */}
      <AnimatePresence>
        {historyOpen && !started && (
          <SidePanel title="Chat history" icon={<History size={18} className="text-primary" />} storeKey="chat-history-w"
            fillHeight urlKey={{ key: 'history', setQuery }} onClose={() => setHistoryOpen(false)}>
            <ChatHistorySidePanelBody navigate={navigate} onOpen={(key) => navigate(`chat/${key}`)} />
          </SidePanel>
        )}
      </AnimatePresence>
    </>
  )
}

/** A tool's full result, over the page while `?result` names one. */
export function ToolResultModal({ panes }: { panes: ChatPanesState }) {
  const { resultRef, setResultRef, resultToolRef, resultBody } = panes
  return (
    <>
      {resultRef && (
        <Modal title={`${resultToolRef.current || 'Tool'} — full result`} onClose={() => setResultRef('')}>
          <div className="flex flex-col gap-2 p-l" style={{ minWidth: 520, maxWidth: 900 }}>
            {resultBody === null ? (
              <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={13} className="animate-spin" /> Loading…</div>
            ) : (
              <>
                {resultBody.length > 0 && (
                  <div className="text-on-surface-low text-[0.75rem]">{resultBody.length.toLocaleString()} chars</div>
                )}
                <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-3 py-2 font-mono text-on-surface-var text-[0.75rem] leading-relaxed">{withoutFence(resultBody.content)}</pre>
              </>
            )}
          </div>
        </Modal>
      )}
    </>
  )
}
