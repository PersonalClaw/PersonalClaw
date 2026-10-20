/**
 * Every surface the motion audit stripped has a reduced-motion picture, or a reason it has none.
 *
 * `docs/design/motion.md` §10 lists each animation the motion pass took off a high-frequency
 * action, file by file. The unit rails keep that motion gone, and `surfaces.spec.ts`'s reduced-motion
 * step fails a surface that still declares a CSS transition or animation over 50 ms with the
 * preference set. Neither sees what a reduced-motion user is shown once the page is at rest: an
 * element left at its entrance's first frame (faded, shifted) reads as clean to both. A picture
 * does see it, so `visual.spec.ts` photographs each surface the audit touched under
 * `prefers-reduced-motion: reduce`, in both themes, plus the command palette and a menu open.
 *
 * This rail ties the two lists together. Each file in §10's strip table maps, by where it renders,
 * to the shots that show it, or to the reason the visual suite cannot show it. A page the audit
 * gains later fails here until it is given a shot or a reason, and a shot named here that the
 * manifest lost fails too. `visualBaselineCoverage.test.ts` holds each shot to a committed golden.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'
import * as manifest from '../../e2e/routes'

const MOTION_MD = join(process.cwd(), '..', 'docs', 'design', 'motion.md')

/** The `web/src`-relative files in §10's "What is stripped" table, once each. */
function strippedFiles(): string[] {
  const doc = readFileSync(MOTION_MD, 'utf8')
  const start = doc.indexOf('### What is stripped')
  const end = doc.indexOf('### ', start + 1)
  if (start === -1 || end === -1) return []
  const rows = doc.slice(start, end).split('\n').filter((line) => line.startsWith('| `'))
  return [...new Set(rows.map((line) => /^\| `([^`]+)`/.exec(line)![1]))]
}

/** The route shots, by id. A primitive under `ui/` or the shell under `app/` renders on all of them. */
const ROUTE_SHOTS = ['dashboard', 'chat', 'projects', 'tasks', 'apps', 'settings']

/** Where each part of the strip table renders: the shot ids that show it, or why none can. The
 *  longest matching prefix wins, so a file can be placed more narrowly than its folder. */
const WHERE: Record<string, string[] | { none: string }> = {
  'app/App.tsx': ROUTE_SHOTS,
  'app/CommandPalette.tsx': ['command-palette'],
  'ui/': [...ROUTE_SHOTS, 'composer-menu'],
  'pages/apps/': ['apps'],
  'pages/chat/': ['chat'],
  'pages/chat/history/SessionPeek.tsx': {
    none: '#/chat/history is a substate URL, which visual.spec.ts leaves out by design ' +
      "(routeManifestParity's NARROWER_BY_DESIGN), and the peek opens on a row the e2e home does not hold",
  },
  'pages/code/': { none: 'the code cockpit is #/code/<id>, which needs a loop to address (EXEMPT_FROM_THE_HARNESS)' },
  'pages/dashboard/': ['dashboard'],
  'pages/discover/': { none: '#/discover is a non-nav route, which visual.spec.ts leaves out by design (NARROWER_BY_DESIGN)' },
  'pages/loops/': { none: 'the loop list is under #/loops, which the harness does not visit (EXEMPT_FROM_THE_HARNESS)' },
  'pages/notifications/': {
    none: "the feed is the Inbox's Notifications filter, which the harness reaches as #/notifications, a non-nav route " +
      'that visual.spec.ts leaves out by design (NARROWER_BY_DESIGN)',
  },
  'pages/projects/': ['projects'],
  'pages/settings/': ['settings'],
  'pages/tasks/': ['tasks'],
}

function placeOf(file: string): string[] | { none: string } | undefined {
  const prefix = Object.keys(WHERE)
    .filter((p) => file === p || (p.endsWith('/') && file.startsWith(p)))
    .sort((a, b) => b.length - a.length)[0]
  return prefix === undefined ? undefined : WHERE[prefix]
}

/** The reduced-motion shots the manifest declares. Read off the module object, so a manifest with
 *  none reports which shots are missing rather than failing to import. */
function shots(): { id: string; key: string; theme: string; route: string; open?: string }[] {
  return (manifest as unknown as { REDUCED_MOTION_SHOTS?: ReturnType<typeof shots> }).REDUCED_MOTION_SHOTS ?? []
}

describe('the reduced-motion pictures cover what the motion audit stripped', () => {
  it('reads the strip table (vacuity floor)', () => {
    // A heading renamed in motion.md would leave nothing to map, and every check below would pass.
    expect(strippedFiles().length, `no rows read from §10's strip table in ${MOTION_MD}`).toBeGreaterThan(40)
  })

  it('places every stripped file on a shot, or names why the visual suite cannot show it', () => {
    const unplaced = strippedFiles().filter((file) => placeOf(file) === undefined)
    expect(
      unplaced,
      'these files are in motion.md §10 "What is stripped" but WHERE above does not say which ' +
        'reduced-motion shot shows them. Add a shot to REDUCED_MOTION_SHOTS in e2e/routes.ts, or ' +
        'give the reason the visual suite cannot reach the surface:',
    ).toEqual([])
  })

  it('has every shot the placements name, in every theme, under reduced motion', () => {
    const want = new Set<string>()
    for (const file of strippedFiles()) {
      const place = placeOf(file)
      if (Array.isArray(place)) for (const id of place) want.add(id)
    }
    const missing: string[] = []
    for (const id of [...want].sort()) {
      for (const theme of manifest.THEMES) {
        if (!shots().some((s) => s.id === id && s.theme === theme)) missing.push(`${id} (${theme})`)
      }
    }
    expect(missing, 'reduced-motion shots WHERE names that e2e/routes.ts does not declare:').toEqual([])
  })

  it('opens the command palette and a menu, as the motion pass asked', () => {
    const opened = new Set(shots().map((s) => s.open).filter(Boolean))
    expect([...opened].sort()).toEqual(['command palette', 'composer menu'])
  })

  it('names each shot by its surface, its theme and the preference', () => {
    // The key is the golden's name, so a variant can never share a file with the motion shot of
    // the same route (`<id>-<theme>`).
    const bad = shots().filter((s) => s.key !== `${s.id}-${s.theme}-reduced-motion`)
    expect(bad.map((s) => s.key)).toEqual([])
    expect(new Set(shots().map((s) => s.key)).size).toBe(shots().length)
  })

  it('declares no shot that no stripped file needs', () => {
    // The other direction: a shot nobody maps to is a golden kept for nothing.
    const named = new Set(Object.values(WHERE).flatMap((p) => (Array.isArray(p) ? p : [])))
    expect(shots().filter((s) => !named.has(s.id)).map((s) => s.key)).toEqual([])
  })
})
