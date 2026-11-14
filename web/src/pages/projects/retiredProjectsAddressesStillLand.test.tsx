import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { waitFor } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { mountList, navigate, project } from '../../test/projectListHarness'

// ── The list's retired addresses still land ──────────────────────────────────────────────────────
//
// The Projects page had a Chats by project view (`?view=chats`) and a peek (`?peek=<id>`). The list
// is that view now, and a project's own page is its peek: a link someone kept still opens, the
// view's search kept, each replacing its entry so Back does not return to the retired address.

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

describe('a retired address of the list', () => {
  it('?view=chats&q=x lands on the list, searched', async () => {
    const { address } = mountList({ projects: [project('p-garden', 'Garden')], query: { view: 'chats', q: 'gate' } })
    await waitFor(() => expect(address.query).toEqual({ q: 'gate' }))
  })

  it("?peek=<id> lands on the project's own page, replacing the entry", async () => {
    mountList({ projects: [project('p-garden', 'Garden')], query: { peek: 'p-garden' } })
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('projects/p-garden', { replace: true }))
  })
})
