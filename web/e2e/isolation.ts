// ── How each spec may share the harness's one gateway and one home ─────────────────────────────
//
// Every invocation boots ONE gateway on ONE scratch home (`playwright.config.ts`'s webServer), and
// the shared project runs its specs in parallel against it. That is safe only for a spec that
// leaves nothing behind another spec reads. A spec that resets, writes or depends on pristine state
// runs in the `stateful` project instead: one worker, after the shared project has finished, then
// once more in a seeded random order (`statefulPasses`).
//
// Serial rather than a gateway per worker, deliberately. A worker's own gateway would also need its
// own preview server, because `vite preview` proxies `/api` to ONE gateway port; that is two boots
// per worker for two specs whose whole runtime is under two minutes.
//
// A spec that MEASURES the home rather than only reading its own part of it runs on a home of its
// own: `alone`, in an invocation that runs nothing but it (and the auth step), on a gateway and
// home that invocation made, as the visual rail does. The UI quality matrix compares every surface
// with baselines taken on a home nothing else wrote to, and the idle-cost probe counts what an idle
// page schedules; in the shared project they measured the threads, chats and turns the other specs
// left (#3905). Each such home is one project of the same name, which the config adds only to an
// invocation whose file filter names that home's specs and nothing else (`npm run e2e:ui`).
//
// Every spec in `e2e/` must be listed here. `playwright.config.ts` refuses to load when one is
// missing, and the isolation reporter fails a run in which a spec runs in a project its mode does
// not allow. `src/design/e2eIsolation.test.ts` holds both to their planted violations.

export type Isolation = 'shared' | 'serial' | 'alone'

export interface SpecIsolation {
  readonly mode: Isolation
  /** What the spec writes or reads that decides its mode. */
  readonly why: string
  /** An `alone` spec's home: the project, and the invocation, it runs in, with no other spec's. */
  readonly home?: string
}

export const SPEC_ISOLATION: Readonly<Record<string, SpecIsolation>> = {
  'smoke.spec.ts': {
    mode: 'shared',
    why: 'answers every /api call in the page itself (503, and a name for the config read), so it never reaches the gateway',
  },
  'a11y.spec.ts': {
    mode: 'shared',
    why: 'reads the chat route; its scripted turns add chat sessions, which no other spec counts',
  },
  'appSurface.spec.ts': { mode: 'shared', why: 'reads the fixture app the boot installs' },
  'buddyPlayground.spec.ts': { mode: 'shared', why: 'plays the buddy playground, which reads nothing from the gateway and writes nothing' },
  'chat.spec.ts': {
    mode: 'shared',
    why: 'adds its own chat sessions and reads only the session it opened',
  },
  'chatHalo.spec.ts': { mode: 'shared', why: 'adds its own chat sessions and reads only its own' },
  'composerStageSettles.spec.ts': { mode: 'shared', why: 'adds its own chat session and reads only its own' },
  'chatHeaderGeometry.spec.ts': { mode: 'shared', why: 'answers its chat reads with route stubs' },
  'chatOpensAtItsLatestMessage.spec.ts': { mode: 'shared', why: 'answers its chat and its image with route stubs' },
  'composerFooterGeometry.spec.ts': { mode: 'shared', why: 'answers its chat read with a route stub, and sends nothing' },
  'chatSplit.spec.ts': { mode: 'shared', why: 'answers its chat reads with route stubs' },
  'chatTurnStatus.spec.ts': { mode: 'shared', why: 'answers its chat reads and its stop with route stubs, and its socket with frames of its own' },
  'compatibilityFixtures.spec.ts': {
    mode: 'shared',
    why: 'answers the reads each fixture opens with route stubs, and writes only its own pages\' local storage',
  },
  'chatInspectorGeometry.spec.ts': { mode: 'shared', why: 'answers its chat reads with route stubs, and writes nothing' },
  'threadRailGeometry.spec.ts': { mode: 'shared', why: 'answers its thread reads with route stubs' },
  'sidebarHoldsItsPlace.spec.ts': { mode: 'shared', why: 'answers its thread reads with route stubs' },
  'focusAndReadingModes.spec.ts': { mode: 'shared', why: 'adds its own chat session and reads only its own' },
  'idleCost.spec.ts': {
    mode: 'alone',
    home: 'idle',
    why:
      'counts what an idle Home and Chat schedule; in the shared project the other specs\' turns and ' +
      'pushes reached its windows, and their threads were on the page it measured',
  },
  'nonAxeA11yChecks.spec.ts': { mode: 'shared', why: 'plants its defects in the page, writes nothing' },
  'surfaceMatrix.control.spec.ts': {
    mode: 'alone',
    home: 'matrix',
    why: 'plants one defect of each kind in the page and reads what the matrix reports, so it runs where the matrix runs',
  },
  'onboardingGeometry.spec.ts': {
    mode: 'shared',
    why:
      'renders first run without writing to the gateway: it blanks user_name in the RESPONSE and ' +
      'stubs both onboarding write paths, so the home stays onboarded for every other spec',
  },
  'pwa.spec.ts': {
    mode: 'shared',
    why: 'serves web/dist from its own server on its own port, and is serial within itself',
  },
  'sessionMap.spec.ts': { mode: 'shared', why: 'adds its own chat sessions and reads only its own' },
  'streamStall.spec.ts': { mode: 'shared', why: 'adds its own chat session and reads only its own' },
  'keyboard.spec.ts': {
    mode: 'shared',
    why: 'answers its approval, inbox and chat-list reads with route stubs; its one scripted turn adds a chat session of its own',
  },
  'surfaces.spec.ts': {
    mode: 'alone',
    home: 'matrix',
    why:
      'holds every route and opened surface to baselines taken on a home nothing else wrote to; in the ' +
      'shared project it measured the threads and chats the other specs left as findings',
  },
  'visual.spec.ts': {
    mode: 'shared',
    why:
      'runs only in an invocation of its own (`npm run e2e:visual`), whose fresh gateway no other ' +
      'spec has written to; helpers.assertPristineFlywheel refuses to compare otherwise',
  },
  'staleWrite.spec.ts': {
    mode: 'serial',
    why:
      'resets tools.projection_rules to none and adds and removes MCP servers and their grants, ' +
      'which the settings and tools sweeps read',
  },
  'deployedArtifacts.spec.ts': {
    mode: 'serial',
    why: 'creates, deploys and deletes artifacts, which the #/artifacts sweeps read',
  },
}

