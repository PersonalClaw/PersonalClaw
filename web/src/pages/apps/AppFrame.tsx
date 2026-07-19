import { useEffect, useRef, useState } from 'react'
import { TopBar } from '../../ui/TopBar'
import { WorkbenchLayout } from '../../ui/WorkbenchLayout'
import { SidePanel } from '../../ui/SidePanel'
import { AppIcon } from './appIcon'
import { ContributedPage } from './ContributedPage'
import type { AppContext } from '../../app/appSdk'

/** A header action an app contributes (rendered right-aligned in the standard
 *  header by the HOST — the app never draws header chrome itself). */
export interface AppHeaderAction {
  id: string
  label: string
  icon?: string        // lucide icon name (no emoji, per the tenet)
  variant?: 'primary' | 'secondary' | 'ghost'
  onClick: () => void
}

/** What an app shows in the shared right-docked detail panel. The app renders
 *  into `el` (imperative, like its page mount) — the HOST owns the panel chrome
 *  (title bar, expand, close, resize), so every app's detail panel looks + behaves
 *  exactly like the rest of PersonalClaw. */
export interface AppPanelSpec {
  title: string
  icon?: string
  render: (el: HTMLElement) => void | (() => void)
}

/** The host bridge handed to an app's mount(el, ctx). Apps DECLARE chrome through
 *  it; the host renders it with host components so apps can't break the shell:
 *   - setHeaderActions: right-aligned buttons in the standard (shell-clearing) header
 *   - openPanel/closePanel: the standard reused SidePanel for detail / overflow /
 *     entity selection
 *  `el` (the first mount arg) is ONLY the content region BELOW the header, so app
 *  content can never hide under the floating shell corners. */
export interface AppHost {
  setHeaderActions: (actions: AppHeaderAction[]) => void
  openPanel: (spec: AppPanelSpec) => void
  closePanel: () => void
}

/** Host-owned frame for every contributed app page (A7 + the layout standard).
 *  The app gets the content slot below a standard TopBar; it contributes header
 *  actions + a standard detail panel via ctx.host. This GUARANTEES the PClaw page
 *  layout (header clears the shell, title + right-aligned controls, content below,
 *  shared SidePanel for detail) regardless of what the app renders. */
export function AppFrame({ app, title, icon, src, mountFunction }: {
  app: AppContext
  title: string
  icon?: string
  src: string
  mountFunction?: string
}) {
  const [actions, setActions] = useState<AppHeaderAction[]>([])
  const [panel, setPanel] = useState<AppPanelSpec | null>(null)
  // Stable host bridge (identity preserved across renders so the app's mount,
  // which captures it once, keeps working).
  const hostRef = useRef<AppHost>({
    setHeaderActions: (a) => setActions(a ?? []),
    openPanel: (spec) => setPanel(spec),
    closePanel: () => setPanel(null),
  })

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={
            <span className="flex items-center gap-s text-on-surface" data-type="title-l">
              <AppIcon name={icon} size={18} /> <span className="truncate">{title}</span>
            </span>
          }
          right={actions.length > 0 ? <AppHeaderActions actions={actions} /> : undefined}
        />
      }
      panel={panel && (
        <SidePanel key={panel.title} fillHeight storeKey="app-detail-w"
          title={panel.title} icon={<AppIcon name={panel.icon} size={18} />}
          onClose={() => setPanel(null)}>
          <PanelMount spec={panel} />
        </SidePanel>
      )}
    >
      {/* The app's content region. Centered to the global content width like every
          other page; the app mounts BELOW the header so it never hides behind the
          floating shell corners. */}
      <div className="mx-auto h-full w-full" style={{ maxWidth: 'var(--content-width)' }}>
        <ContributedPage app={app} host={hostRef.current} src={src} mountFunction={mountFunction} />
      </div>
    </WorkbenchLayout>
  )
}

function AppHeaderActions({ actions }: { actions: AppHeaderAction[] }) {
  return (
    <div className="flex items-center gap-s">
      {actions.map((a) => {
        const variant = a.variant ?? 'secondary'
        const cls = variant === 'primary'
          ? 'bg-primary text-on-primary hover:bg-primary-emphasis'
          : variant === 'ghost'
            ? 'bg-transparent text-on-surface hover:bg-surface-high'
            : 'bg-surface-high text-on-surface hover:bg-surface-highest'
        return (
          <button key={a.id} type="button" onClick={a.onClick}
            className={`inline-flex h-9 shrink-0 items-center gap-s rounded-pill px-l text-[0.8125rem] font-[450] transition-colors ${cls}`}>
            {a.icon && <AppIcon name={a.icon} size={15} />}{a.label}
          </button>
        )
      })}
    </div>
  )
}

/** Mounts the app's imperative panel render into the SidePanel body. */
function PanelMount({ spec }: { spec: AppPanelSpec }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const cleanup = spec.render(ref.current)
    return () => { try { (cleanup as (() => void) | undefined)?.() } catch { /* ignore */ } }
  }, [spec])
  return <div ref={ref} />
}
