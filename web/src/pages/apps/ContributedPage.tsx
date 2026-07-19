import { useEffect, useRef, useState } from 'react'
import { Loader2, AlertTriangle } from 'lucide-react'
import { AppApiProvider, loadContributedModule, type AppContext } from '../../app/appSdk'
import type { AppHost } from './AppFrame'
import { createRoot, type Root } from 'react-dom/client'
import { createElement } from 'react'

// A contributed app's UI bundle exports a mount function. Two shapes are
// supported (the host calls whichever the manifest names, default `mount`):
//   - React component:  export function mount() { return <MyPage/> }  (returns a node)
//   - Imperative:       export function mount(el, ctx) { ...render into el... }
// We try the React-return shape first (clean, themed via AppApiProvider); if the
// function renders imperatively into the passed element, that works too.
// `ctx.host` (the AppHost bridge) lets an imperative app contribute standard
// header actions + open the shared detail panel without drawing chrome itself.

type MountFn =
  | ((el: HTMLElement, ctx: AppContext) => void | (() => void))
  | ((ctx: AppContext) => React.ReactNode)

interface Props {
  app: AppContext
  /** The host bridge (header actions + standard detail panel). Merged into the
   *  ctx handed to the app's mount as `ctx.host`. */
  host?: AppHost
  /** ESM URL to import (the app's ui entryPoint, served at /apps/<name>/ui/...). */
  src: string
  /** Exported mount function name (manifest ui.pages[].mountFunction). */
  mountFunction?: string
}

/** Loads + mounts a contributed app page. Lazy ESM import, host-React shared via
 *  the SDK map, wrapped in AppApiProvider so the app's hooks know their identity
 *  and permission scope. Surfaces import/mount errors instead of a blank frame. */
export function ContributedPage({ app, host, src, mountFunction = 'mount' }: Props) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    let cleanup: (() => void) | undefined
    let root: Root | undefined

    setLoading(true); setError(null)
    // ctx carries the host bridge so an app can contribute header actions + open
    // the standard detail panel. Mutate-merge so the object identity the SDK
    // hooks read (useContext) still matches `app`.
    const ctx: AppContext = host ? Object.assign(app, { host }) : app
    // Load the bundle, rewriting its bare specifiers (react / @personalclaw/app-sdk / …)
    // to host-provided module shims so they resolve without a document import map.
    loadContributedModule(src)
      .then((mod) => {
        if (cancelled || !hostRef.current) return
        const fn = mod[mountFunction] as MountFn | undefined
        if (typeof fn !== 'function') {
          throw new Error(`app bundle has no "${mountFunction}" export`)
        }
        // Try the React-return shape: mount(ctx) → ReactNode.
        let node: React.ReactNode | undefined
        try { node = (fn as (ctx: AppContext) => React.ReactNode)(ctx) } catch { node = undefined }
        if (node !== undefined && node !== null) {
          root = createRoot(hostRef.current)
          root.render(createElement(AppApiProvider, { app: ctx, children: node as React.ReactNode }))
        } else {
          // Fall back to imperative mount(el, ctx).
          const ret = (fn as (el: HTMLElement, ctx: AppContext) => void | (() => void))(hostRef.current, ctx)
          if (typeof ret === 'function') cleanup = ret
        }
        setLoading(false)
      })
      .catch((e) => { if (!cancelled) { setError(String(e?.message || e)); setLoading(false) } })

    return () => {
      cancelled = true
      try { cleanup?.() } catch { /* ignore */ }
      try { root?.unmount() } catch { /* ignore */ }
    }
  }, [src, mountFunction, app, host])

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-on-surface-low">
        <AlertTriangle size={22} className="text-warn" />
        <div data-type="body-m">Failed to load {app.name}</div>
        <div data-type="body-s" className="max-w-md text-center opacity-70">{error}</div>
      </div>
    )
  }
  return (
    <div className="relative h-full">
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center">
          <Loader2 size={22} className="animate-spin text-on-surface-low" />
        </div>
      )}
      <div ref={hostRef} className="h-full" />
    </div>
  )
}
