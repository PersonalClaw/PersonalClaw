import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadFrom } from './download'

// ── An export is saved as a file; the app stays on screen ─────────────────────────────────
//
// 🔴 Before: the room's "Export transcript" (and the project's "Export") pointed the tab at the
// export route. The room's answer was not an attachment, so the browser showed the raw Markdown
// in place of the whole app until Back was pressed.

afterEach(() => { vi.restoreAllMocks() })

describe('downloadFrom', () => {
  it('clicks a download link to the URL and leaves nothing behind', () => {
    const clicked: HTMLAnchorElement[] = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this)
    })
    const before = window.location.href

    downloadFrom('/api/rooms/demo/export?format=md')

    expect(clicked).toHaveLength(1)
    expect(clicked[0].getAttribute('href')).toBe('/api/rooms/demo/export?format=md')
    expect(clicked[0].hasAttribute('download'), 'saved, never navigated to').toBe(true)
    expect(clicked[0].rel).toBe('noopener')
    expect(clicked[0].isConnected, 'the link is removed once clicked').toBe(false)
    expect(window.location.href).toBe(before)
  })
})
