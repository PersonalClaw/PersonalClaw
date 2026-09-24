import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── A close that did not happen must not remove the tab ──────────────────────────────────────────
//
// Both terminal surfaces did `await api.deleteTerminal(id).catch(() => {})` and then dropped the tab
// unconditionally. So a failed close told the user they had closed a terminal while the **PTY kept
// running server-side** — a live process the UI no longer lists, let alone offers a way to stop. This
// is not a cosmetic lie like an empty list: it leaks a resource the user believes they reclaimed.
//
// The rule was already written down in this codebase, two functions above one of the offenders, in
// `WidgetFrame`'s `pin`:
//
//   > Optimistic: flip the pin state immediately, roll back on failure (a swallowed error would look
//   > like a success).
//
// `WidgetFrame`'s own `toggleSave` was the counter-example: both branches swallowed the write and
// flipped `saved` regardless — a failed delete left the artifact on disk under a button now offering
// to save it again.
//
// 🪤 THE FIRST FIX FOR THE DRAWER WAS INERT AND I CAUGHT IT BEFORE SHIPPING. Its `error` state looks
// like the obvious channel, but it renders ONLY inside the `tabs.length === 0` branch — unreachable
// after a failed close, because the tab stays — and its copy is hardcoded "Couldn't open a session",
// the wrong noun for a close. So the Drawer reports through `notify`, while `TerminalPage`'s
// `InlineError` is NOT tab-gated and does carry the message. Two surfaces, two channels, because the
// surfaces genuinely differ — asserted per surface below rather than assumed to match.

const boom = () => Promise.reject(new Error('session is busy'))
const session = { id: 's1', title: 'bash', cwd: '/w' }

const notified: string[] = []
function mockDeps(over: Record<string, unknown>) {
  notified.length = 0
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg) },
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      terminals: () => Promise.resolve([session]),
      createTerminal: () => Promise.resolve(session),
      deleteTerminal: () => Promise.resolve({ ok: true }),
      terminalPersist: () => Promise.resolve({ persist: false }),
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); localStorage.clear() })

describe('the terminal close reports a failure instead of dropping the tab', () => {
  it('keeps the tab and tells the user when the session will not close', async () => {
    mockDeps({ deleteTerminal: boom })
    const { TerminalDrawer } = await import('./TerminalDrawer')
    render(<TerminalDrawer open onClose={() => {}} onOpenFull={() => {}} />)
    const close = await waitFor(() => screen.getByRole('button', { name: /close .*(session|bash)/i }))
    fireEvent.click(close)
    await waitFor(() => expect(notified.some((m) => /close the terminal session/i.test(m))).toBe(true))
    // 🔑 The tab must survive: a live PTY the UI has stopped listing is the actual harm. Asserted on
    // `role="tab"` rather than a label string — the label is derived and the ROLE is what makes it a
    // reachable tab, which is precisely the property that must not disappear.
    expect(screen.getAllByRole('tab'), 'the session is still open, so its tab stays').toHaveLength(1)
  })

  it('closes the tab normally when the session really does close', async () => {
    mockDeps({})
    const { TerminalDrawer } = await import('./TerminalDrawer')
    render(<TerminalDrawer open onClose={() => {}} onOpenFull={() => {}} />)
    const close = await waitFor(() => screen.getByRole('button', { name: /close .*(session|bash)/i }))
    fireEvent.click(close)
    await waitFor(() => expect(screen.queryAllByRole('tab')).toHaveLength(0))
    expect(notified, 'a successful close says nothing').toEqual([])
  })
})

describe('no surface flips local state on a write it discarded', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })
  const codeOf = (f: string) => readFileSync(f, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the two terminal closes await the delete and bail on rejection', () => {
    for (const rel of ['pages/terminal/TerminalPage.tsx', 'pages/terminal/TerminalDrawer.tsx']) {
      const code = codeOf(join(SRC, rel))
      const at = code.indexOf('const closeSession')
      expect(at, `${rel} must still have closeSession`).toBeGreaterThan(-1)
      const fn = code.slice(at, at + 700)
      expect(fn, `${rel}: the delete must not swallow`).not.toMatch(/deleteTerminal\([^)]*\)\.catch\(\(\)\s*=>\s*\{\s*\}\)/)
      expect(fn, `${rel}: a rejection must stop before the tab is dropped`).toMatch(/catch[\s\S]{0,220}?return\b/)
    }
  })

  it('each surface reports through a channel that is actually visible to it', () => {
    // 🪤 Asserted per surface BECAUSE they differ. The Drawer's `error` state is gated on
    // `tabs.length === 0`, so setting it after a failed close would render nothing at all.
    const drawer = codeOf(join(SRC, 'pages/terminal/TerminalDrawer.tsx'))
    const at = drawer.indexOf('const closeSession')
    expect(drawer.slice(at, at + 700), 'the Drawer must not use its open-gated error state')
      .not.toMatch(/setError\(/)
    expect(drawer.slice(at, at + 700), 'and must notify instead').toMatch(/notify\(/)

    const page = codeOf(join(SRC, 'pages/terminal/TerminalPage.tsx'))
    const pAt = page.indexOf('const closeSession')
    expect(page.slice(pAt, pAt + 700), 'TerminalPage renders InlineError ungated, so it sets it')
      .toMatch(/setError\(/)
  })

  it("the widget save toggle obeys the rule its own sibling states", () => {
    const code = codeOf(join(SRC, 'ui/widget/WidgetFrame.tsx'))
    const at = code.indexOf('const toggleSave')
    const fn = code.slice(at, at + 900)
    expect(fn, 'the writes must not be swallowed').not.toMatch(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/)
    expect(fn, 'and the flag moves only after the write returns')
      .toMatch(/await api\.deleteArtifact\([^)]*\)\s*setSaved\(false\)/)
    // The sibling that always did this correctly must still be the reference.
    expect(code, 'pin still rolls back').toMatch(/setPinned\(false\)/)
  })

  it('the family has no unnamed members left', () => {
    // Every `await api.X(...).catch(() => {})` on a MUTATION, tree-wide. The survivors are listed
    // with the reason each is acceptable, compared for exact equality so a new one forces a decision.
    const found: string[] = []
    for (const f of walk(SRC)) {
      const code = codeOf(f)
      for (const m of code.matchAll(/await api\.(\w+)\([^;]{0,200}?\.catch\(\(\)\s*=>\s*\{\s*\}\)/g)) {
        if (/^(get|list|fetch)/.test(m[1])) continue
        found.push(`${f.slice(SRC.length + 1)}:${m[1]}`)
      }
    }
    expect(found.sort()).toEqual([
      // A UI preference. A failed persist means the panel simply is not remembered next time;
      // nothing on screen claims otherwise and there is no server-side resource involved.
      'pages/ChatPage.tsx:sideOpen',
      // Reconciles instead of asserting: the delete is followed by `reloadAnnotations()`, so a
      // failed one brings the row back. The canonical "optimistic + reconcile" form.
      'pages/knowledge/KnowledgeDetailPage.tsx:deleteKnowledgeAnnotation',
      // The owner-taste-call file: a failed name save leaves the UI showing the new name. Recorded
      // in the session handoff with the onboarding-swallow question it belongs to, not fixed here.
      'app/identity.tsx:saveDashboardConfig',
      'app/identity.tsx:saveDashboardConfig',
    ].sort())
  })
})
