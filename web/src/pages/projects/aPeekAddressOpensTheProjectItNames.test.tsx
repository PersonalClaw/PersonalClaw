import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { waitFor } from '@testing-library/react'
import { resetDataStore } from '../../lib/data'
import { mountList, navigate, project } from '../../test/projectListHarness'

// ── A peek address opens the project it names ────────────────────────────────────────────────────
//
// `?peek=<id>`, a retired address, lands on the project's own page. The list navigated to
// `projects/<peek>` with the value as it came, so a value carrying path characters landed the app
// on another page than the project it names. It is one path part now, encoded as every other
// link to a project's page encodes its id.

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset() })
afterEach(() => { resetDataStore() })

describe('a peek address', () => {
  it("🔴 with path characters in it opens that project's page, not another page", async () => {
    mountList({ projects: [project('p-garden', 'Garden')], query: { peek: '../settings/secrets' } })
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('projects/..%2Fsettings%2Fsecrets', { replace: true }))
  })

  it("with a plain id opens that project's page, as before", async () => {
    mountList({ projects: [project('p-garden', 'Garden')], query: { peek: 'p-garden' } })
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('projects/p-garden', { replace: true }))
  })
})
