import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, waitFor } from '@testing-library/react'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data'
import { mountPage } from '../../test/projectPageHarness'

// ── A project page's retired panels still land (§6.1) ───────────────────────────────────────────
//
// `?panel=tasks:<list>` lands on the Tasks view with that list, `?panel=dir:workspace` on the
// folder's tree and `?panel=dir:context` on what a new chat here is told, each replacing its entry,
// and each while the project read has not answered (fix round 3).

beforeEach(() => { resetDataStore() })
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDataStore() })

describe('the retired ?panel= values', () => {
  it.each([
    ['tasks:tl-own', { tab: 'tasks', list: 'tl-own' }],
    ['dir:workspace', { open: 'folder' }],
    ['dir:context', { open: 'told' }],
  ] as const)('%s lands where it says, replacing the entry, before the project read answers', async (panel, lands) => {
    // The project read never answers here, nor does the list's: the mapping does not wait for them.
    const { address, writes } = mountPage({ project: 'pending', query: { panel } })
    await waitFor(() => expect(address.query.panel).toBeUndefined())
    expect(api.project, 'the project read was asked').toHaveBeenCalled()
    // The page's own mapping, as written (the Tasks view may then tidy a list it cannot find).
    expect(writes[0]?.patch).toEqual({ panel: null, ...lands })
    expect(writes[0]?.opts?.replace).toBe(true)
  })

  it('drops a ?panel= value it does not know', async () => {
    const { address } = mountPage({ query: { panel: 'nonsense' } })
    await waitFor(() => expect(address.query.panel).toBeUndefined())
    expect(address.query.open).toBeUndefined()
  })
})
