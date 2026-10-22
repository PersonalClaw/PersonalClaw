// @module-tag tree-scan
/**
 * High-frequency surfaces do not spring and do not cascade.
 *
 * The owner opens menus, the command palette and a handful of lists many times an hour, and each
 * one used to move: a menu dropped and scaled in, a filter's count badge popped on the most
 * overshooting preset, and list rows rose in one after another through `stagger()` or a per-index
 * `delay:`. Motion that repeats that often stops explaining anything; it only stands between the
 * owner and the content. So this rail holds three things:
 *
 *   1. The palette, the menu primitives, the named lists and the count displays use no
 *      `physics.playful`, no `stagger(` and no per-index `delay:`.
 *   2. Anywhere else in the tree a cascade needs an entry in `CASCADE_ALLOWLIST`, with its reason.
 *      The entries are the moments that happen rarely: the onboarding steps and Try one, once per
 *      install, and the planning walkthrough. An entry whose file no longer cascades is stale and
 *      fails, so the list cannot rot into a permission nobody uses.
 *   3. The palette and every menu primitive that draws its own surface enter through
 *      `overlayEnter`, which `motion.test.ts` pins as opacity only on `physics.smooth`. A menu that
 *      hand-rolled its own scale-and-rise would pass (1) and still bounce, which is why (3) exists.
 *
 * It is a TEXT scan over source with comments blanked, because the files explain these shapes in
 * prose. It sees what the source hands framer, not the frames a browser draws.
 */

import { join } from 'node:path'

import { describe, expect, it } from 'vitest'
import { filesUnder, readSource } from '../test/sourceTree'

const SRC = join(process.cwd(), 'src')
const MOTION_MODULE = 'design/motion.ts'

/** The surfaces a cascade or a bounce may never reach, each with what it is. */
const HIGH_FREQUENCY: Record<string, string> = {
  'app/CommandPalette.tsx': 'the command palette',
  'ui/Popover.tsx': 'the popover menu primitive and its rows',
  'ui/motion/ContextMenu.tsx': 'the right-click menu',
  'ui/FilterMenu.tsx': 'the filter menus and their count badge',
  'ui/FilterRow.tsx': 'a filter menu row',
  'ui/HeaderActions.tsx': 'the header overflow menu and its controls',
  'ui/ProjectPicker.tsx': 'the project menu',
  'ui/WidthPill.tsx': 'the content-width menu',
  'ui/composer/controls.tsx': 'the composer’s pill menus',
  'ui/NotificationBell.tsx': 'the bell, its badge and its list',
  'ui/SystemWidget.tsx': 'the System popover and its background-agent rows',
  'ui/ListScaffold.tsx': 'the shared list rows',
  'pages/chat/history/ChatHistorySidePanel.tsx': 'the chat history panel',
  'pages/loops/LoopsListPage.tsx': 'the loops list',
  'pages/chat/hero/ChatHero.tsx': 'the empty chat’s suggestion and starter chips',
  'pages/chat/FollowupChips.tsx': 'the follow-up chips',
  'pages/tasks/TasksListPage.tsx': 'the tasks list',
  'pages/notifications/NotificationsPage.tsx': 'the notifications list',
  'pages/projects/ProjectsSection.tsx': 'the projects list',
  'ui/NeedsYouBadge.tsx': 'the toolbar’s needs-you badge',
  'pages/tasks/TaskBoard.tsx': 'the board’s column counts',
}

/** Files that draw a menu or the palette surface themselves, so each must enter through
 *  `overlayEnter`. `FilterMenu` and the composer's pill menus open through `Popover`. */
const DRAWS_A_MENU = [
  'app/CommandPalette.tsx',
  'ui/Popover.tsx',
  'ui/motion/ContextMenu.tsx',
  'ui/HeaderActions.tsx',
  'ui/ProjectPicker.tsx',
  'ui/WidthPill.tsx',
  'ui/NotificationBell.tsx',
  'ui/SystemWidget.tsx',
]

/** Where a cascade is allowed, and why. A pattern, so a new onboarding step is covered by the
 *  reason that covers the others instead of needing a line of its own. */
const CASCADE_ALLOWLIST: { files: RegExp; reason: string }[] = [
  { files: /^app\/Onboarding\.tsx$/, reason: 'first run: the onboarding steps arrive once per install' },
  { files: /^app\/onboarding\/\w+Step\.tsx$/, reason: 'first run: an onboarding step, Try one included, is seen once per install' },
  { files: /^ui\/PlanningWalkthrough\.tsx$/, reason: 'the planning walkthrough is opened rarely, and its cascade reads the plan out in order' },
]

/** Blank every comment, keeping line structure, so prose about these shapes is not counted. The
 *  `[^:]` guard keeps `https://` from reading as a line comment. */
function blankComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, lead: string) => lead + ' '.repeat(m.length - lead.length))
}

const PLAYFUL = /\bphysics\.playful\b/
const STAGGER = /\bstagger\(/
/** A `delay:` whose value names a row index: `delay: i * 0.04`, `delay: Math.min(index * 0.03, 0.3)`.
 *  A fixed delay (`delay: 0.04`) is not a cascade. */
const PER_INDEX_DELAY = /\bdelay\s*:[^,}\n]*\b(?:i|idx|index)\b/

interface Hit { line: number; kind: string; text: string }

function findings(src: string, kinds: { kind: string; re: RegExp }[]): Hit[] {
  const raw = src.split('\n')
  const hits: Hit[] = []
  blankComments(src).split('\n').forEach((line, i) => {
    for (const { kind, re } of kinds) if (re.test(line)) hits.push({ line: i + 1, kind, text: raw[i].trim() })
  })
  return hits
}

