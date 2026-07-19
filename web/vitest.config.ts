import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config for the web app's unit/component tests. Kept separate from
// vite.config.ts (which carries the dev-server proxy/token plumbing that has no
// place in a test run). jsdom gives component tests a DOM; setup wires
// jest-dom matchers. Integration flows that need a live backend + WS
// (send→stream→render, stop, reconnect) are covered by the as-a-user
// Chrome DevTools validation pass, not mocked here.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
