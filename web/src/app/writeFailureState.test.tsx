import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within, cleanup } from '@testing-library/react'
import type { ComponentType } from 'react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { stripComments } from '../design/tokenLintRule'

// ── A write path whose SUCCESS branch exists and whose FAILURE branch does not ─────────────────────
//
// `ui/loadErrorState.test.tsx` rails the two READ halves of this contract: a fetcher that resolves
// its own rejection into a value the server never sent (`FETCHER_SWALLOW_BUDGET`), and a `useQuery`
// call site that binds neither `error` nor `status` so a dead load is indistinguishable from one
// still in flight (`UNBOUND_ERROR_BUDGET`). This is the WRITE half, and it is a different property:
// a read that fails lies about what you HAVE, a write that fails lies about what you DID.
//
// The shape, from #3540: `POST /api/apps {confirm:true}` fired, the server refused with a correct
// and specific reason, the frontend SET the error, the banner RENDERED it — behind the modal's
// full-viewport `position: fixed` backdrop. Set, rendered, unseeable. Generalised: **a mutation
// whose result is inspected for success and discarded on failure**, leaving the surface in whatever
// state it was already in — a spinner, a stale warning, an optimistic row that never lands, or a
// dialog that will not close.
//
// ── §0 WHY THIS IS ITS OWN FILE, AND NOT A THIRD MAP IN `loadErrorState.test.tsx` ─────────────────
//
// That file's own header argues against two maps measuring ONE property, and it is right; it does
// not argue against two files. Its §D exists because "the right shape for *does this file lie about
// server state* is the wrong shape for *can this CALL ever see its own failure*" — and it put the
// second property in the same file for a concrete reason: §D's machinery (`cachedCalls`,
// `destructuredAs`, `binds`) is `useQuery`-scoped, and §B already owned it. The write half shares
// none of that. `useQuery` is a READ hook; `cachedCalls` cannot see a mutation at all; and the unit
// of measurement here is a call site's RESULT HANDLING rather than a fetcher's `.catch`. Three
// concrete consequences:
//
//   · The one thing genuinely shared is comment stripping — and that already lives extracted, in
//     `design/tokenLintRule.stripComments`. Importing it is exactly what `loadErrorState` does, and
//     exactly what its own header instructs ("THE FIX IS TO REUSE THE ONE THAT ALREADY EXISTS").
//   · The read census deliberately EXCLUDES form C (`.catch(() => {})`) on the stated ground that it
//     is "fire-and-forget mutations" — i.e. the read half's declared out-of-scope set is this half's
//     POPULATION. Folding them into one map would make one number mean two opposite rulings.
//   · `loadErrorState.test.tsx` is 1403 lines and is the merge serialiser of the moment (#3480 and
//     #3522 both edit it while this lands). A third property there buys a conflict and no coherence.
//
// ── §0b THE FIVE RAILS THIS EXTENDS, AND THE ONE GAP THEY ALL LEAVE ───────────────────────────────
//
// The write family has been closed five times, each time over a NAMED or NARROWED population:
//
//   `pages/settings/settingsWriteReported.test.tsx`  scoped to `pages/settings`
//   `pages/userActionReported.test.ts`               12 hand-listed `[file, write, phrase]` rows
//   `pages/confirmedWriteReported.test.ts`           DERIVED, but only for CONFIRM-GATED writes
//   `pages/refusedWriteVisible.test.ts`              4 named sites — and its header states the gap
//                                                   outright: "THE TREE-WIDE GENERALISATION IS
//                                                   DELIBERATELY NOT DONE YET"
//   `pages/terminal/closeReportsFailure.test.tsx`    tree-wide, but ONE syntactic form:
//                                                   `await api.X(…).catch(() => {})` inside a
//                                                   200-character window, so it sees 1 of these 67
//
// This is that generalisation, in the only shape that survives a new file appearing: a tree-wide
// per-file COUNT that may only fall. A name says "this file is known"; only a number says "this file
// is known AND has not got worse". `confirmedWriteReported`'s own `REPORTS` predicate credits a bare
// `\.catch\(` anywhere in the enclosing function, so `.catch(() => {})` satisfies it — that hole is
// the reason a per-site handler inspection is the unit here. And `closeReportsFailure`'s exact-equality
// list is the closest prior art: it is tree-wide, which is right, over a `[^;]{0,200}` window on ONE
// spelling, which is the character-window bug `loadErrorState`'s header records fixing twice. Its
// `KnowledgeDetailPage` row is removed by this change, on its own siblings' reasoning — see the note
// left in its place.
//
// ── §0c 🔴 COMMENTS ARE STRIPPED BEFORE COUNTING, AND THAT IS LOAD-BEARING ────────────────────────
//
// Every census in this tree that scanned raw source has matched the PROSE EXPLAINING THE FIX, so the
// commit that removes the defect is the commit the rail reds — four times in one session, and
// `loadErrorState.test.tsx` makes the argument four separate times ("a rail measures the PROGRAM,
// not the explanation of it"). This file would do it to itself immediately: the comments the fixes
// below carry quote `.catch(() => {})`, `try { … } finally { … }` and `if (r?.ok)` verbatim. So all
// scanning goes through `codeOfText` → `stripComments`, and §B's vacuity block proves the stripping
// both ways (prose quoting a swallow scores 0; a swallow under prose carrying an unclosed `/*`
// scores 1). `codeOfText` is a named local rather than an inline `stripComments` call so those
// controls exercise the function the census actually uses — swapping this one line back to a naive
// regex pair reds them, which is the difference between testing a dependency and testing the program.

const SRC = join(process.cwd(), 'src')
const codeOfText = (text: string) => stripComments(text).code.join('\n')
const codeOf = (abs: string) => codeOfText(readFileSync(abs, 'utf8'))
const rel = (abs: string) => abs.slice(SRC.length + 1)
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

