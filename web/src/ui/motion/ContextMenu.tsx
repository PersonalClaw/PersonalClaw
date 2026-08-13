import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { overlayEnter } from '../../design/motion'
import { menuCursorKeydown, useMenuCursor } from '../../lib/useMenuCursor'
import { MenuRow } from '../Popover'

export interface ContextMenuItem {
  icon?: ReactNode
  label: string
  hint?: string
  onSelect: () => void
  /** Danger-toned row (e.g. delete). */
  danger?: boolean
  disabled?: boolean
}

/** Right-click / long-press context menu. Wraps any child; on contextmenu (or a
 *  ~500ms touch long-press) it opens a portaled, glass menu at the pointer,
 *  clamped to the viewport. Keyboarded (↑/↓/Enter/Esc) and closes on
 *  outside-click/scroll. This is the scoped-actions primitive the redesign adds
 *  across rows/cards (§Goal 5) — today menus are click-only Popovers. */
export function ContextMenu({ items, children, disabled }: { items: ContextMenuItem[]; children: ReactNode; disabled?: boolean }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const longPress = useRef<number | undefined>(undefined)
  // The highlighted row is FOCUS, not a font weight — see lib/useMenuCursor for the measurement.
  const { active, move, restoreFocus, tabIndexFor } = useMenuCursor({
    containerRef: menuRef,
    count: items.length,
    openKey: pos ? `${pos.x},${pos.y}` : null,
  })

  const open = useCallback((x: number, y: number) => {
    if (disabled || items.length === 0) return
    // Clamp so the menu never overflows the viewport (approx 220×(rows*40)). The
    // Math.max(8,…) floor matters on SHORT/narrow viewports: without it, a tall
    // menu (many rows → h up to 360) opened low on a short viewport would compute
    // a negative top and clip off-screen above the fold, hiding the first (often
    // primary) row unrecoverably. Mirrors the FileTree local clamp (see bug #32).
    const w = 220, h = Math.min(items.length * 40 + 12, 360)
    setPos({
      x: Math.min(x, Math.max(8, window.innerWidth - w - 8)),
      y: Math.min(y, Math.max(8, window.innerHeight - h - 8)),
    })
  }, [disabled, items.length])

  const close = useCallback(() => setPos(null), [])
  /** Escape and selection hand focus back to the row that opened the menu; an outside-CLICK does
   *  not, because the pointer is already somewhere else. Same split as ui/Popover. */
  const closeAndReturnFocus = useCallback(() => { setPos(null); restoreFocus() }, [restoreFocus])

  useEffect(() => {
    if (!pos) return
    const onDoc = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) close() }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); closeAndReturnFocus(); return }
      // Arrows / Home / End / Tab come from the shared reducer, so the kit's five popups spell the
      // contract once. No Enter branch: focus sits on the cursor row's button, so the browser
      // activates it natively — handling it here too would fire `onSelect` TWICE per press.
      menuCursorKeydown(e, { move, dismiss: closeAndReturnFocus })
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', close, true)
    }
  }, [pos, close, closeAndReturnFocus, move])

  const bind = {
    onContextMenu: (e: React.MouseEvent) => { e.preventDefault(); open(e.clientX, e.clientY) },
    onTouchStart: (e: React.TouchEvent) => {
      const t = e.touches[0]
      longPress.current = window.setTimeout(() => open(t.clientX, t.clientY), 500)
    },
    onTouchEnd: () => { if (longPress.current) clearTimeout(longPress.current) },
    onTouchMove: () => { if (longPress.current) clearTimeout(longPress.current) },
  }

  return (
    <>
      <div {...bind}>{children}</div>
      {createPortal(
        <AnimatePresence>
          {pos && (
            <motion.div
              ref={menuRef}
              role="menu"
              aria-orientation="vertical"
              variants={overlayEnter} initial="initial" animate="animate" exit="exit"
              className="glass fixed z-50 min-w-[200px] rounded-lgi p-s"
              style={{ left: pos.x, top: pos.y, transformOrigin: 'top left' }}
            >
              {items.map((it, i) => (
                <div key={it.label} className={it.danger ? '[&_span]:!text-danger' : undefined} data-active={i === active || undefined}>
                  <MenuRow
                    // `menuitem`, not `menuitemradio`: these rows are ACTIONS (Peek / Open / Pin),
                    // so `selected` is a cursor rather than a state — an `aria-checked` on an
                    // action would claim something it does not have. The cursor's ARIA channel is
                    // FOCUS (roving tabindex below); `selected` now only paints what focus says.
                    role="menuitem"
                    tabIndex={tabIndexFor(i)}
                    disabled={it.disabled}
                    icon={it.icon}
                    label={it.label}
                    hint={it.hint}
                    selected={i === active}
                    onClick={() => { if (!it.disabled) { it.onSelect(); closeAndReturnFocus() } }}
                  />
                </div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  )
}
