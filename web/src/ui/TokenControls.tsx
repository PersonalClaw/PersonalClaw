import { useId, useState } from 'react'
import { motion } from 'framer-motion'
import { RotateCcw } from 'lucide-react'
import type { ColorToken, ScalarToken, SelectToken } from '../design/tokenRegistry'
import { useAppearance } from '../app/appearance'
import { useMode } from '../app/theme'
import { spring, bounce } from '../design/motion'

/** Shared reset control — the RotateCcw icon spins a full turn on click (a literal
 *  "rewind to default" microinteraction), consistent across all token rows. */
function ResetButton({ onReset }: { onReset: () => void }) {
  const [spins, setSpins] = useState(0)
  return (
    <button onClick={() => { onReset(); setSpins((n) => n - 1) }} title="Reset"
      className="text-on-surface-low hover:text-on-surface transition-colors">
      <motion.span className="inline-grid place-items-center" animate={{ rotate: spins * 360 }} transition={bounce.playful}>
        <RotateCcw size={15} strokeWidth={2} />
      </motion.span>
    </button>
  )
}

/** A single color token row: swatch + hex for the CURRENT mode (dark/light),
 *  plus a reset. Editing applies live. */
export function ColorControl({ token }: { token: ColorToken }) {
  const { colorValue, setColor, resetToken } = useAppearance()
  const { mode } = useMode()
  const val = colorValue(token, mode)
  return (
    <div className="flex items-center gap-m py-2">
      <label className="flex items-center gap-s cursor-pointer">
        <span
          className="size-7 rounded-sm border border-outline-variant overflow-hidden relative"
          style={{ background: val }}
        >
          <input
            type="color"
            value={val}
            onChange={(e) => setColor(token.varName, mode, e.target.value)}
            className="absolute inset-0 opacity-0 cursor-pointer"
            aria-label={`${token.label} color`}
          />
        </span>
      </label>
      <span className="flex-1 text-on-surface text-[0.875rem]">{token.label}</span>
      <input
        value={val}
        onChange={(e) => { const v = e.target.value; if (/^#[0-9a-fA-F]{6}$/.test(v)) setColor(token.varName, mode, v) }}
        className="w-[88px] bg-surface-high rounded-md px-s py-1 text-on-surface-var text-[0.8125rem] font-mono outline-none focus:ring-1 focus:ring-primary"
        spellCheck={false}
      />
      <ResetButton onReset={() => resetToken(token.varName)} />
    </div>
  )
}

/** A select token row: label + segmented options + reset. */
export function SelectControl({ token }: { token: SelectToken }) {
  const { selectValue, setSelect, resetToken } = useAppearance()
  const val = selectValue(token)
  const indicatorId = `tokensel-${useId()}`
  return (
    <div className="flex items-center gap-m py-2">
      <span className="flex-1 min-w-0 text-on-surface text-[0.875rem]">{token.label}</span>
      {/* the pill group hugs its content (was flex-1 → the tinted track stretched
          across the whole row); label takes the slack, group sits right, pills fit. */}
      <div className="flex shrink-0 flex-wrap justify-end gap-1 rounded-2xl bg-surface-high p-1">
        {token.options.map((opt) => {
          const on = opt === val
          return (
            <button
              key={opt}
              onClick={() => setSelect(token.varName, opt)}
              className={`relative rounded-pill px-m h-7 text-[0.8125rem] capitalize transition-colors ${on ? 'text-on-primary' : 'text-on-surface-var hover:text-on-surface'}`}
            >
              {/* liquid active pill — slides between options via layoutId instead of
                  the primary fill jumping instantly from one option to another. */}
              {on && <motion.span layoutId={indicatorId} transition={spring.spatialFast} className="absolute inset-0 rounded-pill bg-primary" />}
              <span className="relative">{opt}</span>
            </button>
          )
        })}
      </div>
      <ResetButton onReset={() => resetToken(token.varName)} />
    </div>
  )
}

/** A scalar token row: label + value + slider + reset. */
export function ScalarControl({ token }: { token: ScalarToken }) {
  const { scalarValue, setScalar, resetToken } = useAppearance()
  const val = scalarValue(token)
  // Format by the token's declared unit: px/% round to an integer; a specific
  // unit (s, °, …) is appended to a 1-decimal value; a UNITLESS token is a
  // multiplier, shown as "1.00×".
  const u = token.unit ?? ''
  const display = u === 'px' || u === '%'
    ? `${Math.round(val)}${u}`
    : u
      ? `${val.toFixed(1)}${u}`
      : `${val.toFixed(2)}×`
  return (
    <div className="flex items-center gap-m py-2">
      <span className="flex-1 text-on-surface text-[0.875rem]">{token.label}</span>
      <span className="w-[56px] text-right text-on-surface-low text-[0.8125rem] font-mono tabular-nums">{display}</span>
      <input
        type="range"
        min={token.min} max={token.max} step={token.step}
        value={val}
        onChange={(e) => setScalar(token.varName, Number(e.target.value))}
        className="w-[180px] accent-primary"
        aria-label={token.label}
      />
      <ResetButton onReset={() => resetToken(token.varName)} />
    </div>
  )
}
