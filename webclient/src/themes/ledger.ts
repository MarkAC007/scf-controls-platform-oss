import type { ThemeDefinition } from './types'

const ledger: ThemeDefinition = {
  id: 'ledger',
  name: 'Ledger',
  description: "Banker's ivory paper with oxblood ink and hunter-green rulings.",
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--bg': '#faf6ee',
    '--panel': '#faf6ee',
    '--card': '#fffdf7',
    '--secondary': '#f1e9d8',
    '--surface-lowest': '#f1e9d8',
    '--surface-bright': '#fbf8f0',
    '--surface-highest': '#fffdf7',
    '--text': '#2a2118',
    '--muted': '#6b5d4a',
    '--label-color': '#6b5d4a',
    '--accent': '#5c1a1b',
    '--border': 'hsl(38, 30%, 82%)',
    '--border-visible': 'hsl(38, 25%, 65%)',
    '--card-border': '1px solid hsl(38, 30%, 78%)',
    '--primary': '#8c2b2d',
    '--primary-hover': '#6d1f21',
    '--primary-foreground': '#fff8ef',
    '--primary-light': '#f3ddd3',
    '--gradient-start': '#8c2b2d',
    '--gradient-end': '#356648',
    '--info': '#356648',
    '--info-bg': '#e4efe4',
    '--accent-muted': 'rgba(140, 43, 45, 0.08)',
    '--card-hover': '#f5eddc',
    '--muted-bg': '#f7f1e3',
    '--graph-highlight': '#8c2b2d',
  },
}

export default ledger
