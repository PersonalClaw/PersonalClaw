import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The settings area's empty states: one string, and two that taught nothing ──────────────────
//
// The coherence scanner's `emptystate` lens reports 39 candidates tree-wide. Most are not empty
// states at all — it matches comment prose and status labels — and the real cluster is in
// `pages/settings/*`: **20 text-only empties across 10 files**, in four different visual shapes:
//
//   plain muted line          MultiInstanceCard · GuardrailsPanel · ArchivePanel · ModelBackends
//   centred, `py-6`           AuditPanel · MemoryPanel (list) · MemoryPanel (audit trail)
//   card (`bg-surface-container`)  PacksPanel · MemoryPanel (recall probes)
//   small italic (`0.75rem`)  OllamaModelManager ×2 · ModelBackends · UpdatesPanel
//
// 🔑 THE FOUR SHAPES ARE NOT DRIFT — they belong to different containers (an inline hint, a list
// slot, a card among cards, a probe result), and flattening them onto one form would ADD chrome to a
// dozen quiet surfaces. `ui/ListScaffold`'s `EmptyState` is a PAGE empty (icon chip, headline,
// action) and the dashboard kit's `SlotEmptyState` is a dashed-border pill; converging settings onto
// either is a redesign, and the owner's call rather than a scanner's. **What IS drift is one shape
// copied verbatim three times**, which is what this cycle took.
//
// 🪤 AND ONE OF THE FOUR HAD ALREADY SLIPPED: a fourth site uses `py-4` where its three siblings use
// `py-6`. Left alone deliberately — changing it moves pixels on a surface this cycle did not measure.
//
// ── The copy rule this area already follows ────────────────────────────────────────────────────
//
// Most of these empties teach: "No archived sessions yet. Closed sessions are archived here.",
// "No instances yet. Add one to start using this provider.", "No models installed. Use “Browse
// library” to pull one.", "No memory graph yet — facts and their links appear here as memory grows."
//
// Two did not. **"No memories yet."** is the first sentence a new user reads on the memory studio,
// and **"No packs installed yet."** sits on a panel whose whole subject arrives from elsewhere
// ("Importable capability bundles … one user can hand to another"). Both now say what fills them, in
// the voice a sibling on the same surface already uses.
//
// 🪤 NOT VERIFIED IN THE BROWSER, and worth stating rather than implying: this dev home HAS memories
// and packs, so neither empty branch renders. Mocking the reads empty got the memory GRAPH empty to
// appear (proving the panel reaches its empty state) but not the list, which composes from several
// endpoints. The markup change is provably inert — the class string moved to one place and the
// helper emits the same `<p>` — and the copy is asserted here.

const SETTINGS = join(process.cwd(), 'src/pages/settings')
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')
const files = () => readdirSync(SETTINGS).filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))

describe('PanelListEmpty is the one home for the centred list empty', () => {
  it('renders the exact markup the three sites shipped', () => {
    // Byte-for-byte the string that was copied: same tag, same classes, same order. Adoption had to
    // change nothing on screen, and this is what says so.
    expect(read('settingsUI.tsx'))
      .toMatch(/<p className="py-6 text-center text-on-surface-low text-\[0\.8125rem\]">\{children\}<\/p>/)
  })

  it('no panel hand-rolls that class string any more', () => {
    // 🪤 MATCHED AS THE WHOLE ATTRIBUTE, not as a substring. The first version flagged
    // `VoicePanel`, which carries those same four utilities INSIDE a longer dashed-card string
    // (`rounded-lg border border-dashed bg-surface-container px-4 py-6 text-center …`) — a different
    // shape with a border and a background, and not what this helper replaces. A rail has to be
    // scoped to exactly what it measured.
    const bare = /className="py-6 text-center text-on-surface-low text-\[0\.8125rem\]"/
    const offenders = files().filter((f) => f !== 'settingsUI.tsx').filter((f) => bare.test(read(f)))
    expect(offenders, `these should use PanelListEmpty:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the dashed-card empty is a DIFFERENT shape and stays as it is', () => {
    // Recorded so a future pass does not read it as a missed adoption: this one has a border and a
    // background because it sits alone in a section, not in a list slot.
    expect(read('VoicePanel.tsx')).toMatch(/border border-dashed[^"]*py-6 text-center/)
  })

  it('the three sites adopted it', () => {
    expect((read('AuditPanel.tsx').match(/<PanelListEmpty>/g) || []).length).toBe(1)
    expect((read('MemoryPanel.tsx').match(/<PanelListEmpty>/g) || []).length).toBe(2)
  })

  it('it stays text-only — no icon, no border', () => {
    // The moment this grows chrome it stops being a drop-in for a quiet panel and becomes the
    // redesign this cycle deliberately did not do.
    const src = read('settingsUI.tsx')
    const fn = src.slice(src.indexOf('export function PanelListEmpty'))
      .slice(0, src.slice(src.indexOf('export function PanelListEmpty')).indexOf('\n}') + 2)
    expect(fn).not.toMatch(/border|Icon|icon=/)
  })
})

describe('a settings empty state says what fills it', () => {
  it('the memory studio teaches, in the voice its own graph empty uses', () => {
    const src = read('MemoryPanel.tsx')
    expect(src, 'the bare "No memories yet." named the emptiness and taught nothing')
      .toMatch(/No memories yet — facts, episodes and lessons appear here as memory grows\./)
    // The sibling this borrowed its voice from, still present — if it changes, this should too.
    expect(read('MemoryGraph.tsx')).toMatch(/No memory graph yet — facts and their links appear here as memory grows\./)
  })

  it('the packs panel teaches what makes a pack appear', () => {
    expect(read('PacksPanel.tsx')).toMatch(/No packs installed yet — imported packs and their setup state appear here\./)
  })

  it("a search-result empty is NOT held to that rule", () => {
    // "No matches." after typing a query is complete — there is nothing to teach about a filter the
    // user just applied. A rail that demanded an explanation everywhere would push noise into these.
    const src = read('MemoryPanel.tsx')
    expect(src).toMatch(/'No matches\.'/)
    expect(read('AuditPanel.tsx')).toMatch(/<PanelListEmpty>No matching events\.<\/PanelListEmpty>/)
  })
})
