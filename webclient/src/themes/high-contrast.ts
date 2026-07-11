import type { ThemeDefinition } from './types'

const highContrast: ThemeDefinition = {
  id: 'high-contrast',
  name: 'High Contrast',
  description: 'Light theme with stronger borders and text for readability.',
  author: 'SCF Controls Platform contributors',
  base: 'light',
  variables: {
    '--text': '#000000',
    '--muted': 'hsl(215, 25%, 27%)',
    '--border': 'hsl(214, 25%, 60%)',
    '--border-visible': 'hsl(214, 30%, 40%)',
    '--card-border': '2px solid hsl(214, 30%, 50%)',
    '--primary': '#0d47a1',
    '--primary-hover': '#08306b',
    '--shadow-color': 'rgba(0, 0, 0, 0.25)',
    '--label-color': 'hsl(215, 25%, 27%)',
  },
}

export default highContrast
