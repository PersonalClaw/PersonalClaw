/** "YOLO was just switched here": rung by the one writer of `agent.yolo` (`setAgentYolo`) once the
 *  gateway took the change, so the corner's YOLO chip reads the status again at once rather than at
 *  the next ring. The gateway's own `refresh` ring (kind `yolo`) covers every other way it moves. */
const YOLO_CHANGED = 'personalclaw:yolo-changed'

export function yoloChanged(): void {
  window.dispatchEvent(new Event(YOLO_CHANGED))
}

/** Call *cb* each time YOLO is switched here; returns the way to stop. */
export function onYoloChanged(cb: () => void): () => void {
  window.addEventListener(YOLO_CHANGED, cb)
  return () => window.removeEventListener(YOLO_CHANGED, cb)
}
