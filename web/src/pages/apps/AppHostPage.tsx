import { Loader2, Blocks } from 'lucide-react'
import { useQuery, invalidateKeys } from '../../lib/data'
import { api, ApiError } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'
import { EmptyState, LoadError, LoadingStatus } from '../../ui/ListScaffold'
import { AppFrame } from './AppFrame'
import type { AppContext, AppPermissions } from '../../app/appSdk'

interface UIPageDecl { route?: string; label?: string; entryPoint?: string; mountFunction?: string }

/** Resolves `#/app/<name>` to an installed app's contributed UI page and mounts
 *  it (A7). Reads the manifest for the ui.pages[].entryPoint + permission scope,
 *  serves the bundle from /apps/<name>/ui/..., and hands both to ContributedPage
 *  via the SDK host. */
export function AppHostPage({ sub, navigate }: Pick<RouteProps, 'sub' | 'navigate'>) {
  const name = sub.split('/')[0]
  const { data, error, refresh } = useQuery(`app-host:${name}`, () => api.app(name), { persist: false })

  if (!name) return <Center>No app specified</Center>
  if (error) {
    // "Not installed" and "couldn't load" are different facts, and the old single branch
    // ("App “x” is not available") told the user neither. A 404 on /api/apps/{name} has
    // exactly one meaning — the app is not installed (the route has no other 404 path) —
    // an EXPECTED state whose fix is installing it, so it renders EmptyState with the
    // Store as the action. Anything else is a genuine load failure (gateway hiccup, 500),
    // which gets the retryable LoadError instead of a sentence that blames the app.
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="flex h-full items-center justify-center">
          <EmptyState icon={Blocks} title={`“${name}” isn’t installed`}
            hint="Install it from the Store to open it here."
            action={{ label: 'Open the Store', onClick: () => navigate('apps?view=store') }} />
        </div>
      )
    }
    return (
      <div className="flex h-full items-center justify-center">
        <LoadError what="app" error={error} onRetry={() => { invalidateKeys(`app-host:${name}`); refresh() }} />
      </div>
    )
  }
  if (data === undefined) return <Center spinner />

  const manifest = (data.manifest ?? {}) as Record<string, unknown>
  const ui = (manifest.ui ?? {}) as { pages?: UIPageDecl[] }
  const page = ui.pages?.find((p) => p.entryPoint)
  if (!page?.entryPoint) return <Center><Blocks size={20} /> This app contributes no UI page.</Center>

  const permissions = (manifest.permissions ?? {}) as AppPermissions
  // APE-11: the declared UI capabilities travel with the ctx, because the bundle loader
  // reads them to decide which SDK subpaths this app's imports may resolve to. Read off
  // the manifest the same way `permissions` is — `AppManifest.to_dict()` emits the key
  // only when non-empty, so absent legitimately means "declared none".
  const uiCapabilities = (manifest.uiCapabilities ?? []) as string[]
  const ctx: AppContext = { name, permissions, uiCapabilities }
  const src = `/apps/${encodeURIComponent(name)}/ui/${page.entryPoint}`
  const title = page.label || (manifest.displayName as string) || name
  const icon = (page as { icon?: string }).icon || (manifest.icon as string) || ''
  // AppFrame owns the chrome (shell-clearing header + standard detail panel); the
  // app only fills the content region below the header, so it can't hide behind
  // the floating shell corners and always matches PClaw's page layout.
  return <AppFrame app={ctx} title={title} icon={icon} src={src} mountFunction={page.mountFunction || 'mount'} />
}

/** The four states this page can show, all centred in the content region.
 *
 *  The `spinner` variant carried no text at all, so while an app's manifest was in flight the
 *  region announced nothing — the same silent-spinner shape as the route-level Suspense fallback.
 *  It is a live region now: `role="status" aria-busy="true"` plus `LoadingStatus`, whose sr-only
 *  text is the thing actually announced (an `aria-label` on a live region is a name, not an
 *  announcement). The message states WHAT is loading, because "Loading…" on a page that exists to
 *  host someone else's UI does not say whose.
 *
 *  The non-spinner variants stay plain: their text is already visible, so a live region would
 *  either double it or, worse, announce a settled message as if it had just changed. */
function Center({ children, spinner }: { children?: React.ReactNode; spinner?: boolean }) {
  if (spinner) {
    return (
      <div role="status" aria-busy="true"
        className="flex h-full items-center justify-center gap-2 text-on-surface-low" data-type="body-m">
        <LoadingStatus what="the app" />
        <Loader2 size={22} className="animate-spin" />
      </div>
    )
  }
  return (
    <div className="flex h-full items-center justify-center gap-2 text-on-surface-low" data-type="body-m">
      {children}
    </div>
  )
}
