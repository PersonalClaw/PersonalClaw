/** Where a video's frames came from, as its item says it: the ingest records the times its
 *  frames were taken at (`file_metadata.frame_times`) and the length they were spread across
 *  (`video_seconds`; absent when the length could not be read, and then they were taken every
 *  10 seconds from the start). A 6-minute walkthrough was once seen only through its first 70
 *  seconds, and nothing on the item said so. */

/** How many of the times the title lists before it says how many more there are. */
const LISTED = 10

/** A position in a video as a player shows it (the second it is in): `m:ss`, or `h:mm:ss` past an
 *  hour. */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  const h = Math.floor(whole / 3600)
  const m = Math.floor((whole % 3600) / 60)
  const s = String(whole % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

function listed(times: string[]): string {
  const shown = times.slice(0, LISTED)
  const more = times.length - shown.length
  if (more > 0) return `${shown.join(', ')} and ${more} more`
  if (shown.length < 2) return shown.join('')
  return `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`
}

/** The label and its title for a video's frames, or `null` when the item records none. */
export function framesSampled(meta?: {
  frames_sampled?: number
  frame_times?: number[]
  video_seconds?: number
}): { label: string; title: string } | null {
  const times = (meta?.frame_times ?? []).filter((t) => typeof t === 'number' && Number.isFinite(t))
  const count = meta?.frames_sampled ?? 0
  if (count < 1 || times.length < 1) return null
  const frames = `${count} frame${count === 1 ? '' : 's'}`
  const at = listed(times.map(clock))
  const length = meta?.video_seconds
  if (typeof length === 'number' && length > 0) {
    return {
      label: `${frames} across ${clock(length)}`,
      title: count === 1
        ? `1 frame was taken from the middle of this ${clock(length)} video, at ${at}.`
        : `${frames} were taken at even points across all ${clock(length)} of this video: ${at}.`,
    }
  }
  return {
    label: `${frames} from the first ${clock(times[times.length - 1])}`,
    title: `This video's length couldn't be read, so its ${frames} ${count === 1 ? 'was' : 'were'} taken every 10 seconds from its start: ${at}.`,
  }
}
