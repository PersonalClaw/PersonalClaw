import type { ReactNode } from 'react'
import { Accessibility, FlaskConical, Palette } from 'lucide-react'
import type { AppQualityWire } from '../../lib/api'

// ── Declared quality badges (APE-4) ────────────────────────────────────────
// An app's `quality` block is a self-declaration the Store shows. For a first-party
// app it is also a promise the apps-repo CI holds to account, so a badge here is
// worth something; for a third-party app it is the author's word, which is why the
// tooltip says "declared" rather than "verified".
//
// The rule that keeps this honest is one line: A DECLARED AXIS GETS A BADGE, AN
// UNDECLARED ONE GETS NOTHING. Three states, three renderings:
//
//   declared true   → a MET badge (positive ink)
//   declared false  → a MISS badge (muted ink, different label)  — an honest miss
//   not declared    → no badge at all                            — silence
//
// The temptation is to fold the last two together and render "not tested" for both.
// That would be dishonest in the direction nobody checks: an app that never claimed
// anything would be shown failing a bar it never entered. The opposite fold — showing
// absent as a pass — is the obvious lie. So all three stay distinct, and
// qualityBadges.test.tsx asserts the three renderings differ from each other.

type Tone = 'met' | 'miss'

interface Badge {
  axis: string
  label: string
  tone: Tone
  title: string
  icon: ReactNode
}

/** Turn a declared block into the badges to render. Exported so a test can assert the
 *  DECISION separately from the markup: the honesty lives here, not in the JSX. */
export function qualityBadges(q: AppQualityWire | undefined | null): Badge[] {
  if (!q) return []
  const out: Badge[] = []
  if (q.tested !== undefined) {
    const met = q.tested === true
    out.push({
      axis: 'tested',
      label: met ? 'Tested' : 'Not tested',
      tone: met ? 'met' : 'miss',
      title: met
        ? 'Declares an automated test suite that passes in CI.'
        : 'Declares that it ships no passing test suite.',
      icon: <FlaskConical size={11} />,
    })
  }
  if (q.designSystem !== undefined) {
    const met = q.designSystem === 'v2'
    out.push({
      axis: 'designSystem',
      // "n/a" is a real third answer (a backend-only app has no UI to style) and must
      // not read as a failure — nor as silence, which is why it still gets a badge.
      label: met ? 'Design system' : q.designSystem === 'n/a' ? 'No UI' : 'Legacy UI',
      tone: met ? 'met' : 'miss',
      title: met
        ? 'Declares its UI passes the host token-lint (design tokens, no raw values).'
        : q.designSystem === 'n/a'
          ? 'Declares no frontend of its own, so the design system does not apply.'
          : 'Declares a UI that predates the current design system.',
      icon: <Palette size={11} />,
    })
  }
  if (q.a11y !== undefined) {
    const met = q.a11y === true
    out.push({
      axis: 'a11y',
      label: met ? 'Accessible' : 'Not audited',
      tone: met ? 'met' : 'miss',
      title: met
        ? 'Declares a clean axe accessibility scan of the shipping version.'
        : 'Declares that its UI has not passed an accessibility audit.',
      icon: <Accessibility size={11} />,
    })
  }
  return out
}

/** The badge row. Renders NOTHING (not an empty chrome row) when the app declared no
 *  quality block — an app that said nothing must not gain a visual slot for it. */
export function QualityBadges({ quality }: { quality?: AppQualityWire | null }) {
  const badges = qualityBadges(quality)
  if (!badges.length) return null
  return (
    <div className="flex flex-wrap gap-1" data-testid="quality-badges">
      {badges.map((b) => (
        <span
          key={b.axis}
          data-testid={`quality-${b.axis}`}
          data-tone={b.tone}
          title={b.title}
          data-type="label-s"
          className={`inline-flex items-center gap-1 rounded-pill px-1.5 py-0.5 ${
            b.tone === 'met'
              ? 'bg-surface-high text-ok'
              : 'bg-surface-high text-on-surface-low'
          }`}
        >
          {b.icon}
          {b.label}
        </span>
      ))}
    </div>
  )
}
