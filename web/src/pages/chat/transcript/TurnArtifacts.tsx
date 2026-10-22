import { ArrowUpRight, Box, PanelRight } from 'lucide-react'
import { fvs } from '../../../design/fontWeight'
import { TextLink } from '../../../ui/TextLink'
import { useOpenBeside } from '../../../ui/OpenBeside'
import type { ChatOutput } from '../inspector/chatOutputs'

/** The artifacts a turn made, each a card under its answer (#3820: "Artifacts show as cards in the
 *  turn that made them"): one for each artifact the turn's own calls saved, by the name it was saved
 *  under. A card opens the artifact beside the conversation, in the detail tile, where the chat
 *  gives it a place, and in the Library either way. Never folded away with the steps: it is what
 *  the turn made, not how. */
export function TurnArtifacts({ artifacts }: { artifacts: Extract<ChatOutput, { kind: 'artifact' }>[] }) {
  const place = useOpenBeside()
  if (!artifacts.length) return null
  return (
    <ul aria-label="What this turn made" className="mt-s flex flex-col gap-xs">
      {artifacts.map((a) => (
        <li key={a.slug} className="flex min-w-0 items-center gap-s rounded-xl border border-outline-variant px-m py-s">
          <Box size={15} aria-hidden className="shrink-0 text-on-surface-low" />
          <span className="min-w-0 flex-1">
            <span data-type="label-s" className="block truncate text-on-surface" style={fvs(500)}>{a.name}</span>
            <span data-type="caption" className="block text-on-surface-low">Artifact</span>
          </span>
          {place && (
            <TextLink onClick={() => place.open(a.address)} size="xs" icon={PanelRight} iconSize={12} className="shrink-0"
              aria-label={`Open ${a.name} beside the conversation`}>
              Beside
            </TextLink>
          )}
          <TextLink href={`#/artifacts/${encodeURIComponent(a.slug)}`} size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}
            className="shrink-0" aria-label={`Open ${a.name} in the Library`}>
            Open
          </TextLink>
        </li>
      ))}
    </ul>
  )
}
