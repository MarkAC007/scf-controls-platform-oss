import type { ThemeDefinition } from './types'

const glacier: ThemeDefinition = {
  id: 'glacier',
  name: 'Glacier',
  description: 'Pale ice-blue light with deep teal water beneath.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--bg': '#f4f9fb',
    '--panel': '#f4f9fb',
    '--card': '#fdfeff',
    '--secondary': '#e3eff4',
    '--surface-lowest': '#e3eff4',
    '--surface-bright': '#f0f7fa',
    '--muted': '#4f6b76',
    '--accent': '#0f3b4c',
    '--border': 'hsl(195, 35%, 86%)',
    '--border-visible': 'hsl(195, 25%, 62%)',
    '--primary': '#0e7490',
    '--primary-hover': '#155e75',
    '--primary-foreground': '#ecfeff',
    '--primary-light': '#cffafe',
    '--gradient-start': '#22d3ee',
    '--gradient-end': '#0e7490',
    '--info': '#0891b2',
    '--info-bg': '#e0f7fa',
    '--accent-muted': 'rgba(14, 116, 144, 0.08)',
    '--card-hover': '#edf5f8',
    '--muted-bg': '#eff6f9',
    '--graph-highlight': '#06b6d4',
  },
}

export default glacier
