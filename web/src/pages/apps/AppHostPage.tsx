import { Loader2, Blocks } from 'lucide-react'
import { useCachedData } from '../../lib/useCachedData'
import { api } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'
import { AppFrame } from './AppFrame'
import type { AppContext, AppPermissions } from '../../app/appSdk'

interface UIPageDecl { route?: string; label?: string; entryPoint?: string; mountFunction?: string }

/** Resolves `#/app/<name>` to an installed app's contributed UI page and mounts
 *  it (A7). Reads the manifest for the ui.pages[].entryPoint + permission scope,
 *  serves the bundle from /apps/<name>/ui/..., and hands both to ContributedPage
 *  via the SDK host. */
export function AppHostPage({ sub }: Pick<RouteProps, 'sub'>) {
  const name = sub.split('/')[0]
  const { data, error } = useCachedData(`app-host:${name}`, () => api.app(name), { persist: false })

  if (!name) return <Center>No app specified</Center>
  if (error) return <Center>App “{name}” is not available</Center>
  if (data === undefined) return <Center spinner />

  const manifest = (data.manifest ?? {}) as Record<string, unknown>
  const ui = (manifest.ui ?? {}) as { pages?: UIPageDecl[] }
  const page = ui.pages?.find((p) => p.entryPoint)
  if (!page?.entryPoint) return <Center><Blocks size={20} /> This app contributes no UI page.</Center>

  const permissions = (manifest.permissions ?? {}) as AppPermissions
  const ctx: AppContext = { name, permissions }
  const src = `/apps/${encodeURIComponent(name)}/ui/${page.entryPoint}`
  const title = page.label || (manifest.displayName as string) || name
  const icon = (page as { icon?: string }).icon || (manifest.icon as string) || ''
  // AppFrame owns the chrome (shell-clearing header + standard detail panel); the
  // app only fills the content region below the header, so it can't hide behind
  // the floating shell corners and always matches PClaw's page layout.
  return <AppFrame app={ctx} title={title} icon={icon} src={src} mountFunction={page.mountFunction || 'mount'} />
}

function Center({ children, spinner }: { children?: React.ReactNode; spinner?: boolean }) {
  return (
    <div className="flex h-full items-center justify-center gap-2 text-on-surface-low" data-type="body-m">
      {spinner ? <Loader2 size={22} className="animate-spin" /> : children}
    </div>
  )
}