// ── §A THE FIXES, DRIVEN ──────────────────────────────────────────────────────────────────────────
//
// 🔴 `getByRole('alert')` PASSING IS NOT THE USER SEEING IT, and that is exactly how #3540 hid:
// jsdom implements no layout, so an error correctly rendered BEHIND a modal backdrop satisfies every
// jsdom assertion anyone would think to write. The jsdom-expressible claim that #3540 would have
// FAILED is containment — its error rendered into the page body, OUTSIDE the dialog element that was
// covering it. So every drive below asserts the alert is `within` the dialog, plus the STATE
// TRANSITION around it: the dialog is still mounted (closing is its success signal and the app was
// not removed), the confirm control is operable again, `onDone` was NOT called, and a second click
// re-fires the write. Occlusion itself belongs to a browser-driven pass (`elementFromPoint`, or
// Playwright's `toBeVisible()`); asserting containment and unmounting is what jsdom can honestly do.

const REFUSAL = 'app not installed: an earlier copy of its data is still on disk'

type DialogComponent = ComponentType<{ name: string; onClose: () => void; onDone: () => void }>

function mockApi(write: 'removeApp' | 'uninstallApp', impl: () => Promise<unknown>) {
  const calls: string[] = []
  vi.doMock('../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as Record<string, unknown>),
        appUninstallPreview: () =>
          Promise.resolve({ dependencies: [], data: { present: false, entries: 0, path: '', unconsumed: [] } }),
        [write]: () => { calls.push(write); return impl() },
      },
    }
  })
  return calls
}

beforeEach(() => { vi.resetModules(); cleanup(); sessionStorage.clear() })

const REMOVAL_DIALOGS = [
  { name: 'RemoveAppModal', write: 'removeApp' as const, button: /^Uninstall$/ },
  { name: 'UninstallModal', write: 'uninstallApp' as const, button: /^Force uninstall$/ },
]

describe('§A a refused app removal keeps its dialog, names the reason IN it, and stays retriable', () => {
  for (const { name, write, button } of REMOVAL_DIALOGS) {
    it(`${name}: the refusal is reported inside the dialog and the control is operable again`, async () => {
      const calls = mockApi(write, () => Promise.reject(new Error(REFUSAL)))
      const mod = (await import('../pages/apps/AppsSection')) as unknown as Record<string, DialogComponent>
      const Dialog = mod[name]
      const onDone = vi.fn()
      render(<Dialog name="slack-channel" onClose={() => {}} onDone={onDone} />)

      const confirm = await waitFor(() => screen.getByRole('button', { name: button }))
      fireEvent.click(confirm)

      // 1. THE REASON, AND IT IS INSIDE THE DIALOG. This is the assertion #3540 fails: its error
      //    rendered in the page body under the backdrop, so `within(dialog)` finds nothing.
      const dialog = await waitFor(() => screen.getByRole('dialog'))
      await waitFor(() => expect(within(dialog).getByRole('alert').textContent).toContain(REFUSAL))
      // 2. THE STATE TRANSITION. Closing is this dialog's success signal, so it must NOT close —
      //    the app is still installed — and the caller must not be told the removal landed.
      expect(screen.queryByRole('dialog'), 'a refused removal must not close the dialog').not.toBeNull()
      expect(onDone, 'onDone is the "it happened" signal').not.toHaveBeenCalled()
      // 3. OPERABLE, not stuck spinning: the one thing the user could tell nothing about before.
      const again = screen.getByRole('button', { name: button })
      expect(again.getAttribute('aria-busy')).not.toBe('true')
      expect(again.getAttribute('aria-disabled')).not.toBe('true')
      // 4. RETRIABLE — a second click reaches the server again rather than dead-ending.
      fireEvent.click(again)
      await waitFor(() => expect(calls.length).toBe(2))
    })

    it(`${name}: a successful removal still closes by telling its caller`, async () => {
      // The control, so the assertions above cannot be satisfied by a dialog that never works.
      mockApi(write, () => Promise.resolve({}))
      const mod = (await import('../pages/apps/AppsSection')) as unknown as Record<string, DialogComponent>
      const Dialog = mod[name]
      const onDone = vi.fn()
      render(<Dialog name="slack-channel" onClose={() => {}} onDone={onDone} />)
      fireEvent.click(await waitFor(() => screen.getByRole('button', { name: button })))
      await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
      expect(screen.queryByRole('alert'), 'a success must not announce a failure').toBeNull()
    })
  }
})

// ── §A2 THE SITES WITH NO MOUNTABLE HOST ──────────────────────────────────────────────────────────
//
// `AppDetailPanel`, `useAppActions` and `KnowledgeDetailPage.removeAnnotation` are internal to
// components with no test-reachable host, which is the same position `refusedWriteVisible` is in for
// `StudioDocEditor`/`RoutingNotesEditor` — and it resolves it the same way: assert the MECHANISM,
// scoped to the construct that owns the write, not to the file. `app/reportingWrite`'s own behaviour
// (it notifies with the server's sentence and returns the outcome) is tested where it lives, so what
// is left to pin here is that these sites route through it AND gate their success consequence on the
// answer. Reporting without gating re-renders the same state and reads as "nothing happened, twice".

/** `name`'s body, brace-matched past its parameter list — the slice that owns the write. */
function bodyOf(relPath: string, name: string): string {
  const src = codeOf(join(SRC, relPath))
  const at = src.search(new RegExp(String.raw`(?:function|const)\s+${name}\b`))
  expect(at, `${relPath} no longer defines ${name}`).toBeGreaterThan(-1)
  let i = src.indexOf('(', at)
  for (let depth = 0; i < src.length; i++) {
    if (src[i] === '(') depth++
    else if (src[i] === ')' && --depth === 0) break
  }
  const open = src.indexOf('{', i)
  for (let j = open, depth = 0; j < src.length; j++) {
    if (src[j] === '{') depth++
    else if (src[j] === '}' && --depth === 0) return src.slice(at, j + 1)
  }
  throw new Error(`${name}'s body did not terminate`)
}

