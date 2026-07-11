import type { ThemeDefinition } from './types'

const meadow: ThemeDefinition = {
  id: 'meadow',
  name: 'Meadow',
  description: 'Botanical field notes — sage paper and deep moss ink.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--bg': '#f6f8f3',
    '--panel': '#f6f8f3',
    '--card': '#fdfef9',
    '--secondary': '#eaf0e2',
    '--surface-lowest': '#eaf0e2',
    '--surface-bright': '#f4f8ef',
    '--muted': '#5b6b52',
    '--accent': '#1e3a29',
    '--border': 'hsl(90, 25%, 85%)',
    '--border-visible': 'hsl(95, 20%, 62%)',
    '--primary': '#3f6212',
    '--primary-hover': '#365314',
    '--primary-foreground': '#f7fee7',
    '--primary-light': '#ecfccb',
    '--gradient-start': '#65a30d',
    '--gradient-end': '#15803d',
    '--info': '#4d7c0f',
    '--info-bg': '#f0f7e0',
    '--accent-muted': 'rgba(63, 98, 18, 0.08)',
    '--card-hover': '#f0f5e8',
    '--muted-bg': '#f1f5ec',
    '--graph-highlight': '#65a30d',
  },
}

export default meadow