/** The projects a spec of each mode may run in. `setup` is the auth step, not a spec. An `alone`
 *  spec runs only in its own home's project (`homeOf`). */
export const PROJECTS_FOR: Readonly<Record<Isolation, readonly string[]>> = {
  shared: ['smoke', 'chromium'],
  serial: ['stateful', 'stateful-again-1', 'stateful-again-2'],
  alone: [],
}

export const STATEFUL_SPECS: readonly string[] = Object.keys(SPEC_ISOLATION).filter(
  (spec) => SPEC_ISOLATION[spec].mode === 'serial',
)

/** Each home of its own, by name, with the specs that run on it. */
export const ALONE_HOMES: Readonly<Record<string, readonly string[]>> = Object.entries(SPEC_ISOLATION).reduce<
  Record<string, string[]>
>((homes, [spec, entry]) => {
  if (entry.mode === 'alone' && entry.home) (homes[entry.home] ??= []).push(spec)
  return homes
}, {})

export const ALONE_SPECS: readonly string[] = Object.values(ALONE_HOMES).flat()

/** The home `file` runs on alone, or '' for a spec that shares one. */
export function homeOf(file: string): string {
  const entry = SPEC_ISOLATION[file]
  return entry?.mode === 'alone' ? (entry.home ?? '') : ''
}

/** Why a run of these projects shares a home of its own with another project's specs, or ''. */
export function sharedHomeViolation(projects: Iterable<string>): string {
  const ran = [...new Set(projects)].filter((p) => p !== 'setup').sort()
  const homes = ran.filter((p) => p in ALONE_HOMES)
  if (!homes.length || ran.length === 1) return ''
  return (
    `the ${homes.join(' and ')} home${homes.length > 1 ? 's' : ''} ran in one invocation with ` +
    `${ran.filter((p) => p !== homes[0]).join(', ')}: a spec that runs alone measures a home only it wrote to`
  )
}

/** The specs in `files` that the table does not classify. */
export function unclassified(files: readonly string[]): string[] {
  return files.filter((file) => !(file in SPEC_ISOLATION))
}

/** Why `file` may not run in `project`, or '' when it may. */
export function isolationViolation(project: string, file: string): string {
  if (project === 'setup') return ''
  const entry = SPEC_ISOLATION[file]
  if (!entry) return `${file} has no isolation mode in e2e/isolation.ts`
  if (entry.mode === 'alone') {
    if (!entry.home) return `${file} is alone but names no home in e2e/isolation.ts`
    if (project === entry.home) return ''
    return (
      `${file} is alone (${entry.why}) but ran in the ${project} project, which is not its own home ` +
      `(${entry.home})`
    )
  }
  if (!PROJECTS_FOR[entry.mode].includes(project)) {
    return (
      `${file} is ${entry.mode} (${entry.why}) but ran in the ${project} project, which does not ` +
      `keep a ${entry.mode} spec apart from the specs that share the gateway`
    )
  }
  return ''
}

/** A seeded shuffle (mulberry32), so a run's second-pass order can be replayed from its seed. */
export function seededOrder<T>(items: readonly T[], seed: number): T[] {
  let state = seed >>> 0
  const next = () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const out = [...items]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}
