/** Adopting a server snapshot into a transcript that is still receiving frames.
 *
 *  A chat rebuilds its transcript from session detail on three event-driven paths: the mount
 *  load (a reload, a deep link, the session-create remount), a reconnect, and a rewind. The
 *  snapshot is a point in time; the socket keeps delivering frames while it is in flight, and
 *  each path used to settle that race its own way — a length floor, a finalization fence, a
 *  counter of ended turns — or not at all. A frame that landed while the read was in flight was
 *  either overwritten by the older snapshot (a reload lost the words streamed during its round
 *  trip) or painted beside it (#3513: the persisted answer AND its terminal chunks).
 *
 *  One rule replaces them: the transcript is a snapshot plus the frames that arrived after it,
 *  in order. While a read is in flight its frames are HELD; when it lands, the snapshot is
 *  adopted and the held frames are replayed on top through the same handler that applies live
 *  ones. Replay is idempotent where it has to be — chunks carry the gateway's stamp and a
 *  snapshot reports the newest stamp it holds, so a chunk the snapshot already shows is
 *  dropped; tool, approval and queue frames refine by id — so it does not matter which side of
 *  the snapshot a frame fell on.
 *
 *  Reads can overlap (a rewind during the mount load). A snapshot replaces the transcript only
 *  if it was issued after the one on screen — an older read resolving later is older than what
 *  is painted — and a snapshot that does replace it gets every frame since its own issue.
 *
 *  A hold is a round trip, so it is bounded: the caller `release`s a read too slow to hold the
 *  stream for (a gateway under load answered in 25-45 s). Its frames then flow live, and are
 *  still recorded: when the read lands and is adopted, the frames it painted over are applied
 *  again on top of it — the ones `reapplicable` says the adoption erased; a frame whose effect
 *  lives outside the transcript survives an adoption and is not repeated. So a slow read costs
 *  a late repaint, never a frame. */
export class SnapshotReplay<F> {
  private issued = 0
  // The read whose snapshot is on screen; an older one never replaces it.
  private adopted = 0
  // The reads in flight: where their frames start in `log`, and whether they still hold them.
  private reads = new Map<number, { from: number; holding: boolean }>()
  // Every frame since the oldest read in flight was issued, in arrival order. The first
  // `applied` of them are on screen; the rest are held.
  private log: F[] = []
  private applied = 0

  constructor(private readonly reapplicable: (frame: F) => boolean) {}

  /** A snapshot read is being issued. Its frames are held until it settles or is released. */
  begin(): number {
    this.issued += 1
    this.reads.set(this.issued, { from: this.log.length, holding: true })
    return this.issued
  }

  /** Whether any read is in flight — held or released. */
  busy(): boolean {
    return this.reads.size > 0
  }

  /** A live frame arrived: `true` if it is held for replay, `false` if it is to be applied now. */
  hold(frame: F): boolean {
    if (this.reads.size === 0) return false
    this.log.push(frame)
    if (this.holding()) return true
    this.applied = this.log.length
    return false
  }

  /** Stop holding the stream for read `gen` (it stays adoptable). Returns the frames to apply
   *  now — the held ones, once no read in flight holds them any more. */
  release(gen: number): F[] {
    const read = this.reads.get(gen)
    if (!read?.holding) return []
    read.holding = false
    return this.holding() ? [] : this.flush()
  }

  /** Read `gen` settled. `adopt` is null when it failed; otherwise it is called only if this
   *  snapshot is newer than the one on screen, and reports whether it replaced the transcript.
   *  Returns the frames to apply now, in order: after an adoption, every frame since this read
   *  was issued that the adoption painted over, then the held ones; otherwise the held ones,
   *  once nothing holds them. */
  settle(gen: number, adopt: (() => boolean) | null): F[] {
    const read = this.reads.get(gen)
    this.reads.delete(gen)
    let out: F[] = []
    if (read && adopt && gen > this.adopted && adopt()) {
      this.adopted = gen
      out = this.log.slice(Math.min(read.from, this.applied), this.applied).filter(this.reapplicable)
      out.push(...this.flush())
    } else if (!this.holding()) {
      out = this.flush()
    }
    if (this.reads.size === 0) {
      this.log = []
      this.applied = 0
    }
    return out
  }

  private holding(): boolean {
    for (const read of this.reads.values()) if (read.holding) return true
    return false
  }

  private flush(): F[] {
    const out = this.log.slice(this.applied)
    this.applied = this.log.length
    return out
  }
}
