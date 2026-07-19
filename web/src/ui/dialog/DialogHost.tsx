import { useEffect, useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { DialogShell } from './DialogShell'
import { closeDialog, subscribeDialogs, type DialogRequest, type DialogResult } from './dialogStore'

interface HostDialog extends DialogRequest { id: number }

/** Global dialog host — mount ONCE in the app shell (next to <Toaster>). It
 *  renders whatever the imperative confirm/promptInput/alertDialog API pushes
 *  onto the dialog store, so any code anywhere can raise a styled dialog with no
 *  local open-state or JSX. Stacks (a confirm raised from within a prompt) with
 *  the newest on top. */
export function DialogHost() {
  const [dialogs, setDialogs] = useState<HostDialog[]>([])

  useEffect(() => subscribeDialogs((list) => setDialogs(list as HostDialog[])), [])

  return (
    <AnimatePresence>
      {dialogs.map((d) => (
        <DialogShell
          key={d.id}
          request={d}
          onClose={(result: DialogResult) => closeDialog(d.id, result)}
        />
      ))}
    </AnimatePresence>
  )
}
