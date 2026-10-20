import { ChipInput } from '../../ui/forms'
import type { WidgetMap } from '../tools/schema'

/** `x-meta.widget: "paths"` — a list of full paths, one chip each: the files an agent-starting
 *  action's job changes (`writes`). The form holds the list itself, so it saves as a list, and the
 *  server refuses a path no automation may change when the trigger is saved, saying which. */
export const PATH_WIDGETS: WidgetMap = {
  paths: ({ value, onChange, schema }) => (
    <ChipInput values={Array.isArray(value) ? value.map(String) : []} onChange={onChange} max={schema.maxItems}
      placeholder="A full path, e.g. ~/Notes/kitchen.md" ariaLabel="Add a file it may change" />
  ),
}
