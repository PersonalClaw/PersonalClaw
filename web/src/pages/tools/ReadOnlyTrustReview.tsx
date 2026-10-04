import type { McpListedTool, McpReadOnlyTrust, McpServer, McpToolPart } from '../../lib/api'

/** The owner's trust in an MCP server's read-only labels covers the tools she saw when she gave it
 *  (`mcp_read_only_trust` in the gateway). A tool the server adds later, or one whose description,
 *  inputs or labels changed, asks until she reviews it: the card says what changed, and Review shows
 *  each change and seals the tools again as shown. These are the words for both, and the question
 *  Trust asks first. A server's descriptions are its own text, so they are shown as text. */

/** What changed since she trusted the server's labels, or `null` when nothing did, she does not
 *  trust them, or no listing of the server as it is defined now is known. */
export interface TrustChanges {
  added: string[]
  changed: Array<{ name: string; parts: McpToolPart[] }>
  removed: string[]
}

export function trustChanges(trust: McpReadOnlyTrust | undefined): TrustChanges | null {
  if (!trust?.trusted || !trust.listed) return null
  const added = trust.added ?? [], changed = trust.changed ?? [], removed = trust.removed ?? []
  return added.length || changed.length || removed.length ? { added, changed, removed } : null
}

/** "a", "a and b", "a, b and c". */
export function joinWords(words: string[]): string {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

const PART_WORDS: Record<McpToolPart, string> = {
  description: 'its description',
  inputSchema: 'its inputs',
  annotations: 'its labels',
}

/** Which parts of a tool changed, in words: "its description and its labels". */
export function partWords(parts: McpToolPart[]): string {
  return joinWords(parts.map((p) => PART_WORDS[p] ?? p))
}

/** The card's line for a trusted server whose tools changed. Product copy. */
export function trustChangeSentence(server: string, c: TrustChanges): string {
  const said: string[] = []
  if (c.added.length) said.push(`added ${joinWords(c.added)}`)
  if (c.changed.length) said.push(`changed ${joinWords(c.changed.map((t) => `${t.name} (${partWords(t.parts)})`))}`)
  if (c.removed.length) said.push(`removed ${joinWords(c.removed)}`)
  const asking = c.added.length + c.changed.length
  const asks = asking === 0 ? ''
    : asking === 1 ? ' Until you review it, that tool asks before it runs.'
      : ' Until you review them, those tools ask before they run.'
  return `Since you trusted ${server}'s read-only labels, it ${joinWords(said)}.${asks}`
}

function listedTool(server: McpServer, name: string): McpListedTool | undefined {
  return server.tools.find((t): t is McpListedTool => typeof t !== 'string' && t.name === name)
}

function saysItReads(tool: McpListedTool | undefined): boolean {
  return tool?.annotations?.readOnlyHint === true
}

/** The tools the server lists now that it labels read-only: the ones a trust lets run unasked. */
export function labelledReadOnly(server: McpServer): string[] {
  const listed = server.readOnlyTrust?.listed ?? {}
  return Object.keys(listed).filter((name) => saysItReads(listedTool(server, name))).sort()
}

/** Trust's question, before the first yes. Product copy: it names what will run without asking. */
export function trustQuestion(server: McpServer): { title: string; body: string } {
  const reads = labelledReadOnly(server)
  const runs = reads.length
    ? `Tools this MCP server labels read-only will run without asking you, and in Ask and Plan mode: ${joinWords(reads)}.`
    : 'This MCP server labels none of its tools read-only right now, so trusting its labels lets nothing run without asking yet.'
  return {
    title: `Trust "${server.name}" to say which tools only read?`,
    body: `${runs} If it labels a tool that changes something as read-only, that change happens without anyone being asked. `
      + 'A tool it adds or changes later asks you until you review it here. '
      + 'Every other tool of this server still asks. No other server is affected, and open chats take this from their next message.',
  }
}

function LabelWord({ tool }: { tool: McpListedTool | undefined }) {
  return saysItReads(tool)
    ? <span className="text-on-surface">labelled read-only</span>
    : <span>not labelled read-only, so it still asks</span>
}

/** Review's body: each tool that changed since the trust, as the server lists it now. */
export function ReadOnlyTrustReview({ server, changes }: { server: McpServer; changes: TrustChanges }) {
  const description = (name: string) => listedTool(server, name)?.description?.trim() || 'It gives no description.'
  return (
    <div className="flex flex-col gap-s">
      <p>Since you trusted its read-only labels, {server.name} changed these tools. Each one asks before it runs until you review it.</p>
      <ul className="flex flex-col gap-s">
        {changes.added.map((name) => (
          <li key={`added-${name}`}>
            <code className="font-mono text-on-surface">{name}</code> is new, <LabelWord tool={listedTool(server, name)} />.
            <span className="block break-words text-on-surface-low">{description(name)}</span>
          </li>
        ))}
        {changes.changed.map((t) => (
          <li key={`changed-${t.name}`}>
            <code className="font-mono text-on-surface">{t.name}</code>: {partWords(t.parts)} changed. It is <LabelWord tool={listedTool(server, t.name)} />.
            <span className="block break-words text-on-surface-low">{description(t.name)}</span>
          </li>
        ))}
        {changes.removed.map((name) => (
          <li key={`removed-${name}`}>
            <code className="font-mono text-on-surface">{name}</code> is no longer listed.
          </li>
        ))}
      </ul>
      <p>Trusting them as they are now lets the ones labelled read-only run without asking you, and in Ask and Plan mode. If one that changes something is labelled read-only, that change happens without anyone being asked.</p>
    </div>
  )
}
