import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// ── A surface's read, answered with a fixed body (`STUBBED_READS` in `surfaces.spec.ts`) ─────────

/** A read a surface's row answers with a fixed body, and why. Only for a read whose answer depends
 *  on what other specs did first in the shared home or on state outside the run, so the surface
 *  would hold one of several states on any run, or one the run's home can only answer empty. Each
 *  state the stub hides is held by a unit test instead, named where the stub is. A `json` answers
 *  without asking the gateway, so nothing the live read says reaches the page. A `patch` answers
 *  with the live body and only the moving fields pinned, so the rest of the read is still the
 *  gateway's. */
export type StubbedRead = { url: RegExp; why: string } & ({ json: unknown } | { patch: (live: any) => unknown })

/** The part of a Playwright `Route` a stubbed read uses, `R` being its live response. */
export type StubRoute<R extends { json(): Promise<unknown> }> = {
  fetch(): Promise<R>
  fulfill(answer: { json: unknown; response?: R }): Promise<void>
}

/** Answer one stubbed read on `route`, as the matrix's rows do. */
export async function answerStubbedRead<R extends { json(): Promise<unknown> }>(stub: StubbedRead, route: StubRoute<R>): Promise<void> {
  if ('json' in stub) return route.fulfill({ json: stub.json })
  const live = await route.fetch()
  return route.fulfill({ response: live, json: stub.patch(await live.json()) })
}

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures')

/** The Store's catalogue: a snapshot of what the run's gateway answered on 2026-10-23
 *  (`fixtures/app-catalog.json`). The live read lists the app registry as it stands upstream
 *  (`apps/catalog.py`'s git sources), so an edit to a listing in another repository changed the
 *  Store's tree and turned core's main red with no core change (#3905: the Menu Bar Companion's
 *  text, PersonalClaw/PersonalClawApps#503). Re-take it with the Store's tree when the Store's
 *  cards change, never to follow the registry. What a card says is held by
 *  `src/pages/apps/storeCardProse.test.tsx` and its siblings, and this pin by
 *  `src/pages/apps/theStoreInTheUiCheckIsASnapshot.test.ts`. */
export const STORE_CATALOGUE: StubbedRead = {
  url: /\/api\/apps\/catalog(\?.*)?$/,
  json: JSON.parse(readFileSync(join(FIXTURES, 'app-catalog.json'), 'utf8')),
  why: 'the Store lists the app registry as it stands upstream, which another repository edits',
}
