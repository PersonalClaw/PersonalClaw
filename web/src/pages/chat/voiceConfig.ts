import { api } from '../../lib/api'
import { invalidateKeys } from '../../lib/data/store'
import { useQuery } from '../../lib/data/useQuery'
import { refreshKinds, useChatSocket } from '../../lib/useChatSocket'
import { DEFAULT_PHRASES } from '../../ui/composer/duplex'

/** Hands-free voice knobs the composer needs (`voice.*`). */
export interface VoiceLoopConfig {
  confirmation_phrases: readonly string[]
  exit_phrases: readonly string[]
  duplex_mute_enabled: boolean
}

const VOICE_CONFIG_KEY = 'chat:voice-config'

/** The chat's voice settings: the hands-free knobs (`voice.*`), and whether a finished reply is
 *  read out on its own ("Speak replies aloud", `auto_speak`, which only counts while
 *  text-to-speech itself is on).
 *
 *  Cached and persisted like the other config reads. A failed read falls back to the shipped
 *  phrases, so hands-free still hears a confirmation, and leaves "Speak replies aloud" off.
 *
 *  Read again whenever the gateway says a voice setting changed (a `refresh` frame naming
 *  `voice`, sent whoever changed it: Settings in this tab or another, another device, a channel
 *  app) and after a reconnect, which may have missed that frame. A chat that is already open
 *  follows the setting both ways instead of keeping the value it opened with. */
export function useVoiceConfig(): { voiceCfg: VoiceLoopConfig; speakReplies: boolean } {
  const { data } = useQuery(VOICE_CONFIG_KEY, async () => {
    const [cfg, tts] = await Promise.all([api.personalclawConfig(), api.useCaseSettings('tts')])
    return { ...(cfg.voice as VoiceLoopConfig), speak_replies: !!tts.value.enabled && !!tts.value.auto_speak }
  }, { persist: true })
  useChatSocket(
    (m) => { if (refreshKinds(m).includes('voice')) invalidateKeys(VOICE_CONFIG_KEY) },
    () => invalidateKeys(VOICE_CONFIG_KEY),
  )
  return {
    voiceCfg: {
      confirmation_phrases: data?.confirmation_phrases?.length ? data.confirmation_phrases : DEFAULT_PHRASES.confirmation,
      exit_phrases: data?.exit_phrases?.length ? data.exit_phrases : DEFAULT_PHRASES.exit,
      duplex_mute_enabled: data?.duplex_mute_enabled ?? true,
    },
    speakReplies: !!data?.speak_replies,
  }
}
