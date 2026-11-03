/**
 * The interface-updated notice UNDER reduced motion: it comes and goes with no fade at all.
 *
 * Its own file, with the `matchMedia` stub installed at MODULE SCOPE before any render, for the
 * reason `ui/motion/Entrance.reducedMotion.test.tsx` records. jsdom runs no animations, so a fade
 * and no fade both leave the DOM at once there; what is asserted is the DECISION, and its
 * motion-allowed half is in `UpdatedInterfaceNotice.test.tsx`.
 */
import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { WsMessage } from '../lib/useChatSocket'

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

const sockets: Array<(m: WsMessage) => void> = []
vi.mock('../lib/useChatSocket', () => ({
  useChatSocket: (onMessage: (m: WsMessage) => void) => { sockets.push(onMessage) },
}))

const { UpdatedInterfaceNotice } = await import('./UpdatedInterfaceNotice')
const { SPA_BUILD_META } = await import('./servedBuild')

describe('under prefers-reduced-motion the notice just IS', () => {
  it('shows and leaves with no fade', () => {
    const meta = document.createElement('meta')
    meta.name = SPA_BUILD_META
    meta.content = '1111aaaa2222bbbb'
    document.head.appendChild(meta)
    render(<UpdatedInterfaceNotice reload={() => {}} />)
    act(() => { for (const s of sockets) s({ type: 'spa_build', data: { build: '3333cccc4444dddd' } }) })

    const card = document.querySelector('[data-updated-interface]') as HTMLElement | null
    expect(card?.getAttribute('data-updated-interface')).toBe('still')
    // No hidden first frame: a fade, however short, starts the card at opacity 0.
    expect(card?.style.opacity).not.toBe('0')
    meta.remove()
  })
})
