import type { ThemeDefinition } from './types'

const light: ThemeDefinition = {
  id: 'light',
  name: 'Light',
  description: 'The default light palette.',
  base: 'light',
  variables: {
    // Historic Light values for tokens the base palette now derives from the
    // active theme's colours (UIP-017) — keeps the default Light unchanged.
    '--bench-bg': '#ffffff',
    '--field-bg': 'hsl(216, 45%, 98.5%)',
    '--surface': '#ffffff',
    '--surface-hover': '#f5f5f5',
    '--field-border-hover': 'hsla(210, 80%, 45%, 0.60)',
    '--field-ring': '0 0 0 3px rgba(25, 118, 210, 0.22)',
  },
}

export default light
