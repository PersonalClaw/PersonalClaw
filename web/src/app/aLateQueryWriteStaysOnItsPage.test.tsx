import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useHashRoute } from './useHashRoute'

// ── A page's late query write stays on its page ─────────────────────────────────────────────────
//
// `setQuery` merges onto the address in the bar. The code cockpit opened at its bare address writes
// its first uncommitted change there (`diff=<path>`, a replace) once the workspace's changes arrive
// (#3821). Pressed Back before they did, the write landed on the page before: CI's
// compatibilityFixtures run read `#/settings?diff=src%2Fbeds.py` (#3905). A write is now the page's
// own: made by a page that is no longer in the bar, it is dropped, so no entry gains another page's
// query, whatever the order the two arrive in.

beforeEach(() => { history.replaceState(null, '', '#/settings') })
afterEach(() => cleanup())

describe('a late query write', () => {
  it('from a page that is gone is dropped, and the page now showing keeps its address', () => {
    history.pushState(null, '', '#/code/c0dec0de')
    const { result } = renderHook(() => useHashRoute('dashboard'))
    expect(result.current.route).toBe('code')
    // The cockpit's write, held from the page it was made on.
    const cockpitWrite = result.current.setQuery
    // Back, as a person presses it, before the write arrives.
    act(() => {
      history.replaceState(null, '', '#/settings')
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(result.current.route).toBe('settings')
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})
    act(() => cockpitWrite({ diff: 'src/beds.py' }, { replace: true }))
    expect(location.hash).toBe('#/settings')
    // A dev build says it dropped the write, naming the page and the keys and never their values.
    expect(debug).toHaveBeenCalledWith('setQuery dropped a write from a page no longer showing',
      { route: 'code', sub: 'c0dec0de', keys: ['diff'] })
    expect(JSON.stringify(debug.mock.calls)).not.toContain('beds.py')
    debug.mockRestore()
  })

  it('from the page still showing lands as before', () => {
    history.replaceState(null, '', '#/code/c0dec0de')
    const { result } = renderHook(() => useHashRoute('dashboard'))
    act(() => result.current.setQuery({ diff: 'src/beds.py' }, { replace: true }))
    expect(location.hash).toBe('#/code/c0dec0de?diff=src%2Fbeds.py')
  })
})
