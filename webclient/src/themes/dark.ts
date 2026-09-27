import type { ThemeDefinition } from './types'

const dark: ThemeDefinition = {
  id: 'dark',
  name: 'Dark',
  description: 'Obsidian Command — the default dark palette.',
  base: 'dark',
  variables: {
    // Historic Dark values for tokens the base palette now derives from the
    // active theme's colours (UIP-017) — keeps the default Dark unchanged.
    '--bench-bg': '#1c2540',
    '--field-bg': '#101a35',
    '--surface': 'hsl(222.2, 47%, 11.2%)',
    '--surface-hover': 'hsl(222.2, 47%, 14%)',
    '--field-border': 'rgba(120, 150, 210, 0.28)',
    '--field-border-hover': 'rgba(66, 165, 245, 0.55)',
    '--field-ring': '0 0 0 3px rgba(66, 165, 245, 0.30)',
  },
}

export default dark
