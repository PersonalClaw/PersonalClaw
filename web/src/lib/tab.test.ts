import { afterEach, expect, it, vi } from 'vitest'
import { tab } from './tab'

// The one test of `tab.reload` itself: that it is the tab's own reload. Every test that watches a
// reload spies on `tab.reload`, so this is what ties them to the browser. It replaces
// `window.location`'s getter, which a VM context's window cannot have replaced, so it runs in the
// `dom-forks` project, on the forks pool (vitest.config.ts).
afterEach(() => { vi.restoreAllMocks() })

it('reloads through window.location.reload, once', () => {
  const reload = vi.fn()
  vi.spyOn(window, 'location', 'get').mockReturnValue({ ...window.location, reload } as Location)
  tab.reload()
  expect(reload).toHaveBeenCalledTimes(1)
})
