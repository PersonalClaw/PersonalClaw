import { useMemo } from 'react'
import { fvs } from '../../../design/fontWeight'
import { motion } from 'framer-motion'
import { MessageSquare, ArrowRight } from 'lucide-react'
import { StartedByApp } from '../StartedByApp'
import { LoadError } from '../../../ui/ListScaffold'
import { spring, stagger, listItemEnter, expr } from '../../../design/motion'
import { api, type ChatSessionSummary } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { sessionRecencyMs, sessionActivitySeconds, epochSeconds } from '../../../lib/epoch'
import { sessionTitle } from '../../../lib/sessionTitle'

/** Relative-time label for the history list (compact: "2m", "3h", "5d", "1w").
 *
 *  Parses through `lib/epoch`'s `epochSeconds` rather than its own `Date.parse`, which makes it
 *  the last time formatter in the tree to converge on that parser (`lib/epoch.test.ts` carried
 *  it as a NAMED EXEMPTION until now — see the correction there: the reason recorded for the
 *  exemption did not hold). Two consequences beyond the de-duplication:
 *
 *    · it accepts a NUMBER as well as an ISO string, so a numeric wire field renders instead of
 *      silently blanking — the failure the exemption was worried about, now impossible
 *    · `epochSeconds` guards on READABILITY, so the epoch itself (0) formats instead of being
 *      discarded by the old `if (!t)` truthiness test
 */
function relTimeShort(at?: string | number): string {
  const at_s = epochSeconds(at)
  if (at_s == null) return ''
  const s = Math.max(0, Date.now() / 1000 - at_s)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  if (s < 604800) return `${Math.floor(s / 86400)}d`
  return `${Math.floor(s / 604800)}w`
}

/** Body of the new-chat "Chat history" SidePanel: the most-recent manual sessions
 *  (title + when), each opening its session; a "View all" row deep-links to the
 *  full chat-history page. Reuses the same `chat:sessions` cache the history page
 *  paints from (instant, no extra fetch). */
export function ChatHistorySidePanelBody({ navigate, onOpen }: { navigate: (p: string) => void; onOpen: (key: string) => void }) {
  // No `.catch(() => [])`: swallowing the rejection makes a 500 indistinguishable from
  // "you have no chats", and this panel then says exactly that. The error rides through so
  // the ladder below can tell the two apart.
  const { data, error: sessionsError, refresh: refreshSessions } = useQuery<ChatSessionSummary[]>('chat:sessions', () => api.chatSessions(), { persist: false })
  const recent = useMemo(() => {
    const all = data ?? []
    return all
      .filter((s) => (s.origin ?? 'manual') === 'manual')
      .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
      .slice(0, 20)
  }, [data])
  return (
    <div className="flex flex-col gap-1">
      {data === undefined && sessionsError ? (
        <LoadError what="chats" error={sessionsError} onRetry={refreshSessions} />
      ) : data === undefined ? (
        <div className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">Loading…</div>
      ) : recent.length === 0 ? (
        <div className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No chats yet.</div>
      ) : (
        <motion.div variants={{ animate: { transition: stagger(0.03) } }} initial="initial" animate="animate" className="flex flex-col gap-0.5">
          {recent.map((s) => (
            <motion.button key={s.key} type="button" variants={listItemEnter} onClick={() => onOpen(s.key)}
              whileHover={{ x: expr(3, 0.3) }} transition={spring.spatialFast}
              className="group flex items-center gap-s rounded-md px-2 py-2 text-left transition-colors hover:bg-surface-high">
              <MessageSquare size={14} className="shrink-0 text-on-surface-low group-hover:text-primary transition-colors" />
              <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.8125rem] group-hover:text-on-surface">{sessionTitle(s)}</span>
              <StartedByApp s={s} />
              <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{relTimeShort(sessionActivitySeconds(s))}</span>
            </motion.button>
          ))}
        </motion.div>
      )}
      {/* deep-link to the full, filterable/organizable chat-history page */}
      <button type="button" onClick={() => navigate('chat/history')}
        className="mt-1 flex items-center justify-center gap-1.5 rounded-md border border-outline-variant/40 px-2 py-2 text-on-surface-var text-[0.8125rem] transition-colors hover:bg-surface-high hover:text-on-surface"
        style={fvs(470)}>
        View all chats <ArrowRight size={13} className="shrink-0" />
      </button>
    </div>
  )
}
