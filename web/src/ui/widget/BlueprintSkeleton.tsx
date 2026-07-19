import { motion, useReducedMotion } from 'framer-motion'
import { spring } from '../../design/motion'

interface Props {
  width?: number
  height?: number
}

export function BlueprintSkeleton({ width = 480, height = 280 }: Props) {
  const reduce = useReducedMotion()
  const strokeLen = 600
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={spring.effects}
      className="relative overflow-hidden rounded-lg"
      style={{ width: '100%', maxWidth: width, height }}
    >
      <svg
        viewBox={`0 0 ${width} ${height}`}
        fill="none"
        className={`h-full w-full ${reduce ? '' : 'blueprint-breathe'}`}
        style={{ '--bp-len': strokeLen } as React.CSSProperties}
        aria-hidden
      >
        {/* outer frame */}
        <rect
          x="16" y="16"
          width={width - 32} height={height - 32}
          rx="12"
          stroke="var(--color-outline-variant)"
          strokeWidth="1.5"
          className={reduce ? '' : 'blueprint-stroke'}
          style={{ animationDelay: '0s' }}
        />
        {/* header bar */}
        <rect
          x="32" y="32"
          width={width - 64} height="20"
          rx="4"
          stroke="var(--color-outline-variant)"
          strokeWidth="1"
          className={reduce ? '' : 'blueprint-stroke'}
          style={{ animationDelay: '0.2s' }}
        />
        {/* main content block */}
        <rect
          x="32" y="68"
          width={Math.round((width - 64) * 0.55)} height={height - 120}
          rx="8"
          stroke="var(--color-primary)"
          strokeWidth="1"
          opacity="0.5"
          className={reduce ? '' : 'blueprint-stroke'}
          style={{ animationDelay: '0.4s' }}
        />
        {/* side details — 3 lines */}
        {[0, 1, 2].map((i) => (
          <rect
            key={i}
            x={Math.round((width - 64) * 0.6) + 32}
            y={68 + i * 36}
            width={Math.round((width - 64) * 0.35)}
            height="12"
            rx="3"
            stroke="var(--color-outline-variant)"
            strokeWidth="1"
            className={reduce ? '' : 'blueprint-stroke'}
            style={{ animationDelay: `${0.6 + i * 0.15}s` }}
          />
        ))}
        {/* bottom CTA */}
        <rect
          x={Math.round(width / 2 - 60)} y={height - 48}
          width="120" height="24"
          rx="12"
          stroke="var(--color-primary)"
          strokeWidth="1.5"
          opacity="0.6"
          className={reduce ? '' : 'blueprint-stroke'}
          style={{ animationDelay: '1.0s' }}
        />
      </svg>
      {/* scanning glow band */}
      {!reduce && (
        <div className="blueprint-scan pointer-events-none absolute inset-0" />
      )}
    </motion.div>
  )
}
