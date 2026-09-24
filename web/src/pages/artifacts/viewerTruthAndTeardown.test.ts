import { describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { useDiffTeardown } from '../../ui/useDiffTeardown'

// ── Issues 581 + 582: the viewer tells the truth, and the diff tears down clean ────
//
// 581: a failed version fetch was `.catch(() => {})` — the CURRENT body stayed on
// screen under "Viewing historical v99 (read-only)" with Revert armed for a version
// that does not exist. The failure is state now; the banner says the version failed,
// offers Back to current, and arms nothing.
//
// 582: @monaco-editor/react disposes text models on unmount while the widget still
// references them — a console error on EVERY Close compare (and the cockpit's
// DiffView shares the shape). One hook detaches the models first; both sites use it.

describe('useDiffTeardown detaches models exactly once, on unmount', () => {
  it('calls setModel(null) on unmount and survives an editor that throws', () => {
    const editor = { setModel: vi.fn() }
    const { result, unmount } = renderHook(() => useDiffTeardown())
    result.current(editor)
    expect(editor.setModel).not.toHaveBeenCalled() // never during life
    unmount()
    expect(editor.setModel).toHaveBeenCalledExactlyOnceWith(null)

    // An already-disposed editor throwing must not turn the guard into new noise.
    const hostile = { setModel: vi.fn(() => { throw new Error('disposed') }) }
    const second = renderHook(() => useDiffTeardown())
    second.result.current(hostile)
    expect(() => second.unmount()).not.toThrow()
  })

  it('is a no-op when no editor ever mounted (Suspense fallback unmounted)', () => {
    const { unmount } = renderHook(() => useDiffTeardown())
    expect(() => unmount()).not.toThrow()
  })
})

describe('both DiffEditor sites route through the teardown hook (source-level)', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const compare = strip(readFileSync(resolve(__dirname, 'ArtifactCompare.tsx'), 'utf8'))
  const diffView = strip(readFileSync(resolve(__dirname, '../code/DiffView.tsx'), 'utf8'))
  for (const [name, src] of [['ArtifactCompare', compare], ['DiffView', diffView]] as const) {
    it(`${name} mounts MonacoDiff with the guard`, () => {
      expect(src).toContain("from '../../ui/useDiffTeardown'")
      expect(src).toMatch(/const onDiffMount = useDiffTeardown\(\)/)
      expect(src).toMatch(/onMount=\{onDiffMount\}/)
    })
  }
})

describe('a failed version fetch is a stated failure, not a false historical view (source-level)', () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
  const viewer = strip(readFileSync(resolve(__dirname, 'ArtifactViewer.tsx'), 'utf8'))

  it('the catch keeps the failure and clears the stale body', () => {
    expect(viewer).not.toMatch(/artifactVersion\(slug, selVersion\)[\s\S]{0,120}\.catch\(\(\) => \{\}\)/)
    expect(viewer).toMatch(/setViewContent\(''\); setViewError\(/)
  })

  it("the error banner offers Back to current and the historical banner is gated on !viewError", () => {
    expect(viewer).toMatch(/\{!isCurrent && viewError && \(/)
    expect(viewer).toContain("Couldn't load v{selVersion}")
    expect(viewer).toMatch(/setSelVersion\(null\)/)
    expect(viewer).toMatch(/\{!isCurrent && !viewError && \(/)
    // Revert lives ONLY inside the healthy-historical branch — count its render sites.
    const reverts = viewer.match(/Revert to v\{selVersion\}/g) ?? []
    expect(reverts.length).toBe(1)
  })
})
