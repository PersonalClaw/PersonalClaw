import { useCallback, useState } from 'react'
import { useChatSocket, type WsMessage } from '../lib/useChatSocket'

// ── The interface build this page runs, and the one the gateway serves (#5901) ─────────────────
//
// An update builds a new SPA and restarts the gateway, and a page that was open kept running the
// old build until a lazy chunk it had never loaded was gone. The gateway says which build it serves
// twice: in the document this page booted from (a meta tag, `frontend.SPA_BUILD_META`) and in a
// `spa_build` frame on every socket open. So a page that reconnects after a restart sees the two
// differ, and `UpdatedInterfaceNotice` offers Reload.

/** The meta tag the gateway stamps into `index.html`. Keep in sync with `frontend.SPA_BUILD_META`. */
export const SPA_BUILD_META = 'personalclaw-spa-build'

/** The served build this tab was last told about, so a build is noticed once per tab. */
export const NOTICED_KEY = 'pc:spa-build-noticed'

/** The build this page booted: the document's meta tag, which nothing changes after load. `''`
 *  under the dev server, which stamps none, so a dev page never offers a reload. */
export function bootedBuild(doc: Document = document): string {
  return doc.querySelector(`meta[name="${SPA_BUILD_META}"]`)?.getAttribute('content')?.trim() ?? ''
}

/** The build a `spa_build` frame says the gateway serves, or `null` for any other frame. */
export function servedBuildOf(m: WsMessage): string | null {
  if (m.type !== 'spa_build') return null
  const build = m.data?.build
  return typeof build === 'string' ? build.trim() : ''
}

/** The served build this tab has already shown the notice for, or `''`. */
export function noticedBuild(): string {
  try {
    return sessionStorage.getItem(NOTICED_KEY) ?? ''
  } catch {
    return ''  // storage refused (a private window's quota): the notice may show again, no more
  }
}

export function markNoticed(build: string): void {
  try {
    sessionStorage.setItem(NOTICED_KEY, build)
  } catch { /* as above */ }
}

/** The build the gateway serves when it is not the one this page booted, or `''`. Read from the
 *  socket's `spa_build` frame, which comes with every open, so a reconnect after a restart says it
 *  with no request of its own and a lost frame leaves nothing stale. */
export function useNewerServedBuild(): string {
  const [served, setServed] = useState('')
  useChatSocket(
    useCallback((m: WsMessage) => {
      const build = servedBuildOf(m)
      if (build !== null) setServed(build)
    }, []),
    null,
  )
  const booted = bootedBuild()
  return booted && served && served !== booted ? served : ''
}
