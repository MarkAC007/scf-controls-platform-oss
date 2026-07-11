import type { ThemeDefinition } from './types'
import light from './light'
import dark from './dark'
import emerald from './emerald'
import midnightViolet from './midnight-violet'
import highContrast from './high-contrast'
import ledger from './ledger'
import harbor from './harbor'
import meadow from './meadow'
import sakura from './sakura'
import saffron from './saffron'
import glacier from './glacier'
import abyss from './abyss'
import boreal from './boreal'
import ember from './ember'
import phosphor from './phosphor'
import synthwave from './synthwave'
import velvet from './velvet'

export type { ThemeDefinition } from './types'
export { validateThemeDefinition } from './types'

export const DEFAULT_LIGHT_ID = 'light'
export const DEFAULT_DARK_ID = 'dark'

/**
 * Built-in theme registry. To contribute a theme, add a file in this
 * directory and register it here — see README.md.
 * Ordered: bases first, then light-based, then dark-based themes.
 */
export const builtinThemes: ThemeDefinition[] = [
  light,
  dark,
  // light base
  emerald,
  glacier,
  harbor,
  highContrast,
  ledger,
  meadow,
  saffron,
  sakura,
  // dark base
  abyss,
  boreal,
  ember,
  midnightViolet,
  phosphor,
  synthwave,
  velvet,
]

export function getBuiltinTheme(id: string): ThemeDefinition | undefined {
  return builtinThemes.find(t => t.id === id)
}
