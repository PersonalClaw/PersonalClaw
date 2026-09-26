import { api } from '../lib/api'
import { notify } from './appSdk'

/** Turn *app* on or off: the one call every switch for an app makes. That is the Apps page's
 *  Activate / Deactivate (its card and its detail panel), and the switch on each of its providers in
 *  Settings → Providers. A provider is its app's and has no on/off of its own, so turning one off
 *  unloads the app and turning it on loads the app from its files. The two pages never disagree
 *  about whether it is on.
 *
 *  An activation can land with a provider of the app refused: a tool it offers has a name another
 *  provider holds, so that provider is off while the app is on. The answer names it, and that is
 *  said out loud here (its card in Settings → Providers keeps the same sentence). */
export async function setActivation(app: { name: string; enabled: boolean }): Promise<void> {
  if (app.enabled) { await api.disableApp(app.name); return }
  const { providerErrors = [] } = await api.enableApp(app.name)
  for (const why of providerErrors) notify(why, 'error')
}
