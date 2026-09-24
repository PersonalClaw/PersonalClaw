/**
 * MC-9: the native-push module drives the Capacitor bridge the shell injects.
 *
 * The bridge is faked at the `window.Capacitor` shape — the module's whole contract —
 * so these tests pin the three behaviors a store build depends on: bridge detection is
 * strict, the token listener is attached BEFORE `register()` fires, and a tap routes
 * from the ids-only ping and nothing else.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { enableNativePush, nativeBridge, watchNativePushTaps } from './nativePush'

vi.mock('../lib/api', () => ({
  api: {
    pushRelayRegister: vi.fn().mockResolvedValue({ ok: true, device_id: 'd1' }),
    pushRelayUnregister: vi.fn().mockResolvedValue({ ok: true }),
  },
}))

import { api } from '../lib/api'

type Listener = (payload: unknown) => void

function fakeBridge({ platform = 'ios', permission = 'granted', token = 'tok-1' } = {}) {
  const listeners = new Map<string, Listener>()
  const plugin = {
    requestPermissions: vi.fn().mockResolvedValue({ receive: permission }),
    register: vi.fn().mockImplementation(async () => {
      // The OS answers through the listener — which must already be attached, or this
      // call resolves a token into the void. The fake fires synchronously on purpose:
      // an attach-after-register bug fails loudly here instead of flaking in the field.
      listeners.get('registration')?.({ value: token })
    }),
    addListener: vi.fn().mockImplementation((name: string, cb: Listener) => {
      listeners.set(name, cb)
      return Promise.resolve({ remove: () => listeners.delete(name) })
    }),
  }
  const win = {
    Capacitor: {
      isNativePlatform: () => true,
      getPlatform: () => platform,
      Plugins: { PushNotifications: plugin },
    },
  }
  return { win, plugin, listeners }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('nativeBridge', () => {
  it('is null outside the shell, on non-store platforms, and without the plugin', () => {
    expect(nativeBridge({})).toBeNull()
    expect(
      nativeBridge({ Capacitor: { isNativePlatform: () => false, getPlatform: () => 'ios' } }),
    ).toBeNull()
    const { win } = fakeBridge({ platform: 'web' })
    expect(nativeBridge(win)).toBeNull()
    expect(
      nativeBridge({
        Capacitor: { isNativePlatform: () => true, getPlatform: () => 'ios', Plugins: {} },
      }),
    ).toBeNull()
  })

  it('yields the platform and plugin inside the shell', () => {
    const { win, plugin } = fakeBridge({ platform: 'android' })
    const bridge = nativeBridge(win)
    expect(bridge?.platform).toBe('android')
    expect(bridge?.plugin).toBe(plugin)
  })
})

describe('enableNativePush', () => {
  it('registers the OS token against this device id with the real platform', async () => {
    const { win, plugin } = fakeBridge({ platform: 'android', token: 'fcm-tok' })
    const result = await enableNativePush(win)
    expect(result).toEqual({ ok: true })
    expect(plugin.register).toHaveBeenCalledOnce()
    expect(api.pushRelayRegister).toHaveBeenCalledOnce()
    const [deviceId, platform, token] = vi.mocked(api.pushRelayRegister).mock.calls[0]
    expect(deviceId).toBeTruthy()
    expect(platform).toBe('android')
    expect(token).toBe('fcm-tok')
  })

  it('attaches the token listener before register fires', async () => {
    // The fake resolves the token synchronously inside register(); success is only
    // possible when the listener was already attached.
    const { win, plugin } = fakeBridge()
    const result = await enableNativePush(win)
    expect(result.ok).toBe(true)
    const attachOrder = plugin.addListener.mock.invocationCallOrder[0]
    const registerOrder = plugin.register.mock.invocationCallOrder[0]
    expect(attachOrder).toBeLessThan(registerOrder)
  })

  it('names a permission refusal without touching the gateway', async () => {
    const { win } = fakeBridge({ permission: 'denied' })
    expect(await enableNativePush(win)).toEqual({ ok: false, reason: 'denied' })
    expect(api.pushRelayRegister).not.toHaveBeenCalled()
  })

  it('names the missing bridge', async () => {
    expect(await enableNativePush({})).toEqual({ ok: false, reason: 'no-bridge' })
  })

  it('surfaces a registrationError as a named failure', async () => {
    const { win, plugin, listeners } = fakeBridge()
    plugin.register.mockImplementation(async () => {
      listeners.get('registrationError')?.({ error: 'the vendor push service said no' })
    })
    const result = await enableNativePush(win)
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.detail).toContain('the vendor push service said no')
    expect(api.pushRelayRegister).not.toHaveBeenCalled()
  })
})

describe('watchNativePushTaps', () => {
  it('routes an approval tap from the ids-only ping', () => {
    const { win, listeners } = fakeBridge()
    const onItem = vi.fn()
    watchNativePushTaps(onItem, win)
    listeners.get('pushNotificationActionPerformed')?.({
      notification: { data: { kind: 'approval', item_id: 'apr-7' } },
    })
    expect(onItem).toHaveBeenCalledWith('approval', 'apr-7')
  })

  it('ignores a tap whose ping carries no ids', () => {
    const { win, listeners } = fakeBridge()
    const onItem = vi.fn()
    watchNativePushTaps(onItem, win)
    listeners.get('pushNotificationActionPerformed')?.({ notification: { data: {} } })
    listeners.get('pushNotificationActionPerformed')?.({})
    expect(onItem).not.toHaveBeenCalled()
  })

  it('is a no-op outside the shell', () => {
    expect(() => watchNativePushTaps(vi.fn(), {})).not.toThrow()
  })
})
