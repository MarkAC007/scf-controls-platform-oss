import type { ThemeDefinition } from './types'

const sakura: ThemeDefinition = {
  id: 'sakura',
  name: 'Sakura',
  description: 'Blossom-soft pink with deep raspberry ink.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--bg': '#fdf6f8',
    '--panel': '#fdf6f8',
    '--card': '#fffbfc',
    '--secondary': '#fbe8ee',
    '--surface-lowest': '#fbe8ee',
    '--surface-bright': '#fdf3f6',
    '--muted': '#7a5c6b',
    '--accent': '#831843',
    '--border': 'hsl(340, 40%, 88%)',
    '--border-visible': 'hsl(340, 30%, 70%)',
    '--primary': '#db2777',
    '--primary-hover': '#be185d',
    '--primary-foreground': '#ffffff',
    '--primary-light': '#fce7f3',
    '--gradient-start': '#f472b6',
    '--gradient-end': '#db2777',
    '--info': '#c026d3',
    '--info-bg': '#fae8ff',
    '--accent-muted': 'rgba(219, 39, 119, 0.08)',
    '--card-hover': '#fbeef2',
    '--muted-bg': '#fbf0f4',
    '--graph-highlight': '#db2777',
  },
}

export default sakura
