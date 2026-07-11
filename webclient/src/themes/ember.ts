import type { ThemeDefinition } from './types'

const ember: ThemeDefinition = {
  id: 'ember',
  name: 'Ember',
  description: 'Forge-warm charcoal with molten orange — heat under control.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#140b08',
    '--panel': '#1f120c',
    '--card': '#261610',
    '--secondary': '#331e14',
    '--surface-highest': '#452a1c',
    '--surface-bright': '#503020',
    '--surface-lowest': '#0b0504',
    '--text': '#fbe9df',
    '--muted': '#d9b3a0',
    '--accent': '#fed7aa',
    '--border': 'rgba(150, 95, 60, 0.22)',
    '--border-visible': '#6b4226',
    '--primary': '#f97316',
    '--primary-hover': '#fb923c',
    '--primary-foreground': '#431407',
    '--primary-light': '#fb923c',
    '--gradient-start': '#f97316',
    '--gradient-end': '#ef4444',
    '--info': '#fdba74',
    '--info-bg': 'rgba(253, 186, 116, 0.12)',
    '--accent-muted': 'rgba(249, 115, 22, 0.14)',
    '--card-hover': '#503020',
    '--muted-bg': '#1f120c',
    '--graph-highlight': '#fb923c',
  },
}

export default ember
