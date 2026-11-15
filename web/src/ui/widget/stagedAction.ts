/** The one staged widget-action turn, in memory: the leaf the bridge stages into and drains from,
 *  and the one place a chat's address checks a nonce against (`app/linkState.ts`'s writer).
 *
 *  A leaf, importing nothing, so the chat's launch writer can check a nonce without importing the
 *  bridge, which reaches the dialog kit and the apps' SDK. The authority stays in-process: the
 *  `[UI]` text never rides the address, only a nonce that is no use without this stage
 *  (`useWidgetActionBridge`'s handoff). */

export interface StagedWidgetAction { text: string; label?: string; at: number; nonce: string }

let staged: StagedWidgetAction | null = null

/** Stage *turn*, replacing any turn staged before. */
export function stageWidgetAction(turn: StagedWidgetAction): void {
  staged = turn
}

/** The staged turn, or null. */
export function stagedWidgetAction(): StagedWidgetAction | null {
  return staged
}

/** Drop the staged turn. */
export function clearStagedWidgetAction(): void {
  staged = null
}

/** Whether *nonce* is the staged turn's own: the shell's launch carries a nonce on only that
 *  launch, so nothing an app raises can name one. */
export function isStagedWidgetNonce(nonce: unknown): nonce is string {
  return typeof nonce === 'string' && !!nonce && staged?.nonce === nonce
}
