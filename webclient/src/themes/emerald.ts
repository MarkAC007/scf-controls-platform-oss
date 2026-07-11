import type { ThemeDefinition } from './types'

const emerald: ThemeDefinition = {
  id: 'emerald',
  name: 'Emerald',
  description: 'Light theme with a calm green primary palette.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--accent': 'hsl(161, 84%, 15%)',
    '--primary': '#047857',
    '--primary-hover': '#065f46',
    '--primary-light': '#d1fae5',
    '--accent-muted': 'rgba(4, 120, 87, 0.1)',
    '--gradient-start': '#059669',
    '--gradient-end': '#0d9488',
    '--info': 'hsl(161, 84%, 30%)',
    '--info-bg': 'hsl(152, 76%, 93%)',
    '--graph-highlight': '#059669',
  },
}

export default emerald
