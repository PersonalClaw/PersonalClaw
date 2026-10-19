import { Loader2, TerminalSquare, X, RotateCcw } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { Centered } from '../../../ui/Centered'
import { TerminalView } from '../../terminal/TerminalView'
import { useBottomTerminal } from './useBottomTerminal'

// ── The cockpit terminal ───────────────────────────────────────────────────────────────────────
//
// The drawer under the editor: a resize handle on its top edge, the workspace it runs in, and the
// shell itself, or why it could not start with a way to try again. Hidden rather than unmounted, so
// the shell and its scrollback survive a toggle. `useBottomTerminal` keeps the session.

export function BottomTerminal({ ws, hidden, onClose, runCmd }: { ws: string; hidden?: boolean; onClose: () => void; runCmd?: { cmd: string; n: number } | null }) {
  const { tab, err, setAttempt, height, onHandleDown, onHandleKey, min, max, liveSessionRef } = useBottomTerminal({ ws, runCmd })
  return (
    <div className="relative flex shrink-0 flex-col border-t border-outline-variant/40"
      // Hide via CSS (not unmount) so the PTY + running process + scrollback survive a
      // header Hide/Show toggle. display:none collapses it; TerminalView's ResizeObserver
      // re-fits xterm when it's shown again (0-size → sized).
      style={{ height, display: hidden ? 'none' : undefined }} aria-hidden={hidden}>
      {/* top-edge resize handle (grows the terminal upward) */}
      <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="horizontal"
        tabIndex={0} aria-label="Resize terminal — arrow keys to resize"
        aria-valuenow={Math.round(height)} aria-valuemin={min} aria-valuemax={max}
        className="group/th absolute inset-x-0 top-0 z-20 h-2 cursor-row-resize outline-none focus-visible:bg-primary/30">
        <div className="mx-auto h-0.5 w-full bg-transparent transition-colors group-hover/th:bg-primary/60 group-focus-visible/th:bg-primary" />
      </div>
      <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-3 py-1">
        <span data-type="caption" className="inline-flex items-center gap-1.5 text-on-surface-low"><TerminalSquare size={12} /> Terminal · {ws.split('/').slice(-1)[0]}</span>
        <IconButton icon={X} label="Hide terminal" onClick={onClose} size={24} iconSize={13} />
      </div>
      <div className="min-h-0 flex-1">
        {err ? (
          <div data-type="body-s" className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
            <span style={{ color: 'var(--color-danger)' }}>{err}</span>
            <div className="flex items-center gap-2">
              <Button variant="ghost-accent" size="xs" onClick={() => setAttempt((n) => n + 1)}><RotateCcw size={13} /> Try again</Button>
              <Button variant="ghost" size="xs" onClick={onClose}>Close</Button>
            </div>
          </div>
        ) : tab ? <TerminalView tab={tab} onExited={() => {}} onClose={onClose}
            onSession={(sid) => { liveSessionRef.current = sid }} /> : <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>}
      </div>
    </div>
  )
}
