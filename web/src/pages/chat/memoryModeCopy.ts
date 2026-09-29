import type { MemoryMode } from '../../lib/api'

/** What each memory mode does with memory and with the chat itself: the one owner of these
 *  words, read by the chat header's mode picker and by the notice above the composer.
 *
 *  Every clause is a backend fact. Reads: `blocks_reads` is `memory_mode == 'temporary'` alone
 *  (state.py), so incognito injects memory context as a persistent chat does and only its writes
 *  are suppressed. History: both modes are kept out of the chat list and its search, yet the
 *  transcript is still saved, and a reopened chat is restored from it — so neither "saved in your
 *  history" nor "forgotten" is true, and each notice says both halves. `memoryModeNoticeIsTrue.
 *  test.ts` holds these words to those facts. */
export const MEMORY_MODES: { id: MemoryMode; label: string; hint: string }[] = [
  { id: 'persistent', label: 'Persistent', hint: 'Remember across sessions' },
  { id: 'temporary', label: 'Temporary', hint: 'No memory read or written' },
  { id: 'incognito', label: 'Incognito', hint: 'Reads memory, writes nothing back' },
]

/** The notice above the composer of a chat that is not persistent — said before the user types,
 *  because such a chat looks like any other. */
export const MEMORY_MODE_NOTICE: Record<Exclude<MemoryMode, 'persistent'>, string> = {
  incognito:
    'Incognito — memory is still read for context, but nothing from this chat is written back to it. '
    + 'The chat stays out of your chat history and search, though PersonalClaw still keeps its transcript.',
  temporary:
    'Temporary — memory is neither read nor written. '
    + 'The chat stays out of your chat history and search, though PersonalClaw still keeps its transcript.',
}
