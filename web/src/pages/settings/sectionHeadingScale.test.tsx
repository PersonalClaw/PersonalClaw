import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Boxes } from 'lucide-react'
import { Section } from './settingsUI'

// ── Two settings pages wrote their section titles 2px larger than the other 23 ─────────────
//
// Censused across `pages/settings/`: **23 panels render section titles through `Section` (72
// instances) at `text-[0.9375rem]`**. Two hand-rolled theirs at `text-[1.0625rem]` — 17px beside a
// sibling page's 15px, on pages a user flips between in one sitting. Measured live before and after:
//
//                          BEFORE                      AFTER
//   #/settings/design       H2 17px ×4                  H2 15px  (h1 20px unchanged)
//   #/settings/diagnostics  H2 17px ×2                  H2 15px
//   #/settings/guardrails   H2 15px  (the majority)     unchanged
//
// 🔑 THE SAME TWO PANELS, A THIRD TIME. `DesignPanel` and `DiagnosticsPanel` were also the only two
// without a `PanelHeader` (#1154, which is why they measured `h1s=0`), and they are the pair here.
// **A panel that opted out of one primitive has usually opted out of the others** — when a census
// names an outlier, check what else it skipped.
//
// 🔑 WHY THEY HAD OPTED OUT, and what it cost to bring them in: their headers carry things `Section`
// could not express — a leading icon (the three control sections), a right-hand control (the
// light/dark switcher, the log toolbar) and a hint with live content (a connection dot + counts).
// So `Section` grew `icon`, `right` and a `ReactNode` hint, with four immediate adopters. **A
// primitive that the majority uses and the outliers cannot is missing a slot, not being ignored.**
//
// The rhythm converged too: both panels drove their own spacing (`gap-2xl`, `gap-l`) while the other
// 23 let each `Section`'s `mb-2xl` set it. Their roots are now plain `<div>`s like the rest.

describe('Section carries what the outliers had opted out for', () => {
  it('renders the title at the panel-section scale, once', () => {
    const { container } = render(<Section title="Backdrop & motion">x</Section>)
    const h = container.querySelector('h2')!
    expect(h.className).toContain('text-[0.9375rem]')
    expect(h.textContent).toBe('Backdrop & motion')
  })

  it('takes a leading icon without changing the heading level', () => {
    const { container } = render(<Section title="Typography & scale" icon={Boxes}>x</Section>)
    expect(container.querySelector('h2 svg'), 'the glyph belongs inside the heading row').not.toBeNull()
    expect(container.querySelectorAll('h3').length, 'still an h2 — the panel title is the h1').toBe(0)
  })

  it('takes a right-hand control beside the title', () => {
    render(<Section title="Color scheme" right={<button type="button">Dark</button>}>x</Section>)
    expect(screen.getByRole('button', { name: 'Dark' })).toBeTruthy()
  })

  it('takes a hint with live content, not just a string', () => {
    // This is the one that kept DiagnosticsPanel out: its hint is a connection dot plus counts.
    render(<Section title="Live logs" hint={<span>Streaming · <b>12</b> shown</span>}>x</Section>)
    expect(screen.getByText('12')).toBeTruthy()
  })

  it('still renders a bare section with no header at all', () => {
    const { container } = render(<Section>only children</Section>)
    expect(container.querySelector('h2')).toBeNull()
    expect(container.textContent).toBe('only children')
  })
})

