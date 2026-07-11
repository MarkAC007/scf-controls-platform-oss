import type { ThemeDefinition } from './types'

const abyss: ThemeDefinition = {
  id: 'abyss',
  name: 'Abyss',
  description: 'Deep-sea operations room — bioluminescent teal on midnight water.',
  author: 'SCF Controls Platform contributors',
  base: 'dark',
  variables: {
    '--bg': '#050f17',
    '--panel': '#0a1b28',
    '--card': '#0d2333',
    '--secondary': '#133146',
    '--surface-highest': '#1b425c',
    '--surface-bright': '#20506e',
    '--surface-lowest': '#02080d',
    '--text': '#d8f0f5',
    '--muted': '#9fc3cd',
    '--accent': '#c2f1ec',
    '--border': 'rgba(60, 110, 130, 0.2)',
    '--border-visible': '#2e5b70',
    '--primary': '#2dd4bf',
    '--primary-hover': '#5eead4',
    '--primary-foreground': '#033f38',
    '--primary-light': '#5eead4',
    '--gradient-start': '#22d3ee',
    '--gradient-end': '#2dd4bf',
    '--info': '#67e8f9',
    '--info-bg': 'rgba(103, 232, 249, 0.12)',
    '--accent-muted': 'rgba(45, 212, 191, 0.12)',
    '--card-hover': '#20506e',
    '--muted-bg': '#0a1b28',
    '--graph-highlight': '#2dd4bf',
  },
}

export default abyss
