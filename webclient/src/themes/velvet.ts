import type { ThemeDefinition } from './types'

const velvet: ThemeDefinition = {
  id: 'velvet',
  name: 'Velvet',
  description: 'After-hours boardroom — deep plum and rose-gold trim.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#190d18',
    '--panel': '#241323',
    '--card': '#2c182b',
    '--secondary': '#3a2038',
    '--surface-highest': '#4c2b49',
    '--surface-bright': '#563153',
    '--surface-lowest': '#0e060d',
    '--text': '#f7e8f3',
    '--muted': '#d3b3c9',
    '--accent': '#f3d9c8',
    '--border': 'rgba(150, 100, 140, 0.22)',
    '--border-visible': '#6b3f64',
    '--primary': '#d4a373',
    '--primary-hover': '#e6bc8f',
    '--primary-foreground': '#3e2412',
    '--primary-light': '#e6bc8f',
    '--gradient-start': '#d4a373',
    '--gradient-end': '#b76e79',
    '--info': '#e6bc8f',
    '--info-bg': 'rgba(230, 188, 143, 0.12)',
    '--accent-muted': 'rgba(212, 163, 115, 0.14)',
    '--card-hover': '#563153',
    '--muted-bg': '#241323',
    '--graph-highlight': '#d4a373',
  },
}

export default velvet
