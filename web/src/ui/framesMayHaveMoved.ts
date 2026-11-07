/** Something over the page's widget frames went away (a ui/Modal, a shell dialog, a ui/Popover,
 *  the command palette, the snip overlay or the tour closing): a pointer resting where a frame
 *  now sits did not move onto it. The widget bridge stamps every frame it tracks as coming to rest
 *  again (`useWidgetActionBridge`'s `widgetFramesMayHaveMoved`), so an arrival right after is not
 *  read as the owner's move onto one. An event, so the overlays need not import the bridge. */
export const FRAMES_MAY_HAVE_MOVED = 'ne:frames-may-have-moved'

export function framesMayHaveMoved(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(FRAMES_MAY_HAVE_MOVED))
}
