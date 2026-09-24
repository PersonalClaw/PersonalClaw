// Service-worker registration (MOBILE-COMPANION T3.1).
//
// Registered at scope '/' from dist/sw.js — see scripts/buildServiceWorker.mjs for
// why the worker is emitted at the dist root rather than into dist/assets/.
//
// Production builds only. In `vite dev` there is no dist/sw.js to register, and a
// worker that survived a dev session would serve a cached shell over Vite's HMR
// output — the single most confusing failure mode in frontend work. The dev server
// is deliberately worker-free.

const SW_URL = '/sw.js'

/** Why the browser cannot run a service worker here, or `null` if it can.
 *
 *  Both reasons are real for PersonalClaw, not theoretical. Service workers
 *  require a secure context, and a gateway reached over plain http on a LAN
 *  address (`http://192.168.1.5:10000`) is not one — only `localhost` and a
 *  TLS-terminated tunnel (the remote-access story, MC-1) qualify. Saying so out
 *  loud beats an install button that silently never appears. */
export function serviceWorkerBlockedReason(
  nav: Navigator = navigator,
  win: { isSecureContext?: boolean } = window,
): string | null {
  if (!('serviceWorker' in nav)) return 'this browser has no service-worker support'
  if (win.isSecureContext === false) {
    return 'the page is not a secure context — reach the gateway over localhost or an https tunnel'
  }
  return null
}

/** Register the worker. Resolves to the registration, or `null` when the worker
 *  was not registered (dev build, or an environment that cannot run one).
 *
 *  `enabled` defaults to the build mode and is a parameter so the decision is
 *  testable without faking `import.meta`. */
export async function registerServiceWorker(
  enabled: boolean = import.meta.env.PROD,
): Promise<ServiceWorkerRegistration | null> {
  if (!enabled) return null
  const blocked = serviceWorkerBlockedReason()
  if (blocked) {
    console.info(`PersonalClaw: offline support and install are unavailable — ${blocked}.`)
    return null
  }
  try {
    return await navigator.serviceWorker.register(SW_URL, { scope: '/' })
  } catch (err) {
    // A failed registration must never take the dashboard down with it.
    console.warn('PersonalClaw: service-worker registration failed', err)
    return null
  }
}
