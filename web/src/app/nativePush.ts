/**
 * MC-9: the relay backend's device half — native push through the Capacitor bridge.
 *
 * This page is served by the GATEWAY, but inside the mobile shell it runs in a WebView
 * whose origin is on `allowNavigation`, so Capacitor injects its bridge here. That is the
 * deliberate split: the shell ships the Capacitor push-notifications plugin (the binary
 * capability), and this served page drives it — registration logic stays updatable with
 * the gateway, without a store release.
 *
 * The bridge is reached through `window.Capacitor` rather than an import on purpose: the
 * plugin's JS module lives in the shell's bundle, not this one, and the bridge object is
 * the shell's own contract for exactly this shape (the same reasoning that keeps
 * `mobile/www/shell/registry.mjs` a parity rail instead of an import — see its header).
 *
 * The registration POST is same-origin (`/api/push/relay-register`), so the device
 * session cookie rides automatically and no CORS surface exists.
 */
import { api } from '../lib/api'
import { pushDeviceId } from './pushClient'

/** The slice of the Capacitor push plugin this module drives. */
interface CapPushPlugin {
  requestPermissions(): Promise<{ receive: string }>
  register(): Promise<void>
  addListener(eventName: string, cb: (payload: unknown) => void): Promise<unknown> | unknown
}

interface CapBridge {
  isNativePlatform?: () => boolean
  getPlatform?: () => string
  Plugins?: Record<string, unknown>
}

export interface NativeBridge {
  platform: 'ios' | 'android'
  plugin: CapPushPlugin
}

/** The injected bridge, when this page runs inside the shell on a store platform. */
export function nativeBridge(win: unknown = globalThis): NativeBridge | null {
  const cap = (win as { Capacitor?: CapBridge }).Capacitor
  if (!cap?.isNativePlatform?.()) return null
  const platform = cap.getPlatform?.()
  if (platform !== 'ios' && platform !== 'android') return null
  const plugin = cap.Plugins?.PushNotifications as CapPushPlugin | undefined
  if (!plugin) return null
  return { platform, plugin }
}

export type NativeEnableResult =
  | { ok: true }
  | { ok: false; reason: 'no-bridge' | 'denied' | 'error'; detail?: string }

/** How long to wait for the OS to hand back a token after `register()`. Registration is a
 *  round-trip to the vendor push service, normally sub-second; the bound exists so a broken vendor
 *  service yields a named failure instead of a spinner that never resolves. */
const REGISTRATION_TIMEOUT_MS = 15_000

/**
 * Ask for permission, register with the OS, and store the token against this device id.
 *
 * The token listener is attached BEFORE `register()` is called — the plugin fires
 * `registration` as soon as the OS answers, and attaching after would race it.
 */
export async function enableNativePush(win: unknown = globalThis): Promise<NativeEnableResult> {
  const bridge = nativeBridge(win)
  if (!bridge) return { ok: false, reason: 'no-bridge' }
  try {
    const permission = await bridge.plugin.requestPermissions()
    if (permission.receive !== 'granted') return { ok: false, reason: 'denied' }
    const token = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error('push registration timed out')),
        REGISTRATION_TIMEOUT_MS,
      )
      void bridge.plugin.addListener('registration', (payload) => {
        clearTimeout(timer)
        resolve(String((payload as { value?: unknown })?.value ?? ''))
      })
      void bridge.plugin.addListener('registrationError', (payload) => {
        clearTimeout(timer)
        reject(new Error(String((payload as { error?: unknown })?.error ?? 'registration failed')))
      })
      void bridge.plugin.register()
    })
    if (!token) return { ok: false, reason: 'error', detail: 'the OS returned an empty token' }
    await api.pushRelayRegister(pushDeviceId(), bridge.platform, token)
    return { ok: true }
  } catch (err) {
    return { ok: false, reason: 'error', detail: err instanceof Error ? err.message : String(err) }
  }
}

/** Drop this device's relay token. True when the gateway removed one. */
export async function disableNativePush(): Promise<boolean> {
  try {
    await api.pushRelayUnregister(pushDeviceId())
    return true
  } catch {
    return false
  }
}

/**
 * Route a notification tap to its item. The payload's `data` is the ids-only ping the
 * relay forwarded (`{kind, item_id}` — content-free by construction, see
 * `personalclaw.push`), so the deep link is composed here from ids alone, exactly as the
 * service worker composes the webpush tap's `#/companion?approval=<id>`.
 */
export function watchNativePushTaps(
  onItem: (kind: string, itemId: string) => void,
  win: unknown = globalThis,
): void {
  const bridge = nativeBridge(win)
  if (!bridge) return
  void bridge.plugin.addListener('pushNotificationActionPerformed', (payload) => {
    const data = (payload as { notification?: { data?: Record<string, unknown> } })?.notification
      ?.data
    const kind = String(data?.kind ?? '')
    const itemId = String(data?.item_id ?? '')
    if (kind && itemId) onItem(kind, itemId)
  })
}
