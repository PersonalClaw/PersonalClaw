import type { ReactNode } from 'react'
import { SystemWidget } from '../ui/SystemWidget'
import { WidgetBoundary } from './ErrorBoundary'
import { useIsMobile } from './useIsMobile'

/** The thin line at the bottom of the window, on every page (#3823, `web/REDESIGN.md` §4): ambient
 *  state only. The shell mounts it once, below the sidebar and the work surface.
 *
 *  - **Left: the gateway connection** (`SystemWidget`'s control, read from `gatewayConnection`): a
 *    still dot while connected, with no words and no pulse, and the state in words while connecting
 *    or disconnected, with when the gateway was last reached. It opens the System status card.
 *  - **Right: `right`**, what the owner pinned there. A phone drops it and keeps the connection only.
 *
 *  It stays in focus mode, which hides the sidebar and the side pane and nothing else. A lost
 *  connection that lasts escalates to `DisconnectedBanner`, over the page. */
export function StatusLine({ right }: { right?: ReactNode }) {
  const isMobile = useIsMobile()
  return (
    <footer aria-label="Status" data-type="caption"
      className="flex h-6 shrink-0 items-center gap-m border-t border-outline-variant/40 px-s text-on-surface-low"
      style={{ background: 'var(--color-canvas)' }}>
      {/* In its own boundary, as it was in the corner: a throw leaves a glyph and Retry here, and
          the page above keeps working. */}
      <WidgetBoundary what="the system status" compact><SystemWidget /></WidgetBoundary>
      {!isMobile && right ? <div className="ml-auto flex min-w-0 items-center gap-m">{right}</div> : null}
    </footer>
  )
}
