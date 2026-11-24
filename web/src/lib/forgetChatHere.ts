import { clearDraft } from './drafts'
import { clearChat } from './outbox'

/** What this browser kept of the chat *key* goes with it: its draft, and every send waiting for
 *  the gateway, so nothing is ever sent into a deleted chat (#4012). */
export async function forgetChatHere(key: string): Promise<void> {
  await Promise.all([clearDraft(key), clearChat(key)])
}
