import type { ThemeDefinition } from './types'

const boreal: ThemeDefinition = {
  id: 'boreal',
  name: 'Boreal',
  description: 'Arctic night — blue-grey ice fields under an aurora-green sweep.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#0b1220',
    '--panel': '#111a2c',
    '--card': '#152036',
    '--secondary': '#1c2a45',
    '--surface-highest': '#263857',
    '--surface-bright': '#2c4064',
    '--surface-lowest': '#060b14',
    '--text': '#e2ecf7',
    '--muted': '#a8bdd4',
    '--accent': '#a7f3d0',
    '--border': 'rgba(100, 130, 170, 0.2)',
    '--border-visible': '#3b5170',
    '--primary': '#34d399',
    '--primary-hover': '#6ee7b7',
    '--primary-foreground': '#022c22',
    '--primary-light': '#6ee7b7',
    '--gradient-start': '#34d399',
    '--gradient-end': '#60a5fa',
    '--info': '#7dd3fc',
    '--info-bg': 'rgba(125, 211, 252, 0.12)',
    '--accent-muted': 'rgba(52, 211, 153, 0.12)',
    '--card-hover': '#2c4064',
    '--muted-bg': '#111a2c',
    '--graph-highlight': '#34d399',
  },
}

export default boreal
