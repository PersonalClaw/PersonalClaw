import type { WorkPhase } from '../../lib/workPhase'

/** Voice mode's orb (#4963, `docs/design/agent-orb.md`): while the composer's hands-free loop is
 *  on, the 64 px orb is `listening` while it hears the owner (the microphone records and is not
 *  muted for a spoken reply), and the turn's own phase the rest of the time: thinking, writing a
 *  reply it speaks, or waiting on an approval. With no turn running it is `idle`, at rest. */
export function voiceOrbPhase({ heard, phase }: { heard: boolean; phase?: WorkPhase | null }): WorkPhase {
  if (heard) return 'listening'
  return phase ?? 'idle'
}
