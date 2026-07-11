import { createContext, useContext, useEffect, useMemo, useState, ReactNode } from 'react'
import {
  ThemeDefinition,
  builtinThemes,
  getBuiltinTheme,
  validateThemeDefinition,
  DEFAULT_LIGHT_ID,
  DEFAULT_DARK_ID,
} from '../themes'

type BaseTheme = 'light' | 'dark'

interface ThemeContextType {
  /** Base of the active theme — kept for backward compatibility */
  theme: BaseTheme
  themeId: string
  activeTheme: ThemeDefinition
  availableThemes: ThemeDefinition[]
  setTheme: (theme: BaseTheme) => void
  setThemeId: (id: string) => void
  toggleTheme: () => void
  installTheme: (json: unknown) => ThemeDefinition
  removeCustomTheme: (id: string) => void
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

const THEME_STORAGE_KEY = 'scf-theme-preference'
const THEME_BASE_STORAGE_KEY = 'scf-theme-base'
const CUSTOM_THEMES_STORAGE_KEY = 'scf-custom-themes'
const OVERRIDE_STYLE_ID = 'scf-theme-overrides'

function loadCustomThemes(): ThemeDefinition[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(CUSTOM_THEMES_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const themes: ThemeDefinition[] = []
    for (const item of parsed) {
      try {
        const theme = validateThemeDefinition(item)
        if (!getBuiltinTheme(theme.id) && !themes.some(t => t.id === theme.id)) {
          themes.push(theme)
        }
      } catch {
        // Skip corrupt entries rather than failing the whole app
      }
    }
    return themes
  } catch {
    return []
  }
}

function persistCustomThemes(themes: ThemeDefinition[]) {
  localStorage.setItem(CUSTOM_THEMES_STORAGE_KEY, JSON.stringify(themes))
}

function getInitialThemeId(customThemes: ThemeDefinition[]): string {
  if (typeof window !== 'undefined') {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    if (stored && (getBuiltinTheme(stored) || customThemes.some(t => t.id === stored))) {
      return stored
    }
    if (window.matchMedia('(prefers-color-scheme: light)').matches) {
      return DEFAULT_LIGHT_ID
    }
  }
  return DEFAULT_DARK_ID
}

function applyTheme(theme: ThemeDefinition) {
  const root = document.documentElement
  root.setAttribute('data-theme', theme.base)
  localStorage.setItem(THEME_STORAGE_KEY, theme.id)
  localStorage.setItem(THEME_BASE_STORAGE_KEY, theme.base)

  let styleEl = document.getElementById(OVERRIDE_STYLE_ID) as HTMLStyleElement | null
  if (!styleEl) {
    styleEl = document.createElement('style')
    styleEl.id = OVERRIDE_STYLE_ID
    document.head.appendChild(styleEl)
  }
  const declarations = Object.entries(theme.variables)
    .map(([key, value]) => `  ${key}: ${value};`)
    .join('\n')
  // :root[data-theme=...] out-specifies the stylesheet's [data-theme=dark] block
  styleEl.textContent = declarations
    ? `:root[data-theme="${theme.base}"] {\n${declarations}\n}`
    : ''
}

interface ThemeProviderProps {
  children: ReactNode
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [customThemes, setCustomThemes] = useState<ThemeDefinition[]>(loadCustomThemes)
  const [themeId, setThemeIdState] = useState<string>(() => getInitialThemeId(loadCustomThemes()))

  const availableThemes = useMemo(
    () => [...builtinThemes, ...customThemes],
    [customThemes]
  )

  const activeTheme = useMemo<ThemeDefinition>(() => {
    return (
      availableThemes.find(t => t.id === themeId) ??
      getBuiltinTheme(DEFAULT_DARK_ID)!
    )
  }, [availableThemes, themeId])

  useEffect(() => {
    applyTheme(activeTheme)
  }, [activeTheme])

  // Follow system preference only while the user has no explicit choice
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: light)')
    const handleChange = (e: MediaQueryListEvent) => {
      const stored = localStorage.getItem(THEME_STORAGE_KEY)
      if (!stored) {
        setThemeIdState(e.matches ? DEFAULT_LIGHT_ID : DEFAULT_DARK_ID)
      }
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  const setThemeId = (id: string) => {
    if (availableThemes.some(t => t.id === id)) {
      setThemeIdState(id)
    }
  }

  const setTheme = (base: BaseTheme) => {
    setThemeIdState(base === 'light' ? DEFAULT_LIGHT_ID : DEFAULT_DARK_ID)
  }

  const toggleTheme = () => {
    setThemeIdState(activeTheme.base === 'light' ? DEFAULT_DARK_ID : DEFAULT_LIGHT_ID)
  }

  const installTheme = (json: unknown): ThemeDefinition => {
    const theme = validateThemeDefinition(json)
    if (getBuiltinTheme(theme.id)) {
      throw new Error(`Theme id "${theme.id}" clashes with a built-in theme`)
    }
    setCustomThemes(prev => {
      const next = [...prev.filter(t => t.id !== theme.id), theme]
      persistCustomThemes(next)
      return next
    })
    // Activate here: callers can't setThemeId(theme.id) yet because their
    // availableThemes closure predates the state update above
    setThemeIdState(theme.id)
    return theme
  }

  const removeCustomTheme = (id: string) => {
    setCustomThemes(prev => {
      const next = prev.filter(t => t.id !== id)
      persistCustomThemes(next)
      return next
    })
    if (themeId === id) {
      setThemeIdState(activeTheme.base === 'dark' ? DEFAULT_DARK_ID : DEFAULT_LIGHT_ID)
    }
  }

  return (
    <ThemeContext.Provider
      value={{
        theme: activeTheme.base,
        themeId,
        activeTheme,
        availableThemes,
        setTheme,
        setThemeId,
        toggleTheme,
        installTheme,
        removeCustomTheme,
      }}
    >
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (context === undefined) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}
