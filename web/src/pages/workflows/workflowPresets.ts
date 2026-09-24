import { Code2, Compass, Palette, Target, Telescope, type LucideIcon } from 'lucide-react'
import type { PresetDef } from '../../ui/PresetEmptyState'
import type { WorkflowDefSummary } from '../../lib/api'
import { templateForKind } from './containerKey'

/** The preset cards the EMPTY Runs tab offers (PEP-2), built from the bundled
 *  templates rather than from copy written here.
 *
 *  The gap this closes is the same one `templateSuggest` names — "the templates tab
 *  lists a dozen bundled workflows by NAME, and a user who knows what they want to DO
 *  should not have to already know that a coding job is called `code-project`" — but
 *  at the moment BEFORE the user can describe anything. `startFromTemplate` asks
 *  "what do you want to do?"; that question assumes a model of what workflows are
 *  for, which is exactly what a newcomer on an empty Runs tab does not have. The old
 *  empty state's one CTA went to the Definitions LIST, so the first thing a newcomer
 *  met was twenty-odd machine names — a signpost to the ontology, not a way in.
 *
 *  ⚠️ WHY THE CARD'S WORDS COME FROM THE DEFINITION. Two of the three lines on a card
 *  are the template's own data: `summary` is its NAME and `description` is its
 *  `description` field, straight off `GET /api/workflows`. Only the `title` is authored
 *  here, and it names the KIND, not the template — the same five kinds
 *  `KIND_TO_TEMPLATE` already enumerates. Restating a template's purpose in a card
 *  would be a second copy of one fact, and the card is the copy that rots.
 *
 *  Showing the machine name as the accent line is deliberate, not a leak: it is the
 *  vocabulary every other workflow surface uses (rows, run titles, the suggest
 *  dialog's fallback), so a preset card teaches it once instead of hiding it until the
 *  user needs it. */

/** One kind offered as a card: its icon and its human label. The TEMPLATE is resolved
 *  through {@link templateForKind}, never named here — a second table of kind→template
 *  would be the drift `KIND_TO_TEMPLATE`'s backend-parity test exists to prevent.
 *
 *  Order is leverage order, not alphabetical: the two kinds a newcomer most often
 *  arrives with (code, research) lead, and `general` is last because it is the
 *  fallback kind rather than a thing to reach for. */
const KIND_CARDS: Array<{ kind: string; icon: LucideIcon; title: string }> = [
  { kind: 'code', icon: Code2, title: 'Work on code' },
  { kind: 'research', icon: Telescope, title: 'Research a topic' },
  { kind: 'design', icon: Palette, title: 'Design something' },
  { kind: 'goal', icon: Target, title: 'Pursue a goal' },
  { kind: 'general', icon: Compass, title: 'Plan a project' },
]

/** What picking a workflow preset seeds: the template NAME, which is all the existing
 *  start flow takes. Deliberately not a richer payload — `start()` already fetches the
 *  definition and asks for whatever inputs it declares, so a preset that pre-answered
 *  those questions would be a second create path rather than a seeded one. */
export type WorkflowPrefill = string

/** The presets offerable on THIS install, in {@link KIND_CARDS} order.
 *
 *  A kind whose template is absent from `defs` is dropped rather than shown: offering a
 *  card that resolves to a template the install does not ship is a dead menu entry, and
 *  the failure would only appear on click. That filter is also why the caller must
 *  handle an EMPTY result — an install with no bundled templates has no presets to
 *  offer, and a preset grid with nothing in it is worse than the plain empty state. */
export function workflowPresets(defs: WorkflowDefSummary[]): PresetDef<WorkflowPrefill>[] {
  const byName = new Map(defs.map((d) => [d.name, d]))
  const out: PresetDef<WorkflowPrefill>[] = []
  for (const card of KIND_CARDS) {
    const template = templateForKind(card.kind)
    const def = template ? byName.get(template) : undefined
    if (!def) continue
    out.push({
      id: card.kind,
      icon: card.icon,
      title: card.title,
      summary: def.name,
      description: def.description,
      prefill: def.name,
    })
  }
  return out
}
