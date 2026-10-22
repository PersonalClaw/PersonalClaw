/** The Automations list's one read (`api.automations`, #3816), composed from the four per-kind
 *  reads a test of the list answers: the page read the list as four reads before it read one. Each
 *  call asks the four again, so a test that changes one answer, or counts the reads of one, still
 *  does. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Read<T = any> = () => Promise<T>

export function automationsFrom(reads: { schedules?: Read; hooks?: Read; storeTriggers?: Read; callbacks?: Read }) {
  return async () => {
    const [list, hooks, stores, callbacks] = await Promise.all([
      reads.schedules ? reads.schedules() : { jobs: [] },
      reads.hooks ? reads.hooks() : [],
      reads.storeTriggers ? reads.storeTriggers() : [],
      reads.callbacks ? reads.callbacks() : [],
    ])
    return {
      jobs: list?.jobs ?? [], hooks, stores, callbacks,
      server_tz: list?.server_tz ?? '', unreadable: list?.unreadable ?? [],
    }
  }
}
