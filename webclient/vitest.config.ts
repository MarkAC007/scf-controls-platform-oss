/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config — kept separate from vite.config.ts to avoid affecting the
// production build. The unit tests run under jsdom so React components can
// render against a DOM. ``setup-tests.ts`` wires @testing-library/jest-dom
// matchers globally.
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/setup-tests.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
  },
})
