import type { ThemeDefinition } from './types'

const saffron: ThemeDefinition = {
  id: 'saffron',
  name: 'Saffron',
  description: 'Cool slate neutrals lit by saffron-gold ink.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--bg': '#fafaf9',
    '--panel': '#fafaf9',
    '--card': '#ffffff',
    '--secondary': '#f0efec',
    '--surface-lowest': '#f0efec',
    '--surface-bright': '#f7f6f4',
    '--text': '#24211c',
    '--muted': '#6b6455',
    '--accent': '#713f12',
    '--border': 'hsl(40, 15%, 86%)',
    '--border-visible': 'hsl(40, 12%, 62%)',
    '--primary': '#b45309',
    '--primary-hover': '#92400e',
    '--primary-foreground': '#fffbeb',
    '--primary-light': '#fef3c7',
    '--gradient-start': '#f59e0b',
    '--gradient-end': '#b45309',
    '--info': '#a16207',
    '--info-bg': '#fef9c3',
    '--accent-muted': 'rgba(180, 83, 9, 0.08)',
    '--card-hover': '#f4f2ee',
    '--muted-bg': '#f5f4f1',
    '--graph-highlight': '#d97706',
  },
}

export default saffron
