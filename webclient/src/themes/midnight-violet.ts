import type { ThemeDefinition } from './types'

const midnightViolet: ThemeDefinition = {
  id: 'midnight-violet',
  name: 'Midnight Violet',
  description: 'Dark theme with a violet primary palette.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#120b26',
    '--panel': '#1b132e',
    '--card': '#201733',
    '--secondary': '#2c223d',
    '--surface-highest': '#382d49',
    '--surface-bright': '#3d314d',
    '--surface-lowest': '#0c0620',
    '--primary': '#a78bfa',
    '--primary-hover': '#c4b5fd',
    '--primary-foreground': '#2e1065',
    '--primary-light': '#c4b5fd',
    '--accent': '#e9ddfd',
    '--text': '#e9e2fd',
    '--gradient-start': '#c4b5fd',
    '--gradient-end': '#8b5cf6',
    '--info': '#c4b5fd',
    '--info-bg': 'rgba(196, 181, 253, 0.12)',
    '--accent-muted': 'rgba(167, 139, 250, 0.1)',
    '--graph-highlight': '#8b5cf6',
  },
}

export default midnightViolet
