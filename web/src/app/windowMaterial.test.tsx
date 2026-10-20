import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, render, renderHook } from '@testing-library/react'
import { Home } from 'lucide-react'
import { MATERIAL_ATTRIBUTE, REDUCED_TRANSPARENCY, readWindowMaterial, useWindowMaterial } from './windowMaterial'
import { NavRail } from '../ui/NavRail'

// The desktop shell gives the sidebar the system's translucent material by writing
// `data-window-material="sidebar"` on the page's root (`desktop/windowChrome.js`). The page then
// leaves the sidebar clear, so the material shows through it, and keeps everything else opaque.
// Either side can say no: the shell (off macOS, or while the system asks for reduced
// transparency), and the page (the browser's `prefers-reduced-transparency: reduce`).

type Listener = () => void

/** A matchMedia stand-in whose reduced-transparency answer a test can change. */
function stubReduced(initial: boolean) {
  let matches = initial
  const listeners = new Set<Listener>()
  const mq = {
    get matches() { return matches },
    media: REDUCED_TRANSPARENCY,
    addEventListener: (_: string, cb: Listener) => listeners.add(cb),
    removeEventListener: (_: string, cb: Listener) => listeners.delete(cb),
  }
  vi.stubGlobal('matchMedia', (q: string) => (q === REDUCED_TRANSPARENCY ? mq : { matches: false, addEventListener() {}, removeEventListener() {} }))
  return { set: (v: boolean) => { matches = v; for (const cb of listeners) cb() }, listeners }
}

const root = () => document.documentElement

afterEach(() => {
  delete root().dataset.windowMaterial
  document.body.style.background = ''
  vi.unstubAllGlobals()
})

describe('whether the sidebar shows the window material', () => {
  it('reads the attribute the shell writes, and only its one value', () => {
    expect(MATERIAL_ATTRIBUTE).toBe('data-window-material')
    stubReduced(false)
    expect(readWindowMaterial()).toBe(false)
    root().dataset.windowMaterial = 'sidebar'
    expect(readWindowMaterial()).toBe(true)
    root().dataset.windowMaterial = 'everything'
    expect(readWindowMaterial()).toBe(false)
  })

  it('stays opaque in a browser tab, where no shell writes the attribute', () => {
    stubReduced(false)
    const { result } = renderHook(() => useWindowMaterial())
    expect(result.current).toBe(false)
    expect(document.body.style.background).toBe('')
  })

  it('the browser asking for reduced transparency wins over the shell', () => {
    stubReduced(true)
    root().dataset.windowMaterial = 'sidebar'
    expect(readWindowMaterial()).toBe(false)
    const { result } = renderHook(() => useWindowMaterial())
    expect(result.current).toBe(false)
  })

  it('follows the shell turning it on and off, and clears the page under the sidebar only while on', async () => {
    stubReduced(false)
    const { result } = renderHook(() => useWindowMaterial())
    expect(result.current).toBe(false)
    await act(async () => { root().dataset.windowMaterial = 'sidebar'; await Promise.resolve() })
    expect(result.current).toBe(true)
    expect(document.body.style.background).toBe('transparent')
    await act(async () => { delete root().dataset.windowMaterial; await Promise.resolve() })
    expect(result.current).toBe(false)
    expect(document.body.style.background).toBe('')
  })

  it('follows the browser setting changing while the page is open', () => {
    const reduced = stubReduced(false)
    root().dataset.windowMaterial = 'sidebar'
    const { result, unmount } = renderHook(() => useWindowMaterial())
    expect(result.current).toBe(true)
    act(() => reduced.set(true))
    expect(result.current).toBe(false)
    expect(document.body.style.background).toBe('')
    unmount()
    expect(reduced.listeners.size).toBe(0)
  })
})

describe('the shell keeps the work surface opaque over the material', () => {
  const app = readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')

  it('clears its root only while the material shows, and paints <main> the canvas then', () => {
    expect(app).toMatch(/const material = useWindowMaterial\(\)/)
    expect(app).toMatch(/<div className="flex h-full" style=\{\{ background: material \? 'transparent' : 'var\(--color-canvas\)' \}\}>/)
    expect(app).toMatch(/<main className="relative flex-1 min-w-0" style=\{material \? \{ background: 'var\(--color-canvas\)' \} : undefined\}>/)
  })

  it('hands the rail the same answer', () => {
    // The element's props hold arrow functions (`=>`), so it is read up to its own `/>`.
    const rail = /<NavRail [\s\S]*?\/>/.exec(app)
    expect(rail, 'the shell renders the rail').not.toBeNull()
    expect(rail![0]).toMatch(/\bmaterial=\{material\}/)
  })
})

describe('the rail with and without the material', () => {
  const ITEMS = [{ id: 'dashboard', label: 'Home', icon: Home }]
  const nav = (c: HTMLElement) => c.querySelector('nav[data-tour="rail"]') as HTMLElement

  it('paints its own surface by default', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} />)
    expect(nav(container).style.background).toBe('var(--color-rail)')
  })

  it('is clear over the material, so the window shows through it', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} material />)
    expect(nav(container).style.background).toBe('transparent')
  })

  it('keeps its surface as the phone drawer, which floats over the page', () => {
    const { container } = render(<NavRail items={ITEMS} activeId="dashboard" onSelect={() => {}} collapsed={false} overlay overlayOpen material />)
    expect(nav(container).style.background).toBe('var(--color-rail)')
  })
})
