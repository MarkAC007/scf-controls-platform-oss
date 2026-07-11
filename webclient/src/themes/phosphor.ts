import type { ThemeDefinition } from './types'

const phosphor: ThemeDefinition = {
  id: 'phosphor',
  name: 'Phosphor',
  description: 'Green-phosphor CRT — the terminal your controls always deserved.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#041004',
    '--panel': '#072107',
    '--card': '#0a2a0a',
    '--secondary': '#0f3a0f',
    '--surface-highest': '#145214',
    '--surface-bright': '#196119',
    '--surface-lowest': '#010701',
    '--text': '#c6f6d5',
    '--muted': '#86c79a',
    '--accent': '#bbf7d0',
    '--border': 'rgba(34, 197, 94, 0.25)',
    '--border-visible': '#1f7a3d',
    '--primary': '#22c55e',
    '--primary-hover': '#4ade80',
    '--primary-foreground': '#052e16',
    '--primary-light': '#4ade80',
    '--gradient-start': '#4ade80',
    '--gradient-end': '#16a34a',
    '--info': '#86efac',
    '--info-bg': 'rgba(134, 239, 172, 0.12)',
    '--accent-muted': 'rgba(34, 197, 94, 0.14)',
    '--card-hover': '#196119',
    '--muted-bg': '#072107',
    '--graph-highlight': '#22c55e',
    '--radius': '0.25rem',
    '--card-radius': '6px',
  },
}

export default phosphor
