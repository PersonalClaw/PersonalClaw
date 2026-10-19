import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, GitBranch, Rewind } from 'lucide-react'
import { spring } from '../../../design/motion'
import { Button } from '../../../ui/Button'
import { QuietButton } from '../../../ui/QuietButton'
import type { ChatTurn } from '../chatTypes'

/** Rewind divider — shown under a user turn that was
 *  edited-and-replayed. States "N messages kept in history" and discloses the
 *  retained tail read-only. Restoring a tail = forking it into a new session
 *  (restore = fork, never an in-place timeline swap). Shows the most recent
 *  snapshot; earlier ones (repeated rewinds) are reachable via the count. */
export function RewindDivider({ snapshots, canFork, onFork }: {
  snapshots: NonNullable<ChatTurn['rewound']>
  canFork: boolean
  onFork: (snapshotIndex?: number) => void
}) {
  const [open, setOpen] = useState(false)
  const latest = snapshots[snapshots.length - 1]
  // The retained tail begins with the edited turn's OLD content; count the
  // messages AFTER it as "kept" (what the user actually rewound past).
  const kept = Math.max(0, (latest?.messages?.length ?? 0) - 1)
  return (
    <div className="mt-2 flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-2 text-on-surface-low text-[0.75rem]">
        <Rewind size={12} className="shrink-0" />
        <span>Rewound from here · {kept} message{kept === 1 ? '' : 's'} kept in history</span>
        <QuietButton onClick={() => setOpen((o) => !o)} ariaExpanded={open} className="h-6">
          {open ? 'Hide' : 'View'} <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        </QuietButton>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast}
            className="w-full max-w-[452px] overflow-hidden rounded-xl border border-outline-variant/50 bg-surface-container/60">
            <div className="flex items-center justify-between border-b border-outline-variant/40 px-3 py-2">
              <span className="text-on-surface-low text-[0.6875rem] uppercase tracking-wide">Retained history (read-only)</span>
              {canFork && (
                <Button size="sm" variant="ghost" onClick={() => onFork()} className="h-6 px-2 text-[0.75rem]">
                  <GitBranch size={12} /> Restore as fork
                </Button>
              )}
            </div>
            <div className="flex flex-col gap-2 px-3 py-2.5">
              {(latest?.messages ?? []).map((m, mi) => (
                <div key={mi} className={`text-[0.8125rem] leading-relaxed ${m.role === 'user' ? 'text-on-surface-var' : 'text-on-surface-low'}`}>
                  <span className="mr-1.5 text-on-surface-low text-[0.6875rem] uppercase tracking-wide">{m.role === 'user' ? 'You' : 'Assistant'}</span>
                  <span className="whitespace-pre-wrap">{m.content.length > 400 ? m.content.slice(0, 400) + '…' : m.content}</span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