describe('no settings panel hand-rolls a section title any more', () => {
  const DIR = join(process.cwd(), 'src/pages/settings')
  /** A hand-rolled section title: an `h2` with an explicit type size. `h3` is a NESTED group
   *  heading (`ProvidersPanel`'s provider groups sit inside a section, so a level-3 there is
   *  correct — converting it would skip a level the other way), and the uppercase micro-labels at
   *  `text-[0.75rem]` are widget labels, not page sections. */
  const offenders = readdirSync(DIR)
    .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
    .flatMap((n) => {
      const src = readFileSync(join(DIR, n), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      return [...src.matchAll(/<h2[^>]*className="([^"]*)"/g)]
        .filter((m) => /text-\[(0\.9375|1\.0625|1(\.\d+)?)rem\]/.test(m[1]))
        .map(() => n)
    })

  it('leaves none', () => {
    // `settingsUI.tsx` itself is where the one `h2` lives — that is the primitive, not a panel.
    expect([...new Set(offenders)].filter((n) => n !== 'settingsUI.tsx'), 'a panel writing its own section title drifts from the other 23').toEqual([])
  })

  it('and Section is genuinely the shared owner (not vacuously green)', () => {
    const uses = readdirSync(DIR)
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
      .reduce((sum, n) => sum + (readFileSync(join(DIR, n), 'utf8').match(/<Section\b/g) ?? []).length, 0)
    // 72 before this change, 76 after.
    expect(uses, 'the primitive must actually be in use').toBeGreaterThanOrEqual(76)
  })
})

describe('a panel that names its sections names ALL of them', () => {
  // ── Content attributed to the wrong group, or to none ────────────────────────────────────────
  //
  // `Section` renders its `h2` only when given a `title` — deliberately, and the test above pins
  // that ("still renders a bare section with no header at all"). An untitled Section is therefore a
  // card, not a named group, and that is fine for a panel whose single group the `h1` already owns.
  //
  // What is NOT fine is a panel that titles some groups and not others. Measured across all 33
  // settings surfaces in a browser (the check a source scan cannot make: walk every control and ask
  // which heading precedes it), FOUR panels rendered controls that belonged to no section while a
  // sibling section on the same panel had a heading:
  //
  //   #/settings/doctor    14 controls unnamed (Re-run + every "Investigate in chat"), "Maintenance" named
  //   #/settings/models    16 controls unnamed (every use-case row),   "Prompt caching" named
  //   #/settings/routing    1 control  unnamed (the use-case select),  "Routing policy" named
  //   #/settings/apps       the whole installed-app-settings half had no heading — and being a bare
  //                         SIBLING of the "Store sources" section, it read as part of it
  //
  // The outline consequence is worse than "unnamed": a reader walking headings on `#/settings/models`
  // went "Models" → "Prompt caching" and never met the bindings the panel exists for.
  //
  // 🪤 `RoutingPolicySection` also dropped its title in ONE BRANCH: `if (rows === null) return
  // <Section>` above `return <Section title="Routing policy">`. A group that names itself only when
  // its data loads loses its heading exactly when it has bad news to deliver. Branch parity is
  // asserted below.
  //
  // 🪤 AND THE NAME `Section` IS TWO COMPONENTS. `ui/`'s Section takes `label`; `settingsUI`'s takes
  // `title`. A tree-wide scan for "Section without a title" returns 104 sites, ~99 of which are the
  // other component being used correctly. This scan is scoped to `pages/settings`, which is the only
  // place `settingsUI`'s Section is imported.

  const DIR = join(process.cwd(), 'src/pages/settings')
  const clean = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  /** Complete `<Section …>` openings, brace-aware — a `[^>]*>` matcher stops at the `>` inside
   *  `hint={<span>x</span>}` and would score a titled section as bare. */
  function sectionTags(src: string): string[] {
    const out: string[] = []
    for (const m of src.matchAll(/<Section\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const c = src[i]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
      }
    }
    return out
  }

  const panels = readdirSync(DIR)
    .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) && n !== 'settingsUI.tsx')
    .map((n) => {
      const tags = sectionTags(clean(readFileSync(join(DIR, n), 'utf8')))
      return { n, titled: tags.filter((t) => /\stitle=/.test(t)).length, bare: tags.length - tags.filter((t) => /\stitle=/.test(t)).length }
    })
    .filter((p) => p.titled + p.bare > 0)

  /** The one panel whose only group is legitimately owned by its `h1`: `SearchPanel` renders a
   *  single untitled Section holding the four use-case rows, and no titled one. Naming it would add
   *  a heading that only repeats the page title. It is listed — not pattern-matched — so that adding
   *  a titled section there forces titling the bare one too. */
  const H1_OWNS_ITS_ONLY_GROUP = new Set(['SearchPanel.tsx'])

  it('no panel mixes titled sections with untitled ones', () => {
    expect(panels.length, 'vacuity floor — the scan must resolve the panels').toBeGreaterThanOrEqual(25)
    expect(panels.reduce((s, p) => s + p.titled, 0), 'and find the titled sections').toBeGreaterThanOrEqual(50)
    const mixed = panels.filter((p) => p.titled > 0 && p.bare > 0).map((p) => `${p.n} (${p.titled} titled, ${p.bare} bare)`)
    expect(mixed, `these attribute content to the wrong group, or to none:\n${mixed.join('\n')}`).toEqual([])
  })

  it('the h1-owns-it exception is exactly one panel, and still has no titled section', () => {
    for (const n of H1_OWNS_ITS_ONLY_GROUP) {
      const p = panels.find((x) => x.n === n)
      expect(p, `${n} must still be in scope`).toBeTruthy()
      expect(p!.bare, `${n} is the single-group case`).toBeGreaterThan(0)
      expect(p!.titled, `${n} gained a titled section — now title its bare one too`).toBe(0)
    }
    const bareOnly = panels.filter((p) => p.titled === 0 && p.bare > 0).map((p) => p.n)
    expect(bareOnly.sort(), 'a new bare-only panel needs a verdict here, not silence')
      .toEqual([...H1_OWNS_ITS_ONLY_GROUP].sort())
  })

  it('the four fixed panels name their primary group', () => {
    const titleOf = (n: string) => clean(readFileSync(join(DIR, n), 'utf8'))
    expect(titleOf('DoctorPanel.tsx')).toMatch(/<Section title="Subsystem probes">/)
    expect(titleOf('ModelsPanel.tsx')).toMatch(/<Section title="Model bindings" hint=/)
    expect(titleOf('RoutingPanel.tsx')).toMatch(/<Section title="Model efficiency">/)
    expect(titleOf('AppsPanel.tsx')).toMatch(/<Section title="Installed app settings" hint=/)
  })

  it('a section keeps its title in its FAILURE branch, not only its success one', () => {
    // RoutingPolicySection returns early when the read fails; both returns must name the group.
    const src = clean(readFileSync(join(DIR, 'RoutingPanel.tsx'), 'utf8'))
    const policy = src.slice(src.indexOf('function RoutingPolicySection'))
    const tags = sectionTags(policy)
    expect(tags.length, 'the early return plus the main render').toBeGreaterThanOrEqual(2)
    for (const t of tags) expect(t, 'every branch of this section names it').toMatch(/title="Routing policy"/)
  })
})

