import { describe, it, expect, afterEach } from 'vitest'
import { buddyBridge, desktopBridge } from './desktopBridge'
import { signs } from './signedUrl'

// ── The desktop bridges are read as the window's own properties ─────────────────────────────
//
// The desktop app's preload scripts define `pclawDesktop` and `pclawBuddy` on the window itself.
// `desktopBridge()`, `buddyBridge()` and the URL signer's `signs()` read those own properties.

const NAMES = ['pclawDesktop', 'pclawBuddy'] as const
/** What the window inherits, from its prototype, rather than holds as its own. */
const inherited = () => Object.getPrototypeOf(window) as Record<string, unknown>

afterEach(() => {
  for (const name of NAMES) {
    delete inherited()[name]
    delete (window as unknown as Record<string, unknown>)[name]
  }
})

describe("the desktop bridges are read as the window's own properties", () => {
  it('a name the window only inherits is no bridge, and the signer reads as absent', () => {
    for (const name of NAMES) Object.defineProperty(inherited(), name, { value: {}, configurable: true, writable: true })
    expect.soft(desktopBridge(), 'the desktop bridge').toBeNull()
    expect.soft(buddyBridge(), 'the buddy bridge').toBeNull()
    expect.soft(signs(), 'the signer').toBe(false)
  })

  it("each bridge is the window's own property, as the preload scripts define it", () => {
    // Control: the reads still find the bridges where they really are.
    const desktop = { capabilities: {} }
    const buddy = { shape: {} }
    Object.defineProperty(window, 'pclawDesktop', { value: desktop, configurable: true, writable: true })
    Object.defineProperty(window, 'pclawBuddy', { value: buddy, configurable: true, writable: true })
    expect(desktopBridge()).toBe(desktop)
    expect(buddyBridge()).toBe(buddy)
    expect(signs()).toBe(true)
  })
})