const HIGH_FREQUENCY_KINDS = [
  { kind: 'physics.playful', re: PLAYFUL },
  { kind: 'stagger(', re: STAGGER },
  { kind: 'per-index delay', re: PER_INDEX_DELAY },
]
const CASCADE_KINDS = [
  { kind: 'stagger(', re: STAGGER },
  { kind: 'per-index delay', re: PER_INDEX_DELAY },
]

const read = (rel: string) => readSource(join(SRC, rel))
const SOURCES = filesUnder(SRC, (name) => /\.tsx?$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name))
  .map((path) => path.slice(SRC.length + 1).split('\\').join('/'))
  .filter((rel) => rel !== MOTION_MODULE && !rel.startsWith('test/'))
  .sort()

const allowed = (rel: string) => CASCADE_ALLOWLIST.find((a) => a.files.test(rel))

describe('high-frequency surfaces do not spring', () => {
  it.each(Object.entries(HIGH_FREQUENCY))('%s (%s) has no playful spring, stagger or per-index delay', (rel) => {
    const hits = findings(read(rel), HIGH_FREQUENCY_KINDS).map((h) => `${rel}:${h.line} ${h.kind}  ${h.text}`)
    expect(hits, `motion on a surface the owner opens many times an hour:\n  ${hits.join('\n  ')}`).toEqual([])
  })

  it.each(DRAWS_A_MENU)('%s enters through overlayEnter', (rel) => {
    expect(blankComments(read(rel)), `${rel} hand-rolls its menu entrance`).toMatch(/variants=\{overlayEnter\}/)
  })
})

describe('a cascade is allowed only where it is listed, with its reason', () => {
  it('no file outside the allowlist cascades', () => {
    const offenders = SOURCES
      .filter((rel) => !allowed(rel))
      .flatMap((rel) => findings(read(rel), CASCADE_KINDS).map((h) => `${rel}:${h.line} ${h.kind}  ${h.text}`))
    expect(offenders, [
      'rows cascade in on a surface that is not a first-run moment:',
      ...offenders.map((o) => `  ${o}`),
      'Render the rows together. A cascade belongs to a rare moment; add one to CASCADE_ALLOWLIST',
      'only with the reason it is rare.',
    ].join('\n')).toEqual([])
  })

  it('every allowlist entry has a reason and still names a file that cascades', () => {
    for (const entry of CASCADE_ALLOWLIST) {
      expect(entry.reason.trim().length, `${entry.files} has no reason`).toBeGreaterThan(20)
      const cascading = SOURCES.filter((rel) => entry.files.test(rel) && findings(read(rel), CASCADE_KINDS).length > 0)
      expect(cascading, `${entry.files} is stale: nothing it names cascades any more`).not.toEqual([])
    }
  })

  it('the first-run moments the issue names are the ones allowed', () => {
    // Try one and the onboarding steps by name, so a rename that left the pattern matching nothing
    // is seen here rather than as a silently narrower allowlist.
    for (const rel of ['app/Onboarding.tsx', 'app/onboarding/TryOneStep.tsx', 'app/onboarding/EssentialsStep.tsx', 'ui/PlanningWalkthrough.tsx']) {
      expect(SOURCES, `${rel} is not in the scanned tree`).toContain(rel)
      expect(allowed(rel), `${rel} is not allowlisted`).toBeTruthy()
    }
  })
})

describe('the rail can fail', () => {
  it('scanned a real tree', () => {
    expect(SOURCES.length, 'the corpus is empty, so this rail proves nothing').toBeGreaterThan(300)
    for (const rel of Object.keys(HIGH_FREQUENCY)) expect(SOURCES, `${rel} moved or was renamed`).toContain(rel)
  })

  it('a stagger() planted on the chat history list is caught', () => {
    const rel = 'pages/chat/history/ChatHistorySidePanel.tsx'
    // The shape the panel shipped with, put back on its list.
    const planted = read(rel).replace(
      /(\{recent\.map\()/,
      '<motion.div variants={{ animate: { transition: stagger(0.03) } }} initial="initial" animate="animate">$1',
    )
    expect(planted, 'the plant did not land').not.toBe(read(rel))
    expect(findings(read(rel), CASCADE_KINDS), 'the unplanted panel already cascades').toEqual([])
    expect(allowed(rel), 'the chat history list must never be allowlisted').toBeUndefined()
    expect(findings(planted, CASCADE_KINDS).map((h) => h.kind)).toContain('stagger(')
    expect(findings(planted, HIGH_FREQUENCY_KINDS).map((h) => h.kind)).toContain('stagger(')
  })

  it('a per-index delay and a playful spring are caught, and a fixed delay is not', () => {
    expect(findings('transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}', CASCADE_KINDS)).toHaveLength(1)
    expect(findings('transition={{ ...spring.spatialFast, delay: 0.04 * i }}', CASCADE_KINDS)).toHaveLength(1)
    expect(findings('animate={{ opacity: 1, transition: { delay: 0.04 } }}', CASCADE_KINDS)).toEqual([])
    expect(findings('transition={physics.playful}', HIGH_FREQUENCY_KINDS).map((h) => h.kind)).toEqual(['physics.playful'])
  })

  it('a cascade named only in a comment is not counted', () => {
    expect(findings('// rows used to stagger() in\nconst a = 1\n/* delay: i * 0.03 */', CASCADE_KINDS)).toEqual([])
  })
})
