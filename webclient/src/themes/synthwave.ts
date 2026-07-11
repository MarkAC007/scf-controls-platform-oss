import type { ThemeDefinition } from './types'

const synthwave: ThemeDefinition = {
  id: 'synthwave',
  name: 'Synthwave',
  description: 'Night-drive neon — hot magenta and cyan over deep indigo.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#12071f',
    '--panel': '#1a0b2e',
    '--card': '#200e38',
    '--secondary': '#2c1548',
    '--surface-highest': '#3a1d5e',
    '--surface-bright': '#43246b',
    '--surface-lowest': '#0a0314',
    '--text': '#f3e5ff',
    '--muted': '#c9b3e0',
    '--accent': '#f5d0fe',
    '--border': 'rgba(120, 80, 170, 0.22)',
    '--border-visible': '#5b3a85',
    '--primary': '#ec4899',
    '--primary-hover': '#f472b6',
    '--primary-foreground': '#3b0764',
    '--primary-light': '#f472b6',
    '--gradient-start': '#f472b6',
    '--gradient-end': '#22d3ee',
    '--info': '#67e8f9',
    '--info-bg': 'rgba(103, 232, 249, 0.12)',
    '--accent-muted': 'rgba(236, 72, 153, 0.14)',
    '--card-hover': '#43246b',
    '--muted-bg': '#1a0b2e',
    '--graph-highlight': '#d946ef',
  },
}

export default synthwave
