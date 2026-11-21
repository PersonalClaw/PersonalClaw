import type { ChatTurn } from './chatTypes'

/** The cards the answer at the end of a chat's transcript shows (a tool call, an approval, an
 *  agent's question), by kind and by the id their frames name them with.
 *
 *  A card frame ends the answer's text run so the text after it starts below the card. That is
 *  right for a card the answer has not shown: it is drawn where the answer has got to. A frame for
 *  a card the answer already shows changes no paragraph. Its reducer refines the card in place, and
 *  the text after it is the same run. Such a frame is common after a re-read: the frames that
 *  arrived while the read was in flight are replayed on top of the snapshot (`snapshotReplay.ts`),
 *  and the snapshot already holds the cards they drew.
 *
 *  Kept at dispatch, where the text run's ownership is decided, because React may not have rendered
 *  the adopted snapshot yet when the frames held behind it replay. Only the answer the live frames
 *  patch counts (the last turn, as `patchLastAssistant` and the card reducers read it): an id one
 *  provider hands out again in a later turn names a new card there. So each adoption sets it from
 *  the snapshot's last answer, every card frame adds its own, and it is cleared wherever the
 *  transcript's tail moves on to another answer. */
export class ShownCards {
  private keys = new Set<string>()

  /** The cards an adopted snapshot's last answer draws, replacing the ones held before. */
  adopt(turns: readonly ChatTurn[]): void {
    const last = turns[turns.length - 1]
    this.keys = new Set(
      last?.role !== 'assistant' ? [] : last.segments.flatMap((sg) =>
        (sg.kind === 'tool' || sg.kind === 'approval' || sg.kind === 'question') && sg.id ? [`${sg.kind}:${sg.id}`] : []),
    )
  }

  /** The transcript's tail moved on (a new answer, a steer, a turn's end, a client boundary). */
  clear(): void {
    this.keys = new Set()
  }

  /** Whether the card a frame names is new to the answer. It is shown from now on. A frame that
   *  names no id is new each time. */
  arrives(kind: 'tool' | 'approval' | 'question', id: unknown): boolean {
    const name = typeof id === 'string' ? id : ''
    if (!name) return true
    const key = `${kind}:${name}`
    if (this.keys.has(key)) return false
    this.keys.add(key)
    return true
  }
}