describe('the panels this rail cannot speak for', () => {
  // Scope honesty. Everything above reasons about `<Section>` call sites, so a panel that renders
  // NONE is invisible to it — and two such panels are whole surfaces with no headings at all.
  // Measured in a browser (both at a 2.6s and a 6s settle, so this is not a load-timing artifact):
  //
  //   #/settings/audit    0 h2, 69 controls   filter chips, an operation filter, 60 log rows
  //   #/settings/memory   0 h2, 20 controls   a search field, 5 kind filters, the browse list
  //
  // Neither is fixed here: giving them an outline is a surface-shaped change (memory is a studio
  // over three stores, audit is one long log), not a title on an existing group. They are pinned so
  // the set cannot grow in silence, which is the failure mode of an unstated scope.
  const DIR = join(process.cwd(), 'src/pages/settings')
  const NO_SECTIONS = new Set([
    'ArchivePanel.tsx',            // a single read-only transcript list
    'AuditPanel.tsx',              // 0 h2 / 69 controls — OPEN, needs its own cycle
    'MultiInstanceCard.tsx',       // a card rendered INSIDE another panel's section
    'ProviderCard.tsx',            // ditto
    'ProviderConfigForm.tsx',      // ditto
  ])

  it('is exactly this set — a new sectionless panel needs a verdict, not silence', () => {
    const found = readdirSync(DIR)
      .filter((n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) && n !== 'settingsUI.tsx')
      .filter((n) => {
        const src = readFileSync(join(DIR, n), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
        return src.includes("from './settingsUI'") && !src.includes('<Section')
      })
    expect(found.length, 'vacuity floor — the scan must resolve files').toBeGreaterThan(0)
    expect(found.sort(), 'add it to NO_SECTIONS with a reason, or give it sections')
      .toEqual([...NO_SECTIONS].sort())
  })
})

describe('a section glyph is muted unless it marks something live', () => {
  // ── Coral spent on decoration ────────────────────────────────────────────────────────────────
  //
  // `settingsUI`'s `iconTone` doc already states the rule, because an earlier cycle wrote it while
  // muting `ProvidersPanel`'s nine entity glyphs: *"`primary` is right where the icon marks a live,
  // primary thing (Design's three control sections). It is WRONG for a decorative category glyph:
  // coral in this app means 'active / primary'."* The default stayed `primary` to keep existing
  // adopters byte-identical — so three later call sites inherited coral by omission.
  //
  // Measured live across all 33 settings surfaces (the icon renders INSIDE the h2, so the selector is
  // `h2 > svg` — three earlier selector guesses found 0 and read as "no icons anywhere"):
  //
  //   before   7 coral · 9 muted     coral: Design ×4, Legibility ×2 (Always-on skills,
  //                                  Project instructions), Durability ×1 (Time travel)
  //   after    4 coral · 12 muted    coral: Design's four control sections only
  //
  // Design's are the case the doc names, so they stay. This rail pins the split rather than the
  // count, so a new decorative glyph fails and a genuinely-live one has to say why here.

  const DIR = join(process.cwd(), 'src/pages/settings')
  const clean = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  /** Brace-aware `<Section …>` openings that pass an icon, per file. */
  function iconSections() {
    const out: { file: string; tag: string; muted: boolean }[] = []
    for (const n of readdirSync(DIR)) {
      if (!/\.tsx$/.test(n) || /\.(test|doc)\.tsx$/.test(n)) continue
      const src = clean(readFileSync(join(DIR, n), 'utf8'))
      for (const m of src.matchAll(/<Section\b/g)) {
        let depth = 0
        for (let i = m.index! + m[0].length; i < src.length; i++) {
          const c = src[i]
          if (c === '{') depth++
          else if (c === '}') depth--
          else if (c === '>' && depth === 0) {
            const tag = src.slice(m.index!, i + 1)
            if (/\sicon=/.test(tag)) out.push({ file: n, tag, muted: /iconTone="muted"/.test(tag) })
            break
          }
        }
      }
    }
    return out
  }

  /** The only file whose section glyphs are allowed to be coral: `DesignPanel`'s control sections
   *  mark a live, primary thing, which is the exception `settingsUI`'s own doc names. Listed, not
   *  pattern-matched, so a new coral glyph anywhere else fails instead of joining a category. */
  const CORAL_IS_MEANT_HERE = new Set(['DesignPanel.tsx'])

  it('every decorative section glyph is muted', () => {
    const sections = iconSections()
    expect(sections.length, 'vacuity floor — the scan must find the icon-passing sections')
      .toBeGreaterThanOrEqual(6)
    const coral = sections.filter((s) => !s.muted).map((s) => s.file)
    expect([...new Set(coral)].sort(), 'coral means "alive/active/primary" — not a category glyph')
      .toEqual([...CORAL_IS_MEANT_HERE].sort())
  })

  it('the three that were muted stay muted', () => {
    // Named, because they are the measured drift; a regression here is silent (coral is the default).
    const sections = iconSections()
    const mutedIn = (file: string) => sections.filter((s) => s.file === file && s.muted).length
    expect(mutedIn('AlwaysOnConventions.tsx'), 'Always-on skills + Project instructions').toBe(2)
    expect(mutedIn('DurabilityPanel.tsx'), 'Time travel').toBe(1)
    expect(mutedIn('ProvidersPanel.tsx'), 'the nine entity glyphs, muted by the earlier cycle').toBe(1)
  })

  it('the primitive still defaults to primary, which is why the list above is needed', () => {
    // If the default ever flips to muted, this rail's job changes: coral becomes opt-in and the
    // exception list stops being load-bearing. Pin the premise so that change is deliberate.
    const src = readFileSync(join(DIR, 'settingsUI.tsx'), 'utf8')
    expect(src).toMatch(/iconTone = 'primary'/)
    expect(src, 'and the two tones must still resolve to different inks')
      .toMatch(/iconTone === 'muted' \? 'text-on-surface-low' : 'text-primary'/)
  })
})
