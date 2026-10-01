import type { MemoryMode } from '../../lib/api'

/** What each memory mode does with memory and with the chat itself: the one owner of these
 *  words, read by the chat header's mode picker and by the notice above the composer.
 *
 *  Every clause is a backend fact. Reads: `blocks_reads` is `memory_mode == 'temporary'` alone
 *  (state.py), so incognito injects memory context as a persistent chat does and only its writes
 *  are suppressed. Writes: the stores refuse every write made for an incognito or temporary chat,
 *  by any path, and the embedding functions embed nothing for one (`memory_writes.py`), so its
 *  memory is searched by keyword. Background models: neither mode is read to one — no model titles
 *  it (it is called by its mode), proposes its follow-ups or tags, condenses its history, or sees it
 *  among the recent chats suggestions are made from (`memory_writes.blocks_background_models`).
 *  History: both modes are kept out of the chat list and its search. An incognito chat's
 *  transcript is still saved, and a reopened one is restored from it.
 *  A Temporary chat's transcript lasts only while its session runs (a reload keeps it): when the gateway stops or
 *  restarts, however it stops, the transcript and the files attached to it are deleted and the
 *  chat never opens again (`dashboard/chat_forget.py`). `memoryModeNoticeIsTrue.test.ts` holds
 *  these words to those facts. */
export const MEMORY_MODES: { id: MemoryMode; label: string; hint: string }[] = [
  { id: 'persistent', label: 'Persistent', hint: 'Remember across sessions' },
  { id: 'temporary', label: 'Temporary', hint: 'No memory read or written, and forgotten when the session ends' },
  { id: 'incognito', label: 'Incognito', hint: 'Reads memory, writes nothing back' },
]

/** The notice above the composer of a chat that is not persistent — said before the user types,
 *  because such a chat looks like any other. */
export const MEMORY_MODE_NOTICE: Record<Exclude<MemoryMode, 'persistent'>, string> = {
  incognito:
    'Incognito — memory is still read for context, but nothing from this chat is written back to it '
    + 'or sent to the embedding model, and no background model reads it for a title, follow-ups or suggestions. '
    + 'The chat stays out of your chat history and search, though PersonalClaw still keeps its transcript.',
  temporary:
    'Temporary — memory is neither read nor written, and this chat is forgotten when its session ends: '
    + 'when PersonalClaw stops or restarts, its messages and the files attached to it are deleted. '
    + 'Until then it stays out of your chat history and search, and no background model reads it for a '
    + 'title, follow-ups or suggestions.',
}
