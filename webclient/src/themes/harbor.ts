import type { ThemeDefinition } from './types'

const harbor: ThemeDefinition = {
  id: 'harbor',
  name: 'Harbor',
  description: 'Nautical chart — crisp paper, navy ink, signal-red markers.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--bg': '#f7fafc',
    '--panel': '#f7fafc',
    '--card': '#ffffff',
    '--secondary': '#e8eff5',
    '--surface-lowest': '#e8eff5',
    '--surface-bright': '#f2f7fa',
    '--muted': '#52667a',
    '--accent': '#0c2d48',
    '--border': 'hsl(210, 30%, 88%)',
    '--border-visible': 'hsl(210, 25%, 65%)',
    '--primary': '#1e40af',
    '--primary-hover': '#1e3a8a',
    '--primary-foreground': '#ffffff',
    '--primary-light': '#dbeafe',
    '--gradient-start': '#1e40af',
    '--gradient-end': '#0e7490',
    '--info': '#0369a1',
    '--info-bg': '#e0f2fe',
    '--accent-muted': 'rgba(30, 64, 175, 0.08)',
    '--card-hover': '#eef4f9',
    '--muted-bg': '#f1f6fa',
    '--graph-highlight': '#dc2626',
  },
}

export default harbor
