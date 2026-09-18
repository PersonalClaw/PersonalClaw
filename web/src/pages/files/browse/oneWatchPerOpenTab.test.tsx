/** Issue 515: the Files page held one SSE stream PER OPEN TAB, so a 6th tab wedged the whole SPA.
 *
 * The mechanism, measured in the report: `FilesSection` mounted EVERY open tab's `FileViewer`
 * simultaneously and hid the inactive ones with `display:none` (`absolute inset-0`, not conditional
 * rendering). Each mounted viewer calls `useFileWatch`, which holds an `EventSource` on
 * `/api/file-watch` for the lifetime of the effect, and the handler is an infinite poll that never
 * returns — so N tabs pinned N HTTP/1.1 connections. Browsers cap 6 per origin, `MAX_TABS` is 12,
 * and past the 6th tab every `fetch` in the dashboard hung forever: `/api/status`, `/api/system`,
 * `/api/loops`, `/api/notifications`. The header kept reading "Gateway connected" (its status poll
 * was one of the hung requests) with `console.error` count 0, `curl` returned 200 in 6ms from the
 * same moment, closing tabs did not release the pool, and the tab list persists to localStorage —
 * so a reload re-opened the streams and landed re-wedged before the user touched anything.
 *
 * 🔑 WHY THE ASSERTION IS A CONNECTION COUNT AND NOT A RENDER SNAPSHOT. A tab that is not the
 * active tab is invisible; the only thing that made the 6th tab fatal was the resource it held
 * while invisible. So the invariant this file pins is "open tabs consume ONE watch", and it holds
 * whatever the viewport does visually.
 *
 * These tests boot from a PERSISTED tab list, which is the worst form of the bug (wedged on load,
 * with a spinner as the only symptom) and also the cheapest honest repro of "six tabs are open".
 *
 * `api.fileRead` never resolves here on purpose: it parks each viewer in its loading branch, which
 * keeps Monaco out of jsdom while leaving the watch effect — the thing under test — fully live.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

/** Records every stream the page opens, and which of them are still holding a connection.
 *  jsdom ships no EventSource at all, so production's `new EventSource(...)` throws and is
 *  swallowed by `useFileWatch`'s try/catch — without this fake there is nothing to count. */
class FakeEventSource {
  static opened: string[] = []
  static live: FakeEventSource[] = []
  static reset(): void { FakeEventSource.opened = []; FakeEventSource.live = [] }
  /** The paths whose streams are OPEN right now (what consumes the 6-per-origin pool). */
  static livePaths(): string[] {
    return FakeEventSource.live.map((s) => new URL(s.url, 'http://localhost').searchParams.get('path') || '')
  }
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  constructor(readonly url: string) {
    FakeEventSource.opened.push(url)
    FakeEventSource.live.push(this)
  }
  close(): void { FakeEventSource.live = FakeEventSource.live.filter((s) => s !== this) }
}

vi.stubGlobal('EventSource', FakeEventSource)

vi.mock('../../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../../lib/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      fileRoots: async () => ({ roots: [{ label: 'Workspace', path: '/ws', name: 'ws', is_dir: true }], entries: [], path: '' }),
      fileList: async () => ({ roots: [], entries: [], path: '/ws' }),
      fileGitStatus: async () => ({ repoRoot: '', branch: '', statuses: {} }),
      artifacts: async () => [],
      // Parked read → the viewer stays in its loader; the watch is already open by then.
      fileRead: () => new Promise<never>(() => {}),
    },
  }
})

// Imported AFTER the mock so the page (and FileViewer beneath it) binds the stubbed api.
const { FilesSection } = await import('../FilesSection')
const { registerBuiltinContentTypes } = await import('../../../ui/content/registerBuiltins')

// The content-type registry is populated at app bootstrap (`main.tsx`); FileViewer resolves its
// type from it on the first render, so without this the viewer throws before opening any watch.
registerBuiltinContentTypes()

const OPEN = ['a.md', 'b.md', 'c.md', 'd.md', 'e.md', 'f.md'].map((n) => ({ path: `/ws/${n}`, name: n }))

/** The report's repro state: six tabs restored from a previous session. */
function seedTabs(active = '/ws/f.md'): void {
  localStorage.setItem('files-open-tabs', JSON.stringify(OPEN))
  localStorage.setItem('files-open-tabs-active', active)
}

function renderFiles() {
  return render(
    <FilesSection sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />,
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  FakeEventSource.reset()
})

describe('the Files page with six tabs open', () => {
  it('holds ONE file-watch connection, not one per tab', async () => {
    seedTabs()
    renderFiles()

    // All six tabs are genuinely open — the strip lists them — so this is not passing by
    // having restored fewer tabs than the repro had.
    await waitFor(() => expect(screen.getAllByRole('tab')).toHaveLength(6))

    // 🪤 The pre-fix number here was 6, one per mounted viewer. Six is already the browser's
    // per-origin ceiling, which is why the 6th tab and not the 12th (MAX_TABS) was the cliff.
    await waitFor(() => expect(FakeEventSource.livePaths()).toEqual(['/ws/f.md']))
  })

  it('watches the ACTIVE tab, so the one connection is the one the user is looking at', async () => {
    seedTabs('/ws/c.md')
    renderFiles()

    await waitFor(() => expect(FakeEventSource.livePaths()).toEqual(['/ws/c.md']))
  })

  it('moves the watch on a tab switch instead of accumulating one per visited tab', async () => {
    seedTabs('/ws/a.md')
    renderFiles()
    await waitFor(() => expect(FakeEventSource.livePaths()).toEqual(['/ws/a.md']))

    // Visit four more tabs. A viewport that keeps deactivated tabs alive would end on five
    // live streams; the invariant is that switching MOVES the single connection.
    for (const path of ['/ws/b.md', '/ws/c.md', '/ws/d.md', '/ws/e.md']) {
      await userEvent.click(screen.getByRole('tab', { name: new RegExp(path.split('/').pop() as string) }))
      await waitFor(() => expect(FakeEventSource.livePaths()).toEqual([path]))
    }

    // Every switch DID open its own stream (the watch follows the user) — they just don't pile up.
    expect(FakeEventSource.opened).toHaveLength(5)
    expect(FakeEventSource.live).toHaveLength(1)
  })
})
