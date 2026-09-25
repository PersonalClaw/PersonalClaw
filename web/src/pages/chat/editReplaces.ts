/** Editing or rewinding an EARLIER user turn replaces every exchange below it.
 *
 *  Measured (day-56b `s24E`): the inline editor resent a middle turn without `rewind`, the
 *  server answered `{"rewound": 0}`, and the later turn was gone from disk — nothing on screen
 *  said it would be, and nothing afterwards could bring it back. Both halves live here so the
 *  two surfaces that do this (Edit & resend, Rewind to here) decide it the same way and tell the
 *  user the same thing: whether it happens, BEFORE it happens, and where the replaced turns went.
 *
 *  The rule mirrors the server's (`chat_regenerate.api_chat_session_edit_resend`): a LATER USER
 *  turn is what makes it a rewind. Editing the latest message only replaces its own reply. */
export function editReplacesLaterTurns(turns: readonly { role: string }[], turnIndex: number): boolean {
  return turns.slice(turnIndex + 1).some((t) => t.role === 'user')
}

/** Where the replaced turns go — the rewind divider under the edited message. Only a persistent
 *  chat can be branched (the server refuses to fork temporary/incognito ones), so the restore
 *  half of the promise is made only where it can be kept. */
export function replacedTurnsAreKept(canFork: boolean): string {
  return canFork
    ? 'What’s there now is kept in this chat’s history: you can view it, or restore it as a branch, from the note that appears under this message.'
    : 'What’s there now is kept in this chat’s history: you can view it from the note that appears under this message.'
}