describe('§A2 the three sites with no mountable host route through the shared reporter', () => {
  const PINS: Array<[string, string, RegExp]> = [
    // The panel's Activate/Deactivate button: was `try { …enable/disable… } finally { setBusy(false) }`
    // with no catch, so a refusal stopped the spinner and left the old label.
    ['pages/apps/AppsSection.tsx', 'AppDetailPanel', /reportingWrite\(`\$\{verb\} \$\{app\.name\}`/],
    // The CARD/menu twin of the same action — `p.then(reload).finally(clear)`, no handler at all.
    ['pages/apps/AppsSection.tsx', 'useAppActions', /reportingWrite\(`\$\{verb\} \$\{app\.name\}`/],
    // A highlight DELETE the reader clicked, previously `.catch(() => {})`.
    ['pages/knowledge/KnowledgeDetailPage.tsx', 'removeAnnotation', /reportingWrite\('remove that highlight'/],
  ]

  for (const [relPath, name, reporter] of PINS) {
    it(`${relPath} › ${name} reports its refusal`, () => {
      const body = bodyOf(relPath, name)
      expect(body, `${name} must report through app/reportingWrite`).toMatch(reporter)
      expect(body, `${name} must not swallow it instead`).not.toMatch(/catch\s*(?:\([^)]*\))?\s*\{\s*\}/)
    })
  }

  it('the two app-lifecycle sites GATE their success consequence on the answer', () => {
    // `reportingWrite` returns the outcome precisely so the caller can skip the refetch. A repaint
    // that runs anyway re-renders the state the user already sees.
    expect(bodyOf('pages/apps/AppsSection.tsx', 'AppDetailPanel')).toMatch(/if \(!\(await reportingWrite\([\s\S]{0,60}?\)\)\) return\s*\n\s*onChanged\(\)/)
    expect(bodyOf('pages/apps/AppsSection.tsx', 'useAppActions')).toMatch(/\.then\(\(ok\) => \{ if \(ok\) reload\(\) \}\)/)
  })

  it("and the digest reconcile no longer flashes SUCCESS over its own failure", () => {
    // `setTriage` patched the config and then reconciled the schedule row. The reconcile's rejection
    // went into `.catch(() => undefined)` and fell through to `flash` — a success confirmation over
    // exactly the state the panel's own comment calls wrong (a cron still firing for a disabled
    // digest). Two failures, two truths: the patch's revert must not fire for the reconcile (the
    // setting really did change), and `flash` must not fire either.
    const body = bodyOf('pages/settings/InboxSettingsPanel.tsx', 'setTriage')
    expect(body, 'the reconcile must report').toMatch(/api\.proactiveInstall\(\)\s*\n?\s*\.then\(flash\)\s*\n?\s*\.catch\(reportActionFailure\(/)
    expect(body, 'and must not swallow into the success path').not.toMatch(/proactiveInstall\(\)\.catch\(\(\)\s*=>\s*undefined\)/)
  })
})

// ── §B THE TREE-WIDE BUDGET ───────────────────────────────────────────────────────────────────────
//
// The write set is READ OUT OF `lib/api.ts` rather than listed, for the reason
// `confirmedWriteReported` records: a name like `verifySkill`, `sideTurn` or `grillTree` gives no
// clue that it POSTs, and a hand-rolled census missed every write whose wrapper was not literally
// named `delete*`. A method is a WRITE iff its own declaration reaches `post`/`put`/`patch`/`del`,
// `_installReq`, or a literal non-GET `method:`. A new endpoint is therefore in scope the day it
// lands, with nobody to remember it.
//
// 🔑 FOUR SHAPES, and the boundary between them and the fifth is MEASURED, not assumed — because
// "we do not model that" is only a boundary if someone counted what is outside it:
//
//   S1  `api.write(…).catch(h)`          h neither reports nor rethrows              MODELLED  30
//   S2  `try { …write… } catch { … }`    the catch body reports nothing              MODELLED  15
//   S3  `try { …write… } finally { … }`  NO catch — the rejection is unhandled       MODELLED  14
//   S4  `if (r.ok) { … }` with no else   an envelope write's failure discarded       MODELLED  10
//   S5  `const p = write(); p.then(ok)`  a promise handed NO rejection handler on    out, MEASURED
//       / a bare `await write()`         any path, and no enclosing try                  60 sites
//                                                                                        in 25 files
//
// S5 is the one genuinely open form and it is deliberately unmodelled: crediting it correctly needs
// dataflow (a write awaited inside a helper whose CALLER catches is handled, and no syntactic rule
// sees that), so a fourth budget over it would be mostly false positives — the exact "rail with false
// positives gets weakened, and a weakened rail is worse than none" trap `confirmedWriteReported`
// names. Its population is written down here so the next pass starts from a number. Two of its
// members were fixed anyway, by hand, because they are twins of S1/S3 sites in the same file
// (`useAppActions`'s toggle, and the digest reconcile) — a rail's blind spot is not a licence.
//
// ── The credits, and why each is not a weakening ──
//
//   · REPORTS — a handler that throws, toasts, notifies, calls a `report*`/`fail*` helper, writes an
//     Err/Msg/Note/Detail/Outcome/Result/Status setter, returns `ok: false`, consumes its own caught
//     parameter, or contains a written SENTENCE. The sentence member is the load-bearing one: it is
//     what credits `setNote("Couldn't save that — nothing changed.")`, a report with no house symbol
//     in it at all.
//   · S1 + A BOUND SUBSTITUTE — `const cls = await api.classifyULoop(…).catch(() => null)` is a
//     SENTINEL the call site is meant to inspect, and `LoopComposer` does: `if (!cls) setError('Could
//     not analyze the task — is a model configured?')`. So when the substitute is bound, the rest of
//     the enclosing block is credited too. An UNBOUND `.catch(() => {})` hands the call site nothing
//     to inspect, so it gets no such credit. That distinction removed 2 false positives.
//   · S3 ONLY FOR A NAKED WRITE — a write with its own attached `.catch` never rejects into the
//     enclosing `try`, so a missing catch says nothing about it, and counting it would score one
//     defect twice (it did: `ChatPage`'s title regen and `LoopCockpitPage`'s rename each appeared in
//     both S1 and S3).
//   · S4 READS THE WHOLE ENCLOSING BLOCK, not the remainder after the `if`. `DoctorPanel` reports on
//     the line ABOVE its `if (r.ok) onFixed()` — `notify(r.ok ? … : \`Fix failed: …\`)` — and a
//     remainder-only scan called that a defect. The fall-through form (`if (r.ok) { …; return }` then
//     the failure copy) is the same credit and is how `CompanionPage` and `EssentialsStep`'s local
//     bind are correctly excluded.
//
// A NUMBER HERE IS A DEBT, NOT A DISPENSATION. The only supported edit is DOWNWARD, and there is no
// regenerate mode: a regenerate on a budget like this is not a convenience, it is a loophole — it
// would let the next lane bless a new silent write by re-running a script instead of writing down why
// the site is correct. Classification of all 67, as shipped: **(a) real defect 32 · (b) deliberate
// and correct with the reason already in code 35 · (c) unreachable 0.** Five (a) sites were fixed
// (the app remove / force-uninstall / activate pair and the highlight delete), plus the digest
// reconcile and `useAppActions`' S5 twin. The remaining (a) set is carried below with its reason,
// per this brief's own instruction to fix the install/consent/credential/delete paths and declare
// the rest rather than sweep 30 per-surface product decisions in one commit.

const SILENT_WRITE_BUDGET: Record<string, number> = {
  // (b) Fire-and-forget by construction, reason at the site: "the flow's job is to get the user
  // working, and a progress write that fails must cost them nothing… resume is a convenience, not a
  // gate." A first-run resume point is the one write where silence IS the product decision.
  'app/Onboarding.tsx': 1,
  // `app/identity.tsx` carried 2 here and is GONE from this budget rather than lowered to zero: the
  // debt is discharged, not merely unobserved. It was described as "a decision about WHERE an identity
  // failure belongs" — and that decision was taken: `setName` now awaits its write before adopting the
  // value and rejects on failure, and `Onboarding.finish()` reports it. `pages/terminal/
  // closeReportsFailure.test.tsx` carries the measurement in full. A zero entry would have read as
  // live debt with a comment that is no longer true.
  // (a) `install` / `confirmInstall`, the #3540 family — and the fix is one layer DOWN, in
  // `lib/useGuardedInstall`, which reports through `guarded.error`; this step renders it via
  // `GuardedFailure`. The call sites are counted because the shape is genuinely there: every one of
  // them is `if (r?.ok) { … }` with no else, so a hook that stopped reporting would be invisible here.
  'app/onboarding/EssentialsStep.tsx': 2,
  // (a) `createAgent` inside the ACP agent-adoption helper. It returns the profile name regardless,
  // so a failed create yields a session bound to a profile that does not exist. A library module with
  // no surface; the remedy is to propagate to the picker that called it.
  'lib/agents.ts': 1,
  // 11: the side-chat open, the title regen, five optimistic organise writes (pin/folder/tags ×2/
  // never-archive), the `/optimize` fall-through, the `/undo` fall-through, and the two auto-nudge
  // controls.
  // (b) `/optimize` sends EITHER WAY, and its comment rules on it: "the turn appearing in the
  // transcript is already the answer to what did my click do."
  // (a) for the rest, and this file carries its own exemplar: `setLifecycle` two lines from
  // `setNeverArchive` already does `reportActionFailure` + an UNGATED `load()` (the refetch is the
  // repair for an optimistic move that already lied). The five organise writes are that same shape
  // with the report missing, and `userActionReported` already pinned three of their siblings.
  'pages/ChatPage.tsx': 11,
  // (a) Seven `if (r?.ok)` guarded-install call sites — same ruling as EssentialsStep above: the
  // reporting lives in `useGuardedInstall` and renders through `GuardedFailure`/`ConsentModal`.
  // The three removal/lifecycle sites this file used to carry are FIXED and gone from this number.
  'pages/apps/AppsSection.tsx': 7,
  // (b) Ruled on twice: its own comment ("a background refresh must never block the open or surface
  // an error toast") and `userActionReported`'s header, which names it as one of two deliberate
  // silences because it is a `view`-trigger side effect of navigation, not an action.
  'pages/chat/RoutingChip.tsx': 2,
  // (b) The DOUBLE-write beside a primary action that IS reported: `route` toasts its own outcome and
  // `dismiss` goes through `reportingWrite`. A failed feedback row costs the learning signal, not the
  // action the user asked for, and a second toast about it would explain nothing.
  'pages/artifacts/ArtifactViewer.tsx': 1,
  // (b) "transient — the poll loop will reconcile", and the sibling `del` in the same component does
  // set an error. A lifecycle pill that a 5s poll corrects is not a claim the user has to act on.
  'pages/chat/SdlcProgressCard.tsx': 1,
  // (b) Teardown on unmount, twice: "unmounted mid-create → don't leak". The user has left the
  // surface; there is nobody to tell and nothing for them to do.
  'pages/code/CodeCockpitPage.tsx': 2,
  // (b) the 60-SECOND POLL, named deliberate by `userActionReported`: "Nobody asked; one toast per
  // failed poll is noise, and a stale tile still shows its last value with its own timestamp."
  // (a) the tile resolve beside it — a keep/dismiss the user clicked, whose sibling refresh BUTTON
  // already uses `reportActionFailure`.
  'pages/dashboard/PinnedTiles.tsx': 2,
  // (a) A loop nudge. "keep the text so the user can retry" is a correct STATE decision and is kept;
  // `refusedWriteVisible`'s ruling is that a state decision is not a reason to stay quiet.
  'pages/dashboard/widgets/ActiveWork.tsx': 1,
  // (a) `catch { load() }` on an unpin — reconcile without report, byte-for-byte the
  // `settingsWidgets.mutate()` defect `settingsWriteReported` fixed one directory over.
  'pages/dashboard/widgets/PinnedArtifacts.tsx': 1,
  // (a) "Reveal in Finder": a click that silently does nothing when the gateway's `open -R` fails.
  'pages/files/browse/FileViewer.tsx': 1,
  // (b) Two engagement signals, both backend-GATED no-ops unless ranking is on, both labelled at the
  // site ("best-effort signal", "non-fatal"). Nobody asked for either.
  'pages/inbox/InboxPage.tsx': 2,
  // (a) The drawer copy's engagement toggle: a silent optimistic revert. Its own file already reports
  // `saveInboxSettings` correctly two functions up, so the panel answers for two of its three writes.
  'pages/inbox/InboxSettingsPanel.tsx': 1,
  // (b) ×2 "surfaced by reload" — both are BACKFILL triggers (re-enrich, re-embed) whose progress the
  // list's own badges render as they drain, so the reload genuinely is the surface.
  'pages/knowledge/KnowledgeListPage.tsx': 2,
  // (b) A read-state latch reset so the next scroll tick retries. Auto-marking an article as "reading"
  // is inferred from scroll position; nobody asked, and nothing claims it happened.
  'pages/knowledge/ReadingView.tsx': 1,
  // (b) "best-effort; preview still loads from whatever's there" — named in `refusedWriteVisible`'s
  // deferred list as one of three with a genuinely stated reason.
  'pages/loops/DesignStepPreview.tsx': 1,
  // (a) A title rename that silently keeps the old name (`.catch(() => null)` then `if (updated)`).
  'pages/loops/LoopCockpitPage.tsx': 1,
  // (a) ×3: a marketplace SKILL install ("leave un-installed; user can retry"), the pre-launch plan
  // update whose `.catch(() => {})` lets the loop launch with the OLD plan under a success path, and
  // the suggest-more-sub-goals click. The install and the plan write are the sharp two.
  'pages/loops/LoopPlanReview.tsx': 3,
  // (b) A dry-run PREVIEW behind a settings row; `setPreview(null)` hides a count, claims nothing.
  'pages/settings/ChatPanel.tsx': 1,
  // (a) The canonical panel's engagement toggle — the twin of the drawer copy above, and the same
  // silent revert. Its two SIBLING toggles in the same component already notify, so this panel
  // answers for three of its five writes. The digest reconcile that was the fifth is FIXED.
  'pages/settings/InboxSettingsPanel.tsx': 1,
  // (a) ×2: an episodic-memory UNDO with no catch at all, and a promote whose success sets
  // `dreamResult` while its failure sets nothing — "surfaced by no change" is the defect's own
  // description, and it is `refusedWriteVisible`'s exact shape.
  'pages/settings/MemoryPanel.tsx': 2,
  // (a) ×4 — one uniform family: a provider/binding toggle or save as `try { …write… } finally
  // { setBusy(false) }` with NO catch, so the rejection is unhandled and the spinner stopping is what
  // success looks like too. This is verbatim what `reportingWrite`'s docstring was extracted for; the
  // fix is mechanical and they are held together so the four land as one legible change.
  'pages/settings/MultiInstanceCard.tsx': 1,
  'pages/settings/PromptsPanel.tsx': 1,
  'pages/settings/ProviderCard.tsx': 1,
  'pages/settings/SearchPanel.tsx': 1,
  // (a) ×5: the voice-loop config patch's silent rollback, and four lexicon writes (add term, add
  // correction, rebuild, toggle auto-apply) in the same no-catch family as the four above. Note
  // `settingsWriteReported` already covers this panel's `saveUseCaseSettings` — a DIFFERENT write, so
  // a green there is not evidence about these.
  'pages/settings/VoicePanel.tsx': 5,
  // (a) The skill-install call site — same ruling as the app ones: `useGuardedInstall` reports, and
  // this surface renders `guarded.error` in a `FieldError` beside the button. Its own docstring
  // records why it is not occludable ("everything the user needs is already on one scrolling surface
  // with nothing overlaying anything"), which is the #3540 contrast worth keeping counted.
  'pages/skills/MarketplaceDetail.tsx': 1,
  // (b) "keep old id; connect will retry" — the terminal's own status line is the surface, and this
  // is the retry path. Named in `refusedWriteVisible`'s deferred list.
  'pages/terminal/TerminalView.tsx': 1,
  // (a) A tool-group enable/disable toggle, same no-catch family as the four settings ones.
  'pages/tools/ToolGroupsTile.tsx': 1,
  // (b) "status surfaces on reload" for a single-server reconnect — the server's rendered status IS
  // the answer to "did the reconnect work".
  // (a) the MCP import beside it: `try { …importMcpServer… } finally { setBusy(null) }`, no catch.
  'pages/tools/ToolsPage.tsx': 2,
  // (a) A lifecycle-hook toggle, same no-catch family.
  'pages/triggers/LifecycleDetail.tsx': 1,
  // (a) A 👍/👎 that stays optimistic on a failed record. "never break the host surface" argues for
  // not THROWING, not for silence — but the cost of one lost verdict is low and a toast per thumb
  // would be noise, so the remedy is a quiet inline revert rather than a report. Left as debt with
  // that judgment stated rather than guessed at here.
  'ui/FeedbackThumbs.tsx': 1,
  // (b) "Best-effort active-work probe for the warning; a failure just omits the count." The restart
  // it guards reports separately, at this same call site.
  'ui/SystemWidget.tsx': 1,
  // (b) "dismiss locally regardless" — the overlay is already gone by the time this fires, and what
  // it clears is server-side PROGRESS state. Telling a user who just dismissed an overlay that the
  // dismissal's bookkeeping failed is noise about something they cannot act on.
  'ui/UpdateProgressOverlay.tsx': 1,
  // (b) A teardown signal on unmount/stop, to drop the server-side slot immediately rather than at
  // the next drain. The share is already stopped locally.
  'ui/composer/useScreenShare.ts': 1,
}

/** Methods `lib/api.ts` implements with a non-GET verb, read out of its own object literal. */
function writeMethods(): Set<string> {
  const src = codeOf(join(SRC, 'lib/api.ts'))
  const body = src.slice(src.indexOf('export const api = {'))
  const HELPER = /\b(?:post|put|patch|del)\s*[<(]|_installReq\(|method:\s*'(?:POST|PUT|PATCH|DELETE)'/
  const out = new Set<string>()
  const members = [...body.matchAll(/^  (\w+)\s*:/gm)]
  for (let i = 0; i < members.length; i++) {
    const from = members[i].index!
    const to = i + 1 < members.length ? members[i + 1].index! : body.length
    if (HELPER.test(body.slice(from, to))) out.add(members[i][1])
  }
  return out
}

/** Index just past the delimiter matching the one at `open`. */
function matchAt(src: string, open: number, o = '(', c = ')'): number {
  let depth = 1
  let i = open + 1
  while (i < src.length && depth > 0) {
    if (src[i] === o) depth++
    else if (src[i] === c) depth--
    i++
  }
  return i
}

const REPORTER =
  /\bthrow\b|toast|notify\(|report\w*\(|\bfail\w*\b|onError|console\.(?:error|warn)|\bok\s*:\s*false|return\s+false|set\w*(?:Err|Error|Msg|Message|Note|Detail|Outcome|Result|Status|Test|Out|Warn|Refus|Reason|Done|Problem)\w*\(/i
/** A written sentence — the credit with no house symbol in it. 3+ chars so `''`/`'up'` are not one. */
const SENTENCE = /'[^']{3,}'|"[^"]{3,}"|`[^`]{3,}`/

/** Does this slice produce anything a user — or a caller who will tell one — can observe? */
function reports(body: string, param = ''): boolean {
  if (REPORTER.test(body)) return true
  if (SENTENCE.test(body)) return true
  return !!param && new RegExp(String.raw`\b${param}\b`).test(body)
}

/** The rest of the enclosing block, from `from` to its unmatched `}`. */
function restOfBlock(src: string, from: number): string {
  let depth = 0
  let i = from
  while (i < src.length) {
    const c = src[i]
    if (c === '{') depth++
    else if (c === '}') { if (depth === 0) break; depth-- }
    i++
  }
  return src.slice(from, i)
}
/** The whole enclosing block around `at` — backwards to its unmatched `{`, forwards to its partner. */
function wholeBlock(src: string, at: number): string {
  let depth = 0
  let j = at
  while (j > 0) {
    const c = src[j]
    if (c === '}') depth++
    else if (c === '{') { if (depth === 0) break; depth-- }
    j--
  }
  return src.slice(j, at) + restOfBlock(src, at)
}

/** Every silent write-failure site in one file, as `kind:line` strings. */
function silentSites(src: string, W: Set<string>): string[] {
  const out: string[] = []
  const lineAt = (i: number) => src.slice(0, i).split('\n').length
  const CATCH_CHAIN = /^\s*(?:\.then\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)\s*)?\.catch\(/

  // S1 — an attached `.catch(handler)` whose handler reports nothing.
  for (const m of src.matchAll(/\bapi\.(\w+)\(/g)) {
    if (!W.has(m[1])) continue
    const close = matchAt(src, m.index! + m[0].length - 1)
    const chain = src.slice(close, close + 400).match(CATCH_CHAIN)
    if (!chain) continue
    const co = close + chain[0].length - 1
    const chainEnd = matchAt(src, co)
    const arg = src.slice(co + 1, chainEnd - 1)
    // `.catch(fnRef)` is out of scope for the same reason `loadErrorState`'s form E is: every live
    // instance in this tree is a CAPTURE (`.catch(reportActionFailure('…'))`), i.e. the fix.
    const pm = arg.match(/^\s*(?:\(\s*(\w*)\s*\)|(\w+))\s*=>/)
    if (!pm) continue
    if (reports(arg.slice(pm[0].length), pm[1] || pm[2] || '')) continue
    // A BOUND substitute is a sentinel the call site is meant to inspect — credit the block too.
    const bound = /(?:const|let|var)\s+\w+\s*=\s*(?:await\s+)?$/.test(src.slice(Math.max(0, m.index! - 60), m.index!))
    if (bound && reports(restOfBlock(src, chainEnd))) continue
    out.push(`S1-catch:${lineAt(m.index!)}`)
  }

  // S2/S3 — `try { …write… }` with a silent catch, or with no catch at all.
  for (const m of src.matchAll(/\btry\s*\{/g)) {
    const open = m.index! + m[0].length - 1
    const end = matchAt(src, open, '{', '}')
    const tryBody = src.slice(open + 1, end - 1)
    const writes = [...tryBody.matchAll(/\bapi\.(\w+)\(/g)].filter((x) => W.has(x[1]))
    if (!writes.length) continue
    const cat = src.slice(end, end + 200).match(/^\s*catch\s*(?:\(\s*(\w*)\s*\))?\s*\{/)
    if (!cat) {
      if (/report(?:ingWrite|ActionFailure)\(/.test(tryBody)) continue
      // A write carrying its OWN `.catch` never rejects into this `try`, so the missing catch says
      // nothing about it — and counting it would score one defect twice.
      const naked = writes.some((x) => {
        const e2 = matchAt(tryBody, x.index! + x[0].length - 1)
        return !CATCH_CHAIN.test(tryBody.slice(e2, e2 + 300))
      })
      if (naked) out.push(`S3-nocatch:${lineAt(m.index!)}`)
      continue
    }
    const co = end + cat[0].length - 1
    if (reports(src.slice(co + 1, matchAt(src, co, '{', '}') - 1), cat[1] || '')) continue
    out.push(`S2-silent:${lineAt(m.index!)}`)
  }

  // S4 — an envelope write's result tested for success with nothing reporting the failure.
  for (const m of src.matchAll(/\bif\s*\(/g)) {
    const condOpen = m.index! + m[0].length - 1
    const condEnd = matchAt(src, condOpen)
    const cond = src.slice(condOpen + 1, condEnd - 1)
    // `.ok` must be the LEADING conjunct: `if (!r.ok)` IS the failure branch, and a `||` makes the
    // branch reachable on failure too.
    if (!/^\s*[\w.]+\s*\??\.ok\s*(?:&&|$)/.test(cond) || cond.includes('||')) continue
    let k = condEnd
    while (k < src.length && /\s/.test(src[k])) k++
    const endOfIf = src[k] === '{' ? matchAt(src, k, '{', '}') : (src.indexOf('\n', k) + 1 || src.length)
    if (/^\s*else\b/.test(src.slice(endOfIf, endOfIf + 20))) continue
    // The WHOLE block, minus the success consequent itself: the failure copy legitimately sits
    // either above the `if` (`DoctorPanel`) or below it as a fall-through (`CompanionPage`).
    const block = wholeBlock(src, m.index!).split(src.slice(m.index!, endOfIf)).join('')
    if (reports(block)) continue
    out.push(`S4-result:${lineAt(m.index!)}`)
  }
  return out
}

describe('§B no write path discards its own failure, tree-wide and by COUNT', () => {
  const W = writeMethods()
  const census = (): Map<string, string[]> => {
    const out = new Map<string, string[]>()
    for (const abs of walk(SRC)) {
      if (rel(abs) === 'lib/api.ts') continue
      const at = silentSites(codeOf(abs), W)
      if (at.length) out.set(rel(abs), at)
    }
    return out
  }

  it('VACUITY: every shape the census counts is still recognised, in BOTH directions', () => {
    // A selector that matches nothing reads exactly like a clean tree. Measured floors first, then
    // one control PER SHAPE — per-shape rather than one aggregate, because the defect this file's
    // siblings kept hitting is a single narrowing going unnoticed behind a still-passing total.
    expect(W.size, 'no write methods parsed out of lib/api.ts').toBeGreaterThan(300)
    const c = census()
    expect(c.size, 'the write-failure scanner found no file at all').toBeGreaterThanOrEqual(30)
    expect([...c.values()].reduce((n, v) => n + v.length, 0)).toBeGreaterThanOrEqual(55)

    const kinds = (src: string) => silentSites(src, W).map((s) => s.split(':')[0])
    const n = (src: string) => kinds(src).length
    // ── POSITIVE CONTROLS, AS SHAPES. Not `file:line` pins: a pin rots on the next line shift, and
    // worse, a pin on a site someone FIXES inverts into a demand that the defect come back.
    expect(kinds('api.removeApp(x).catch(() => {})'), 'S1 the void swallow').toEqual(['S1-catch'])
    expect(kinds('api.removeApp(x).catch(() => null)'), 'S1 the null substitute').toEqual(['S1-catch'])
    expect(kinds('api.removeApp(x).catch(() => load())'), 'S1 the silent reconcile').toEqual(['S1-catch'])
    expect(kinds('api.removeApp(x).catch(() => setOn(!v))'), 'S1 the silent revert').toEqual(['S1-catch'])
    expect(kinds('try { await api.removeApp(x) } catch {}'), 'S2 the empty catch').toEqual(['S2-silent'])
    expect(kinds('try { await api.removeApp(x) } catch { setBusy(false) }'), 'S2 a spinner reset is not a message').toEqual(['S2-silent'])
    expect(kinds('try { await api.removeApp(x); onDone() } finally { setBusy(false) }'), 'S3 no catch at all').toEqual(['S3-nocatch'])
    expect(kinds('async function f() { const r = await api.removeApp(x)\n  if (r.ok) { onDone() }\n}'), 'S4 no else').toEqual(['S4-result'])
    expect(n('async function f() { const r = await api.removeApp(x)\n  if (r?.ok && ready) { onDone() }\n}'), 'S4 through a compound condition').toBe(1)

    // ── NEGATIVE CONTROLS. A census with no reachable zero is decoration.
    expect(n('api.removeApp(x).catch((e) => setErr(e))'), 'a CAPTURE is not a swallow').toBe(0)
    expect(n("api.removeApp(x).catch(() => notify('Could not remove that'))"), 'a toast is a report').toBe(0)
    expect(n("api.removeApp(x).catch(() => setNote(\"Couldn't save that — nothing changed.\"))"), 'a written SENTENCE is a report even with no house symbol').toBe(0)
    expect(n("api.removeApp(x).catch(reportActionFailure('remove the app'))"), 'form E — a handler REFERENCE is out of scope').toBe(0)
    expect(n('api.apps().catch(() => [])'), 'a READ is the other half of the contract, not this one').toBe(0)
    expect(n("try { await api.removeApp(x) } catch (e) { setErr(e instanceof Error ? e.message : 'failed') }"), 'S2 with a real handler').toBe(0)
    expect(n('try { await api.removeApp(x) } catch { throw new Error(1) }'), 'a rethrow hands the failure up').toBe(0)
    expect(n("try { if (!(await reportingWrite('remove it', () => api.removeApp(x)))) return\n  onDone() } finally { setBusy(false) }"), 'S3 credited: the shared reporter owns it').toBe(0)
    expect(n('async function f() { const r = await api.removeApp(x)\n  if (r.ok) { onDone() } else { setErr(r.error) }\n}'), 'S4 with an else').toBe(0)
    expect(n("async function f() { const r = await api.removeApp(x)\n  if (r.ok) { onDone(); return }\n  setNote('That did not go through.')\n}"), 'S4 fall-through: the failure copy is BELOW the if').toBe(0)
    expect(n("async function f() { const r = await api.removeApp(x)\n  notify(r.ok ? 'Done.' : 'Failed.')\n  if (r.ok) onDone()\n}"), 'S4: the report is ABOVE the if — DoctorPanel').toBe(0)
    expect(n('async function f() { if (!r.ok) { setErr(1) }\n}'), 'the negated form IS the failure branch').toBe(0)
    expect(n("async function f() { const cls = await api.classifyULoop(k).catch(() => null)\n  if (!cls) { setError('Could not analyze the task') }\n}"), 'a BOUND sentinel inspected by the call site — LoopComposer').toBe(0)
    expect(n('api.removeApp(x).catch(() => {})\nconst q = 1'), 'an UNBOUND substitute gets no block credit').toBe(1)

    // ── THE COMMENT-STRIPPING CONTROLS, upstream of every control above ──────────────────────────
    // 🔴 If the stripper is wrong, none of the above means anything: a widened selector cannot score
    // a site preprocessing already deleted, and a rail that reads its own prose as a program reds the
    // commit that fixes the defect. Every fixture ends in a JSDoc, and THAT IS LOAD-BEARING: the old
    // naive regex pair needs a LATER `*/` to close the block it wrongly opens, so with nothing below
    // it the defect does not reproduce and these controls pass vacuously.
    const CLOSER = '\n/** an ordinary doc comment, further down the file */\n'
    const stripped = (src: string) => silentSites(codeOfText(src + CLOSER), W).length
    expect(
      stripped('// the `/api/**` glob in prose\nconst a = api.removeApp(x).catch(() => {})\n'),
      'a `//` comment carrying an unclosed `/*` must not eat the swallow below it',
    ).toBe(1)
    expect(
      stripped("const pat = '/*'\nconst a = api.removeApp(x).catch(() => {})\n"),
      'a `/*` inside a STRING must not open a block comment',
    ).toBe(1)
    // And the inverse — the trap that has bitten four lanes here: prose QUOTING the defect is prose.
    expect(
      stripped('// we deleted `api.removeApp(x).catch(() => {})` here, see #3540\nconst a = 1\n'),
      'a swallowed write QUOTED in a line comment is not code',
    ).toBe(0)
    expect(
      stripped('/* block prose naming try { await api.removeApp(x) } catch {} */\nconst a = 1\n'),
      'the same, in a block comment',
    ).toBe(0)
    expect(
      stripped('// a comment mentioning if (r.ok) { onDone() } with no else\nconst a = 1\n'),
      'and the S4 shape in prose',
    ).toBe(0)
    // `stripComments` says when it ended mid-block, and a stuck-open tracker reads as "the rest of
    // the file is clean" — the exact silent weakening. Its own docstring says callers MUST assert it.
    const stuck = walk(SRC).filter((abs) => stripComments(readFileSync(abs, 'utf8')).endState === 'block')
    expect(stuck.map(rel), 'these files leave the comment scanner stuck open').toEqual([])

    // The live positive control: the guarded-install call sites are correct code whose SHAPE is
    // permanent (the reporting lives in the hook), so this file can never legitimately score zero.
    expect((c.get('pages/apps/AppsSection.tsx') ?? []).length, 'the live control stopped counting').toBeGreaterThan(0)
  })

  it('every file with a silent write is in the budget — a NEW one turns CI red', () => {
    const unlisted = [...census().keys()].filter((f) => !(f in SILENT_WRITE_BUDGET)).sort()
    expect(
      unlisted,
      'these files let a write fail without telling anyone. Do not add them here — report the '
      + 'failure. Four ways, in order of preference:\n'
      + '  · `if (!(await reportingWrite("delete the thing", () => api.x()))) return` — reports AND '
      + 'gates the repaint (app/reportingWrite)\n'
      + '  · `.catch(reportActionFailure("…"))` when the caller needs the result\n'
      + '  · report inline through this surface\'s own error state, when the surface has somewhere '
      + 'to put a sentence beside the control that was pressed (ui/forms `FieldError`)\n'
      + '  · rethrow, if a caller reports\n'
      + 'A `finally { setBusy(false) }` with no catch is NOT one of them: the rejection goes '
      + 'unhandled and the spinner stopping is what success looks like too.',
    ).toEqual([])
  })

  it('and no file is silent MORE times than its budget', () => {
    const over = [...census().entries()]
      .filter(([f, at]) => at.length > (SILENT_WRITE_BUDGET[f] ?? 0))
      .map(([f, at]) => `${f}: ${at.length} > ${SILENT_WRITE_BUDGET[f] ?? 0} — at ${at.join(', ')}`)
    // The check a name-only allowlist cannot make. Being listed is not a licence to add more.
    expect(over, 'a budgeted file grew a new silent write').toEqual([])
  })

  it('and no file is silent FEWER times — fixing one ratchets the number down', () => {
    const c = census()
    const under = Object.entries(SILENT_WRITE_BUDGET)
      .filter(([f, n]) => (c.get(f) ?? []).length < n)
      .map(([f, n]) => `${f}: ${(c.get(f) ?? []).length} < ${n} — lower it to ${(c.get(f) ?? []).length}`)
    // The house rule: "slack is not a safety margin here, it is a hole."
    expect(under, 'ratchet these down in the same commit that fixed them').toEqual([])
  })

  it('and the budget names no file that has stopped existing', () => {
    const all = new Set(walk(SRC).map(rel))
    expect(Object.keys(SILENT_WRITE_BUDGET).filter((f) => !all.has(f)), 'delete these entries').toEqual([])
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length, 'the walker must find the tree').toBeGreaterThan(400)
  })
})
