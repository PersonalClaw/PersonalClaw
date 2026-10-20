import type { MemoryMode } from '../../lib/api'

/** What each memory mode does with memory and with the chat itself: the one owner of these
 *  words, read by the chat header's mode picker and by the notice above the composer.
 *
 *  Every clause is a backend fact. Reads: `blocks_reads` is `memory_mode == 'temporary'` alone
 *  (state.py), so incognito injects memory context as a persistent chat does and only its writes
 *  are suppressed. Writes: the stores refuse every write made for an incognito or temporary chat,
 *  by any path (`memory_writes.py`). Models: nothing of either mode goes to any model but the one
 *  the chat runs on (`memory_writes.model_may_read`, the one answer every seam that reaches a model
 *  asks): the embedding functions embed nothing for one, so its memory is searched by keyword; a
 *  tool's one-shot call runs on the chat's own model, and a tool that needs another kind of model
 *  (an image model) says it can't, in the tool process an agent CLI runs too; the turn does not
 *  fall back to another model. No background model reads it either — no model titles it (it is
 *  called by its mode), proposes its follow-ups or tags, condenses its history, or sees it among
 *  the recent chats suggestions are made from (`memory_writes.blocks_background_models`). What
 *  the person gives the chat in a form its model cannot read is the one exception: an attached
 *  file read for its text and a shared screen are read by the models set up for them
 *  (`memory_writes.reading_their_input`), and dictation and read-aloud are requests of their own.
 *  History: both modes are kept out of the chat list and its search. An incognito chat's
 *  transcript is still saved, and a reopened one is restored from it. Nor does the learning log keep
 *  any of either mode's words: a turn of one records no skill offer (`learning/surfacing_events.py`
 *  refuses work that changes no memory). Kept as for any chat: the Audit log records each tool call
 *  (`sel.log_tool_invocation`), and what a chat's work raises for the person, an approval, a question
 *  or a note, reaches the Inbox and the bell (`inbox.emit_attention_item`, `DashboardState.notify`).
 *  An audit and a consent record keep what ran and what was asked whatever the chat's mode, so the
 *  notices say so rather than the records leaving a private chat out.
 *  A Temporary chat's transcript lasts only while its session runs (a reload keeps it): when the gateway stops or
 *  restarts, however it stops, the transcript and the files attached to it are deleted and the
 *  chat never opens again (`dashboard/chat_forget.py`), and the workflow runs it started are
 *  stopped and deleted with what they produced (`workflows/private_runs.py`).
 *  `memoryModeNoticeIsTrue.test.ts` holds these words to those facts. */
export const MEMORY_MODES: { id: MemoryMode; label: string; hint: string }[] = [
  { id: 'persistent', label: 'Persistent', hint: 'Remember across sessions' },
  { id: 'temporary', label: 'Temporary', hint: 'No memory read or written, and forgotten when the session ends' },
  { id: 'incognito', label: 'Incognito', hint: 'Reads memory, writes nothing back' },
]

/** What a chat that keeps nothing still leaves as any chat does: the Audit log's record of what its
 *  agent did, and what its work raised for the person to answer or read. */
const KEPT_AS_FOR_ANY_CHAT =
  'Your Audit log records each tool call it makes, and the approvals, questions and notes it raises '
  + "reach your Inbox and notifications as any chat's do."

/** The notice above the composer of a chat that is not persistent — said before the user types,
 *  because such a chat looks like any other. */
export const MEMORY_MODE_NOTICE: Record<Exclude<MemoryMode, 'persistent'>, string> = {
  incognito:
    'Incognito — memory is still read for context, searched by keyword, but '
    + 'nothing from this chat is written back to it or sent to any model but the one it runs on: '
    + "a tool that needs a model uses this one or says it can't, and no background model reads it for a title, follow-ups or suggestions. "
    + 'Files you attach, a screen you share, your dictation and replies read aloud still use the models you set up for them. '
    + 'The chat stays out of your chat history and search, though PersonalClaw still keeps its transcript. '
    + KEPT_AS_FOR_ANY_CHAT,
  temporary:
    'Temporary — memory is neither read nor written, and this chat is forgotten when its session ends: '
    + 'when PersonalClaw stops or restarts, its messages, the files attached to it and the workflow runs it started are deleted. '
    + 'Until then it stays out of your chat history and search, and nothing from it is sent to any model but '
    + "the one it runs on: a tool that needs a model uses this one or says it can't, and no background model "
    + 'reads it for a title, follow-ups or suggestions. '
    + 'Files you attach, a screen you share, your dictation and replies read aloud still use the models you set up for them. '
    + KEPT_AS_FOR_ANY_CHAT,
}
