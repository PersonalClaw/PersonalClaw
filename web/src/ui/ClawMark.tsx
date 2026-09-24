import { motion, useReducedMotion } from 'framer-motion'

/** PersonalClaw brand mark — the claw silhouette painted with the ACTIVE scheme's
 *  gradient (reads --grad-1..4, which the appearance store re-tints per scheme),
 *  so the logo tracks Coral/Jade/Lavender/etc. + any custom fork automatically.
 *
 *  `blob` wraps the mark in a soft, scheme-tinted shape that slowly morphs its
 *  border-radius through organic keyframes (the "liquid / alive" thinking feel) —
 *  used on the large thinking indicator, not the tiny inline lockups. Respects
 *  reduced-motion (the morph + rotate go static). */
export function ClawMark({ size = 24, animated = false, idGradient = 'claw-grad', blob = false }: {
  size?: number; animated?: boolean; idGradient?: string; blob?: boolean
}) {
  const reduce = useReducedMotion()
  const spin = animated && !reduce
  const svg = (
    <motion.svg
      width={size}
      height={size}
      viewBox="0 0 512 512"
      role="img"
      aria-label="PersonalClaw"
      animate={spin ? { rotate: [0, 6, -3, 0] } : undefined}
      transition={spin ? { duration: 7, ease: 'easeInOut', repeat: Infinity } : undefined}
      style={{ display: 'block' }}
    >
      <defs>
        <linearGradient id={idGradient} x1="0" y1="0" x2="512" y2="512" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--grad-1)" />
          <stop offset="0.45" stopColor="var(--grad-2)" />
          <stop offset="0.75" stopColor="var(--grad-3)" />
          <stop offset="1" stopColor="var(--grad-4)" />
        </linearGradient>
      </defs>
      <path
        fill={`url(#${idGradient})`}
        d="M256 16C106 76 46 226 46 226c0 45 60 90 90 90 90 0 180-195 135-285l-15-15zm45 15c30 60 0 135 0 135 120 30 120 180 75 330 75-75 90-150 90-210 0-90-15-225-165-255z"
      />
    </motion.svg>
  )
  if (!blob) return svg

  // Blob halo: a scheme-tinted soft square whose border-radius morphs between
  // organic asymmetric shapes, with the claw mark centered on top. Padding scales
  // with the mark so the halo reads as a living surround, not a hard tile.
  const pad = Math.round(size * 0.42)
  return (
    <motion.div
      aria-hidden
      style={{
        display: 'grid', placeItems: 'center', padding: pad,
        background: 'radial-gradient(circle at 50% 45%, color-mix(in srgb, var(--grad-2) 24%, transparent), transparent 70%)',
      }}
      animate={reduce ? undefined : {
        borderRadius: [
          '42% 58% 63% 37% / 45% 45% 55% 55%',
          '58% 42% 40% 60% / 60% 42% 58% 40%',
          '38% 62% 56% 44% / 52% 58% 42% 48%',
          '42% 58% 63% 37% / 45% 45% 55% 55%',
        ],
      }}
      transition={reduce ? undefined : { duration: 8, ease: 'easeInOut', repeat: Infinity }}
    >
      {svg}
    </motion.div>
  )
}
